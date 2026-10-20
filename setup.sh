#!/usr/bin/env bash
# ============================================================================
#  PayRam Hardened — instalación de máximo control
# ============================================================================
#  Este script:
#    - prepara un VPS Debian/Ubuntu (Docker, UFW, fail2ban, updates)
#    - clona el repo y fija un ref reproducible
#    - genera secretos y .env.qa
#    - despliega el stack desde GHCR privado (o Docker Hub si se pide)
#    - instala una unidad systemd y deja credenciales en /root
#
#  NO existe un despliegue "100% seguro". El core PayRam es cerrado y tiene
#  CVEs conocidos. Este script reduce la superficie, pero no certifica el core.
#  Por eso exige --accept-risk de forma explícita.
#
#  Uso:
#    sudo ./setup.sh --domain billing.example.com --payram-domain pay.example.com \
#      --email admin@example.com --accept-risk
#
#  Variables útiles:
#    GHCR_TOKEN=ghp_xxx          PAT con read:packages (si no hay login previo)
#    GHCR_USERNAME=eliaselp
#    ADMIN_PASSWORD=...          opcional; si no, se genera
#    COMPANY_LEGAL_NAME=...      opcional
#    PAYRAM_API_KEY=...          opcional; si no, se configura tras onboarding
#
#  Uninstall: ./uninstall.sh --yes
# ============================================================================
set -Eeuo pipefail
IFS=$'\n\t'

REPO_URL="${REPO_URL:-https://github.com/eliaselp/payram-hardened.git}"
REF="${REF:-master}"
INSTALL_DIR="${INSTALL_DIR:-/opt/payram-hardened}"
SOURCE="${SOURCE:-ghcr}"                 # ghcr | dockerhub
ASSUME_YES=0
ACCEPT_RISK=0
SKIP_OS=0
SKIP_FIREWALL=0
SKIP_DNS=0
SKIP_PORT_CHECK=0
GHCR_USERNAME="${GHCR_USERNAME:-}"
GHCR_TOKEN="${GHCR_TOKEN:-}"
BILLING_DOMAIN="${BILLING_DOMAIN:-}"
PAYRAM_DOMAIN="${PAYRAM_DOMAIN:-}"
ACME_EMAIL="${ACME_EMAIL:-}"
ADMIN_USERNAME="${ADMIN_USERNAME:-admin}"
ADMIN_PASSWORD="${ADMIN_PASSWORD:-}"
PAYRAM_API_KEY="${PAYRAM_API_KEY:-}"
COMPANY_NAME="${COMPANY_NAME:-Mi Empresa}"
COMPANY_LEGAL_NAME="${COMPANY_LEGAL_NAME:-Mi Empresa S.A.S.}"
COMPANY_TAX_ID="${COMPANY_TAX_ID:-NIT / RUC / RFC}"
COMPANY_EMAIL="${COMPANY_EMAIL:-}"
COMPANY_ADDRESS="${COMPANY_ADDRESS:-Dirección fiscal}"
COMPANY_PHONE="${COMPANY_PHONE:-}"
COMPANY_WEBSITE="${COMPANY_WEBSITE:-}"

STATE_DIR="/var/lib/payram-hardened"
STATE_FILE="$STATE_DIR/install.state"
CREDS_FILE="/root/payram-credentials.txt"
COMPOSE_PROJECT="payram-qa"
COMPOSE_FILE="docker-compose.qa.yml"
ENV_FILE=""
DOCKER_BIN=""

log()  { printf '\033[1;32m[setup]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[setup]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[setup]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Uso: setup.sh [opciones]

Obligatorio (o variables de entorno):
  --domain DOMINIO          Dominio de Billing (ej. billing.example.com)
  --payram-domain DOMINIO   Dominio de PayRam (ej. pay.example.com)
  --email EMAIL             Email para Let's Encrypt
  --accept-risk             Acepta el riesgo residual del core cerrado

Opciones:
  --dir RUTA                Directorio de instalación (default: /opt/payram-hardened)
  --repo URL                Repo a clonar
  --ref REF                 Rama/tag/commit (default: master)
  --source ghcr|dockerhub   Origen de imágenes (default: ghcr)
  --ghcr-user USUARIO       Usuario GHCR (default: owner del repo)
  --yes                     No pedir confirmación interactiva
  --skip-os                 No instalar paquetes del sistema
  --skip-firewall           No tocar UFW
  --skip-dns                No validar DNS
  --skip-port-check         No validar puertos 80/443
  -h, --help                Esta ayuda

Variables:
  GHCR_TOKEN                PAT con read:packages (si no hay docker login)
  ADMIN_PASSWORD            Contraseña admin de Billing (si no, se genera)
  PAYRAM_API_KEY            API key de PayRam (si ya la tienes)
  COMPANY_LEGAL_NAME, COMPANY_TAX_ID, COMPANY_EMAIL, COMPANY_ADDRESS
USAGE
}

