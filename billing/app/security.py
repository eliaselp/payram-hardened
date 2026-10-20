"""Seguridad: hash de contraseña, CSRF, rate limit de login y API keys."""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import threading
import time
from collections import defaultdict, deque

from fastapi import Request

_SCRYPT_N = 2**14
_SCRYPT_R = 8
_SCRYPT_P = 1
_SCRYPT_DKLEN = 32


def hash_password(password: str) -> str:
    if not password:
        raise ValueError("La contraseña no puede estar vacía")
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(
        password.encode("utf-8"),
        salt=salt,
        n=_SCRYPT_N,
        r=_SCRYPT_R,
        p=_SCRYPT_P,
        dklen=_SCRYPT_DKLEN,
    )
    return "scrypt:{}:{}:{}:{}:{}".format(
        _SCRYPT_N,
        _SCRYPT_R,
        _SCRYPT_P,
        base64.b64encode(salt).decode("ascii"),
        base64.b64encode(digest).decode("ascii"),
    )


def verify_password(password: str, encoded: str | None) -> bool:
    if not password or not encoded:
        return False
    try:
        if encoded.startswith("scrypt:"):
            scheme, n_s, r_s, p_s, salt_b64, hash_b64 = encoded.split(":")
        elif encoded.startswith("scrypt$"):
            # Compatibilidad con hashes antiguos.
            scheme, n_s, r_s, p_s, salt_b64, hash_b64 = encoded.split("$")
        else:
            return False
        if scheme != "scrypt":
            return False
        salt = base64.b64decode(salt_b64)
        expected = base64.b64decode(hash_b64)
        digest = hashlib.scrypt(
            password.encode("utf-8"),
            salt=salt,
            n=int(n_s),
            r=int(r_s),
            p=int(p_s),
            dklen=len(expected),
        )
        return hmac.compare_digest(digest, expected)
    except (ValueError, TypeError):
        return False


def ensure_csrf_token(request: Request) -> str:
    token = request.session.get("csrf_token")
    if not token:
        token = secrets.token_urlsafe(32)
        request.session["csrf_token"] = token
    return token


def verify_csrf(request: Request, token: str | None) -> None:
    expected = request.session.get("csrf_token")
    if not expected or not token or not hmac.compare_digest(str(token), str(expected)):
        from fastapi import HTTPException

        raise HTTPException(status_code=400, detail="Token CSRF inválido o ausente")


def verify_api_key(provided: str | None, expected: str) -> bool:
    if not expected or not provided:
        return False
    return hmac.compare_digest(provided, expected)


class LoginRateLimiter:
    """Límite simple en memoria. Suficiente para una sola instancia.

    Si se escala a varios workers/procesos, mover a Redis.
    """

    def __init__(self, max_attempts: int, window_seconds: int) -> None:
        self.max_attempts = max_attempts
        self.window_seconds = window_seconds
        self._attempts: dict[str, deque[float]] = defaultdict(deque)
        self._lock = threading.Lock()

    def _prune(self, key: str, now: float) -> deque[float]:
        attempts = self._attempts[key]
        cutoff = now - self.window_seconds
        while attempts and attempts[0] < cutoff:
            attempts.popleft()
        return attempts

    def is_blocked(self, key: str) -> bool:
        now = time.monotonic()
        with self._lock:
            attempts = self._prune(key, now)
            return len(attempts) >= self.max_attempts

    def record_failure(self, key: str) -> None:
        now = time.monotonic()
        with self._lock:
            attempts = self._prune(key, now)
            attempts.append(now)

    def reset(self, key: str) -> None:
        with self._lock:
            self._attempts.pop(key, None)
