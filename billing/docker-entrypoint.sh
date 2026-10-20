#!/bin/sh
set -eu

# Permite ejecutar comandos de mantenimiento:
#   docker compose run --rm --no-deps billing python -m app.cli hash-password --stdin
if [ "$#" -gt 0 ]; then
  exec "$@"
fi

if [ "${AUTO_CREATE_SCHEMA:-false}" != "true" ]; then
  echo "[entrypoint] Aplicando migraciones..."
  alembic upgrade head
fi

exec uvicorn app.main:app \
  --host 0.0.0.0 \
  --port 8000 \
  --proxy-headers \
  --forwarded-allow-ips="*" \
  --workers "${WEB_WORKERS:-1}"
