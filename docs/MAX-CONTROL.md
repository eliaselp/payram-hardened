# Máximo control práctico

Este documento describe el despliegue que implementan `setup.sh` y
`uninstall.sh`. No convierte el core cerrado de PayRam en código auditable;
reduce la superficie, fija artefactos y deja el control operativo en tu VPS.

## Qué hace `setup.sh`

1. Verifica Debian/Ubuntu y root.
2. Instala paquetes base, Docker oficial y Compose.
3. Verifica el fingerprint de la clave GPG de Docker.
4. Endurece Docker (`no-new-privileges`, `live-restore`, logs rotados).
5. Crea swap si hace falta y ajusta `vm.swappiness`/`vm.overcommit_memory`.
6. Configura UFW (SSH detectado, 80/443) y fail2ban para SSH.
7. Activa unattended-upgrades.
8. Clona el repo y fija un `ref` (rama, tag o commit).
9. Genera `.env.qa` con secretos aleatorios y permisos `0600`.
10. Baja imágenes GHCR privadas y verifica digests.
11. Genera el hash de admin de Billing.
12. Ejecuta `security-gate.sh` y valida el compose.
13. Comprueba DNS y puertos.
14. Levanta el stack y espera healthchecks.
15. Instala `payram-hardened.service`.
16. Deja credenciales en `/root/payram-credentials.txt` (0600).

## Requisitos

- VPS Debian/Ubuntu **x86_64** si usas GHCR (las imágenes publicadas son amd64).
- DNS de `BILLING_DOMAIN` y `PAYRAM_DOMAIN` apuntando al VPS.
- Puertos 80/443 libres.
- PAT de GitHub con `read:packages` o `docker login ghcr.io` previo.
- Aceptar el riesgo residual del core: `--accept-risk`.

## Instalación

```bash
sudo GHCR_TOKEN=ghp_xxx ./setup.sh \
  --domain billing.tudominio.com \
  --payram-domain pay.tudominio.com \
  --email admin@tudominio.com \
  --accept-risk
```

Sin TTY:

```bash
sudo -E GHCR_TOKEN=... ADMIN_PASSWORD='...' ./setup.sh \
  --domain ... --payram-domain ... --email ... --accept-risk --yes
```

No uses `--accept-risk` si no aceptas que PayRam 3.9.0 contiene CVEs críticos
conocidos y que el overlay solo mitiga rutas/configuración.

## Después del setup

1. Abre `https://PAYRAM_DOMAIN` y completa el onboarding:
   admin, proyecto, wallets/xpub, rutas Base/USDC, bridge y tarjeta.
2. Configura `PAYRAM_API_KEY` en `.env.qa` y reinicia Billing:
   ```bash
   docker compose -f docker-compose.qa.yml --env-file .env.qa up -d billing
   ```
3. Configura el webhook:
   `https://BILLING_DOMAIN/webhooks/payram`
4. Completa `COMPANY_*`, SMTP y WhatsApp en `.env.qa`.
5. Prueba USD 1 en Base, luego bridge y tarjeta.

## Desinstalación

```bash
sudo ./uninstall.sh --yes
```

Por defecto borra contenedores, redes, volúmenes, imágenes, repo, `.env.qa`,
credenciales y reglas de firewall añadidas. **Borra las claves cifradas y la DB
de facturas.**

Conservar datos:

```bash
sudo ./uninstall.sh --yes --keep-data
```

Purgar Docker (solo si no hay otros contenedores):

```bash
sudo ./uninstall.sh --yes --purge-system
```

## Qué NO resuelve

- No tienes el source del core: no puedes auditarlo por completo.
- Los CVEs de Next.js/Go siguen dentro de la imagen.
- Las claves siguen en el VPS (Postgres cifrado + `AES_KEY` en `.env`), no en
  un HSM ni en una hardware wallet.
- No hay multisig ni aprobación dual para payouts.
- El procesador de tarjeta y el bridge siguen siendo terceros.
- No hay cifrado de disco completo: depende del proveedor del VPS.
- No hay backup automático ni auditoría externa.

## Para subir el nivel

- Custodia: hardware wallet/multisig para la tesorería; hot wallet con float.
- Payouts: allowlist, aprobación manual, límites y conciliación on-chain.
- Billing: verificador propio de transacciones en Base (hoy confía en PayRam).
- Core: source/licencia/escrow o clean-room.
- Infra: disco cifrado, backups cifrados, monitoreo de egress, IDS.
- Legal: contrato con PayRam, del procesador y del bridge; auditoría externa.
