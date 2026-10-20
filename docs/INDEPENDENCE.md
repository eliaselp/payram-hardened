# ¿Se puede obviar el código privado?

## Respuesta corta

- **Para ejecutar PayRam tal cual: no.** El binario Go y el build de Next.js
  son el gateway. Sin ellos no hay PayRam; el overlay no los reemplaza.
- **Para desplegar solo desde tu GitHub: sí, con GHCR.** Publicas una imagen
  derivada en tu registry privado y el VPS no toca Docker Hub. Pero el código
  dentro sigue siendo del vendor.
- **Para no depender del código privado: hay que reemplazarlo.** Eso es un
  gateway propio (clean-room) o un proyecto MIT real, no un parche.

## Opciones

### A. GHCR privado (rápido)

Repo + GHCR + VPS. Cumple “solo mi GitHub” a nivel de artefactos. Mantiene
todas las funciones de PayRam. Riesgo: CVEs y código cerrado siguen dentro.

### B. Clean-room de un gateway USDC/Base

Construir desde cero con código propio:

- Facturas y links (ya está en `billing/`).
- Depósitos USDC en Base (HD wallets, detección on-chain, sweeps).
- Smart contracts de sweep/fund collector.
- Payouts y conciliación.
- Tarjeta: integración con un onramp regulado (Transak/Wert/Coinbase), KYC.
- Multi-chain: BTC, Tron, Solana, BNB y bridges; es la parte más grande.

Esto sí sería 100% tuyo y auditable, pero es un proyecto fintech serio:
contratos, custodia, cumplimiento, auditoría y operación 24/7. No es un parche
de una tarde.

### C. Fork de un gateway MIT real

Por ejemplo BTCPay Server (MIT) para BTC/Lightning; para USDC/Base y tarjeta
haría falta desarrollo/plugins y un onramp. Legalmente limpio, pero no es
PayRam y la cobertura no es idéntica.

## Recomendación

Para QA/mainnet controlado ya: **A (GHCR) + overlay + billing**. Para producción
estable: **B o C**, o un rebuild del vendor con Next/Go parcheados y firma.

No llames “seguro” a A: es riesgo residual aceptado y auditado, no una
certificación.
