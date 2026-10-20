#!/usr/bin/env bash
# ============================================================================
#  PayRam Hardened — desinstalación espejo
# ============================================================================
#  Borra lo que instaló setup.sh:
#    - contenedores, redes, volúmenes e imágenes del proyecto
#    - unidad systemd, reglas UFW añadidas, jail de fail2ban
#    - swap/sysctl creados por setup
#    - repo, .env.qa, credenciales y estado
#
#  Por defecto NO borra Docker ni paquetes del sistema. Para eso:
#    ./uninstall.sh --yes --purge-system
#
#  Conservar datos:
#    ./uninstall.sh --yes --keep-data
# ============================================================================
set -Eeuo pipefail
IFS=$'\n\t'

STATE_DIR="/var/lib/payram-hardened"
STATE_FILE="$STATE_DIR/install.state"
INSTALL_DIR=""
COMPOSE_PROJECT="payram-qa"
COMPOSE_FILE="docker-compose.qa.yml"
ENV_FILE=""
CREDS_FILE="/root/payram-credentials.txt"
EDGE_MODE=direct
CADDYFILE=""
CADDY_BIND_IP=""
CLOUDFLARED_IMAGE=""
DOCKER_INSTALLED=0
DOCKERD_BACKUP=0
DOCKERD_CONFIG=0
UFW_RULES_ADDED=0
UFW_WAS_ACTIVE=unknown
FAIL2BAN_JAIL=0
SWAP_CREATED=0
SYSCTL_ADDED=0
SYSTEMD_UNIT=0
GHCR_LOGIN=0
ASSUME_YES=0
KEEP_DATA=0
KEEP_IMAGES=0
PURGE_SYSTEM=0
PURGE_FIREWALL=0

log()  { printf '\033[1;32m[uninstall]\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m[uninstall]\033[0m %s\n' "$*"; }
die()  { printf '\033[1;31m[uninstall]\033[0m %s\n' "$*" >&2; exit 1; }

usage() {
  cat <<'USAGE'
Uso: uninstall.sh [opciones]

  --yes               No pedir confirmación (sigue siendo destructivo)
  --dir RUTA          Directorio de instalación
  --keep-data         Conserva volúmenes Docker (facturas, DB, claves)
  --keep-images       No borra las imágenes del proyecto
  --purge-system      Borra Docker y configuración del sistema si lo instaló setup
  --purge-firewall    Desactiva UFW si setup lo activó
  -h, --help          Esta ayuda

Sin --keep-data se borran los volúmenes: SQLite de facturas, Postgres de
PayRam, claves cifradas, logs y certificados de Caddy.
USAGE
}

parse_args() {
  while (($#)); do
    case "$1" in
      --yes) ASSUME_YES=1; shift ;;
      --dir) INSTALL_DIR="${2:-}"; shift 2 ;;
      --keep-data) KEEP_DATA=1; shift ;;
      --keep-images) KEEP_IMAGES=1; shift ;;
      --purge-system) PURGE_SYSTEM=1; shift ;;
      --purge-firewall) PURGE_FIREWALL=1; shift ;;
      -h|--help) usage; exit 0 ;;
      *) die "Opción desconocida: $1. Usa --help." ;;
    esac
  done
}

require_root() {
  [[ "${EUID}" -eq 0 ]] || die "Ejecuta como root (sudo)."
}

load_state() {
  if [[ -f "$STATE_FILE" ]]; then
    while IFS='=' read -r key value; do
      [[ "$key" =~ ^[A-Za-z_][A-Za-z0-9_]*$ ]] || continue
      case "$key" in
        INSTALL_DIR|COMPOSE_PROJECT|COMPOSE_FILE|ENV_FILE|CREDS_FILE|EDGE_MODE|CADDYFILE|CADDY_BIND_IP|CLOUDFLARED_IMAGE|DOCKER_INSTALLED|DOCKERD_BACKUP|DOCKERD_CONFIG|UFW_RULES_ADDED|UFW_WAS_ACTIVE|FAIL2BAN_JAIL|SWAP_CREATED|SYSCTL_ADDED|SYSTEMD_UNIT|GHCR_LOGIN|PAYRAM_IMAGE|BILLING_IMAGE|POSTGRES_IMAGE|CADDY_IMAGE)
          printf -v "$key" '%s' "$value"
          export "${key?}"
          ;;
      esac
    done <"$STATE_FILE"
  else
    warn "No existe $STATE_FILE; se limpiará solo lo que se pueda inferir."
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

