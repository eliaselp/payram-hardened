#!/usr/bin/env bash
# Gate previo al despliegue del overlay. Falla si falta una mitigación o un
# secreto es débil. No puede certificar el código cerrado de PayRam.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"

load_env() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
  done <"$file"
}
ENV_FILE="${ENV_FILE:-$APP_DIR/.env}"
load_env "$ENV_FILE"

ERRORS=()
WARNINGS=()
fail() { ERRORS+=("$1"); }
warn() { WARNINGS+=("$1"); }

[[ "${PAYRAM_HARDENING_ENABLED:-}" == "true" ]] || fail "PAYRAM_HARDENING_ENABLED debe ser true."
[[ -x "$APP_DIR/hardening/entrypoint.sh" ]] || fail "Falta hardening/entrypoint.sh ejecutable."
[[ -f "$APP_DIR/hardening/patch_payram.py" ]] || fail "Falta hardening/patch_payram.py."

for var in PAYRAM_IMAGE POSTGRES_IMAGE CADDY_IMAGE; do
  value="${!var:-}"
  [[ -n "$value" ]] || fail "$var vacío."
  [[ "$value" == *"@sha256:"* ]] || fail "$var no está fijado por digest."
done
[[ "${PAYRAM_IMAGE:-}" == *"${PAYRAM_APPROVED_DIGEST:-__none__}"* ]] || fail "PAYRAM_IMAGE no coincide con PAYRAM_APPROVED_DIGEST."
[[ -n "${PAYRAM_APPROVED_DIGEST:-}" ]] || fail "PAYRAM_APPROVED_DIGEST vacío."
[[ -n "${BILLING_DOMAIN:-}" ]] || fail "BILLING_DOMAIN vacío."
[[ -n "${PAYRAM_DOMAIN:-}" ]] || fail "PAYRAM_DOMAIN vacío."

if [[ "${EDGE_MODE:-direct}" == "tunnel" ]]; then
  [[ -n "${CLOUDFLARE_TUNNEL_TOKEN:-}" ]] || fail "CLOUDFLARE_TUNNEL_TOKEN vacío para EDGE_MODE=tunnel."
  [[ "${CADDY_BIND_IP:-}" == "127.0.0.1" ]] || fail "En tunnel, CADDY_BIND_IP debe ser 127.0.0.1."
  [[ "${CADDYFILE:-}" == *Caddyfile.tunnel* ]] || fail "En tunnel, CADDYFILE debe ser Caddyfile.tunnel(.mtls)."
  caddyfile_path="${CADDYFILE:-deploy/qa/Caddyfile.tunnel}"
  [[ -f "$caddyfile_path" || -f "$APP_DIR/$caddyfile_path" ]] || fail "No existe $caddyfile_path."
  [[ "${CLOUDFLARED_IMAGE:-}" == *"@sha256:"* ]] || fail "CLOUDFLARED_IMAGE debe estar fijado por digest."
  if [[ "${BILLING_MTLS_ENABLED:-false}" == "true" ]]; then
    [[ "${CADDYFILE:-}" == *Caddyfile.tunnel.mtls ]] || fail "mTLS activo requiere CADDYFILE=Caddyfile.tunnel.mtls."
    [[ -f "$APP_DIR/deploy/qa/Caddyfile.tunnel.mtls" ]] || fail "Falta Caddyfile.tunnel.mtls."
  fi
fi

KNOWN=(
  "sha256:1181cd5d886a50d68565c275dad231d9a1d75735dca3ce5873f334c2f9db3791"
  "sha256:f07125333b444051415c7d4b35fead3c0a4d51834d7f1fd9e762b9b3df175029"
  "sha256:a5866d0f28d5eb5a7ddeed491f718502ac8b8ca7a228dbd2ab9c4a4968f1c6a8"
)
KNOWN_HIT=false
for d in "${KNOWN[@]}"; do
  if [[ "${PAYRAM_IMAGE:-}" == *"$d"* || "${PAYRAM_VENDOR_DIGEST:-}" == *"$d"* ]]; then
    KNOWN_HIT=true
  fi
