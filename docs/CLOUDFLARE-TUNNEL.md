# Cloudflare Tunnel para `payment.accesscontrol.ink`

Este modo protege la IP del VPS: **cloudflared sale a Cloudflare por 443/TCP** y
no se publican 80/443 en el host. Cloudflare termina TLS en el edge y envía el
tráfico a Caddy por la red interna de Docker (`http://caddy:80`).

## Arquitectura

```text
Cliente
  → Cloudflare edge (HTTPS)
  → tunnel (cloudflared saliente)
  → http://caddy:80 (red Docker)
      ├── payment.accesscontrol.ink → PayRam
      └── billing.accesscontrol.ink → Billing
```

Caddy escucha en `127.0.0.1:80` y en la red Docker. No hay `A`/`AAAA` pública
del VPS para estos hostnames; Cloudflare crea un `CNAME` proxied al tunnel.

## 1. Crear el tunnel en Cloudflare

1. Entra a **Cloudflare Zero Trust → Networks → Tunnels**.
2. **Create a tunnel → Cloudflared**.
3. Ponle un nombre, por ejemplo `payram-accesscontrol`.
4. Copia el **token**.
5. Agrega dos **Public Hostnames**:

| Hostname | Service |
|---|---|
| `payment.accesscontrol.ink` | `http://caddy:80` |
| `billing.accesscontrol.ink` | `http://caddy:80` |

Cloudflare crea los CNAME proxied. Si existe un `A`/`AAAA` viejo para
`payment` o `billing`, bórralo para que no compita.

> `payment` queda para el checkout de PayRam. `billing` es para el panel de
> facturación. La app necesita dos hostnames porque Caddy enruta por `Host`.
> Si quieres invertirlos, cambia los prompts; igual necesitas ambos.

## 2. Ejecutar setup

```bash
sudo GHCR_TOKEN=ghp_xxx ./setup.sh \
  --tunnel \
  --accept-risk
```

El script usa constantes para tu caso:

```text
BILLING_DOMAIN=billing.accesscontrol.ink
PAYRAM_DOMAIN=payment.accesscontrol.ink
```

Solo pide por consola el **Cloudflare Tunnel token**. Puedes sobrescribir los
dominios con `--domain` / `--payram-domain` si algún día cambias de nombre.

Puedes pasarlos sin interacción:

```bash
sudo -E \
  GHCR_TOKEN=ghp_xxx \
  CLOUDFLARE_TUNNEL_TOKEN='eyJ...' \
  BILLING_DOMAIN='billing.accesscontrol.ink' \
  PAYRAM_DOMAIN='payment.accesscontrol.ink' \
  ./setup.sh --tunnel --accept-risk --yes
```

## 3. Qué configura automáticamente

```env
EDGE_MODE=tunnel
CADDYFILE=./deploy/qa/Caddyfile.tunnel
CADDY_BIND_IP=127.0.0.1
CLOUDFLARED_IMAGE=cloudflare/cloudflared:2026.10.2@sha256:0e56...
CLOUDFLARE_TUNNEL_TOKEN=...
```

- Caddy usa `deploy/qa/Caddyfile.tunnel` y `auto_https off`.
- Caddy se publica solo en `127.0.0.1:80/443`; cloudflared usa `caddy:80`.
- UFW **no abre 80/443**; solo deja el puerto SSH.
- El servicio `cloudflared` se levanta con `--profile tunnel`.
- systemd arranca el stack con el perfil tunnel.

## 4. Verificar

```bash
cd /opt/payram-hardened

docker compose -f docker-compose.qa.yml --env-file .env.qa \
  --profile tunnel ps

docker compose -f docker-compose.qa.yml --env-file .env.qa \
  --profile tunnel logs -f cloudflared

curl -I https://payment.accesscontrol.ink
curl -I https://billing.accesscontrol.ink
```

En Cloudflare, el tunnel debe estar **Healthy** y los dos hostnames deben
apuntar a `http://caddy:80`.

## 5. Si algo falla

| Síntoma | Causa probable |
|---|---|
| 502/1033 | cloudflared no conecta o el hostname apunta a otro service |
| 404 | Caddy no reconoce el `Host`; revisa `BILLING_DOMAIN`/`PAYRAM_DOMAIN` |
| Redirect loop | Caddyfile.tunnel no está montado o Caddy sigue en modo direct |
| TLS error en Cloudflare | El hostname no está proxied o hay un A/AAAA viejo |
| `cloudflared` reinicia | Token inválido/expirado o sin salida 443/TCP |

## 6. Variables

| Variable | ¿Quién la pone? | Obligatoria |
|---|---|---|
| `EDGE_MODE` | `--tunnel` | Sí |
| `BILLING_DOMAIN` | Prompt / `--domain` | Sí |
| `PAYRAM_DOMAIN` | Prompt / `--payram-domain` | Sí |
| `CLOUDFLARE_TUNNEL_TOKEN` | Prompt / env | Sí |
| `CADDYFILE` | setup | Sí, automática |
| `CADDY_BIND_IP` | setup | Sí, `127.0.0.1` |
| `CLOUDFLARED_IMAGE` | setup | Sí, digest |
| `ACME_EMAIL` | no se usa en tunnel | No |
| `GHCR_TOKEN` | tú | Sí, para imágenes privadas |
| `PAYRAM_API_KEY` | onboarding PayRam | Para cobrar |

## 7. mTLS para Billing

El panel `/admin*`, `/login` y `/logout` puede exigir certificado de cliente.
Sin certificado, Caddy responde 404. Los links públicos `/i/*` y el webhook
siguen abiertos. Configuración completa en
[BILLING-MTLS.md](BILLING-MTLS.md).

## 8. Seguridad

- No abras 80/443 en UFW para este VPS; cloudflared solo necesita salida.
- No publiques el token; queda en `.env.qa` con `0600`.
- Si el token se filtra, revócalo en Cloudflare y genera otro.
- Cloudflare Tunnel protege la IP, pero **no sustituye** el hardening del core.
- No pongas Cloudflare Access en `payment`; romperías el checkout de clientes.
  Si quieres proteger el panel de Billing, evalúa rutas separadas.
