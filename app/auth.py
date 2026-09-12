"""Session authentication for the dashboard.

Deliberately small: one account, a PBKDF2 password hash, and a signed cookie.
The service sits on a private network, so this exists to stop casual access,
not to withstand a determined attacker who is already inside it.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import secrets
import time
from typing import Any

from app import config, db

COOKIE = "arrsubsync_session"
SESSION_DAYS = 30
_ITERATIONS = 240_000


def hash_password(password: str, salt: bytes | None = None) -> str:
    salt = salt or os.urandom(16)
    digest = hashlib.pbkdf2_hmac("sha256", password.encode(), salt, _ITERATIONS)
    return f"pbkdf2${_ITERATIONS}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def verify_password(password: str, stored: str) -> bool:
    try:
        _, rounds, salt_b64, digest_b64 = stored.split("$")
        digest = hashlib.pbkdf2_hmac(
            "sha256", password.encode(), base64.b64decode(salt_b64), int(rounds)
        )
        return hmac.compare_digest(digest, base64.b64decode(digest_b64))
    except Exception:  # noqa: BLE001 - a malformed hash is simply a failed login
        return False


def secret() -> bytes:
    """A stable signing key: from the environment, or generated once and stored."""
    from_env = os.environ.get(config.SECRET_ENV)
    if from_env:
        return from_env.encode()
    stored = db.get_secret("_session_secret")
    if not stored:
        stored = secrets.token_urlsafe(32)
        db.put_secret("_session_secret", stored)
    return stored.encode()


def password_hash() -> str | None:
    """The configured password, preferring the environment variable."""
    if config.PASSWORD_ENV:
        stored = db.get_secret("_env_password_hash")
        marker = hashlib.sha256(config.PASSWORD_ENV.encode()).hexdigest()
        if not stored or db.get_secret("_env_password_marker") != marker:
            stored = hash_password(config.PASSWORD_ENV)
            db.put_secret("_env_password_hash", stored)
            db.put_secret("_env_password_marker", marker)
        return stored
    return db.get_secret("_password_hash")


def set_password(password: str) -> None:
    db.put_secret("_password_hash", hash_password(password))


def configured() -> bool:
    return config.AUTH_DISABLED or password_hash() is not None


def make_token(username: str) -> str:
    expires = int(time.time()) + SESSION_DAYS * 86400
    payload = f"{username}:{expires}"
    signature = hmac.new(secret(), payload.encode(), hashlib.sha256).hexdigest()
    return base64.urlsafe_b64encode(f"{payload}:{signature}".encode()).decode()


def read_token(token: str | None) -> str | None:
    if config.AUTH_DISABLED:
        return config.USERNAME
    if not token:
        return None
    try:
        username, expires, signature = base64.urlsafe_b64decode(token.encode()).decode().rsplit(":", 2)
    except Exception:  # noqa: BLE001
        return None
    expected = hmac.new(secret(), f"{username}:{expires}".encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(expected, signature):
        return None
    if int(expires) < time.time():
        return None
    return username


def check_login(username: str, password: str) -> bool:
    stored = password_hash()
    if not stored:
        return False
    return hmac.compare_digest(username, config.USERNAME) and verify_password(password, stored)


def status() -> dict[str, Any]:
    return {
        "enabled": not config.AUTH_DISABLED,
        "configured": configured(),
        "username": config.USERNAME,
        "password_from_env": bool(config.PASSWORD_ENV),
    }
