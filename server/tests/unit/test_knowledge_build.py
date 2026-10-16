"""E5.2 `build_knowledge`: the real adapter and DB guard over a fake Cognee, SQLite, storage."""

from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from cognee_fake import EmbeddingException
from knowledge_db import (
    TENANT,
    World,
    sources,
    versions,
)
from sbam_contracts.knowledge import KnowledgeCoverage
from sbam_workers.knowledge.build import (
    BUILD_KNOWLEDGE_ATTEMPTS,
    COGNEE_UNAVAILABLE,
    EMPTY_CORPUS,
    NOT_PARSED,
)
from sbam_workers.knowledge.cognee_adapter import Principal
from sbam_workers.pipeline.project_pipeline import PLAN
from temporalio.exceptions import ApplicationError


@pytest.fixture
def w(tmp_path: Path) -> Iterator[World]:
    yield World(tmp_path)


def coverage(version: Any) -> KnowledgeCoverage:
    return KnowledgeCoverage.model_validate(version.coverage)


def test_e5_2_t01_three_parsed_documents_make_version_1_ready(w: World) -> None:
    docs = w.docs("RFQ ZEBRA-7731", "BOM for PCBA", "SOP for soldering")
    kv_id = w.build(w.run_input())
    (v1,) = versions(w.sessions, w.project)
    assert (v1.id, v1.version, v1.state) == (kv_id, 1, "READY")
    assert v1.master_snapshot_id is not None  # the masters were pinned for this build
    assert v1.cognee_dataset == f"sbam_{TENANT}_{w.project}"
    rows = sources(w.sessions, kv_id)
    assert sorted(r["document_version_id"] for r in rows) == sorted(docs)
    assert {r["status"] for r in rows} == {"ADDED"}
    assert coverage(v1).entities == 3
    assert w.beats >= 1  # cognify ran with the heartbeat task alive


def test_e5_2_t02_one_failing_document_does_not_block_the_others(w: World) -> None:
    one, two, three = w.docs("RFQ", "broken", "SOP")
    w.cognee.refuse.add(str(two))
    kv_id = w.build(w.run_input())
    (v1,) = versions(w.sessions, w.project)
    assert v1.state == "READY"
    status = {
        r["document_version_id"]: (r["status"], r["reason"]) for r in sources(w.sessions, kv_id)
    }
    assert status[two] == ("FAILED", "ValueError")  # the type of Cognee's error, never its text
    assert status[one][0] == status[three][0] == "ADDED"


def test_e5_2_t03_cognee_down_retries_then_fails_on_the_last_attempt(w: World) -> None:
    w.docs("RFQ")
    w.cognee.fail = EmbeddingException("Bifrost unreachable")  # what Cognee raises (ADR 0001)
    inp = w.run_input()
    for attempt in range(1, BUILD_KNOWLEDGE_ATTEMPTS):
        with pytest.raises(ApplicationError) as err:
            w.build(inp, attempt)
        assert err.value.type == COGNEE_UNAVAILABLE and not err.value.non_retryable
        assert versions(w.sessions, w.project)[0].state == "BUILDING"  # retried, still building
    with pytest.raises(ApplicationError):
        w.build(inp, BUILD_KNOWLEDGE_ATTEMPTS)
    (v1,) = versions(w.sessions, w.project)
    assert (v1.state, v1.error_reason) == ("FAILED", COGNEE_UNAVAILABLE)


def test_e5_2_t04_no_entities_is_a_non_retryable_empty_corpus(w: World) -> None:
    w.docs("RFQ")
    w.cognee.entities = False
    with pytest.raises(ApplicationError) as err:
        w.build(w.run_input())
    assert err.value.type == EMPTY_CORPUS and err.value.non_retryable
    (v1,) = versions(w.sessions, w.project)
    assert (v1.state, v1.error_reason) == ("FAILED", EMPTY_CORPUS)


