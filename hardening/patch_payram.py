#!/usr/bin/env python3
"""Hardening overlay para PayRam Core (imagen precompilada).

Se ejecuta como entrypoint antes de /startup.sh. No redistribuye la imagen
del vendor: parchea en runtime los archivos de configuración del contenedor.

Falla cerrado: si un patrón esperado no existe (por ejemplo, tras una
actualización del vendor), el contenedor no arranca.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path


class PatchError(RuntimeError):
    pass


def env_bool(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def env_int(name: str, default: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        return int(raw)
    except ValueError as exc:
        raise PatchError(f"{name} debe ser entero") from exc


def read(path: Path) -> str:
    if not path.is_file():
        raise PatchError(f"No existe {path}")
    return path.read_text(encoding="utf-8")


def write(path: Path, text: str, mode: int | None = None) -> None:
    path.write_text(text, encoding="utf-8")
    if mode is not None:
        path.chmod(mode)


def replace_line_containing(text: str, needle: str, replacement: str, label: str) -> str:
    if replacement in text:
        return text
    lines = text.splitlines()
    hits = [i for i, line in enumerate(lines) if needle in line]
    if len(hits) != 1:
        raise PatchError(f"{label}: se esperaba 1 línea con {needle!r}, hay {len(hits)}")
    lines[hits[0]] = replacement
    return "\n".join(lines) + ("\n" if text.endswith("\n") else "")


def patch_startup(root: Path) -> None:
    path = root / "startup.sh"
    text = read(path)
    old_password = "CREATE ROLE payram LOGIN PASSWORD 'payram123';"
    if old_password in text:
        password = os.getenv("INTERNAL_POSTGRES_PASSWORD", "")
        if len(password) < 24:
            raise PatchError("INTERNAL_POSTGRES_PASSWORD debe tener al menos 24 caracteres")
        escaped = password.replace("'", "''")
        text = text.replace(
            old_password,
            f"CREATE ROLE payram LOGIN PASSWORD '{escaped}';",
            1,
        )
    elif "CREATE ROLE payram LOGIN PASSWORD '" not in text:
        raise PatchError("startup.sh: no se encontró la creación del rol payram")

    text = replace_line_containing(
        text,
        "grep -q '^host",
        """    su - postgres -c "grep -q '^host\\s\\+all\\s\\+all\\s\\+127\\.0\\.0\\.1/32' '$PG_HBA_CONF' || echo 'host    all             all             127.0.0.1/32               scram-sha-256' >> '$PG_HBA_CONF'" """.rstrip(),
        "startup.sh pg_hba",
    )
    text = replace_line_containing(
        text,
        "listen_addresses",
        """    su - postgres -c "sed -i 's/^#\\?listen_addresses\\s*=.*/listen_addresses = '\\''localhost'\\''/' $PG_CONF" """.rstrip(),
        "startup.sh listen_addresses",
    )

    external_db = os.getenv("POSTGRES_HOST", "").strip().lower() not in {
        "",
        "localhost",
        "127.0.0.1",
    }
    guard_marker = "# HARDENING: internal postgres guard"
    if external_db and guard_marker not in text:
        init_marker = "# 🟡 Initialize PostgreSQL if needed"
        supervisor_marker = "# 🟢 Now launch Supervisor"
        if init_marker not in text or supervisor_marker not in text:
            raise PatchError("startup.sh: no se encontraron los marcadores de PostgreSQL/Supervisor")
        guard = (
            f"{guard_marker}\n"
            'if [ "${POSTGRES_HOST:-}" = "localhost" ] || '
            '[ "${POSTGRES_HOST:-}" = "127.0.0.1" ] || '
            '[ -z "${POSTGRES_HOST:-}" ]; then\n'
        )
        text = text.replace(init_marker, guard + init_marker, 1)
        text = text.replace(supervisor_marker, "fi\n" + supervisor_marker, 1)

    write(path, text, 0o755)


def patch_supervisor(root: Path) -> None:
    path = root / "etc/supervisor/conf.d/supervisord.conf"
    text = read(path)
    external_db = os.getenv("POSTGRES_HOST", "").strip().lower() not in {
        "",
        "localhost",
        "127.0.0.1",
    }
    if not external_db:
        return
    redis_command = 'command=/usr/bin/redis-server /etc/redis/redis.conf --logfile ""'
    if redis_command in text and "user=redis" not in text.split("[program:redis-server]", 1)[-1].split("[program:", 1)[0]:
        text = text.replace(
            "[program:redis-server]\n" + redis_command,
            "[program:redis-server]\n" + redis_command + "\nuser=redis",
            1,
        )
        write(path, text)

    if "[program:postgres]" not in text:
        return
    new, count = re.subn(
        r"(?ms)^\[program:postgres\].*?(?=^\[program:)",
        "",
        text,
        count=1,
    )
    if count != 1:
        raise PatchError("supervisord.conf: no se encontró [program:postgres]")
    write(path, new)


def patch_redis(root: Path) -> None:
    path = root / "etc/redis/redis.conf"
    if not path.is_file():
        return
    text = read(path)
    if "daemonize yes" in text:
        text = text.replace("daemonize yes", "daemonize no", 1)
    elif "daemonize no" not in text:
        raise PatchError("redis.conf: no se encontró daemonize")
    if "pidfile /run/redis/redis-server.pid" in text:
        text = text.replace(
            "pidfile /run/redis/redis-server.pid",
            "pidfile /var/lib/redis/redis-server.pid",
            1,
        )
    write(path, text)


def patch_config(root: Path) -> None:
    auto_approve = "true" if env_bool("PAYRAM_AUTO_APPROVE", False) else "false"
    values = {
        "autoApproveEnabled": auto_approve,
        "autoApproveAmountInUSD": str(env_int("PAYRAM_AUTO_APPROVE_USD", 0)),
        "otpThresholdInUSD": str(env_int("PAYRAM_OTP_THRESHOLD_USD", 0)),
        "dailyLimitInUSD": str(env_int("PAYRAM_DAILY_LIMIT_USD", 1000)),
        "hourlyLimitInUSD": str(env_int("PAYRAM_HOURLY_LIMIT_USD", 500)),
    }
    found_any = False
    for name in ("config_production.yaml", "config_development.yaml"):
        path = root / name
        if not path.is_file():
            continue
        found_any = True
        text = read(path)
        for key, value in values.items():
            pattern = rf"^{re.escape(key)}:\s*[^\n]+$"
            new_text, count = re.subn(pattern, f"{key}: {value}", text, count=1, flags=re.M)
            if count != 1:
                raise PatchError(f"{name}: no se encontró {key}")
            text = new_text
        write(path, text)
    if not found_any:
        raise PatchError("No se encontró config_production.yaml ni config_development.yaml")


def patch_nginx(root: Path) -> None:
    changed = False
    for rel in ("etc/nginx/nginx.conf", "etc/nginx/nginx-ssl.conf.template"):
        path = root / rel
        if not path.is_file():
            continue
        changed = True
        text = read(path)
        text = re.sub(r"^\s*set_real_ip_from\s+.*\n", "", text, flags=re.M)
        text = re.sub(r"^\s*real_ip_header\s+.*\n", "", text, flags=re.M)
        text = re.sub(r"^\s*real_ip_recursive\s+.*\n", "", text, flags=re.M)
        if "server_tokens off;" not in text:
            if "http {" not in text:
                raise PatchError(f"{rel}: no se encontró el bloque http")
            text = text.replace("http {", "http {\n    server_tokens off;", 1)
        if "X-Content-Type-Options" not in text:
            text = text.replace(
                "server_tokens off;",
                'server_tokens off;\n'
                '    add_header X-Content-Type-Options "nosniff" always;\n'
                '    add_header Referrer-Policy "strict-origin-when-cross-origin" always;',
                1,
            )
        write(path, text)
    if not changed:
        raise PatchError("No se encontró nginx.conf")

    locations = root / "etc/nginx/payram-locations.conf"
    text = read(locations)
    marker = "# HARDENING: block vulnerable/unnecessary routes"
    if marker not in text:
        blocks = (
            f"{marker}\n"
            "    location ^~ /_next/image { return 404; }\n"
            "    location ^~ /api/og { return 404; }\n"
            "    location ^~ /_next/webpack-hmr { return 404; }\n"
            "    location ^~ /__nextjs { return 404; }\n"
            "    location ^~ /swagger { return 404; }\n\n"
        )
        text = blocks + text
        write(locations, text)


def patch_frontend(root: Path) -> None:
    """Elimina endpoints de desarrollo/telemetría del build de Next.js.

    Son sustituciones de strings en assets ya compilados; no tocan la lógica
    de negocio. Si después quedan endpoints prohibidos, falla cerrado.
    """
    if not env_bool("PAYRAM_PATCH_FRONTEND", True):
        return
    web = root / "web/.next"
    if not web.is_dir():
        raise PatchError("No se encontró /web/.next")

    replacements = {
        "https://develop.payments.betora.vip": "https://x.payram.com",
        "https://preprod-wallet.resuefas.vip": "https://x.payram.com",
        "https://payram-web-wallet-develop.vercel.app": "https://wallet.payram.com",
        "https://payram-web-wallet-pre-prod.vercel.app/": "https://wallet.payram.com/",
        "https://mm-sdk-analytics.api.cx.metamask.io/": "http://127.0.0.1:9/",
        "curl -fsSL https://raw.githubusercontent.com/PayRam/payram-updates/main/setup_payram_updater.sh | sudo bash": "# updater disabled by hardening overlay",
    }
    forbidden = [
        "develop.payments.betora.vip",
        "preprod-wallet.resuefas.vip",
        "payram-web-wallet-develop.vercel.app",
        "payram-web-wallet-pre-prod.vercel.app",
        "mm-sdk-analytics.api.cx.metamask.io",
        "setup_payram_updater.sh",
    ]

    changed = 0
    for path in web.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        new = text
        for old, value in replacements.items():
            new = new.replace(old, value)
        if new != text:
            path.write_text(new, encoding="utf-8")
            changed += 1

    remaining = []
    for path in web.rglob("*"):
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except (UnicodeDecodeError, OSError):
            continue
        for marker in forbidden:
            if marker in text:
                remaining.append(f"{path}:{marker}")
    if remaining:
        raise PatchError(f"Frontend con endpoints prohibidos tras el parche: {remaining[:5]}")
    print(f"[hardening] frontend saneado ({changed} archivos modificados)", flush=True)


def validate(root: Path) -> None:
    if root != Path("/"):
        return
    nginx = Path("/usr/sbin/nginx")
    if not nginx.exists():
        raise PatchError("nginx no encontrado para validar")
    result = subprocess.run(
        [str(nginx), "-t"],
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    if result.returncode != 0:
        raise PatchError(f"nginx -t falló: {result.stderr[-1000:]}")


def main() -> int:
    if not env_bool("PAYRAM_HARDENING_ENABLED", True):
        raise PatchError("PAYRAM_HARDENING_ENABLED debe ser true")
    root = Path(os.getenv("PAYRAM_ROOT", "/")).resolve()
    patch_startup(root)
    patch_supervisor(root)
    patch_redis(root)
    patch_config(root)
    patch_nginx(root)
    patch_frontend(root)

    if root == Path("/"):
        # El binario arranca con un entrypoint propio; validar antes de exec.
        validate(root)

    print(
        "[hardening] OK: postgres interno asegurado, payouts restrictivos, "
        "nginx sin XFF spoofing y rutas vulnerables bloqueadas.",
        flush=True,
    )
    if "--check" in sys.argv:
        return 0

    startup = root / "startup.sh"
    os.execv(str(startup), [str(startup), *[a for a in sys.argv[1:] if a != "--check"]])
    return 1


if __name__ == "__main__":
    try:
        raise SystemExit(main())
    except PatchError as exc:
        print(f"[hardening] FAIL: {exc}", file=sys.stderr, flush=True)
        raise SystemExit(2)
