"""Encrypting a stored value (ADR 0030, decisions 2 and 3): AES-256-GCM under a per-tenant subkey.

* The master keys come from ONE secret (`SECRETS_KEYS`; deployed it is the Docker secret file
  `sbam_secrets_keys`, read through `SECRETS_KEYS_FILE`): `key_id:base64(32 bytes)`, comma
  separated, the key that encrypts marked with a leading `*` (`*k2:...,k1:...`). One file, so
  rotation is one atomic edit. Never in code or the database.
* A subkey per tenant is derived with HKDF-SHA256 (`info` names the tenant), so a flaw in one
  tenant's data does not become a flaw in the master key.
* Each value gets a fresh random 96-bit nonce, stored in front: `nonce || ciphertext || tag`.
* The additional authenticated data is `tenant_id:project_id:name`, so a ciphertext copied to
  another project or another name fails its tag instead of decrypting.
* The ciphertext is stored with the `key_id` it was made under, so rows made under an older key
  still decrypt after a new key becomes the active one; `rekey` moves them over.

Nothing here logs, formats or raises with a value or a key: errors name the secret and the key id.
A value is a `SecretStr` going in and coming out.
"""

import base64
import binascii
import os
import re
import uuid
from collections.abc import Mapping
from dataclasses import dataclass
from typing import Final

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.hkdf import HKDF
from pydantic import SecretStr

KEY_BYTES: Final = 32
NONCE_BYTES: Final = 12
TAG_BYTES: Final = 16
KEY_ID: Final = re.compile(r"^[A-Za-z0-9_-]{1,32}$")
PLACEHOLDER: Final = b"change-me"  # the development key says so; a deployed process refuses it
INFO_PREFIX: Final = b"sbam-connections:v1:"


class SecretsKeyError(ValueError):
    """`SECRETS_KEYS` is unusable. Never holds key material."""


class DecryptionError(Exception):
    """A stored value cannot be decrypted. Names the secret and the key id, never a value."""

    def __init__(self, name: str, key_id: str) -> None:
        super().__init__(f"cannot decrypt the value of {name} (key {key_id})")
        self.name = name
        self.key_id = key_id


@dataclass(frozen=True)
class Sealed:
    """What the store keeps for a value."""

    ciphertext: bytes  # nonce || ciphertext || tag
    key_id: str


def _aad(tenant_id: uuid.UUID, project_id: uuid.UUID, name: str) -> bytes:
    return f"{tenant_id}:{project_id}:{name}".encode()


def _subkey(master: bytes, tenant_id: uuid.UUID) -> bytes:
    return HKDF(
        algorithm=hashes.SHA256(),
        length=KEY_BYTES,
        salt=None,
        info=INFO_PREFIX + str(tenant_id).encode(),
    ).derive(master)


class KeyRing:
    """The master keys by id and which one encrypts."""

    def __init__(self, keys: Mapping[str, bytes], active_id: str) -> None:
        if not keys:
            raise SecretsKeyError("SECRETS_KEYS holds no key")
        for key_id, key in keys.items():
            if not KEY_ID.fullmatch(key_id):
                raise SecretsKeyError("a key id must be 1 to 32 letters, digits, `_` or `-`")
            if len(key) != KEY_BYTES:
                raise SecretsKeyError(f"key {key_id} is not {KEY_BYTES} bytes")
        if active_id not in keys:
            raise SecretsKeyError(f"active key {active_id!r} is not in SECRETS_KEYS")
        self._keys = dict(keys)
        self.active_id = active_id

    def __repr__(self) -> str:  # never the keys
        return f"KeyRing(ids={sorted(self._keys)!r}, active={self.active_id!r})"

    @property
    def key_ids(self) -> tuple[str, ...]:
        return tuple(sorted(self._keys))

    @classmethod
    def parse(cls, spec: str, *, allow_placeholder: bool) -> "KeyRing":
        """`id:base64,*id:base64`: the `*` marks the one active key. Refuses a malformed entry, a
        key that is not 32 bytes, a repeated id, no or several active marks and (unless
        `allow_placeholder`, development only) a development key. Messages name an entry's
        position or a key id, never the key."""
        keys: dict[str, bytes] = {}
        active: list[str] = []
        parts = [p.strip() for p in spec.split(",")]
        for position, part in enumerate(parts, start=1):
            key_id, sep, encoded = part.partition(":")
            if key_id.startswith("*"):
                key_id = key_id[1:]
                active.append(key_id)
            if not sep or not key_id or not encoded:
                raise SecretsKeyError(f"SECRETS_KEYS entry {position} is not `id:base64key`")
            if not KEY_ID.fullmatch(key_id):
                raise SecretsKeyError(f"SECRETS_KEYS entry {position} has an invalid key id")
            if key_id in keys:
                raise SecretsKeyError(f"SECRETS_KEYS names key {key_id} twice")
            try:
                key = base64.b64decode(encoded, validate=True)
            except (binascii.Error, ValueError):
                raise SecretsKeyError(f"key {key_id} is not valid base64") from None
            if len(key) != KEY_BYTES:
                raise SecretsKeyError(f"key {key_id} is not {KEY_BYTES} bytes")
            if PLACEHOLDER in key and not allow_placeholder:
                raise SecretsKeyError(f"key {key_id} is the development placeholder")
            keys[key_id] = key
        if len(active) != 1:
            raise SecretsKeyError(
                "SECRETS_KEYS must mark exactly one key active with a leading `*`"
                f" (found {len(active)})"
            )
        return cls(keys, active[0])

    def seal(
        self, tenant_id: uuid.UUID, project_id: uuid.UUID, name: str, value: SecretStr | str
    ) -> Sealed:
        """Encrypt under the active key. A new nonce every time, so the same value never gives
        the same ciphertext."""
        text = value.get_secret_value() if isinstance(value, SecretStr) else value
        nonce = os.urandom(NONCE_BYTES)
        sub = _subkey(self._keys[self.active_id], tenant_id)
        sealed = AESGCM(sub).encrypt(nonce, text.encode("utf-8"), _aad(tenant_id, project_id, name))
        return Sealed(nonce + sealed, self.active_id)

    def open(
        self,
        tenant_id: uuid.UUID,
        project_id: uuid.UUID,
        name: str,
        sealed: Sealed,
    ) -> SecretStr:
        """Decrypt with the key the value was made under. Another tenant, project or name, an
        unknown key id, a short or altered ciphertext all raise `DecryptionError`."""
        master = self._keys.get(sealed.key_id)
        data = sealed.ciphertext
        if master is None or len(data) < NONCE_BYTES + TAG_BYTES:
            raise DecryptionError(name, sealed.key_id)
        try:
            plain = AESGCM(_subkey(master, tenant_id)).decrypt(
                data[:NONCE_BYTES], data[NONCE_BYTES:], _aad(tenant_id, project_id, name)
            )
            return SecretStr(plain.decode("utf-8"))
        except (InvalidTag, UnicodeDecodeError):
            raise DecryptionError(name, sealed.key_id) from None
