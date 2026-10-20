# mTLS para el panel de Billing

Objetivo: que `/`, `/admin*`, `/login` y `/logout` de
`billing.accesscontrol.ink` solo respondan si el cliente presenta un
certificado válido. Sin certificado, Caddy responde **404**. Los links
públicos `/i/*` y el webhook `/webhooks/payram` siguen abiertos.

## Por qué no se hace solo en Caddy

Cloudflare Tunnel termina TLS en el edge. Caddy recibe HTTP desde
`cloudflared`, no el certificado del cliente. Por eso:

1. Cloudflare valida el certificado del cliente (mTLS).
2. Cloudflare envía a Caddy el resultado con las cabeceras de mTLS.
3. Caddy comprueba `cf-cert-verified: true` en las rutas de administración.
   Si falta o es `false`, devuelve **404**.
4. Caddy comprueba también `cf-cert-revoked: true` y devuelve 404.

La verificación real la hace Cloudflare; Caddy es la última barrera y evita
que un request sin cabecera llegue a Billing.

## 1. Crear el certificado de cliente

En Cloudflare:

1. **SSL/TLS → Client Certificates**.
2. **Create certificate**.
3. Selecciona el hostname `billing.accesscontrol.ink`.
4. Descarga el certificado y la clave privada.
5. Instálalos solo en los dispositivos/servicios autorizados.

No instales ese certificado en clientes que solo deben ver facturas públicas.

## 2. Activar mTLS solo en las rutas de admin

Cloudflare permite reglas mTLS con condiciones de hostname y path. Crea la
regla para:

```text
/
/admin*
/login
/logout
```

No la apliques a `/i/*` ni a `/webhooks/payram`: romperías los links de
factura y la confirmación de pagos de PayRam.

Si tu plan/UI solo permite activar mTLS para **todo el hostname**, no lo hagas
así: usa una regla WAF/Worker con path, o un hostname separado para el admin.
De lo contrario, los clientes sin certificado no podrán abrir sus facturas.

Luego activa las cabeceras de mTLS hacia el origen:

1. Ve a **Rules → Transform Rules → Managed Transforms**.
2. Activa **Add TLS client auth headers**.

Eso envía al origen:

- `cf-cert-verified`
- `cf-cert-revoked`
- `cf-cert-presented`

Caddy exige `cf-cert-verified: true` en las rutas de admin. Si el header falta
o es `false`, devuelve **404**.

## 3. Si Cloudflare bloquea antes del origen

Si Cloudflare corta el handshake y devuelve 403/526, Caddy no puede cambiarlo
a 404. Tienes dos opciones:

### Opción A: WAF con respuesta 404 (plan Pro)

Crea una regla WAF para las rutas de admin:

```text
(http.host eq "billing.accesscontrol.ink"
 and http.request.uri.path matches "^/(admin|login|logout)")
 and (not cf.tls_client_auth.cert_verified
      or cf.tls_client_auth.cert_revoked)
```

Acción: **Block** con respuesta personalizada **404**.

### Opción B: Worker (plan Free)

Asocia un Worker a `billing.accesscontrol.ink` para las rutas de admin:

```js
export default {
  async fetch(request) {
    const t = request.cf?.tlsClientAuth;
    if (!t || t.certPresented !== "1" || t.certVerified !== "SUCCESS" || t.certRevoked === "1") {
      return new Response("Not Found", { status: 404 });
    }
    return fetch(request);
  }
}
```

El Worker devuelve 404 antes de llegar a Caddy. Caddy mantiene su propia
comprobación como defensa en profundidad.

## 4. Verificar

Sin certificado:

```bash
curl -i https://billing.accesscontrol.ink/admin
# HTTP/2 404
```

Con certificado:

```bash
curl -i \
  --cert client.pem \
  --key client.key \
  https://billing.accesscontrol.ink/admin
# 200 o redirect al login
```

Los links públicos deben seguir funcionando sin certificado:

```bash
curl -i https://billing.accesscontrol.ink/i/<token>
# 200
```

## 5. Límites

- Cloudflare ve el tráfico y termina TLS; esto no te oculta de Cloudflare.
- Si el header `cf-cert-verified` no está configurado, **todo el admin
  devuelve 404**. Es intencional: falla cerrado.
- No pongas mTLS en `/i/*` ni en `/webhooks/payram`.
- No uses Cloudflare Access para esto si necesitas 404 exacto: Access suele
  devolver login/403.
- El token del tunnel es secreto; no lo pongas en Git.
