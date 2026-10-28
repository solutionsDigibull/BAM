"""#378: a stored SOW reads back with its sections in TD §10.6 order.

Postgres JSONB keeps no key order, so a `SowDocument` read back from it was rejected by the model's
own order check and the SOW page answered 500. SQLite JSON keeps order, so the API tests never saw
it: these tests give the column type the order JSONB would."""

from pathlib import Path
from typing import Any

import pytest
from sbam_api.sow.models import SowJson, SowVersion
from sbam_contracts.sow import SECTIONS, SowDocument
from sqlalchemy import JSON
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.dialects.postgresql.psycopg import PGDialect_psycopg
from sqlalchemy.engine.default import DefaultDialect

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "sow" / "ms01_1_sow.json"
POSTGRES = PGDialect_psycopg()  # type: ignore[no-untyped-call]  # SQLAlchemy has no stub
DOC = SowDocument.model_validate_json(FIXTURE.read_text(encoding="utf-8")).model_dump(mode="json")


def as_jsonb_returns_it(doc: dict[str, Any]) -> dict[str, Any]:
    """Keys sorted by length, then bytes: how Postgres JSONB hands an object back."""
    return {k: doc[k] for k in sorted(doc, key=lambda k: (len(k), k.encode()))}


def load(value: Any) -> Any:
    return SowJson().process_result_value(value, POSTGRES)


def test_e6_378_jsonb_order_is_not_the_contract_order() -> None:
    assert tuple(as_jsonb_returns_it(DOC)) != SECTIONS  # the case that broke
    with pytest.raises(ValueError, match=r"TD §10\.6 order"):
        SowDocument.model_validate(as_jsonb_returns_it(DOC))


def test_e6_378_a_document_read_back_from_jsonb_validates() -> None:
    loaded = load(as_jsonb_returns_it(DOC))
    assert tuple(loaded) == SECTIONS
    assert SowDocument.model_validate(loaded).model_dump(mode="json") == DOC


def test_e6_378_the_content_is_unchanged_only_the_order() -> None:
    assert load(as_jsonb_returns_it(DOC)) == DOC


def test_e6_378_a_failed_version_stays_null() -> None:
    assert load(None) is None


def test_e6_378_something_that_is_not_a_whole_sow_is_returned_as_stored() -> None:
    partial: dict[str, Any] = {"scope": {}, "overview": {}}
    extra = {**DOC, "unknown_section": {}}
    assert load(partial) == partial and list(load(partial)) == ["scope", "overview"]
    assert load(extra) == extra
    assert load([1, 2]) == [1, 2]


def test_e6_378_the_column_is_jsonb_on_postgres_and_json_elsewhere() -> None:
    assert isinstance(SowJson().load_dialect_impl(POSTGRES), JSONB)
    elsewhere = SowJson().load_dialect_impl(DefaultDialect())
    assert isinstance(elsewhere, JSON) and not isinstance(elsewhere, JSONB)
    assert isinstance(SowVersion.__table__.c.structured.type, SowJson)
