#!/usr/bin/env bash
# Despliegue QA: PayRam hardened + billing + Caddy + PostgreSQL.
set -euo pipefail

APP_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$APP_DIR"
ENV_FILE="$APP_DIR/.env.qa"
COMPOSE=(docker compose -f "$APP_DIR/docker-compose.qa.yml" --env-file "$ENV_FILE")

log()  { printf '\033[1;32m[bootstrap-qa]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[bootstrap-qa]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[bootstrap-qa]\033[0m %s\n' "$*" >&2; exit 1; }

load_env() {
  local file="$1"
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
  done <"$file"
}

[[ $EUID -eq 0 ]] || die "Ejecuta como root."
BILLING_DOMAIN="${BILLING_DOMAIN:-}"
PAYRAM_DOMAIN="${PAYRAM_DOMAIN:-}"
ACME_EMAIL="${ACME_EMAIL:-}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
[[ -n "$BILLING_DOMAIN" ]] || read -rp "Dominio billing: " BILLING_DOMAIN
[[ -n "$PAYRAM_DOMAIN" ]] || read -rp "Dominio PayRam: " PAYRAM_DOMAIN
[[ -n "$ACME_EMAIL" ]] || read -rp "Email Let's Encrypt: " ACME_EMAIL

export DEBIAN_FRONTEND=noninteractive
apt-get update -y
apt-get install -y --no-install-recommends ca-certificates curl gnupg jq rsync ufw fail2ban unattended-upgrades cron openssl python3 tar gzip

if ! swapon --show | grep -q '/swapfile'; then
  fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048
  chmod 600 /swapfile; mkswap /swapfile; swapon /swapfile
  grep -q '^/swapfile' /etc/fstab || echo '/swapfile none swap sw 0 0' >> /etc/fstab
fi
sysctl -w vm.swappiness=10 >/dev/null || true
sysctl -w vm.overcommit_memory=1 >/dev/null || true
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
grep -q '^vm.overcommit_memory' /etc/sysctl.conf || echo 'vm.overcommit_memory=1' >> /etc/sysctl.conf

if ! command -v docker >/dev/null 2>&1; then
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL https://download.docker.com/linux/ubuntu/gpg -o /etc/apt/keyrings/docker.asc
  chmod a+r /etc/apt/keyrings/docker.asc
  EXPECTED_FPR="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
  ACTUAL_FPR="$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.asc | awk -F: '/^fpr:/ {print $10; exit}')"
  [[ "$ACTUAL_FPR" == "$EXPECTED_FPR" ]] || die "Fingerprint Docker no coincide."
  echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/docker.asc] https://download.docker.com/linux/ubuntu $(. /etc/os-release && echo "$VERSION_CODENAME") stable" >/etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y --no-install-recommends docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
fi
docker compose version >/dev/null 2>&1 || die "Falta docker compose."
mkdir -p /etc/docker
cat >/etc/docker/daemon.json <<'DOCKERD'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "no-new-privileges": true,
  "live-restore": true
}
DOCKERD
systemctl enable --now docker
systemctl restart docker

ufw allow OpenSSH >/dev/null || true
ufw allow 80/tcp >/dev/null || true
ufw allow 443/tcp >/dev/null || true
ufw allow 443/udp >/dev/null || true
ufw --force enable >/dev/null
systemctl enable --now fail2ban >/dev/null 2>&1 || true
systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true

if [[ ! -f "$ENV_FILE" ]]; then
  log "Generando .env.qa..."
  cp "$APP_DIR/.env.qa.example" "$ENV_FILE"
  chmod 600 "$ENV_FILE"
  SECRET_KEY="$(openssl rand -hex 32)"
  AES_KEY="$(openssl rand -hex 32)"
  POSTGRES_PASSWORD="$(openssl rand -base64 32 | tr -d '/+=' | cut -c1-32)"
  INTERNAL_POSTGRES_PASSWORD="$(openssl rand -base64 36 | tr -d '/+=' | cut -c1-36)"
  BILLING_API_KEY="$(openssl rand -hex 32)"
  [[ -n "$ADMIN_PASSWORD" ]] || ADMIN_PASSWORD="$(openssl rand -base64 24 | tr -d '/+=' | cut -c1-20)"
  python3 - "$ENV_FILE" "$BILLING_DOMAIN" "$PAYRAM_DOMAIN" "$ACME_EMAIL" "$SECRET_KEY" "$AES_KEY" "$POSTGRES_PASSWORD" "$INTERNAL_POSTGRES_PASSWORD" "$BILLING_API_KEY" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]); s=p.read_text()
vals={
 'BILLING_DOMAIN':sys.argv[2],'PAYRAM_DOMAIN':sys.argv[3],'ACME_EMAIL':sys.argv[4],
 'BASE_URL':f'https://{sys.argv[2]}','ALLOWED_HOSTS':f'{sys.argv[2]},localhost,127.0.0.1',
 'PAYRAM_BASE_URL':f'https://{sys.argv[3]}','PAYRAM_PUBLIC_URL':f'https://{sys.argv[3]}',
 'SECRET_KEY':sys.argv[5],'AES_KEY':sys.argv[6],'POSTGRES_PASSWORD':sys.argv[7],
 'INTERNAL_POSTGRES_PASSWORD':sys.argv[8],'BILLING_API_KEY':sys.argv[9],
}
out=[]
for line in s.splitlines():
    k=line.split('=',1)[0] if '=' in line else ''
    out.append(f'{k}={vals[k]}' if k in vals else line)
