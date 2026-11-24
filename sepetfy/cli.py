"""Komut satırı arayüzü.

    python -m sepetfy init-db         # tabloları oluştur + kaynak kataloğunu yükle
    python -m sepetfy check           # sağlık kontrolü (gizli değer göstermez)
    python -m sepetfy sources         # kaynakların erişim durumu
    python -m sepetfy backup          # anlık yedek
    python -m sepetfy costs           # maliyet özeti
    python -m sepetfy set-budget 30   # aylık bütçe (USD)
    python -m sepetfy run             # 7/24 zamanlayıcı servisi
    python -m sepetfy feeds           # feed'lerin durumu
    python -m sepetfy telegram-test   # bot token'ını doğrula, deneme mesajı gönder
    python -m sepetfy bot             # yalnızca Telegram botunu çalıştır
    python -m sepetfy feed-run AD     # bir feed'i şimdi kontrol et
    python -m sepetfy feed-import AD dosya.xml   # elle indirilen XML'i işle
"""

from __future__ import annotations

import argparse
import json
import sys

from sqlalchemy import select

from sepetfy.app.bootstrap import bootstrap


def _cmd_init_db(settings) -> int:
    from sepetfy.database import session_scope
    from sepetfy.database.seed import seed_stores

    with session_scope() as s:
        added = seed_stores(s)
    print(f"Veritabanı hazır. Eklenen kaynak sayısı: {added}")
    return 0


def _cmd_check(settings, healthcheck: bool = False) -> int:
    from sepetfy.database import ping
    from sepetfy.scheduler.runner import heartbeat_age_seconds

    ok = True
    try:
        db_ok = ping()
    except Exception as exc:  # noqa: BLE001
        db_ok = False
        print(f"Veritabanı HATA: {type(exc).__name__}")
    ok &= db_ok

    if healthcheck:
        age = heartbeat_age_seconds(settings.log_dir)
        ok &= age is not None and age < 180
        return 0 if ok else 1

    db_kind = settings.database_url.split(":", 1)[0]
    print(f"Ortam            : {settings.app_env}")
    print(f"Veritabanı       : {db_kind} -> {'OK' if db_ok else 'HATA'}")
    print(f"Yayın şalteri    : {'AÇIK' if settings.publishing_enabled else 'kapalı'}")
    print(f"DRY_RUN          : {'açık (gerçek gönderim yok)' if settings.dry_run else 'KAPALI'}")
    print(f"Yetkili onaycı   : {len(settings.admin_user_ids)} kullanıcı")
    print(f"Aylık bütçe      : {settings.monthly_budget_usd:.2f} USD (.env varsayılanı)")
    print("Entegrasyonlar   :")
    for name, configured in settings.integration_status().items():
        print(f"  - {name:9s}: {'yapılandırıldı' if configured else 'yapılandırılmadı'}")
    return 0 if ok else 1


def _cmd_sources(settings) -> int:
    from sepetfy.database import session_scope
    from sepetfy.database.models import Store

    with session_scope() as s:
        stores = s.scalars(select(Store).order_by(Store.id)).all()
        if not stores:
            print("Kaynak yok. Önce: python -m sepetfy init-db")
            return 1
        for st in stores:
            flag = "AÇIK " if st.enabled else "kapalı"
            print(f"[{flag}] {st.name:32s} {st.access_method:18s} {st.access_status}")
    return 0


def _cmd_backup(settings) -> int:
    from sepetfy.database.backup import backup_database

    out = backup_database(settings.database_url, settings.backup_dir, settings.backup_keep)
    print(f"Yedek: {out}")
    return 0


def _cmd_costs(settings) -> int:
    from sepetfy.analytics.costs import summary
    from sepetfy.database import session_scope

    with session_scope() as s:
        print(json.dumps(summary(s, settings), indent=2, ensure_ascii=False))
    return 0


def _cmd_set_budget(settings, value: float) -> int:
    from sepetfy.analytics.costs import set_budget
    from sepetfy.database import session_scope

    with session_scope() as s:
        set_budget(s, value)
    print(f"Aylık bütçe {value:.2f} USD olarak ayarlandı")
    return 0


def _print_report(report) -> int:
    print(f"Feed {report.feed}: {report.status}" + (f" — {report.message}" if report.message else ""))
    if report.stats:
        print(json.dumps(report.stats, indent=2, ensure_ascii=False))
    if report.deal_ids:
        print(f"Yeni fırsat adayları (deal id): {report.deal_ids}")
    return 0 if report.status not in ("failed", "rejected") else 1


def _cmd_feeds(settings) -> int:
    from sepetfy.collectors.feed import load_feed_configs
    from sepetfy.database import session_scope
    from sepetfy.database.models import FeedState

    configs = load_feed_configs()
    if not configs:
        print("Tanımlı feed yok (sepetfy/config/feeds/*.json)")
        return 0
    with session_scope() as s:
        for cfg in configs.values():
            st = s.get(FeedState, cfg.name)
            url = "URL var" if cfg.resolve_url() else f"URL YOK ({cfg.url_env})"
            flag = "AÇIK " if cfg.enabled else "kapalı"
            last = st.last_success_at.strftime("%Y-%m-%d %H:%M") if st and st.last_success_at else "-"
            count = st.last_item_count if st and st.last_item_count is not None else "-"
            print(f"[{flag}] {cfg.name:16s} her {cfg.interval_min:>3} dk  {url:28s} "
                  f"son başarı: {last}  ürün: {count}")
            if st and st.last_error:
                print(f"         son hata: {st.last_error}")
    return 0


