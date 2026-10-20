#!/usr/bin/env python3
"""Prepara .env.qa para CI con secretos deterministas de prueba."""
import sys
from pathlib import Path

p = Path(sys.argv[1])
hash_value = sys.argv[2]
s = p.read_text()
vals = {
    "BILLING_DOMAIN": "billing.example.com",
    "PAYRAM_DOMAIN": "pay.example.com",
    "ACME_EMAIL": "admin@example.com",
    "BASE_URL": "https://billing.example.com",
    "PAYRAM_BASE_URL": "https://pay.example.com",
    "PAYRAM_PUBLIC_URL": "https://pay.example.com",
    "ALLOWED_HOSTS": "billing.example.com,localhost,127.0.0.1",
    "SECRET_KEY": "a" * 64,
    "AES_KEY": "b" * 64,
    "POSTGRES_PASSWORD": "ci-postgres-password-strong-123",
    "INTERNAL_POSTGRES_PASSWORD": "ci-internal-postgres-password-strong-123",
    "BILLING_API_KEY": "c" * 64,
    "ADMIN_PASSWORD_HASH": hash_value,
    "ADMIN_PASSWORD": "",
}
out = []
for line in s.splitlines():
    key = line.split("=", 1)[0] if "=" in line else ""
    out.append(f"{key}={vals[key]}" if key in vals else line)
p.write_text("\n".join(out) + "\n")
