import hashlib

from cryptography.fernet import Fernet, InvalidToken
from flask import current_app


def _cipher():
    key = current_app.config.get("IDENTITY_ENCRYPTION_KEY")

    if not key:
        raise RuntimeError("Push subscription encryption is not configured.")

    try:
        return Fernet(key.encode("ascii"))
    except (ValueError, TypeError, UnicodeEncodeError):
        raise RuntimeError(
            "Push subscription encryption configuration is invalid."
        ) from None


def encrypt_subscription_secret(value):
    if not isinstance(value, str) or not value:
        raise ValueError("A valid push subscription value is required.")

    return _cipher().encrypt(value.encode("utf-8")).decode("ascii")


def decrypt_subscription_secret(encrypted_value):
    if not isinstance(encrypted_value, str) or not encrypted_value:
        raise ValueError("Encrypted push subscription value is invalid.")

    try:
        decrypted = _cipher().decrypt(encrypted_value.encode("ascii"))
        return decrypted.decode("utf-8")
    except (InvalidToken, UnicodeError, ValueError):
        raise ValueError(
            "Push subscription value could not be decrypted."
        ) from None


def hash_push_endpoint(endpoint):
    if not isinstance(endpoint, str) or not endpoint:
        raise ValueError("A valid push endpoint is required.")

    return hashlib.sha256(endpoint.encode("utf-8")).hexdigest()