parse_args() {
  while (($#)); do
    case "$1" in
      --domain) BILLING_DOMAIN="${2:-}"; shift 2 ;;
      --payram-domain) PAYRAM_DOMAIN="${2:-}"; shift 2 ;;
      --email) ACME_EMAIL="${2:-}"; shift 2 ;;
      --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
      --repo) REPO_URL="${2:-}"; shift 2 ;;
      --ref) REF="${2:-}"; shift 2 ;;
      --source) SOURCE="${2:-}"; shift 2 ;;
      --ghcr-user) GHCR_USERNAME="${2:-}"; shift 2 ;;
      --yes) ASSUME_YES=1; shift ;;
      --accept-risk) ACCEPT_RISK=1; shift ;;
      --skip-os) SKIP_OS=1; shift ;;
      --skip-firewall) SKIP_FIREWALL=1; shift ;;
      --skip-dns) SKIP_DNS=1; shift ;;
      --skip-port-check) SKIP_PORT_CHECK=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "Opción desconocida: $1. Usa --help." ;;
    esac
  done
  case "$SOURCE" in ghcr|dockerhub) ;; *) die "--source debe ser ghcr o dockerhub." ;; esac
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "Ejecuta este script como root (sudo)."
}

state_init() {
  install -d -m 0700 "$STATE_DIR"
  touch "$STATE_FILE"
  chmod 0600 "$STATE_FILE"
  state_set INSTALL_DIR "$INSTALL_DIR"
  state_set REPO_URL "$REPO_URL"
  state_set REF "$REF"
  state_set COMPOSE_PROJECT "$COMPOSE_PROJECT"
  state_set COMPOSE_FILE "$COMPOSE_FILE"
  state_set STATE_VERSION "1"
}

state_set() {
  local key="$1" value="$2"
  if grep -q "^${key}=" "$STATE_FILE" 2>/dev/null; then
    sed -i "s|^${key}=.*|${key}=${value}|" "$STATE_FILE"
  else
    printf '%s=%s\n' "$key" "$value" >>"$STATE_FILE"
  fi
}

load_env() {
  local file="$1"
  [[ -f "$file" ]] || return 0
  while IFS='=' read -r key value; do
    [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
    export "$key=$value"
  done <"$file"
}

set_env_py() {
  local file="$1"
  shift
  python3 - "$file" "$@" <<'PY'
import sys
from pathlib import Path
p = Path(sys.argv[1])
updates = {}
for item in sys.argv[2:]:
    if "=" not in item:
        raise SystemExit(f"update inválido: {item}")
    k, v = item.split("=", 1)
    updates[k] = v
lines = p.read_text().splitlines()
out, seen = [], set()
for line in lines:
    if "=" in line and not line.lstrip().startswith("#"):
        k = line.split("=", 1)[0]
        if k in updates:
            out.append(f"{k}={updates[k]}")
            seen.add(k)
            continue
    out.append(line)
for k, v in updates.items():
    if k not in seen:
        out.append(f"{k}={v}")
p.write_text("\n".join(out) + "\n")
p.chmod(0o600)
PY
}

confirm() {
  [[ "$ASSUME_YES" -eq 1 ]] && return 0
  cat <<EOF

  Se modificará este VPS:
    - paquetes del sistema, Docker, UFW, fail2ban y unattended-upgrades
    - se abrirán 80/tcp, 443/tcp y 443/udp
    - se clonará el repo en $INSTALL_DIR
    - se crearán volúmenes Docker con datos y claves cifradas
    - se instalará el servicio systemd $COMPOSE_PROJECT

  Escribe DEPLOY para continuar:
EOF
  local answer
  read -rp "> " answer
  [[ "$answer" == "DEPLOY" ]] || die "Cancelado."
}

install_packages() {
  [[ "$SKIP_OS" -eq 1 ]] && { warn "Se omite instalación de paquetes (--skip-os)."; return; }
  export DEBIAN_FRONTEND=noninteractive
  log "Instalando paquetes base..."
  apt-get update -y
  apt-get install -y --no-install-recommends \
    ca-certificates curl gnupg jq openssl git rsync python3 \
    ufw fail2ban unattended-upgrades iproute2
}

install_docker() {
  if command -v docker >/dev/null 2>&1 && docker compose version >/dev/null 2>&1; then
    DOCKER_BIN="$(command -v docker)"
    state_set DOCKER_INSTALLED "0"
    log "Docker ya está instalado: $($DOCKER_BIN --version)"
    return
  fi
  [[ "$SKIP_OS" -eq 0 ]] || die "Docker no está instalado y usaste --skip-os."
  log "Instalando Docker desde el repositorio oficial..."
  state_set DOCKER_INSTALLED "1"
  install -m 0755 -d /etc/apt/keyrings
  curl -fsSL "https://download.docker.com/linux/${ID}/gpg" -o /tmp/payram-docker.asc
  gpg --dearmor --yes -o /etc/apt/keyrings/docker.gpg /tmp/payram-docker.asc
  chmod a+r /etc/apt/keyrings/docker.gpg
  local expected actual
  expected="9DC858229FC7DD38854AE2D88D81803C0EBFCD88"
  actual="$(gpg --show-keys --with-colons /etc/apt/keyrings/docker.gpg | awk -F: '/^fpr:/ {print $10; exit}')"
  [[ "$actual" == "$expected" ]] || die "Fingerprint de Docker no coincide: $actual"
  printf 'deb [arch=%s signed-by=/etc/apt/keyrings/docker.gpg] https://download.docker.com/linux/%s %s stable\n' \
    "$(dpkg --print-architecture)" "$ID" "$VERSION_CODENAME" >/etc/apt/sources.list.d/docker.list
  apt-get update -y
  apt-get install -y --no-install-recommends \
    docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin
  DOCKER_BIN="$(command -v docker)"
}

configure_docker() {
  install -d -m 0755 /etc/docker
  local daemon="/etc/docker/daemon.json"
  local backup="${daemon}.payram.bak"
  if [[ -f "$daemon" && ! -f "$backup" ]]; then
    cp -a "$daemon" "$backup"
    state_set DOCKERD_BACKUP "1"
  fi
  if [[ -f "$daemon" ]] && command -v jq >/dev/null 2>&1; then
    jq '. + {
      "log-driver": "json-file",
      "log-opts": {"max-size": "10m", "max-file": "3"},
      "no-new-privileges": true,
      "live-restore": true
    }' "$daemon" >"${daemon}.tmp" && mv "${daemon}.tmp" "$daemon"
  else
    cat >"$daemon" <<'JSON'
{
  "log-driver": "json-file",
  "log-opts": {"max-size": "10m", "max-file": "3"},
  "no-new-privileges": true,
  "live-restore": true
}
JSON
  fi
  state_set DOCKERD_CONFIG "1"
  systemctl enable --now docker
  systemctl restart docker
}

