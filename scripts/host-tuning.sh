#!/usr/bin/env bash
# Ajustes de host recomendados para Redis/Postgres en el VPS.
set -euo pipefail
sysctl -w vm.overcommit_memory=1
grep -q '^vm.overcommit_memory' /etc/sysctl.conf || echo 'vm.overcommit_memory=1' >> /etc/sysctl.conf
sysctl -w vm.swappiness=10
grep -q '^vm.swappiness' /etc/sysctl.conf || echo 'vm.swappiness=10' >> /etc/sysctl.conf
echo "Host tuning OK. Redis dejará de advertir por overcommit."
