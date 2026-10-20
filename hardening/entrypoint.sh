#!/bin/sh
# Entrypoint de hardening. Parchea la configuración del vendor en runtime y
# luego ejecuta el entrypoint original de PayRam.
set -eu

if [ "${PAYRAM_HARDENING_ENABLED:-true}" != "true" ]; then
  echo "[hardening] PAYRAM_HARDENING_ENABLED != true; abortando." >&2
  exit 2
fi

# PayRam imprime una clave efímera de 64 hex en el log de arranque.
# Redactarla localmente mientras el proceso arranca.
(
  for _ in $(seq 1 120); do
    for f in /var/log/payram-core.out.log /var/log/payram-core.err.log; do
      if [ -f "$f" ] && grep -qE 'Generated Key: [0-9a-fA-F]{64}' "$f" 2>/dev/null; then
        sed -i -E 's/Generated Key: [0-9a-fA-F]{64}/Generated Key: [REDACTED]/g' "$f" 2>/dev/null || true
      fi
    done
    sleep 2
  done
) &

exec python3 /hardening/patch_payram.py "$@"
