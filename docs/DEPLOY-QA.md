# Despliegue QA (mainnet controlado)

Stack: **PayRam hardened + Billing + Caddy + PostgreSQL**.

> Riesgo residual: PayRam 3.9.0 conserva CVEs críticos en Next.js/Go. Este stack
> es para QA/mainnet controlado por el propietario, con límites bajos y wallets
> de prueba. No es un producto certificado.

## 1. Requisitos

- VPS Ubuntu 22.04/24.04, x86_64 o arm64, 2 CPU/4 GB/50 GB.
- DNS:
  - `billing.tudominio.com` → IP del VPS
  - `pay.tudominio.com` → IP del VPS
- Wallet Master, Cold wallet hardware y Hot wallet con gas en Base.
- Acceso SSH root.

## 2. Subir el repo

```bash
rsync -avz --exclude '.git' --exclude '.env.qa' \
  ./ root@IP_VPS:/opt/payram-hardened/
```

## 3. Bootstrap

```bash
ssh root@IP_VPS
cd /opt/payram-hardened

BILLING_DOMAIN=billing.tudominio.com \
PAYRAM_DOMAIN=pay.tudominio.com \
ACME_EMAIL=admin@tudominio.com \
bash scripts/bootstrap-qa.sh
```

Genera `.env.qa`, secretos, hash admin, valida con `security-gate`, levanta el
stack y guarda credenciales en `/root/payram-qa-credentials.txt`.

## 4. Configurar PayRam

En `https://pay.tudominio.com`:

1. Crear cuenta root fuerte + 2FA.
2. Wallets:
   - Master (MetaMask/WalletConnect) solo para desplegar.
   - Cold wallet hardware, distinta de Master.
   - Hot wallet con ETH en Base solo para gas.
3. Wallet Management → Deposit Wallet → Base → Deploy Contract.
4. Payment Options:
   - USDC en Base.
   - Smart Bridge: Bitcoin, USDT-Tron, Solana, BNB.
   - Tarjetas vía PayRam Wallet (liquidación Base).
   - **No** habilitar Ethereum/Polygon nativos si quieres todo en USDC/Base.
5. Settings → Site URL: `https://pay.tudominio.com`.
6. Project → API Keys: crear key.
7. Settings → Webhook: `https://billing.tudominio.com/webhooks/payram`, activo.
8. Payouts: `autoApproveEnabled=false`, OTP 0, límites bajos (el overlay ya
   intenta dejar la semilla; verifica en dashboard).

## 5. Conectar Billing

Edita `.env.qa`:

```env
PAYRAM_API_KEY=la_key_del_proyecto
COMPANY_NAME=...
COMPANY_LEGAL_NAME=...
COMPANY_TAX_ID=...
COMPANY_ADDRESS=...
COMPANY_EMAIL=...
SMTP_HOST=...
SMTP_USERNAME=...
SMTP_PASSWORD=...
SMTP_FROM_EMAIL=...
```

Aplica:

```bash
docker compose -f docker-compose.qa.yml --env-file .env.qa up -d billing
```

Verifica:

```bash
curl -fsS https://billing.tudominio.com/healthz
./scripts/qa-smoke.sh
```

## 6. Smoke test

`qa-smoke.sh`:

- health de billing,
- health interno de PayRam,
- comprueba `/swagger`, `/api/v1/health` y `/_next/image` bloqueados,
- crea una factura de USD 1 vía API y muestra el link.

Si `PAYRAM_API_KEY` está vacío, la factura se crea sin link (`payment_link_error`).

## 7. Primer pago real

Sigue [QA-MAINNET-TEST.md](QA-MAINNET-TEST.md). Empieza con **USD 1** por USDC en
Base y tarjeta; no empieces con importes altos.

## 8. Operación

- Logs: `docker compose -f docker-compose.qa.yml --env-file .env.qa logs -f payram billing caddy`
- Reinicio: `docker compose ... up -d`
- Actualización: revisar digest/SBOM; no usar `latest`.
- Backups: añadir `pg_dump` + SQLite + `.env.qa` cifrado antes de producción.