validate_dir() {
  [[ -n "$INSTALL_DIR" ]] || INSTALL_DIR="/opt/payram-hardened"
  [[ "$INSTALL_DIR" == /* ]] || die "INSTALL_DIR debe ser absoluto."
  case "$INSTALL_DIR" in
    /|/bin|/boot|/dev|/etc|/home|/lib|/lib64|/opt|/proc|/root|/run|/sbin|/srv|/sys|/tmp|/usr|/var)
      die "INSTALL_DIR inseguro: $INSTALL_DIR" ;;
  esac
}

confirm() {
  [[ "$ASSUME_YES" -eq 1 ]] && return 0
  cat <<EOF

  Se eliminará la instalación PayRam Hardened:
    repo:        $INSTALL_DIR
    proyecto:    $COMPOSE_PROJECT
    datos:       $([[ "$KEEP_DATA" -eq 1 ]] && echo "CONSERVAR" || echo "BORRAR volúmenes")
    imágenes:    $([[ "$KEEP_IMAGES" -eq 1 ]] && echo "CONSERVAR" || echo "BORRAR")
    systemd:     payram-hardened.service
    firewall:    $([[ "$PURGE_FIREWALL" -eq 1 ]] && echo "desactivar UFW" || [[ "$EDGE_MODE" == "tunnel" ]] && echo "solo SSH (tunnel)" || echo "quitar reglas 80/443")
    sistema:     $([[ "$PURGE_SYSTEM" -eq 1 ]] && echo "PURGAR Docker/paquetes" || echo "conservar Docker")

  Escribe DELETE para continuar:
EOF
  local answer
  read -rp "> " answer
  [[ "$answer" == "DELETE" ]] || die "Cancelado."
}

docker_available() {
  command -v docker >/dev/null 2>&1 && docker info >/dev/null 2>&1
}

stop_stack() {
  docker_available || return 0
  local -a compose=()
  if [[ -f "$INSTALL_DIR/$COMPOSE_FILE" ]]; then
    compose=(docker compose -f "$INSTALL_DIR/$COMPOSE_FILE")
    if [[ -f "$INSTALL_DIR/.env.qa" ]]; then
      compose+=(--env-file "$INSTALL_DIR/.env.qa")
    fi
    if [[ "$EDGE_MODE" == "tunnel" ]]; then
      compose+=(--profile tunnel)
    fi
  fi

  if ((${#compose[@]})); then
    log "Deteniendo stack..."
    if [[ "$KEEP_DATA" -eq 1 ]]; then
      "${compose[@]}" down --remove-orphans >/dev/null 2>&1 || true
    else
      "${compose[@]}" down -v --remove-orphans >/dev/null 2>&1 || true
    fi
  fi

  # Limpieza por labels: cubre el caso de que falte el compose o el .env.
  local -a cids=()
  mapfile -t cids < <(docker ps -aq --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" 2>/dev/null || true)
  if ((${#cids[@]})); then
    log "Eliminando contenedores restantes..."
    docker rm -f "${cids[@]}" >/dev/null 2>&1 || true
  fi

  local -a nets=()
  mapfile -t nets < <(docker network ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" 2>/dev/null || true)
  if ((${#nets[@]})); then
    docker network rm "${nets[@]}" >/dev/null 2>&1 || true
  fi

  if [[ "$KEEP_DATA" -eq 0 ]]; then
    local -a vols=()
    mapfile -t vols < <(docker volume ls -q --filter "label=com.docker.compose.project=$COMPOSE_PROJECT" 2>/dev/null || true)
    if ((${#vols[@]})); then
      log "Eliminando volúmenes..."
      docker volume rm "${vols[@]}" >/dev/null 2>&1 || true
    fi
  else
    warn "Se conservan volúmenes (--keep-data)."
  fi
}

remove_images() {
  [[ "$KEEP_IMAGES" -eq 1 ]] && { warn "Se conservan imágenes (--keep-images)."; return; }
  docker_available || return 0
  local -a images=()
  [[ -n "${PAYRAM_IMAGE:-}" ]] && images+=("$PAYRAM_IMAGE")
  [[ -n "${BILLING_IMAGE:-}" ]] && images+=("$BILLING_IMAGE")
  [[ -n "${POSTGRES_IMAGE:-}" ]] && images+=("$POSTGRES_IMAGE")
  [[ -n "${CADDY_IMAGE:-}" ]] && images+=("$CADDY_IMAGE")
  [[ -n "${CLOUDFLARED_IMAGE:-}" ]] && images+=("$CLOUDFLARED_IMAGE")
  images+=("payram-hardened:local" "payram-billing-qa:local")
  local img
  for img in "${images[@]}"; do
    [[ -n "$img" ]] || continue
    docker image rm "$img" >/dev/null 2>&1 || true
  done
  log "Imágenes del proyecto eliminadas (si no estaban en uso)."
}

remove_systemd() {
  if [[ -f /etc/systemd/system/payram-hardened.service ]]; then
    log "Eliminando unidad systemd..."
    systemctl disable --now payram-hardened.service >/dev/null 2>&1 || true
    rm -f /etc/systemd/system/payram-hardened.service
    systemctl daemon-reload
  fi
}

remove_firewall() {
  if [[ "$UFW_RULES_ADDED" != "1" && "$PURGE_FIREWALL" -ne 1 ]]; then
    return
  fi
  command -v ufw >/dev/null 2>&1 || return
  log "Quitando reglas UFW añadidas..."
  ufw delete allow 80/tcp >/dev/null 2>&1 || true
  ufw delete allow 443/tcp >/dev/null 2>&1 || true
  ufw delete allow 443/udp >/dev/null 2>&1 || true
  if [[ "$PURGE_FIREWALL" -eq 1 && "$UFW_WAS_ACTIVE" == "inactive" ]]; then
    warn "UFW estaba inactivo antes del setup; se desactiva."
    ufw --force disable >/dev/null 2>&1 || true
  fi
}

remove_fail2ban() {
  [[ "$FAIL2BAN_JAIL" == "1" ]] || return
  log "Quitando jail de fail2ban..."
  rm -f /etc/fail2ban/jail.d/payram-hardened.local
  systemctl restart fail2ban >/dev/null 2>&1 || true
}

remove_swap() {
  [[ "$SWAP_CREATED" == "1" ]] || return
  if [[ -f /swapfile ]]; then
    log "Desactivando swap creado por setup..."
    swapoff /swapfile >/dev/null 2>&1 || true
    sed -i '\|^/swapfile |d' /etc/fstab || true
    rm -f /swapfile
  fi
}

remove_sysctl() {
  [[ "$SYSCTL_ADDED" == "1" ]] || return
  log "Quitando sysctl añadidos..."
  sed -i '/^vm.swappiness=10$/d; /^vm.overcommit_memory=1$/d' /etc/sysctl.conf || true
  sysctl --system >/dev/null 2>&1 || true
}

restore_docker_config() {
  [[ "$PURGE_SYSTEM" -eq 1 ]] && return
  [[ "$DOCKERD_CONFIG" == "1" ]] || return
  local daemon="/etc/docker/daemon.json"
  local backup="${daemon}.payram.bak"
  if [[ "$DOCKERD_BACKUP" == "1" && -f "$backup" ]]; then
    if docker_available && [[ -n "$(docker ps -aq 2>/dev/null)" ]]; then
      warn "Hay otros contenedores; no se restaura daemon.json para no romperlos."
      return
    fi
    cp -a "$backup" "$daemon"
    rm -f "$backup"
    systemctl restart docker >/dev/null 2>&1 || true
    log "daemon.json restaurado."
  fi
}

purge_system() {
  [[ "$PURGE_SYSTEM" -eq 1 ]] || return
  docker_available || { warn "Docker no está disponible; se omite purga."; return; }
  if [[ -n "$(docker ps -aq 2>/dev/null)" ]]; then
    die "Hay contenedores de otros proyectos. No se purga Docker. Usa --keep-data y limpia manualmente."
  fi
  log "Purgando Docker y configuración instalada..."
  systemctl stop docker.socket docker containerd >/dev/null 2>&1 || true
  apt-get purge -y docker-ce docker-ce-cli containerd.io docker-buildx-plugin docker-compose-plugin >/dev/null 2>&1 || true
  rm -rf /etc/docker /var/lib/docker /var/lib/containerd /etc/apt/keyrings/docker.gpg /etc/apt/sources.list.d/docker.list
  apt-get autoremove -y >/dev/null 2>&1 || true
  docker logout ghcr.io >/dev/null 2>&1 || true
}

remove_files() {
  local uninstall_copy="/usr/local/sbin/payram-uninstall"
  log "Eliminando repo, env, credenciales y estado..."
  cd /
  rm -rf "$INSTALL_DIR"
  rm -f "$CREDS_FILE" "$uninstall_copy"
  rm -rf "$STATE_DIR"
}

summary() {
  cat <<EOF

Desinstalación terminada.
  Repo:      eliminado ($INSTALL_DIR)
  Datos:     $([[ "$KEEP_DATA" -eq 1 ]] && echo "conservados" || echo "eliminados")
  Imágenes:  $([[ "$KEEP_IMAGES" -eq 1 ]] && echo "conservadas" || echo "eliminadas")
  Docker:    $([[ "$PURGE_SYSTEM" -eq 1 ]] && echo "purgado" || echo "conservado")
  UFW:       $([[ "$PURGE_FIREWALL" -eq 1 ]] && echo "reglas quitadas" || echo "reglas 80/443 quitadas; SSH intacto")

Si conservaste datos, los volúmenes Docker siguen en el host.
EOF
}

main() {
  parse_args "$@"
  [[ "$KEEP_DATA" -eq 1 && "$PURGE_SYSTEM" -eq 1 ]] && die "--keep-data y --purge-system son incompatibles: purgar Docker borra /var/lib/docker."
  [[ "$KEEP_IMAGES" -eq 1 && "$PURGE_SYSTEM" -eq 1 ]] && die "--keep-images y --purge-system son incompatibles: purgar Docker borra las imágenes."
  require_root
  load_state
  validate_dir
  ENV_FILE="${ENV_FILE:-$INSTALL_DIR/.env.qa}"
  load_env "$ENV_FILE"
  confirm
  stop_stack
  remove_images
  remove_systemd
  remove_firewall
  remove_fail2ban
  remove_swap
  remove_sysctl
  restore_docker_config
  purge_system
  remove_files
  summary
}

main "$@"
