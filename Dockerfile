FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    TZ=Europe/Istanbul

WORKDIR /app

# Bağımlılık katmanı (kod değişince yeniden kurulmaz)
COPY pyproject.toml ./
RUN mkdir sepetfy && touch sepetfy/__init__.py \
    && pip install ".[postgres]" \
    && rm -rf sepetfy build *.egg-info

COPY sepetfy ./sepetfy
RUN pip install --no-deps . \
    && useradd --create-home --uid 10001 sepetfy \
    && mkdir -p /app/data /app/logs /app/backups \
    && chown -R sepetfy:sepetfy /app

USER sepetfy

# Ayarlar çalışma anında env_file ile verilir; imaja gizli bilgi gömülmez.
ENV DATABASE_URL=sqlite:////app/data/sepetfy.db \
    LOG_DIR=/app/logs \
    BACKUP_DIR=/app/backups

HEALTHCHECK --interval=60s --timeout=10s --start-period=30s --retries=3 \
    CMD python -m sepetfy check --healthcheck || exit 1

CMD ["sh", "-c", "python -m sepetfy init-db && exec python -m sepetfy run"]
