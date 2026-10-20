# Prueba runtime del overlay

Fecha: 2026-10-20
Imagen: `payramapp/payram@sha256:1181cd5d886a50d68565c275dad231d9a1d75735dca3ce5873f334c2f9db3791` (3.9.0)
PostgreSQL: `postgres:16@sha256:1a6ab3f5345eb6dbe04a1349529caabdb0ab09293a09590fad07b2246bfa4b54` (externo, red Docker).

## Comando

Se arrancó el contenedor con:

- `entrypoint=/hardening/entrypoint.sh`
- `POSTGRES_HOST=payram-test-db` (externo)
- `PAYRAM_HARDENING_ENABLED=true`
- `PAYRAM_PATCH_FRONTEND=true`
- `PAYRAM_ALLOWED_ORIGINS=https://pay.example.com`
- `SENTRY_DSN=""`
- `LOG_LEVEL=warn`
- `PAYRAM_AUTO_APPROVE=false`, `OTP=0`, límites 1000/500.

## Resultados

### Arranque

```text
[hardening] frontend saneado (8 archivos modificados)
[hardening] OK: postgres interno asegurado, payouts restrictivos, nginx sin XFF spoofing y rutas vulnerables bloqueadas.
```

El primer arranque tardó ~2 minutos en migraciones/seed. El healthcheck debe usar `start_period` de 240 s.

### Health

```json
{
  "status": "ok",
  "db": "ok",
  "version": "3.9.0",
  "workers": {
    "account-processor": "RUNNING",
    "base-listener": "RUNNING",
    "broadcast-scw-deposit-wallet-processor": "RUNNING",
    "btc-listener": "RUNNING",
    "deposit-processor": "RUNNING",
    "email-processor": "RUNNING",
    "erc20-sweep-approval-processor": "RUNNING",
    "eth-listener": "RUNNING",
    "nginx": "RUNNING",
    "payram-core": "RUNNING",
    "payram-web": "RUNNING",
    "polygon-listener": "RUNNING",
    "redis-server": "RUNNING",
    "trx-listener": "RUNNING",
    "webhook-processor": "RUNNING"
  }
}
```

No aparece `postgres` porque el interno se desactivó (DB externa). Redis corre como usuario `redis`, no root.

### Rutas

| Ruta | Resultado |
|---|---|
| `/` | 200 |
| `/swagger/` | 404 |
| `/_next/image?...` | 404 |
| `/api/v1/health` (público) | bloqueado por Caddy; el healthcheck interno sigue funcionando |

### Parches verificados

```text
grep payram123 /startup.sh        -> sin coincidencias
autoApproveEnabled: false
otpThresholdInUSD: 0
dailyLimitInUSD: 1000
hourlyLimitInUSD: 500
server_tokens off
X-Content-Type-Options: nosniff
```

### Log de arranque

PayRam imprime una clave efímera de 64 hex; el redactor local la reemplaza:

```text
Generated Key: [REDACTED]
```

### Limitaciones

- No se validó Caddyfile con `caddy validate` porque Docker Hub falló al descargar la imagen Caddy en este entorno; `docker compose config` sí pasó.
- No se probó un pago on-chain real; eso requiere wallets y mainnet/testnet.
- No se probaron los CVEs de Next.js/Go: siguen presentes. El overlay solo bloquea rutas y elimina defaults.