configure_host() {
  if ! swapon --show 2>/dev/null | grep -q .; then
    local avail_kb
    avail_kb="$(df --output=avail / | tail -1 | tr -d ' ')"
    if [[ "${avail_kb:-0}" -gt 5242880 ]]; then
      log "Creando swap de 2G..."
      fallocate -l 2G /swapfile || dd if=/dev/zero of=/swapfile bs=1M count=2048 status=none
      chmod 600 /swapfile
      mkswap /swapfile >/dev/null
      swapon /swapfile
      grep -q '^/swapfile ' /etc/fstab || echo '/swapfile none swap sw 0 0' >>/etc/fstab
      state_set SWAP_CREATED "1"
    else
      warn "No hay espacio suficiente para swap (se omite)."
    fi
  fi
  if ! grep -q '^vm.swappiness=10$' /etc/sysctl.conf; then
    echo 'vm.swappiness=10' >>/etc/sysctl.conf
    state_set SYSCTL_ADDED "1"
  fi
  if ! grep -q '^vm.overcommit_memory=1$' /etc/sysctl.conf; then
    echo 'vm.overcommit_memory=1' >>/etc/sysctl.conf
    state_set SYSCTL_ADDED "1"
  fi
  sysctl --system >/dev/null 2>&1 || true
}

