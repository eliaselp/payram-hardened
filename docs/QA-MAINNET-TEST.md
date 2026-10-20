# QA mainnet — USDC Base y tarjeta Visa

Objetivo: validar el flujo real con importes pequeños antes de operar.

## Seguridad previa

- [ ] `PAYRAM_AUTO_APPROVE=false`, `PAYRAM_OTP_THRESHOLD_USD=0`.
- [ ] Límites hourly/daily bajos (1000/500 o menos).
- [ ] Hot wallet solo gas; cold wallet hardware.
- [ ] `ALLOW_KNOWN_VULNERABLE_PAYRAM=true` solo en QA, nunca en producción estable.
- [ ] Webhook activo y `PAYRAM_API_KEY` correcta.
- [ ] Backup de `.env.qa` y claves.

## Matriz mínima

| # | Caso | Importe | Resultado esperado |
|---|---|---|---|
| 1 | USDC nativo en Base | USD 1 | Checkout muestra Base/USDC; webhook `FILLED`; factura `Pagada`; tx en BaseScan. |
| 2 | Tarjeta Visa vía PayRam Wallet | USD 1 | Onramp/KYC; pago con tarjeta; recibes USDC en Base; factura `Pagada`. |
| 3 | Bitcoin Smart Bridge | USD 1 | Cliente paga BTC; recibes USDC/Base; `FILLED`. |
| 4 | USDT Tron Smart Bridge | USD 1 | Cliente paga TRC-20; recibes USDC/Base. |
| 5 | Solana | USD 1 | Cliente paga SOL/USDC; recibes USDC/Base. |
| 6 | BNB Smart Bridge | USD 1 | Cliente paga BNB/USDC; recibes USDC/Base. |
| 7 | Pago parcial | USD 0.50 de 1 | Factura `Pago parcial`; saldo correcto. |
| 8 | Exceso | USD 2 de 1 | Factura `Pagada (exceso)`. |
| 9 | Webhook caído | USD 1 | Billing apagado; pagar; levantar; reconciliar. |
| 10 | Reembolso | USD 1 | Payout USDC/Base aprobado/OTP. |

## Evidencia por prueba

- Número de factura y `reference_id`.
- Estado en PayRam y en billing.
- `tx_hash` y enlace al explorer.
- Captura del checkout (sin datos sensibles).
- Log del webhook (`docker compose logs billing`).
- Saldo/sweep en PayRam.

## Criterios de aborto

- Webhook no llega y la reconciliación no corrige en 15 min.
- El importe/token recibido no coincide con la factura.
- Aparece un payout no autorizado.
- El Hot wallet se vacía o hay movimientos desconocidos.
- El checkout muestra métodos fuera de USDC/Base/Smart Bridge/tarjeta.

En cualquiera de esos casos: detener el stack, revisar logs, mover fondos del hot
wallet a una wallet nueva y no continuar hasta entender la causa.

## Post-prueba

- [ ] Verificar sweep al cold wallet.
- [ ] Verificar recibo/PDF.
- [ ] Verificar que la factura no se pueda pagar dos veces.
- [ ] Verificar reconciliación y health.
- [ ] Ajustar límites y auto-aprobación para producción.
