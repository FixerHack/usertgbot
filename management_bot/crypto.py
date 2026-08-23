"""Symmetric encryption for Telethon session strings before they hit the DB.

Uses ENCRYPTION_KEY (Fernet key) — sessions are never stored in plaintext.
"""

from cryptography.fernet import Fernet

from management_bot.config import settings

_fernet = Fernet(settings.encryption_key.encode())


def encrypt_session(session_string: str) -> bytes:
    return _fernet.encrypt(session_string.encode())


def decrypt_session(token: bytes) -> str:
    return _fernet.decrypt(token).decode()