done
if [[ "$KNOWN_HIT" == true ]]; then
  if [[ "${ALLOW_KNOWN_VULNERABLE_PAYRAM:-false}" == "true" ]]; then
    warn "Imagen 3.9.0 con CVEs conocidos (RCE Next.js). El overlay mitiga configuración/red, pero NO es un parche completo. No usar en mainnet sin aceptación de riesgo."
  else
    fail "Imagen 3.9.0 bloqueada. Define ALLOW_KNOWN_VULNERABLE_PAYRAM=true solo si aceptas el riesgo residual y el overlay está activo."
  fi
fi

[[ "${POSTGRES_PASSWORD:-}" != "" && "${POSTGRES_PASSWORD:-}" != "payram123" && "${POSTGRES_PASSWORD:-}" != *"change-me"* ]] || fail "POSTGRES_PASSWORD débil."
INTERNAL_POSTGRES_PASSWORD="${INTERNAL_POSTGRES_PASSWORD:-}"
[[ ${#INTERNAL_POSTGRES_PASSWORD} -ge 24 && "$INTERNAL_POSTGRES_PASSWORD" != *"change-me"* ]] || fail "INTERNAL_POSTGRES_PASSWORD débil."
[[ "${AES_KEY:-}" =~ ^[0-9a-fA-F]{64}$ ]] || fail "AES_KEY debe ser 64 hex."
[[ "${PAYRAM_AUTO_APPROVE:-}" == "false" ]] || fail "PAYRAM_AUTO_APPROVE debe ser false."
[[ "${PAYRAM_OTP_THRESHOLD_USD:-}" == "0" ]] || fail "PAYRAM_OTP_THRESHOLD_USD debe ser 0 (OTP para todos)."
[[ "${PAYRAM_PATCH_FRONTEND:-}" == "true" ]] || fail "PAYRAM_PATCH_FRONTEND debe ser true."
[[ -z "${SENTRY_DSN:-}" ]] || fail "SENTRY_DSN debe estar vacío para no enviar telemetría externa."
[[ "${PAYRAM_DAILY_LIMIT_USD:-}" =~ ^[0-9]+$ ]] || fail "PAYRAM_DAILY_LIMIT_USD inválido."
[[ "${PAYRAM_HOURLY_LIMIT_USD:-}" =~ ^[0-9]+$ ]] || fail "PAYRAM_HOURLY_LIMIT_USD inválido."
ARCH="$(uname -m)"
if [[ "$ARCH" == "aarch64" ]]; then
  [[ "${PAYRAM_IMAGE:-}" == *"sha256:1181cd5d"* || "${PAYRAM_VENDOR_DIGEST:-}" == *"sha256:1181cd5d"* ]] && fail "Core amd64 en host arm64. Publica una imagen arm64."
fi
if [[ "$ARCH" == "x86_64" ]]; then
  [[ "${PAYRAM_IMAGE:-}" == *"sha256:a5866d0f"* || "${PAYRAM_VENDOR_DIGEST:-}" == *"sha256:a5866d0f"* ]] && fail "Core arm64 en host x86_64."
fi

[[ "${PAYRAM_DOMAIN:-}" == *.* ]] || fail "PAYRAM_DOMAIN inválido."
[[ "${ACME_EMAIL:-}" == *@* ]] || fail "ACME_EMAIL inválido."
if grep -Rqs 'docker.sock' "$APP_DIR/docker-compose.yml" "$APP_DIR/deploy" 2>/dev/null; then
  fail "Hay un montaje de /var/run/docker.sock; elimínalo."
fi

for w in "${WARNINGS[@]}"; do printf '[security-gate] WARN: %s\n' "$w"; done
if ((${#ERRORS[@]})); then
  for e in "${ERRORS[@]}"; do printf '[security-gate] FAIL: %s\n' "$e"; done
  echo "Security gate: NO-GO."
  exit 1
fi
echo "Security gate: OK (overlay activo; riesgo residual de la imagen cerrada documentado)."
