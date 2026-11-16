import base64
import hashlib
import os
import secrets

from cryptography.fernet import Fernet, InvalidToken

from ..config import DATA_DIR

SECRET_FILE = DATA_DIR / "app.secret"


def _secret_value():
    from_env = os.environ.get("APP_SECRET", "").strip()
    if from_env:
        return from_env
    try:
        return SECRET_FILE.read_text(encoding="utf-8").strip()
    except OSError:
        value = secrets.token_hex(32)
        SECRET_FILE.write_text(value, encoding="utf-8")
        try:
            os.chmod(SECRET_FILE, 0o600)
        except OSError:
            pass
        return value


def _fernet():
    key = base64.urlsafe_b64encode(hashlib.sha256(_secret_value().encode("utf-8")).digest())
    return Fernet(key)


def encrypt(value):
    if not value:
        return ""
    return _fernet().encrypt(str(value).encode("utf-8")).decode("ascii")


def decrypt(value):
    if not value:
        return ""
    try:
        return _fernet().decrypt(str(value).encode("ascii")).decode("utf-8")
    except (InvalidToken, ValueError):
        return ""
