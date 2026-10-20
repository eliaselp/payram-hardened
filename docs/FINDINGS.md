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

## Riesgo residual

El RCE de Next.js y los CVEs del binario Go/Next **siguen presentes**. Este
overlay reduce la superficie y elimina defaults peligrosos, pero no sustituye
un rebuild del proveedor. No usar en mainnet con fondos reales sin aceptación
de riesgo, VPS separado y WAF.
