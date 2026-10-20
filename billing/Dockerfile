FROM python:3.12-slim@sha256:a6e34c598f2467ed0e9a8d349809fcd8b5c603269512df273a0bb1784edc11b1

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /app

RUN addgroup --system --gid 10001 app \
    && adduser --system --uid 10001 --gid 10001 --home /app app

COPY requirements.lock ./
RUN pip install --no-cache-dir --require-hashes -r requirements.lock

COPY alembic.ini ./
COPY migrations ./migrations
COPY app ./app
COPY docker-entrypoint.sh ./
RUN chmod +x docker-entrypoint.sh \
    && mkdir -p /data \
    && chown -R app:app /app /data

USER app
EXPOSE 8000

HEALTHCHECK --interval=30s --timeout=10s --start-period=30s --retries=3 \
  CMD python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=5)"

ENTRYPOINT ["./docker-entrypoint.sh"]