def _cmd_feed_run(settings, name: str) -> int:
    from sepetfy.collectors.feed import load_feed_configs, run_feed
    from sepetfy.database import session_scope

    cfg = load_feed_configs()[name]
    with session_scope() as s:
        report = run_feed(s, cfg, settings.data_dir)
    return _print_report(report)


def _cmd_feed_import(settings, name: str, path: str) -> int:
    from pathlib import Path

    from sepetfy.collectors.feed import import_feed_file, load_feed_configs
    from sepetfy.database import session_scope

    cfg = load_feed_configs()[name]
    with session_scope() as s:
        report = import_feed_file(s, cfg, Path(path))
    return _print_report(report)


def _cmd_bot(settings) -> int:
    """Yalnızca Telegram botunu ön planda çalıştırır (geliştirme / deneme için)."""
    import signal
    import threading

    from sepetfy.approval.bot import BotRunner

    if settings.telegram_bot_token is None:
        print("TELEGRAM_BOT_TOKEN tanımlı değil (.env)")
        return 1
    stop = threading.Event()
    signal.signal(signal.SIGINT, lambda *_: stop.set())
    signal.signal(signal.SIGTERM, lambda *_: stop.set())
    print("Bot çalışıyor. Durdurmak için Ctrl+C")
    BotRunner(settings).run(stop)
    return 0


def _cmd_telegram_test(settings) -> int:
    """Token'ı doğrular ve yöneticilere deneme mesajı gönderir."""
    from sepetfy.integrations.telegram_api import TelegramAPI, TelegramError
    from sepetfy.notifications.telegram import Notifier

    if settings.telegram_bot_token is None:
        print("TELEGRAM_BOT_TOKEN tanımlı değil (.env)")
        return 1
    api = TelegramAPI(settings.telegram_bot_token.get_secret_value())
    try:
        me = api.get_me()
    except TelegramError as exc:
        print(f"Token doğrulanamadı: {exc}")
        return 1
    print(f"Token geçerli: @{me.get('username')}")
    if not settings.admin_user_ids:
        print("TELEGRAM_ADMIN_USER_IDS boş. Bota /start yazın; bot size kimliğinizi söyleyecek "
              "(önce 'python -m sepetfy bot' çalışıyor olmalı).")
        return 1
    sent = Notifier(settings, api).send("✅ Sepetfy bağlantı testi başarılı. Komutlar için /yardim")
    print(f"Deneme mesajı gönderildi: {len(sent)}/{len(settings.admin_user_ids)} yönetici")
    if not sent:
        print("Mesaj gitmediyse: Telegram'da botunuzu açıp bir kez /start yazın, sonra tekrar deneyin.")
    return 0 if sent else 1


def _cmd_run(settings) -> int:
    from sepetfy.scheduler.runner import run

    run(settings)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="sepetfy", description="Sepetfy otomasyon sistemi")
    sub = parser.add_subparsers(dest="cmd", required=True)
    sub.add_parser("init-db")
    chk = sub.add_parser("check")
    chk.add_argument("--healthcheck", action="store_true", help="Docker healthcheck modu (sessiz)")
    sub.add_parser("sources")
    sub.add_parser("backup")
    sub.add_parser("costs")
    sb = sub.add_parser("set-budget")
    sb.add_argument("usd", type=float)
    sub.add_parser("run")
    sub.add_parser("feeds")
    sub.add_parser("bot")
    sub.add_parser("telegram-test")
    fr = sub.add_parser("feed-run")
    fr.add_argument("name")
    fi = sub.add_parser("feed-import")
    fi.add_argument("name")
    fi.add_argument("path")
    args = parser.parse_args(argv)

    # Sağlık kontrolü her dakika çalışır: göç çalıştırma, sadece bağlantıyı ve heartbeat'i kontrol et
    healthcheck = args.cmd == "check" and args.healthcheck
    settings = bootstrap(create_tables=not healthcheck)
    if args.cmd == "init-db":
        return _cmd_init_db(settings)
    if args.cmd == "check":
        return _cmd_check(settings, args.healthcheck)
    if args.cmd == "sources":
        return _cmd_sources(settings)
    if args.cmd == "backup":
        return _cmd_backup(settings)
    if args.cmd == "costs":
        return _cmd_costs(settings)
    if args.cmd == "set-budget":
        return _cmd_set_budget(settings, args.usd)
    if args.cmd == "run":
        return _cmd_run(settings)
    if args.cmd == "bot":
        return _cmd_bot(settings)
    if args.cmd == "telegram-test":
        return _cmd_telegram_test(settings)
    if args.cmd == "feeds":
        return _cmd_feeds(settings)
    if args.cmd == "feed-run":
        return _cmd_feed_run(settings, args.name)
    if args.cmd == "feed-import":
        return _cmd_feed_import(settings, args.name, args.path)
    return 2


if __name__ == "__main__":
    sys.exit(main())
