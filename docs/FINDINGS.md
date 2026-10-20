# Hallazgos que justifican este overlay

Auditoría base: 2026-10-19. Detalle completo en el proyecto de facturación
(`docs/SECURITY-AUDIT.md`).

| Severidad | Hallazgo | Mitigación en este repo |
|---|---|---|
| CRÍTICO | Next.js 16.2.6 RCE no autenticado en `/_next/image` (`GHSA-2xp9-vwfh-vxw4`) | Bloqueo en nginx y Caddy. **No elimina el CVE.** |
| CRÍTICO | Imagen cerrada, sin firma cosign; SBOM/procedencia no verificable contra repo público | Digest fijado; repo privado; no se redistribuye la imagen. |
| ALTO | Postgres interno `payram/payram123`, `0.0.0.0`, `pg_hba 0.0.0.0/0` | Password fuerte, `127.0.0.1/32`, `scram-sha-256`, skip si DB externa. |
| ALTO | Payouts autoaprobados USD 25.000 | Autoapprove off, OTP 0, límites bajos (semilla). |
| ALTO | 935 avisos OSV en 125 paquetes | `apt-get upgrade` opcional; no parchea Next/Go. |
| ALTO | Instalador oficial instala updater root desde GitHub | No se usa; gate busca artefactos. |
| MEDIO | Nginx confía XFF de rangos privados | Líneas eliminadas. |
| MEDIO | `/swagger/` expuesto | Bloqueado. |
| MEDIO | Emails cargan Google Fonts/CDN | No mitigado aquí; personalizar plantillas. |
| MEDIO | Build de Next contiene endpoints dev/preprod (`develop.payments.betora.vip`, Vercel dev) | Reemplazados por endpoints de producción en runtime. |
| MEDIO | Sentry integrado (`SENTRY_DSN`) y MetaMask analytics por defecto | `SENTRY_DSN` vacío; fallback de analytics a localhost en el bundle. |
| ALTO | CORS de PayRam permitía todos los orígenes si `PAYRAM_ALLOWED_ORIGINS` no estaba definido | Se define el origen público de PayRam. |
| MEDIO | Redis corría como root y con `daemonize yes` (Supervisor lo veía FATAL) | `user=redis`, `daemonize no`, PID file en `/var/lib/redis`. |
| MEDIO | PayRam imprime una clave efímera de 64 hex en el log de arranque | Redactor local la reemplaza por `[REDACTED]`. |
| MEDIO | `/api/v1/health` expuesto públicamente con detalle de workers | Bloqueado en Caddy; el healthcheck interno sigue funcionando. |
| ALTO | JWT secrets (`jwt.access.secret`/`jwt.refresh.secret`) se guardan en claro en la DB | No se puede cambiar sin código; DB con password fuerte, red aislada y backups cifrados. |
| INFO | `os/exec` solo se usa para `supervisorctl` (status/restart), argumentos fijos | Revisado; no hay shell con input de usuario. |

## Riesgo residual

El RCE de Next.js y los CVEs del binario Go/Next **siguen presentes**. Este
overlay reduce la superficie y elimina defaults peligrosos, pero no sustituye
un rebuild del proveedor. No usar en mainnet con fondos reales sin aceptación
de riesgo, VPS separado y WAF.
