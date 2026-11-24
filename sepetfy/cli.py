"""Komut satırı arayüzü.

    python -m sepetfy init-db         # tabloları oluştur + kaynak kataloğunu yükle
    python -m sepetfy check           # sağlık kontrolü (gizli değer göstermez)
    python -m sepetfy sources         # kaynakların erişim durumu
    python -m sepetfy backup          # anlık yedek
    python -m sepetfy costs           # maliyet özeti
    python -m sepetfy set-budget 30   # aylık bütçe (USD)
    python -m sepetfy run             # 7/24 zamanlayıcı servisi
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
    args = parser.parse_args(argv)

    settings = bootstrap()
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
    return 2


if __name__ == "__main__":
    sys.exit(main())
