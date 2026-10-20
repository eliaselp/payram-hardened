# PayRam Hardened Overlay

Overlay de seguridad para ejecutar **PayRam Core 3.9.0** (imagen propietaria, closed-source) con controles compensatorios: Postgres interno sin contraseña por defecto, payouts restrictivos, nginx sin XFF spoofing, bloqueo de rutas vulnerables, red aislada y Caddy con TLS.

> **No es un parche completo del vendor.** PayRam Core es código cerrado. Este repositorio **no incluye** la imagen ni el código de PayRam y **no puede eliminar** los CVEs dentro del binario Go ni del build de Next.js. Mitiga la configuración, la superficie de red y los defaults peligrosos. Para mainnet con fondos reales sigue siendo necesario un rebuild parcheado del proveedor. Ver [docs/FINDINGS.md](docs/FINDINGS.md) y el informe detallado [docs/AUDIT-2026-10-20.md](docs/AUDIT-2026-10-20.md).

## Qué hace el overlay

`hardening/entrypoint.sh` se ejecuta antes de `/startup.sh` y aplica parches en runtime al contenedor:

1. **Postgres interno**
   - Reemplaza la contraseña hardcodeada `payram123` por `INTERNAL_POSTGRES_PASSWORD`.
   - Cambia `pg_hba` de `0.0.0.0/0 md5` a `127.0.0.1/32 scram-sha-256`.
   - Cambia `listen_addresses='*'` a `localhost`.
   - Si `POSTGRES_HOST` es externo (`db`), **omite el Postgres interno** y desactiva su programa en Supervisor.
2. **Payouts**
   - `autoApproveEnabled=false`, `autoApproveAmountInUSD=0`, `otpThresholdInUSD=0`.
   - `dailyLimitInUSD=1000`, `hourlyLimitInUSD=500` por defecto (configurables).
   - Aplica a `config_production.yaml` y `config_development.yaml` (semilla; una DB ya inicializada puede requerir ajuste en dashboard).
3. **Nginx**
   - Elimina `set_real_ip_from`/`real_ip_header` de rangos privados (evita spoofing de IP).
   - `server_tokens off`, `X-Content-Type-Options`, `Referrer-Policy`.
   - Bloquea `/_next/image`, `/api/og`, `/_next/webpack-hmr`, `/__nextjs`, `/swagger`.
4. **Contenedor/red**
   - `no-new-privileges`, `pids_limit`, healthcheck.
   - Caddy 2.11.4 con TLS, HSTS y bloqueo de rutas.
   - `payram_net` separada de `db_net` (interna).
5. **CORS/telemetría/frontend**
   - Define `PAYRAM_ALLOWED_ORIGINS` al dominio público.
   - Desactiva `SENTRY_DSN` y el fallback de MetaMask analytics en el bundle.
   - Reemplaza endpoints dev/preprod de Next por producción.
   - Redacta la clave efímera que PayRam imprime en el log de arranque.
6. **Redis**
   - `user=redis`, `daemonize no`, PID file en `/var/lib/redis`.
7. **Opcional**: `Dockerfile.hardened` actualiza paquetes Debian (no Next/Go).

## Qué NO hace

- No actualiza Next.js 16.2.6 ni elimina el RCE `GHSA-2xp9-vwfh-vxw4`; solo bloquea la ruta conocida `/_next/image` y rutas OG.
- No actualiza el binario Go ni los módulos npm compilados.
- No convierte la imagen cerrada en auditable.
- No garantiza que no exista una backdoor no detectada.

**Riesgo residual: alto.** No lo uses en mainnet con fondos reales sin aceptación explícita y, idealmente, sin un VPS separado y WAF.

## Uso

```bash
cp .env.example .env
# Genera secretos:
#   openssl rand -hex 32   -> AES_KEY
#   openssl rand -base64 32 -> POSTGRES_PASSWORD / INTERNAL_POSTGRES_PASSWORD
# Edita PAYRAM_DOMAIN, ACME_EMAIL y PAYRAM_IMAGE/PAYRAM_APPROVED_DIGEST
chmod 600 .env

./scripts/security-gate.sh
docker compose up -d
docker compose logs -f payram
```

Verifica que el log muestre:

```text
[hardening] OK: postgres interno asegurado, payouts restrictivos, nginx sin XFF spoofing y rutas vulnerables bloqueadas.
```

Para validar los parches dentro de la imagen (sin arrancar servicios):

```bash
./scripts/verify-hardening.sh
```

Si quieres actualizar paquetes Debian (opcional, puede romper compatibilidad del vendor):

```bash
docker compose -f docker-compose.yml -f docker-compose.build.yml up -d --build
```

`Dockerfile.hardened` **no se probó en esta sesión**; el overlay runtime sí se probó contra la imagen real. Si usas el build, valida con `./scripts/verify-hardening.sh`.

## Ajustes de host

```bash
sudo ./scripts/host-tuning.sh
```

Activa `vm.overcommit_memory=1` (Redis) y `vm.swappiness=10`.

## Stack QA completo

Incluye `billing/` (nuestra app de facturación), `docker-compose.qa.yml`, Caddy
con dos dominios, PostgreSQL y PayRam hardened.

```bash
cp .env.qa.example .env.qa
# edita dominios y secretos
./scripts/bootstrap-qa.sh
./scripts/qa-smoke.sh
```

Guía: [docs/DEPLOY-QA.md](docs/DEPLOY-QA.md) · pruebas: [docs/QA-MAINNET-TEST.md](docs/QA-MAINNET-TEST.md).

## Requisitos

- Docker Compose v2.24+.
- VPS x86_64 o arm64 (la imagen PayRam se fija por digest).
- PostgreSQL 16-alpine para el servicio `db`; la imagen PayRam también arranca un Postgres interno, que este overlay desactiva cuando `POSTGRES_HOST=db`.
- No instalar `payram-updater`. No publicar 5432.

## Legal

PayRam Core es software propietario de PayRam; este repositorio no redistribuye su imagen ni su código. El overlay es código propio (MIT). Usa la imagen conforme a los términos del proveedor. No afiliado a PayRam.
