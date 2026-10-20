"""CLI de operación: hash de contraseña, reconciliación y backups."""

from __future__ import annotations

import argparse
import getpass
import json
import logging
import sqlite3
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.engine import make_url

from app.config import Settings
from app.db import Database
from app.invoices import reconcile_invoice
from app.models import Invoice
from app.payram import PayRamClient, PayRamError
from app.security import hash_password


def cmd_hash_password(args: argparse.Namespace) -> int:
    if args.stdin:
        password = sys.stdin.read().strip()
    elif args.password:
        password = args.password
    else:
        password = getpass.getpass("Contraseña: ")
        confirm = getpass.getpass("Repite la contraseña: ")
        if password != confirm:
            print("Las contraseñas no coinciden.", file=sys.stderr)
            return 1
    if len(password) < 10:
        print("Usa al menos 10 caracteres.", file=sys.stderr)
        return 1
    print(hash_password(password))
    return 0


def _runtime() -> tuple[Settings, Database, PayRamClient]:
    settings = Settings()
    db = Database(settings.database_url)
    client = PayRamClient(
        settings.payram_base_url,
        settings.payram_api_key,
        timeout=settings.payram_timeout_seconds,
        public_url=settings.payram_public_base,
    )
    return settings, db, client


def cmd_reconcile(_args: argparse.Namespace) -> int:
    settings, db, client = _runtime()
    logging.basicConfig(level=settings.log_level.upper(), format="%(levelname)s %(message)s")
    if not settings.payram_api_key:
        print("PAYRAM_API_KEY vacío; no se puede reconciliar.", file=sys.stderr)
        return 2
    session = db.session()
    try:
        invoices = list(
            session.scalars(
                select(Invoice).where(
                    Invoice.status.in_(["open", "partially_paid"]),
                    Invoice.payram_reference_id.is_not(None),
                )
            ).all()
        )
        updated = 0
        errors = 0
        for invoice in invoices:
            try:
                if reconcile_invoice(session, invoice, client, actor="cron"):
                    updated += 1
                print(f"{invoice.number}: {invoice.status}")
            except PayRamError as exc:
                errors += 1
                print(f"{invoice.number}: ERROR {exc}", file=sys.stderr)
        print(f"Reconciliadas {len(invoices)} facturas; {updated} pasaron a pagada; {errors} errores.")
        return 0 if errors == 0 else 1
    finally:
        session.close()
        db.dispose()


def cmd_backup_sqlite(args: argparse.Namespace) -> int:
    settings = Settings()
    url = make_url(settings.database_url)
    if url.get_backend_name() != "sqlite" or not url.database:
        print("DATABASE_URL no es SQLite.", file=sys.stderr)
        return 2
    source = Path(url.database)
    destination = Path(args.destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    if not source.exists():
        print(f"No existe la base: {source}", file=sys.stderr)
        return 1
    with sqlite3.connect(str(source)) as src, sqlite3.connect(str(destination)) as dst:
        src.backup(dst)
    print(str(destination))
    return 0


def cmd_stats(_args: argparse.Namespace) -> int:
    _settings, db, _client = _runtime()
    session = db.session()
    try:
        rows = session.execute(
            select(Invoice.status, func.count(), func.coalesce(func.sum(Invoice.total_cents), 0))
            .group_by(Invoice.status)
        ).all()
        print(json.dumps(
            [
                {"status": row[0], "count": int(row[1]), "total_cents": int(row[2])}
                for row in rows
            ],
            indent=2,
        ))
        return 0
    finally:
        session.close()
        db.dispose()


def cmd_check(_args: argparse.Namespace) -> int:
    settings = Settings()
    for warning in settings.validation_warnings():
        print(f"WARN: {warning}")
    print(f"PAYRAM_BASE_URL={settings.payram_base_url}")
    print(f"PAYRAM_CONFIGURED={settings.payram_configured}")
    print(f"DATABASE_URL={settings.database_url}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="python -m app.cli")
    sub = parser.add_subparsers(dest="command", required=True)

    p = sub.add_parser("hash-password", help="Genera ADMIN_PASSWORD_HASH")
    p.add_argument("password", nargs="?")
    p.add_argument("--stdin", action="store_true", help="Lee la contraseña por stdin")
    p.set_defaults(func=cmd_hash_password)

    p = sub.add_parser("reconcile", help="Sincroniza facturas abiertas con PayRam")
    p.set_defaults(func=cmd_reconcile)

    p = sub.add_parser("backup-sqlite", help="Copia consistente de la base SQLite")
    p.add_argument("destination")
    p.set_defaults(func=cmd_backup_sqlite)

    p = sub.add_parser("stats", help="Resumen por estado")
    p.set_defaults(func=cmd_stats)

    p = sub.add_parser("check", help="Muestra advertencias de configuración")
    p.set_defaults(func=cmd_check)
    return parser


def main() -> int:
    parser = build_parser()
    args = parser.parse_args()
    return int(args.func(args))


if __name__ == "__main__":
    raise SystemExit(main())
