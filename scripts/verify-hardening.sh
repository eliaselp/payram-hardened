#!/usr/bin/env bash
# Valida los parches dentro de un contenedor efímero, sin arrancar PayRam.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

load_env() {
  local file="$1"
  [[ -f "$file" ]] || { echo "Falta .env" >&2; exit 1; }
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
  done <"$file"
}
load_env "$APP_DIR/.env"

docker run --rm \
  --env-file "$APP_DIR/.env" \
  -e POSTGRES_HOST=db \
  -v "$APP_DIR/hardening:/hardening:ro" \
  --entrypoint python3 \
  "${PAYRAM_IMAGE:?PAYRAM_IMAGE vacío}" \
  /hardening/patch_payram.py --check
