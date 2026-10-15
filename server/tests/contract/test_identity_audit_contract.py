"""E2.8 contracts: the invite email is RFC 5322 (no DNS lookup), and `AuditEntry` round-trips."""

import uuid
from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sbam_contracts.audit import AuditEntry
from sbam_contracts.identity import UserInvite


def _invite(email: str) -> UserInvite:
    return UserInvite(email=email, roles=["sbam-fde"])


@pytest.mark.parametrize(
    "email",
    [
        "a@b",  # no dot in the domain
        '"x"@',  # quoted local part, no domain
        '"quoted"@acme.com',  # quoted local parts are refused
        "x@y..z",
        "a b@acme.com",
        "@acme.com",
        "n@localhost",
        f"{'a' * 65}@acme.com",  # local part over 64 (RFC 5321)
        f"a@{'b' * 250}.com",  # over 254 in total
    ],
)
def test_e2_8_t05_invite_email_must_be_rfc5322(email: str) -> None:
    with pytest.raises(ValidationError) as err:
        _invite(email)
    assert [e["loc"] for e in err.value.errors()] == [("email",)]


@pytest.mark.parametrize(
    ("raw", "stored"),
    [
        ("  New@Acme.com ", "new@acme.com"),  # trimmed and lowercased
        ("fde3@acme.com", "fde3@acme.com"),
        ("admin-a@sbam.test", "admin-a@sbam.test"),  # dev seed domains stay valid
        ("pat+invite@acme.example", "pat+invite@acme.example"),
    ],
)
def test_e2_8_valid_invite_emails_are_normalised(raw: str, stored: str) -> None:
    assert _invite(raw).email == stored


def test_e2_8_audit_entry_round_trips() -> None:
    entry = AuditEntry(
        id=uuid.uuid4(),
        action="USER_INVITED",
        entity_type="user",
        entity_id=str(uuid.uuid4()),
        actor_id=str(uuid.uuid4()),
        before=None,
        after={"email": "fde3@acme.com", "roles": ["sbam-fde"]},
        correlation_id="c-1",
        created_at=datetime(2026, 10, 15, 8, 0, tzinfo=UTC),
    )
    assert AuditEntry.model_validate_json(entry.model_dump_json()) == entry
