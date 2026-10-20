# Despliegue solo con tu GitHub (GHCR privado)

El repo `payram-hardened` puede desplegarse usando **solo tu GitHub**:

- Código y JSON de despliegue en el repo.
- Imágenes en tu **GitHub Container Registry (GHCR) privado**.
- El VPS solo necesita un PAT con `read:packages` para `ghcr.io`.

> Esto **no convierte el core en código tuyo**. La imagen GHCR contiene el
> binario cerrado de PayRam; solo elimina la dependencia de Docker Hub en el
> runtime y centraliza todo bajo tu cuenta.

## 1. Publicar las imágenes

En GitHub → Actions → **publish-ghcr** → Run workflow.

El workflow:

1. Construye `ghcr.io/<owner>/payram-hardened:3.9.0` desde `Dockerfile.ghcr`.
2. Construye `ghcr.io/<owner>/payram-billing:1.0.0`.
3. Espeja `postgres:16-alpine` y `caddy:2.11.4-alpine` a GHCR.
4. Imprime los digests en el resumen del run.

Los paquetes deben quedar **privados** (Settings → Package visibility).

## 2. Configurar `.env.qa`

Copia del resumen del workflow:

```env
PAYRAM_IMAGE=ghcr.io/eliaselp/payram-hardened@sha256:<digest>
PAYRAM_APPROVED_DIGEST=sha256:<digest>
PAYRAM_VENDOR_DIGEST=sha256:1181cd5d886a50d68565c275dad231d9a1d75735dca3ce5873f334c2f9db3791
POSTGRES_IMAGE=ghcr.io/eliaselp/payram-postgres@sha256:<digest>
CADDY_IMAGE=ghcr.io/eliaselp/payram-caddy@sha256:<digest>
BILLING_IMAGE=ghcr.io/eliaselp/payram-billing@sha256:<digest>
ALLOW_KNOWN_VULNERABLE_PAYRAM=true
```

`PAYRAM_VENDOR_DIGEST` es el digest del core vendor dentro de la imagen; el
security-gate lo usa para no “olvidar” los CVEs conocidos al usar una imagen
derivada.

## 3. Login en el VPS

Crea un PAT con `read:packages` y:

```bash
echo "$GHCR_PAT" | docker login ghcr.io -u eliaselp --password-stdin
```

## 4. Desplegar

```bash
docker compose -f docker-compose.qa.yml --env-file .env.qa up -d --no-build
```

`--no-build` usa la imagen de billing de GHCR. Si no defines `BILLING_IMAGE`,
puedes construir billing desde el repo (`up -d --build`).

## 5. Verificar

```bash
./scripts/security-gate.sh   # ENV_FILE=.env.qa si no es el .env por defecto
./scripts/qa-smoke.sh
```

El gate seguirá mostrando el warning de CVEs conocidos: la imagen derivada
sigue conteniendo Next.js 16.2.6 y el binario Go vulnerable.

## Límites

- GHCR es de GitHub, no de PayRam; el paquete privado sigue siendo una copia
  del software propietario.
- No elimina los CVEs; solo centraliza la cadena de suministro en tu cuenta.
- Para una independencia real del código cerrado se necesita un gateway propio
  o un proyecto MIT real (ver `INDEPENDENCE.md`).