configure_firewall() {
  [[ "$SKIP_FIREWALL" -eq 1 ]] && { warn "Se omite UFW (--skip-firewall)."; return; }
  command -v ufw >/dev/null 2>&1 || { warn "UFW no está disponible; se omite."; return; }
  local was_active="inactive"
  ufw status 2>/dev/null | grep -qi 'active' && was_active="active"
  state_set UFW_WAS_ACTIVE "$was_active"
  local -a ssh_ports=()
  mapfile -t ssh_ports < <(sshd -T 2>/dev/null | awk '/^port / {print $2}' | sort -u || true)
  ((${#ssh_ports[@]})) || ssh_ports=(22)
  local p
  for p in "${ssh_ports[@]}"; do ufw allow "${p}/tcp" >/dev/null 2>&1 || true; done
  ufw allow 80/tcp >/dev/null 2>&1 || true
  ufw allow 443/tcp >/dev/null 2>&1 || true
  ufw allow 443/udp >/dev/null 2>&1 || true
  state_set UFW_RULES_ADDED "1"
  ufw --force enable >/dev/null
  log "UFW activo; SSH: ${ssh_ports[*]}; HTTP/HTTPS permitidos."
}

configure_fail2ban() {
  install -d -m 0755 /etc/fail2ban/jail.d
  cat >/etc/fail2ban/jail.d/payram-hardened.local <<'EOF'
[sshd]
enabled = true
backend = systemd
maxretry = 5
findtime = 10m
bantime = 1h
EOF
  state_set FAIL2BAN_JAIL "1"
  systemctl enable --now fail2ban >/dev/null 2>&1 || warn "No se pudo iniciar fail2ban."
  systemctl restart fail2ban >/dev/null 2>&1 || true
}

configure_unattended() {
  cat >/etc/apt/apt.conf.d/20auto-upgrades <<'EOF'
APT::Periodic::Update-Package-Lists "1";
APT::Periodic::Unattended-Upgrade "1";
EOF
  systemctl enable --now unattended-upgrades >/dev/null 2>&1 || true
}

repo_owner() {
  local url="$1"
  if [[ "$url" =~ github\.com[:/]([^/]+)/ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
  else
    printf '%s\n' "eliaselp"
  fi
}

clone_repo() {
  if [[ -e "$INSTALL_DIR" ]]; then
    [[ -d "$INSTALL_DIR/.git" ]] || die "$INSTALL_DIR existe y no es un repo git."
    local origin
    origin="$(git -C "$INSTALL_DIR" remote get-url origin)"
    [[ "$origin" == "$REPO_URL" ]] || die "El repo existente tiene origin distinto: $origin"
    [[ -z "$(git -C "$INSTALL_DIR" status --porcelain)" ]] || die "El repo tiene cambios locales. Haz commit/stash antes."
    log "Actualizando repo existente..."
    git -C "$INSTALL_DIR" fetch --prune --tags origin
  else
    log "Clonando $REPO_URL en $INSTALL_DIR..."
    install -d -m 0755 "$(dirname "$INSTALL_DIR")"
    git clone "$REPO_URL" "$INSTALL_DIR"
    git -C "$INSTALL_DIR" fetch --prune --tags origin
  fi
  if git -C "$INSTALL_DIR" rev-parse --verify "origin/$REF" >/dev/null 2>&1; then
    git -C "$INSTALL_DIR" checkout -B "$REF" "origin/$REF"
  else
    git -C "$INSTALL_DIR" checkout --detach "$REF"
  fi
  chmod 0750 "$INSTALL_DIR"
  chmod +x "$INSTALL_DIR"/scripts/*.sh "$INSTALL_DIR/setup.sh" "$INSTALL_DIR/uninstall.sh" 2>/dev/null || true
  [[ -f "$INSTALL_DIR/$COMPOSE_FILE" ]] || die "Falta $COMPOSE_FILE en el repo."
  [[ -x "$INSTALL_DIR/hardening/entrypoint.sh" ]] || die "Falta hardening/entrypoint.sh."
  log "Repo en $REF: $(git -C "$INSTALL_DIR" rev-parse --short HEAD)"
}

prepare_env() {
  ENV_FILE="$INSTALL_DIR/.env.qa"
  local template env_existed=0
  if [[ "$SOURCE" == "ghcr" ]]; then
    template="$INSTALL_DIR/deploy/ghcr.env.example"
  else
    template="$INSTALL_DIR/.env.qa.example"
  fi
  [[ -f "$template" ]] || die "Falta template $template."

  if [[ -f "$ENV_FILE" ]]; then
    env_existed=1
    chmod 0600 "$ENV_FILE"
    warn ".env.qa ya existe; se conservan secretos, wallets y API key."
  else
    log "Creando .env.qa desde $template..."
    cp "$template" "$ENV_FILE"
    chmod 0600 "$ENV_FILE"
  fi

  load_env "$ENV_FILE"

  if [[ -z "$BILLING_DOMAIN" && -t 0 ]]; then read -rp "Dominio Billing: " BILLING_DOMAIN; fi
  if [[ -z "$PAYRAM_DOMAIN" && -t 0 ]]; then read -rp "Dominio PayRam: " PAYRAM_DOMAIN; fi
  if [[ -z "$ACME_EMAIL" && -t 0 ]]; then read -rp "Email Let's Encrypt: " ACME_EMAIL; fi
  [[ -n "$BILLING_DOMAIN" ]] || die "Falta --domain / BILLING_DOMAIN."
  [[ -n "$PAYRAM_DOMAIN" ]] || die "Falta --payram-domain / PAYRAM_DOMAIN."
  [[ -n "$ACME_EMAIL" ]] || die "Falta --email / ACME_EMAIL."
  [[ "$BILLING_DOMAIN" != "$PAYRAM_DOMAIN" ]] || die "Billing y PayRam no pueden usar el mismo dominio."
  [[ "$BILLING_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "Dominio Billing inválido."
  [[ "$PAYRAM_DOMAIN" =~ ^[A-Za-z0-9.-]+$ ]] || die "Dominio PayRam inválido."
  [[ "$ACME_EMAIL" == *@*.* ]] || die "Email inválido."
  COMPANY_EMAIL="${COMPANY_EMAIL:-$ACME_EMAIL}"

  local -a updates=(
    "BILLING_DOMAIN=$BILLING_DOMAIN"
    "PAYRAM_DOMAIN=$PAYRAM_DOMAIN"
    "ACME_EMAIL=$ACME_EMAIL"
    "BASE_URL=https://$BILLING_DOMAIN"
    "ALLOWED_HOSTS=$BILLING_DOMAIN,localhost,127.0.0.1"
    "PAYRAM_BASE_URL=https://$PAYRAM_DOMAIN"
    "PAYRAM_PUBLIC_URL=https://$PAYRAM_DOMAIN"
    "ALLOW_KNOWN_VULNERABLE_PAYRAM=true"
  )

  if [[ "$env_existed" -eq 0 ]]; then
    updates+=(
      "AES_KEY=$(openssl rand -hex 32)"
      "SECRET_KEY=$(openssl rand -hex 32)"
      "POSTGRES_PASSWORD=$(openssl rand -hex 24)"
      "INTERNAL_POSTGRES_PASSWORD=$(openssl rand -hex 24)"
      "BILLING_API_KEY=$(openssl rand -hex 32)"
      "ADMIN_USERNAME=$ADMIN_USERNAME"
      "ADMIN_PASSWORD_HASH="
      "ADMIN_PASSWORD="
      "COMPANY_NAME=$COMPANY_NAME"
      "COMPANY_LEGAL_NAME=$COMPANY_LEGAL_NAME"
      "COMPANY_TAX_ID=$COMPANY_TAX_ID"
      "COMPANY_EMAIL=$COMPANY_EMAIL"
      "COMPANY_ADDRESS=$COMPANY_ADDRESS"
      "COMPANY_PHONE=$COMPANY_PHONE"
      "COMPANY_WEBSITE=$COMPANY_WEBSITE"
      "PAYRAM_API_KEY=$PAYRAM_API_KEY"
    )
  else
    # Rellenar solo si falta o sigue en placeholder. Nunca rotar secretos vivos.
    [[ -n "${AES_KEY:-}" && "${AES_KEY}" != change-me* ]] || updates+=("AES_KEY=$(openssl rand -hex 32)")
    [[ -n "${SECRET_KEY:-}" && "${SECRET_KEY}" != change-me* ]] || updates+=("SECRET_KEY=$(openssl rand -hex 32)")
    [[ -n "${POSTGRES_PASSWORD:-}" && "${POSTGRES_PASSWORD}" != change-me* ]] || updates+=("POSTGRES_PASSWORD=$(openssl rand -hex 24)")
    [[ -n "${INTERNAL_POSTGRES_PASSWORD:-}" && "${INTERNAL_POSTGRES_PASSWORD}" != change-me* ]] || updates+=("INTERNAL_POSTGRES_PASSWORD=$(openssl rand -hex 24)")
    [[ -n "${BILLING_API_KEY:-}" && "${BILLING_API_KEY}" != change-me* ]] || updates+=("BILLING_API_KEY=$(openssl rand -hex 32)")
    [[ -n "${PAYRAM_API_KEY:-}" ]] && updates+=("PAYRAM_API_KEY=$PAYRAM_API_KEY")
    if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
      updates+=("ADMIN_PASSWORD_HASH=" "ADMIN_PASSWORD=")
    fi
    if ! grep -q '^ADMIN_PASSWORD_HASH=scrypt[:$]' "$ENV_FILE"; then
      ADMIN_PASSWORD="${ADMIN_PASSWORD:-$(openssl rand -hex 16)}"
    fi
  fi

  if [[ -n "${PAYRAM_IMAGE:-}" ]]; then updates+=("PAYRAM_IMAGE=$PAYRAM_IMAGE"); fi
  if [[ -n "${PAYRAM_APPROVED_DIGEST:-}" ]]; then updates+=("PAYRAM_APPROVED_DIGEST=$PAYRAM_APPROVED_DIGEST"); fi
  if [[ -n "${PAYRAM_VENDOR_DIGEST:-}" ]]; then updates+=("PAYRAM_VENDOR_DIGEST=$PAYRAM_VENDOR_DIGEST"); fi
  if [[ -n "${POSTGRES_IMAGE:-}" ]]; then updates+=("POSTGRES_IMAGE=$POSTGRES_IMAGE"); fi
  if [[ -n "${CADDY_IMAGE:-}" ]]; then updates+=("CADDY_IMAGE=$CADDY_IMAGE"); fi
  if [[ -n "${BILLING_IMAGE:-}" ]]; then updates+=("BILLING_IMAGE=$BILLING_IMAGE"); fi

  set_env_py "$ENV_FILE" "${updates[@]}"
  chmod 0600 "$ENV_FILE"
  load_env "$ENV_FILE"
  state_set ENV_FILE "$ENV_FILE"
  state_set PAYRAM_IMAGE "${PAYRAM_IMAGE:-}"
  state_set BILLING_IMAGE "${BILLING_IMAGE:-}"
  state_set POSTGRES_IMAGE "${POSTGRES_IMAGE:-}"
  state_set CADDY_IMAGE "${CADDY_IMAGE:-}"

  if [[ -z "${ADMIN_PASSWORD:-}" ]] && ! grep -q '^ADMIN_PASSWORD_HASH=scrypt[:$]' "$ENV_FILE"; then
    ADMIN_PASSWORD="$(openssl rand -hex 16)"
  fi

  for key in PAYRAM_IMAGE POSTGRES_IMAGE CADDY_IMAGE; do
    [[ -n "${!key:-}" ]] || die "$key no está definido en .env.qa."
    [[ "${!key}" == *"@sha256:"* ]] || die "$key debe estar fijado por digest."
  done
  if [[ "$SOURCE" == "ghcr" ]]; then
    [[ "${BILLING_IMAGE:-}" == ghcr.io/*@sha256:* ]] || die "BILLING_IMAGE GHCR debe estar fijado por digest."
  fi
  grep -q 'change-me' "$ENV_FILE" && die ".env.qa aún contiene placeholders 'change-me'."
  log "Secretos generados; .env.qa en 0600."
}

ghcr_login() {
  [[ "$SOURCE" == "ghcr" ]] || return 0
  local owner="${GHCR_USERNAME:-$(repo_owner "$REPO_URL")}"
  GHCR_USERNAME="$owner"
  if [[ -n "$GHCR_TOKEN" ]]; then
    printf '%s' "$GHCR_TOKEN" | docker login ghcr.io -u "$GHCR_USERNAME" --password-stdin
    state_set GHCR_LOGIN "1"
  elif [[ -t 0 ]]; then
    warn "Login GHCR necesario para bajar imágenes privadas."
    docker login ghcr.io -u "$GHCR_USERNAME"
    state_set GHCR_LOGIN "1"
  else
    warn "Sin GHCR_TOKEN y sin TTY; se usarán credenciales existentes de Docker."
  fi
}

compose_pull() {
  local -a compose=(docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" --env-file "$ENV_FILE")
  log "Descargando imágenes..."
  if [[ "$SOURCE" == "ghcr" ]]; then
    "${compose[@]}" pull
  else
    "${compose[@]}" pull db payram caddy
    log "Construyendo billing desde el repo..."
    "${compose[@]}" build billing
  fi
}

admin_hash() {
  local -a compose=(docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" --env-file "$ENV_FILE")
  if grep -q '^ADMIN_PASSWORD_HASH=scrypt[:$]' "$ENV_FILE"; then
    log "ADMIN_PASSWORD_HASH ya existe."
    return
  fi
  if [[ -z "$ADMIN_PASSWORD" ]]; then
    if [[ -t 0 ]]; then
      read -rsp "Contraseña admin de Billing: " ADMIN_PASSWORD; echo
    else
      die "Falta ADMIN_PASSWORD y no hay hash existente."
    fi
  fi
  [[ ${#ADMIN_PASSWORD} -ge 12 ]] || die "ADMIN_PASSWORD debe tener al menos 12 caracteres."
  log "Generando hash admin..."
  local hash
  hash="$(printf '%s' "$ADMIN_PASSWORD" | "${compose[@]}" run --rm --no-deps -T billing python -m app.cli hash-password --stdin)"
  [[ "$hash" == scrypt:* || "$hash" == scrypt\$* ]] || die "Hash admin inválido."
  set_env_py "$ENV_FILE" "ADMIN_PASSWORD_HASH=$hash" "ADMIN_PASSWORD="
  chmod 0600 "$ENV_FILE"
  load_env "$ENV_FILE"
}

validate_stack() {
  local -a compose=(docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" --env-file "$ENV_FILE")
  log "Validando compose..."
  "${compose[@]}" config --quiet
  log "Ejecutando security gate..."
  ENV_FILE="$ENV_FILE" "$INSTALL_DIR/scripts/security-gate.sh"
}

check_dns() {
  [[ "$SKIP_DNS" -eq 1 ]] && { warn "Se omite validación DNS."; return; }
  local server_ip=""
  server_ip="$(curl -4 -fsS --max-time 10 https://api.ipify.org 2>/dev/null || true)"
  if [[ -z "$server_ip" ]]; then
    warn "No se pudo obtener la IP pública; se omite comparación DNS."
    return
  fi
  local domain resolved
  for domain in "$BILLING_DOMAIN" "$PAYRAM_DOMAIN"; do
    resolved="$(getent ahostsv4 "$domain" | awk 'NR==1{print $1}' || true)"
    [[ -n "$resolved" ]] || die "DNS: $domain no resuelve."
    [[ "$resolved" == "$server_ip" ]] || die "DNS: $domain -> $resolved, VPS -> $server_ip. Corrige el DNS o usa --skip-dns."
    log "DNS OK: $domain -> $resolved"
  done
}

check_ports() {
  [[ "$SKIP_PORT_CHECK" -eq 1 ]] && { warn "Se omite validación de puertos."; return; }
  if ss -H -ltn '( sport = :80 or sport = :443 )' 2>/dev/null | grep -q .; then
    if docker ps --format '{{.Label "com.docker.compose.project"}}' 2>/dev/null | grep -qx "$COMPOSE_PROJECT"; then
      warn "Puertos 80/443 ya usados por el stack $COMPOSE_PROJECT; se reutiliza."
    else
      die "Puertos 80/443 ocupados por otro proceso. Libera o usa --skip-port-check."
    fi
  fi
}

start_stack() {
  local -a compose=(docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" --env-file "$ENV_FILE")
  log "Arrancando stack..."
  "${compose[@]}" up -d --no-build
  local i healthy=0
  for i in $(seq 1 60); do
    if "${compose[@]}" exec -T billing python -c \
      "import urllib.request; urllib.request.urlopen('http://127.0.0.1:8000/healthz', timeout=3)" >/dev/null 2>&1; then
      log "Billing saludable."
      healthy=1
      break
    fi
    sleep 5
  done
  [[ "$healthy" -eq 1 ]] || die "Billing no pasó el healthcheck en 5 minutos."
  "${compose[@]}" ps
}

configure_payram_key() {
  if [[ -n "${PAYRAM_API_KEY:-}" ]]; then
    log "PAYRAM_API_KEY ya está configurada."
    return
  fi
  if [[ "$ASSUME_YES" -eq 1 || ! -t 0 ]]; then
    warn "PAYRAM_API_KEY pendiente; configúrala tras el onboarding."
    return
  fi
  cat <<'EOF'

  Onboarding PayRam:
   1) Abre el panel de PayRam y crea/activa el proyecto.
   2) Genera una API key.
   3) Pégala aquí para conectar Billing (Enter para omitir).

EOF
  local key
  read -rsp "PAYRAM_API_KEY: " key; echo
  key="${key//$'\r'/}"
  key="${key//$'\n'/}"
  if [[ -n "$key" ]]; then
    set_env_py "$ENV_FILE" "PAYRAM_API_KEY=$key"
    chmod 0600 "$ENV_FILE"
    load_env "$ENV_FILE"
    local -a compose=(docker compose -f "$INSTALL_DIR/$COMPOSE_FILE" --env-file "$ENV_FILE")
    "${compose[@]}" up -d --no-build billing
    log "API key guardada; Billing reiniciado."
  else
    warn "API key omitida; Billing no podrá crear links hasta configurarla."
  fi
}

install_systemd() {
  DOCKER_BIN="${DOCKER_BIN:-$(command -v docker)}"
  cat >/etc/systemd/system/payram-hardened.service <<EOF
[Unit]
Description=PayRam Hardened stack
Requires=docker.service
After=docker.service network-online.target
Wants=network-online.target

[Service]
Type=oneshot
RemainAfterExit=yes
WorkingDirectory=$INSTALL_DIR
ExecStart=$DOCKER_BIN compose -f $INSTALL_DIR/$COMPOSE_FILE --env-file $INSTALL_DIR/.env.qa up -d --no-build
ExecStop=$DOCKER_BIN compose -f $INSTALL_DIR/$COMPOSE_FILE --env-file $INSTALL_DIR/.env.qa down
TimeoutStartSec=0

[Install]
WantedBy=multi-user.target
EOF
  systemctl daemon-reload
  systemctl enable --now payram-hardened.service
  state_set SYSTEMD_UNIT "1"
}

write_credentials() {
  umask 077
  {
    echo "PayRam Hardened — credenciales de instalación"
    echo "Fecha: $(date -u +%FT%TZ)"
    echo "Billing: https://$BILLING_DOMAIN"
    echo "PayRam:  https://$PAYRAM_DOMAIN"
    echo "Admin usuario: $ADMIN_USERNAME"
    if [[ -n "${ADMIN_PASSWORD:-}" ]]; then
      echo "Admin password: $ADMIN_PASSWORD"
    else
      echo "Admin password: (ya existente; no se vuelve a mostrar)"
    fi
    echo "Archivo env: $ENV_FILE"
    echo "Repo: $INSTALL_DIR"
    echo "PayRam API key: ${PAYRAM_API_KEY:+configurada}${PAYRAM_API_KEY:-PENDIENTE (onboarding)}"
    echo
    echo "Guarda este archivo y bórralo cuando ya no lo necesites."
  } >"$CREDS_FILE"
  chmod 0600 "$CREDS_FILE"
  state_set CREDS_FILE "$CREDS_FILE"
}

summary() {
  cat <<EOF

============================================================================
Stack desplegado
============================================================================
Billing:  https://$BILLING_DOMAIN
PayRam:   https://$PAYRAM_DOMAIN
Repo:     $INSTALL_DIR
Env:      $ENV_FILE
Creds:    $CREDS_FILE

Siguiente:
  1) Abre https://$PAYRAM_DOMAIN y completa el onboarding de PayRam:
     admin, proyecto, wallets/xpub, rutas Base/USDC, bridge y tarjeta.
  2) Copia la API key de PayRam a .env.qa:
       PAYRAM_API_KEY=...
     y reinicia billing:
       docker compose -f $INSTALL_DIR/$COMPOSE_FILE --env-file $ENV_FILE up -d billing
  3) Configura el webhook en PayRam:
       https://$BILLING_DOMAIN/webhooks/payram
  4) Completa COMPANY_* y SMTP/WhatsApp en .env.qa.
  5) Prueba con USD 1 antes de montos grandes.

Límites:
  - El core PayRam sigue siendo cerrado y con CVEs conocidos.
  - La custodia real depende de quién tenga xpub/seed y claves.
  - Tarjeta y bridge son proveedores externos.
  - Esto no es una certificación de seguridad.

Desinstalar:
  sudo $INSTALL_DIR/uninstall.sh --yes

============================================================================
EOF
}

main() {
  parse_args "$@"
  if [[ "$ACCEPT_RISK" -ne 1 ]]; then
    cat <<'EOF'

  ADVERTENCIA: PayRam 3.9.0 contiene CVEs críticos conocidos (Next.js/Go).
  El overlay mitiga rutas y configuración, pero NO parchea el core.
  No existe un despliegue "100% seguro" con este binario.

  Vuelve a ejecutar con --accept-risk si aceptas el riesgo residual.
EOF
    exit 2
  fi
  require_root
  if [[ -r /etc/os-release ]]; then . /etc/os-release; else die "No se pudo leer /etc/os-release."; fi
  case "${ID:-}" in ubuntu|debian) ;; *) die "Solo Debian/Ubuntu. Detectado: ${ID:-?}." ;; esac
  [[ -n "${VERSION_CODENAME:-}" ]] || die "Falta VERSION_CODENAME."
  [[ "$INSTALL_DIR" == /* && "$INSTALL_DIR" != "/" ]] || die "INSTALL_DIR debe ser absoluto y distinto de /."
  [[ "$INSTALL_DIR" != *[[:space:]]* ]] || die "INSTALL_DIR no puede contener espacios."

  if [[ "$(uname -m)" != "x86_64" ]]; then
    if [[ "$SOURCE" == "ghcr" ]]; then
      die "Las imágenes GHCR publicadas son amd64. Usa un VPS x86_64 o publica imágenes arm64."
    fi
    if [[ -z "${PAYRAM_IMAGE:-}" || -z "${PAYRAM_APPROVED_DIGEST:-}" ]]; then
      die "En $(uname -m) define PAYRAM_IMAGE y PAYRAM_APPROVED_DIGEST arm64 antes de ejecutar."
    fi
    warn "Arquitectura $(uname -m): se usarán las imágenes arm64 que definiste."
  fi

  cat <<'EOF'

  PayRam Hardened — máximo control práctico
  -----------------------------------------
  Este setup instala y endurece el host, pero el core sigue siendo cerrado.
  La auditoría completa del core no es posible sin source/licencia.

EOF
  confirm
  state_init
  install_packages
  install_docker
  configure_docker
  configure_host
  configure_firewall
  configure_fail2ban
  configure_unattended
  clone_repo
  install -m 0755 "$INSTALL_DIR/uninstall.sh" /usr/local/sbin/payram-uninstall 2>/dev/null || warn "No se pudo copiar uninstall.sh a /usr/local/sbin."
  prepare_env
  ghcr_login
  compose_pull
  admin_hash
  validate_stack
  check_dns
  check_ports
  start_stack
  configure_payram_key
  install_systemd
  write_credentials
  summary
}

main "$@"
