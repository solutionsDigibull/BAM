"""E8.8-T04 (backend side): the shared gate-expression corpus matches `gate_expr.parse`.

`tests/fixtures/isatvon/gate_corpus.json` is read by this test and by the client's TS parser test
(`isatvon/gate-expr.test.ts`): both parsers must accept / reject every entry the same way, with the
same code and message. A grammar change here fails this test until the corpus (and the TS port) is
updated with it.
"""

import json
from typing import Any

import pytest
from fixtures import fixture_path
from sbam_contracts import gate_expr

CORPUS: list[dict[str, Any]] = json.loads(
    fixture_path("isatvon", "gate_corpus.json").read_text(encoding="utf-8")
)


def test_e8_8_t04_corpus_is_large_and_covers_every_outcome() -> None:
    assert len(CORPUS) >= 100
    codes = {entry.get("code", "OK") for entry in CORPUS}
    assert codes >= {
        "OK",
        "SYNTAX",
        "CALL_NOT_ALLOWED",
        "ATTRIBUTE_NOT_ALLOWED",
        "SUBSCRIPT_NOT_ALLOWED",
        "TOO_DEEP",
        "TOO_LONG",
    }


@pytest.mark.parametrize("entry", CORPUS, ids=lambda e: repr(e["expr"])[:60])
def test_e8_8_t04_backend_parser_matches_the_corpus(entry: dict[str, Any]) -> None:
    try:
        tree = gate_expr.parse(entry["expr"])
    except gate_expr.ExpressionError as exc:
        assert not entry["ok"]
        assert (exc.code.value, exc.message) == (entry["code"], entry["message"])
    else:
        assert entry["ok"]
        assert sorted(gate_expr.identifiers(tree)) == entry["identifiers"]
