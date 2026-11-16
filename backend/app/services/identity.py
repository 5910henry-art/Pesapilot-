import re

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _cipher():
    key = current_app.config.get("IDENTITY_ENCRYPTION_KEY")

    if not key:
        raise RuntimeError("Identity encryption is not configured.")

    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        raise RuntimeError("Identity encryption configuration is invalid.") from None


def normalize_national_id(value):
    if not isinstance(value, str):
        raise ValueError("A valid National ID number is required.")

    normalized = re.sub(r"[\s-]", "", value)

    if not re.fullmatch(r"[A-Za-z0-9]{5,30}", normalized):
        raise ValueError("National ID number format is invalid.")

    return normalized.upper()


def encrypt_national_id(value):
    normalized = normalize_national_id(value)
    return _cipher().encrypt(normalized.encode("utf-8")).decode("ascii")


def decrypt_national_id(encrypted_value):
    if not isinstance(encrypted_value, str) or not encrypted_value:
        raise ValueError("Encrypted identity value is invalid.")

    try:
        decrypted = _cipher().decrypt(encrypted_value.encode("ascii"))
        return decrypted.decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        raise ValueError("Identity value could not be decrypted.") from None


def mask_national_id(value):
    normalized = normalize_national_id(value)

    if len(normalized) <= 4:
        return "*" * len(normalized)

    return "*" * (len(normalized) - 4) + normalized[-4:]
