"""Persistent Ed25519 identity used to sign federation attestations."""
import base64
import hashlib

from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey, Ed25519PublicKey

from .federation_store import FederationStore
from .security_controls import protect_value, unprotect_value

PRIVATE_KEY_META = "federation_identity_private_v1"
PUBLIC_KEY_META = "federation_identity_public_v1"


def _b64(data):
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")


def _unb64(value):
    value = str(value or "")
    return base64.urlsafe_b64decode(value + "=" * (-len(value) % 4))


class FederationIdentity:
    def __init__(self, root):
        self.store = FederationStore(root)

    def _read_meta(self, key):
        with self.store._db() as db:
            row = db.execute("SELECT value FROM federation_meta WHERE key=?", (key,)).fetchone()
        return str(row["value"]) if row else ""

    def _write_meta(self, key, value):
        with self.store._db() as db:
            db.execute(
                "INSERT OR REPLACE INTO federation_meta(key,value) VALUES(?,?)",
                (key, str(value)),
            )

    def _ensure(self):
        private_enc = self._read_meta(PRIVATE_KEY_META)
        public_b64 = self._read_meta(PUBLIC_KEY_META)
        if private_enc and public_b64:
            return private_enc, public_b64
        private = Ed25519PrivateKey.generate()
        private_raw = private.private_bytes(
            serialization.Encoding.Raw,
            serialization.PrivateFormat.Raw,
            serialization.NoEncryption(),
        )
        public_raw = private.public_key().public_bytes(
            serialization.Encoding.Raw,
            serialization.PublicFormat.Raw,
        )
        private_enc = protect_value(_b64(private_raw), "federation-identity-private")
        public_b64 = _b64(public_raw)
        self._write_meta(PRIVATE_KEY_META, private_enc)
        self._write_meta(PUBLIC_KEY_META, public_b64)
        return private_enc, public_b64

    def public_identity(self):
        _private, public_b64 = self._ensure()
        public_raw = _unb64(public_b64)
        return {
            "public_key": public_b64,
            "fingerprint": hashlib.sha256(public_raw).hexdigest(),
        }

    def sign(self, payload):
        private_enc, _public = self._ensure()
        private_raw = _unb64(unprotect_value(private_enc, "federation-identity-private"))
        private = Ed25519PrivateKey.from_private_bytes(private_raw)
        return _b64(private.sign(bytes(payload)))


def verify(public_key, payload, signature):
    try:
        key = Ed25519PublicKey.from_public_bytes(_unb64(public_key))
        key.verify(_unb64(signature), bytes(payload))
        return True
    except (TypeError, ValueError):
        return False
