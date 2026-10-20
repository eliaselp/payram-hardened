#!/bin/sh
# Entrypoint de hardening. Parchea la configuración del vendor en runtime y
# luego ejecuta el entrypoint original de PayRam.
set -eu

if [ "${PAYRAM_HARDENING_ENABLED:-true}" != "true" ]; then
  echo "[hardening] PAYRAM_HARDENING_ENABLED != true; abortando." >&2
  exit 2
fi

exec python3 /hardening/patch_payram.py "$@"
