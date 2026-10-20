# Auditoría de licencia — PayRam

Fecha: 2026-10-20

## Resultado corto

**No se encontró una licencia MIT verificable para PayRam Core ni para la imagen
`payramapp/payram`.** El hecho de que el README de `payram-scripts` o
`payram-runtime-base` mencione “MIT” **no cubre el core cerrado**, y en esos
repositorios ni siquiera existe un archivo `LICENSE`.

Por tanto, **no es legalmente seguro clonar, reconstruir o redistribuir el core**
basándose en esa afirmación. El overlay de este repositorio sí es MIT y es
nuestro, pero la imagen/binario siguen siendo del proveedor.

## Evidencia

| Elemento | Resultado |
|---|---|
| `PayRam/payram-core` | **404 / privado**; no hay código fuente público. |
| `PayRam/payram-scripts` | GitHub API: `license: null`; **no hay archivo LICENSE**. README dice “MIT License” sin archivo. |
| `PayRam/payram-runtime-base` | GitHub API: `license: null`; **no hay archivo LICENSE**. README/Dockerfile dicen MIT sin archivo. |
| `PayRam/payram-mcp` | `license: null`. |
| `PayRam/payram-shopify` | `license: null`. |
| Repos públicos de la org | Los 20 listados por GitHub: `license: NONE`. |
| Imagen `payramapp/payram` | `org.opencontainers.image.licenses: ""` (vacío). |
| Rootfs | No hay `LICENSE`/`NOTICE` de PayRam; solo licencias de terceros (Next, Node, etc.). |

## Por qué “MIT” en el instalador no basta

`payram-scripts` es el instalador; `payram-runtime-base` es una base Debian con
Postgres/Redis/Node. Aunque esos componentes fueran MIT, **no son el gateway**.
El gateway es el binario Go `/payram` y el build de Next.js, distribuidos en una
imagen separada y sin licencia declarada. Una licencia MIT sobre un instalador
no se extiende automáticamente al software que el instalador descarga.

Además, para redistribuir la imagen completa habría que cumplir las licencias de
todos los componentes de terceros incluidos (Next.js, Node, Redis, PostgreSQL,
librerías npm, etc.), no solo la de PayRam.

## Qué haría legalmente posible el clon/rebuild

1. Que PayRam publique `payram-core` con un archivo `LICENSE` MIT y el código
   fuente; o
2. Una autorización escrita del titular que permita copiar, modificar,
   reconstruir y redistribuir el core y la imagen; o
3. Una auditoría/escrow con derechos de reconstrucción expresamente concedidos.

Sin uno de esos tres, clonar el binario y “hacerlo tuyo” es un riesgo de
infracción de copyright, aunque técnicamente se pueda extraer el rootfs.

## Opciones legales

### A. Overlay (lo que ya está hecho)

Mantener la imagen del proveedor, aplicar nuestro overlay MIT y no
redistribuir la imagen. Es lo que está en este repositorio. Riesgo técnico
residual: los CVEs de Next/Go siguen dentro.

### B. Clean-room / gateway propio

Construir un gateway nuevo con código propio (o sobre un proyecto MIT real,
por ejemplo BTCPay Server) y adaptarlo a USDC/Base. Es legalmente tuyo y
auditable, pero es un desarrollo grande y no es “PayRam clonado”.

### C. Fork de lo público

Puedes hacer fork de `payram-scripts` y `payram-runtime-base` con atribución,
pero eso **no te da el core**. No los presentes como “PayRam completo”.

## Conclusión

- El overlay `payram-hardened` es tuyo (MIT) y se puede publicar.
- La imagen/core de PayRam **no** se puede declarar tuya ni reconstruir
  legalmente sin licencia/permiso.
- Si PayRam te entrega el core bajo MIT, este análisis se revisa y se puede
  hacer el fork/rebuild completo.