p.write_text('\n'.join(out)+'\n'); p.chmod(0o600)
PY
  case "$(uname -m)" in
    x86_64) ;;
    aarch64)
      sed -i "s|^PAYRAM_IMAGE=.*|PAYRAM_IMAGE=payramapp/payram@sha256:a5866d0f28d5eb5a7ddeed491f718502ac8b8ca7a228dbd2ab9c4a4968f1c6a8|" "$ENV_FILE"
      sed -i "s|^PAYRAM_APPROVED_DIGEST=.*|PAYRAM_APPROVED_DIGEST=sha256:a5866d0f28d5eb5a7ddeed491f718502ac8b8ca7a228dbd2ab9c4a4968f1c6a8|" "$ENV_FILE"
      ;;
    *) die "Arquitectura no soportada: $(uname -m)" ;;
  esac
else
  warn ".env.qa ya existe; no lo sobrescribo."
fi

load_env "$ENV_FILE"
if [[ "${BILLING_IMAGE:-}" == ghcr.io/* ]]; then
  log "Billing desde GHCR; descargando (requiere docker login ghcr.io)..."
  "${COMPOSE[@]}" pull billing
else
  log "Construyendo billing desde el repo..."
  "${COMPOSE[@]}" build billing
fi

if ! grep -q '^ADMIN_PASSWORD_HASH=scrypt\$' "$ENV_FILE"; then
  [[ -n "$ADMIN_PASSWORD" ]] || { read -rsp "Contraseña admin: " ADMIN_PASSWORD; echo; }
  log "Generando hash admin..."
  ADMIN_HASH="$(printf '%s' "$ADMIN_PASSWORD" | "${COMPOSE[@]}" run --rm --no-deps -T billing python -m app.cli hash-password --stdin)"
  python3 - "$ENV_FILE" "$ADMIN_HASH" <<'PY'
import sys
from pathlib import Path
p=Path(sys.argv[1]); s=p.read_text().splitlines(); out=[]
for line in s:
    if line.startswith('ADMIN_PASSWORD_HASH='): out.append(f'ADMIN_PASSWORD_HASH={sys.argv[2].strip()}')
    elif line.startswith('ADMIN_PASSWORD='): out.append('ADMIN_PASSWORD=')
    else: out.append(line)
p.write_text('\n'.join(out)+'\n'); p.chmod(0o600)
PY
fi

if ! "${COMPOSE[@]}" config --quiet >/dev/null 2>&1; then
  die "docker compose no pudo validar docker-compose.qa.yml."
fi
if ! ENV_FILE="$ENV_FILE" "$APP_DIR/scripts/security-gate.sh"; then
  die "Security gate NO-GO. Corrige .env.qa antes de desplegar."
fi

log "Comprobando DNS..."
SERVER_IP="$(curl -fsS https://api.ipify.org || true)"
for domain in "$BILLING_DOMAIN" "$PAYRAM_DOMAIN"; do
  resolved="$(getent ahostsv4 "$domain" | awk 'NR==1{print $1}' || true)"
  if [[ -z "$resolved" ]]; then warn "DNS: $domain no resuelve."
  elif [[ -n "$SERVER_IP" && "$resolved" != "$SERVER_IP" ]]; then warn "DNS: $domain -> $resolved, VPS $SERVER_IP."
  else log "DNS OK: $domain -> $resolved"; fi
done

log "Arrancando stack QA..."
"${COMPOSE[@]}" up -d --no-build

for _ in $(seq 1 40); do
  if "${COMPOSE[@]}" exec -T billing python -c "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" >/dev/null 2>&1; then
    log "Billing saludable."
    break
  fi
  sleep 5
done

CREDS="/root/payram-qa-credentials.txt"
{
  echo "PayRam QA — $(date -u +%FT%TZ)"
  echo "Billing: https://$BILLING_DOMAIN"
  echo "PayRam:  https://$PAYRAM_DOMAIN"
  echo "Admin:   ${ADMIN_USERNAME:-admin}"
  echo "Password: ${ADMIN_PASSWORD:-<la definida>}"
  echo
  echo "Siguiente: onboarding PayRam, wallets Base, API key en .env.qa, webhook, SMTP y prueba USD 1."
} >"$CREDS"
chmod 600 "$CREDS"

cat <<EOF

Stack QA levantado.
Billing: https://$BILLING_DOMAIN
PayRam:  https://$PAYRAM_DOMAIN
Credenciales: $CREDS

Pasos:
1) Onboarding PayRam + wallets Base + USDC + Smart Bridge + tarjetas.
2) API key de PayRam -> .env.qa (PAYRAM_API_KEY) -> docker compose -f docker-compose.qa.yml --env-file .env.qa up -d billing
3) Webhook: https://$BILLING_DOMAIN/webhooks/payram
4) Completar COMPANY_* y SMTP en .env.qa.
5) ./scripts/qa-smoke.sh
6) Pruebas reales con USD 1 y tarjeta, según docs/QA-MAINNET-TEST.md.
EOF