def test_e5_2_all_documents_failing_is_empty_corpus_with_reasons(w: World) -> None:
    docs = w.docs("a", "b")
    w.cognee.refuse.update(str(d) for d in docs)
    with pytest.raises(ApplicationError) as err:
        w.build(w.run_input())
    assert err.value.type == EMPTY_CORPUS
    (v1,) = versions(w.sessions, w.project)
    assert v1.state == "FAILED"
    assert {r["status"] for r in sources(w.sessions, v1.id)} == {"FAILED"}
    assert all(e.reason for e in coverage(v1).documents)


def test_e5_2_only_clean_parsed_documents_with_a_derivative_reach_cognee(w: World) -> None:
    good = w.docs("RFQ")
    unparsed = w.docs(None)  # PARSED but no doc.md yet
    infected = w.docs("EICAR", state="REJECTED", scan="INFECTED")
    parsing = w.docs("half", state="PARSING")
    kv_id = w.build(w.run_input())
    rows = {r["document_version_id"]: r for r in sources(w.sessions, kv_id)}
    assert rows[good[0]]["status"] == "ADDED"
    assert rows[unparsed[0]]["reason"] == rows[parsing[0]]["reason"] == NOT_PARSED
    assert infected[0] not in rows  # REJECTED is not an active document: never considered
    added = {name.split("_")[0] for ds in w.cognee.data.values() for name in ds}
    assert added == {str(good[0])}  # nothing else reached Cognee


def test_e5_2_t08_retry_after_sources_were_written_does_not_duplicate(w: World) -> None:
    w.docs("RFQ", "BOM")
    inp = w.run_input()
    w.cognee.fail = None
    first = w.build(inp)
    second = w.build(inp)  # the activity retried after it had finished
    assert first == second
    assert len(versions(w.sessions, w.project)) == 1
    assert len(sources(w.sessions, first)) == 2
    assert w.cognee.calls.count("cognify") == 1


def test_e5_2_t08_retry_mid_build_reuses_the_version_and_skips_added_items(w: World) -> None:
    w.docs("RFQ", "BOM")
    inp = w.run_input()
    w.cognee.entities = True

    original = w.cognee.cognify
    crashed = {"done": False}

    async def crash_once(who: Principal, dataset: str) -> None:
        if not crashed["done"]:
            crashed["done"] = True
            raise ConnectionError("worker lost Cognee")  # -> CogneeUnavailable, retryable
        await original(who, dataset)

    w.cognee.cognify = crash_once  # type: ignore[method-assign]
    with pytest.raises(ApplicationError):
        w.build(inp, 1)
    kv_id = w.build(inp, 2)
    assert len(versions(w.sessions, w.project)) == 1
    assert len(sources(w.sessions, kv_id)) == 2
    assert w.cognee.calls.count("add") == 2  # each document once; the retry skipped both


def test_e5_2_a_new_ready_version_supersedes_the_previous_one(w: World) -> None:
    w.docs("RFQ")
    v1 = w.build(w.run_input())
    v2 = w.build(w.run_input())
    states = {v.id: (v.version, v.state) for v in versions(w.sessions, w.project)}
    assert states == {v1: (1, "OUTDATED"), v2: (2, "READY")}


def test_e5_2_a_failed_build_leaves_the_ready_version_usable(w: World) -> None:
    w.docs("RFQ")
    v1 = w.build(w.run_input())
    w.cognee.entities = False
    with pytest.raises(ApplicationError):
        w.build(w.run_input())
    states = [(v.id == v1, v.state) for v in versions(w.sessions, w.project)]
    assert states == [(True, "READY"), (False, "FAILED")]


def test_e5_2_a_second_concurrent_build_waits(w: World) -> None:
    w.docs("RFQ")
    w.cognee.fail = EmbeddingException("down")
    with pytest.raises(ApplicationError):
        w.build(w.run_input(), 1)  # leaves its version BUILDING
    w.cognee.fail = None
    with pytest.raises(ApplicationError) as err:
        w.build(w.run_input())  # another run while that one is BUILDING
    assert err.value.type == "KnowledgeBuilding" and not err.value.non_retryable


def test_e5_2_attempts_match_the_pipeline_plan() -> None:
    assert PLAN["build_knowledge"][2] == BUILD_KNOWLEDGE_ATTEMPTS
