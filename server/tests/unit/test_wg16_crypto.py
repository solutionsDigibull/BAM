"""#764 (ADR 0030): the connections crypto, with nothing but the module: round trip, AAD binding,
tamper, rotation and the key settings."""

import base64
import os
import uuid

import pytest
from pydantic import SecretStr
from sbam_api.connections.crypto import (
    KEY_BYTES,
    NONCE_BYTES,
    DecryptionError,
    KeyRing,
    Sealed,
    SecretsKeyError,
    _subkey,
)

TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()
NAME = "LEDGER_API_KEY"
VALUE = "sk_live_Zq7-correct horse battery staple"


def key() -> bytes:
    return os.urandom(KEY_BYTES)


def b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def ring(**keys: bytes) -> KeyRing:
    return KeyRing(keys, next(iter(keys)))


def seal(r: KeyRing, value: str = VALUE, name: str = NAME) -> Sealed:
    return r.seal(TENANT, PROJECT, name, value)


# --- round trip ---------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "value",
    [VALUE, "", "line one\nline two\n", "ключ-密钥-🔑", "x" * 8192, "-----BEGIN KEY-----\nAB\n"],
)
def test_wg16_a_value_round_trips(value: str) -> None:
    r = ring(k1=key())
    opened = r.open(TENANT, PROJECT, NAME, seal(r, value))
    assert isinstance(opened, SecretStr) and opened.get_secret_value() == value


def test_wg16_the_ciphertext_is_nonce_then_data_then_tag_and_never_the_plain_value() -> None:
    sealed = seal(ring(k1=key()))
    assert VALUE.encode() not in sealed.ciphertext
    assert len(sealed.ciphertext) == NONCE_BYTES + len(VALUE.encode()) + 16
    assert sealed.key_id == "k1"


def test_wg16_the_same_value_never_gives_the_same_ciphertext() -> None:
    r = ring(k1=key())
    assert seal(r).ciphertext != seal(r).ciphertext  # a fresh nonce each time


def test_wg16_a_str_and_a_secretstr_seal_alike() -> None:
    r = ring(k1=key())
    a = r.seal(TENANT, PROJECT, NAME, SecretStr(VALUE))
    assert r.open(TENANT, PROJECT, NAME, a).get_secret_value() == VALUE


# --- AAD: a ciphertext cannot be moved ----------------------------------------------------------


@pytest.mark.parametrize("what", ["tenant", "project", "name"])
def test_wg16_a_ciphertext_moved_to_another_tenant_project_or_name_does_not_decrypt(
    what: str,
) -> None:
    r = ring(k1=key())
    sealed = seal(r)
    tenant, project, name = TENANT, PROJECT, NAME
    if what == "tenant":
        tenant = uuid.uuid4()
    elif what == "project":
        project = uuid.uuid4()
    else:
        name = "OTHER_API_KEY"
    with pytest.raises(DecryptionError):
        r.open(tenant, project, name, sealed)


def test_wg16_each_tenant_has_its_own_subkey() -> None:
    master = key()
    other = uuid.uuid4()
    assert _subkey(master, TENANT) != _subkey(master, other)
    assert _subkey(master, TENANT) == _subkey(master, TENANT)  # deterministic
    assert _subkey(master, TENANT) != master and len(_subkey(master, TENANT)) == KEY_BYTES


# --- tamper -------------------------------------------------------------------------------------


@pytest.mark.parametrize("where", ["nonce", "data", "tag"])
def test_wg16_a_flipped_bit_fails_the_tag(where: str) -> None:
    r = ring(k1=key())
    sealed = seal(r)
    data = bytearray(sealed.ciphertext)
    index = {"nonce": 0, "data": NONCE_BYTES, "tag": len(data) - 1}[where]
    data[index] ^= 0x01
    with pytest.raises(DecryptionError):
        r.open(TENANT, PROJECT, NAME, Sealed(bytes(data), sealed.key_id))


@pytest.mark.parametrize("size", [0, 5, NONCE_BYTES + 15])
def test_wg16_a_short_ciphertext_is_refused(size: int) -> None:
    r = ring(k1=key())
    with pytest.raises(DecryptionError):
        r.open(TENANT, PROJECT, NAME, Sealed(b"\x00" * size, "k1"))


def test_wg16_another_key_does_not_decrypt() -> None:
    sealed = seal(ring(k1=key()))
    with pytest.raises(DecryptionError):
        ring(k1=key()).open(TENANT, PROJECT, NAME, sealed)


