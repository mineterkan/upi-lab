"""Customer signatures with Ed25519.

The customer's device holds a private key and signs every payment it approves.
The switch only knows the public key, which can check a signature but cannot
make one. So a valid signature proves the customer's device approved exactly
this payment.
"""

import base64
import re

from cryptography.exceptions import InvalidSignature
from cryptography.hazmat.primitives.asymmetric.ed25519 import (
    Ed25519PrivateKey,
    Ed25519PublicKey,
)
from cryptography.hazmat.primitives.serialization import Encoding, PublicFormat

FIELDS = ("request_id", "payer", "payee", "amount_minor", "idempotency_key")
SAFE_VALUE = re.compile(r"^[A-Za-z0-9@._-]+$")


def instruction_message(instruction: dict) -> bytes:
    """The exact bytes that get signed. Same input, same bytes, every time."""
    lines = ["UPILAB-PAY-v1"]
    for field in FIELDS:
        value = str(instruction[field])
        if not SAFE_VALUE.match(value):
            raise ValueError(f"unsafe characters in {field}")
        lines.append(f"{field}={value}")
    return "\n".join(lines).encode()


def b64(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode()


def unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text)


def verify(public_key: str, message: bytes, signature: str) -> bool:
    try:
        Ed25519PublicKey.from_public_bytes(unb64(public_key)).verify(
            unb64(signature), message
        )
        return True
    except (InvalidSignature, ValueError):
        return False


class DeviceKey:
    """Plays the customer's phone. In the real app this lives in the browser."""

    def __init__(self) -> None:
        self._private = Ed25519PrivateKey.generate()

    @property
    def public_key(self) -> str:
        return b64(
            self._private.public_key().public_bytes(Encoding.Raw, PublicFormat.Raw)
        )

    def sign(self, message: bytes) -> str:
        return b64(self._private.sign(message))
