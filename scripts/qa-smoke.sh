#!/usr/bin/env bash
# Smoke test del stack QA. No realiza pagos.
set -euo pipefail
APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
load_env() {
  local file="$1"
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
  done <"$file"
}
load_env "$APP_DIR/.env.qa"
COMPOSE=(docker compose -f "$APP_DIR/docker-compose.qa.yml" --env-file "$APP_DIR/.env.qa")

echo "== billing health =="
curl -fsS --max-time 15 "https://${BILLING_DOMAIN}/healthz"; echo
echo "== payram health (interno) =="
"${COMPOSE[@]}" exec -T payram wget -qO- http://127.0.0.1/api/v1/health; echo
echo "== rutas bloqueadas =="
for p in "/swagger/" "/api/v1/health" "/_next/image?url=%2Ffavicon.png&w=64&q=75"; do
  code="$(curl -sS -o /dev/null -w '%{http_code}' --max-time 10 "https://${PAYRAM_DOMAIN}${p}" || true)"
  echo "$p -> $code"
done
echo "== factura de humo USD 1 =="
curl -sS --max-time 20 -X POST "https://${BILLING_DOMAIN}/api/v1/invoices" \
  -H "X-API-Key: ${BILLING_API_KEY}" \
  -H "Content-Type: application/json" \
  -d '{"customer_name":"QA Smoke","customer_email":"qa@example.com","description":"Smoke test","items":[{"description":"QA","quantity":1,"unit_price":1.00}],"tax_rate":0,"send_email":false,"external_id":"qa-smoke-'"$(date -u +%s)"'"}'
echo
