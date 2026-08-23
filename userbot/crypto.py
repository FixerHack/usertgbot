"""Decrypts the Telethon session strings that management_bot encrypted.

Uses the same ENCRYPTION_KEY (Fernet) — see management_bot.crypto for the
write side. Kept per-service so userbot doesn't import management_bot.
"""

from cryptography.fernet import Fernet

from userbot.config import settings

_fernet = Fernet(settings.encryption_key.encode())


def encrypt_session(session_string: str) -> bytes:
    return _fernet.encrypt(session_string.encode())


def decrypt_session(token: bytes) -> str:
    return _fernet.decrypt(token).decode()