def test_wg16_a_decryption_error_names_the_secret_and_the_key_never_the_value() -> None:
    r = ring(k1=key())
    sealed = seal(r)
    with pytest.raises(DecryptionError) as caught:
        r.open(TENANT, PROJECT, "OTHER", sealed)
    text = str(caught.value) + repr(caught.value)
    assert "OTHER" in text and "k1" in text and VALUE not in text
    assert caught.value.__cause__ is None  # the tag error is not chained: nothing leaks through it


# --- rotation -----------------------------------------------------------------------------------


def test_wg16_a_value_made_under_an_old_key_still_decrypts_and_new_ones_use_the_active_key() -> (
    None
):
    old, new = key(), key()
    before = KeyRing({"k1": old}, "k1")
    sealed_old = seal(before)
    after = KeyRing({"k1": old, "k2": new}, "k2")
    assert sealed_old.key_id == "k1"
    assert after.open(TENANT, PROJECT, NAME, sealed_old).get_secret_value() == VALUE
    fresh = seal(after)
    assert fresh.key_id == "k2" == after.active_id
    assert after.open(TENANT, PROJECT, NAME, fresh).get_secret_value() == VALUE
    # re-encrypting (what rekey does) moves the row to the active key
    moved = after.seal(TENANT, PROJECT, NAME, after.open(TENANT, PROJECT, NAME, sealed_old))
    assert moved.key_id == "k2"


def test_wg16_a_removed_key_leaves_its_rows_unreadable() -> None:
    old, new = key(), key()
    sealed = seal(KeyRing({"k1": old}, "k1"))
    only_new = KeyRing({"k2": new}, "k2")
    with pytest.raises(DecryptionError):
        only_new.open(TENANT, PROJECT, NAME, sealed)


# --- the key settings ---------------------------------------------------------------------------

ONE, TWO = key(), key()
SPEC = f"k1:{b64(ONE)},*k2:{b64(TWO)}"  # `*` marks the active key


def test_wg16_a_valid_spec_parses_and_the_repr_shows_no_key() -> None:
    parsed = KeyRing.parse(SPEC, allow_placeholder=False)
    assert parsed.key_ids == ("k1", "k2") and parsed.active_id == "k2"
    assert b64(ONE) not in repr(parsed) and b64(TWO) not in repr(parsed)


@pytest.mark.parametrize(
    "spec",
    [
        "",
        "k1",
        "k1:",
        ":abc",
        "k1:!!!notbase64!!!",
        f"k1:{b64(b'short')}",
        f"k1:{b64(os.urandom(33))}",
        f"*k1:{b64(ONE)},k1:{b64(TWO)}",
        f"bad id:{b64(ONE)}",
        f"{'x' * 33}:{b64(ONE)}",
        f"k1:{b64(ONE)},",
    ],
)
def test_wg16_a_malformed_spec_is_refused_without_echoing_key_material(spec: str) -> None:
    with pytest.raises(SecretsKeyError) as caught:
        KeyRing.parse(spec, allow_placeholder=False)
    for secret in (b64(ONE), b64(TWO)):
        assert secret not in str(caught.value)


@pytest.mark.parametrize("spec", [f"k1:{b64(ONE)},k2:{b64(TWO)}", f"*k1:{b64(ONE)},*k2:{b64(TWO)}"])
def test_wg16_exactly_one_key_must_be_marked_active(spec: str) -> None:
    with pytest.raises(SecretsKeyError, match="exactly one key active"):
        KeyRing.parse(spec, allow_placeholder=False)


def test_wg16_rotation_is_moving_the_mark_in_the_one_secret() -> None:
    before = KeyRing.parse(f"*k1:{b64(ONE)},k2:{b64(TWO)}", allow_placeholder=False)
    after = KeyRing.parse(f"k1:{b64(ONE)},*k2:{b64(TWO)}", allow_placeholder=False)
    assert (before.active_id, after.active_id) == ("k1", "k2")
    sealed = before.seal(TENANT, PROJECT, NAME, "v")
    assert after.open(TENANT, PROJECT, NAME, sealed).get_secret_value() == "v"


DEV = b64(b"change-me-dev-only-secrets-key!!")


def test_wg16_the_development_placeholder_key_is_refused_unless_development() -> None:
    with pytest.raises(SecretsKeyError, match="development placeholder"):
        KeyRing.parse(f"*dev1:{DEV}", allow_placeholder=False)
    assert KeyRing.parse(f"*dev1:{DEV}", allow_placeholder=True).active_id == "dev1"
    assert len(base64.b64decode(DEV)) == KEY_BYTES  # what .env.example ships is a usable key
