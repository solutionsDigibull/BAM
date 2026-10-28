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
    inp = w.run_input()
    kv_id = w.build(inp)
    assert versions(w.sessions, w.project)[0].state == "BUILDING"  # READY comes with the bundle
    w.bundle(inp, kv_id)
    (v1,) = versions(w.sessions, w.project)
    assert (v1.id, v1.version, v1.state) == (kv_id, 1, "READY")
    assert v1.master_snapshot_id is not None  # the masters were pinned for this build
    assert v1.cognee_dataset == f"sbam_{TENANT}_{w.project}"
    rows = sources(w.sessions, kv_id)
    assert sorted(r["document_version_id"] for r in rows) == sorted(docs)
    assert {r["status"] for r in rows} == {"ADDED"}
    assert coverage(v1).entities > 0
    assert w.beats >= 1  # cognify ran with the heartbeat task alive


def test_e5_2_t02_one_failing_document_does_not_block_the_others(w: World) -> None:
    one, two, three = w.docs("RFQ", "broken", "SOP")
    w.cognee.refuse.add(str(two))
    kv_id = w.build_all(w.run_input())
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
    v1 = w.build_all(w.run_input())
    v2 = w.build_all(w.run_input())
    states = {v.id: (v.version, v.state) for v in versions(w.sessions, w.project)}
    assert states == {v1: (1, "OUTDATED"), v2: (2, "READY")}


def test_e5_2_a_failed_build_leaves_the_ready_version_usable(w: World) -> None:
    w.docs("RFQ")
    v1 = w.build_all(w.run_input())
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


# --- E3.8b: parsed master documents are added as master context -----------------------------


def added_names(w: World) -> list[str]:
    return sorted(name for data in w.cognee.data.values() for name in data)


def test_e3_8b_a_parsed_master_document_is_added_to_cognee_as_master_context(w: World) -> None:
    (doc,) = w.docs("RFQ ZEBRA-7731")
    master = w.master_doc("Costing standard: scrap is 1.5 percent, APPROVED by Finance")
    kv_id = w.build_all(w.run_input())
    (v1,) = versions(w.sessions, w.project)
    assert v1.state == "READY" and coverage(v1).master_documents == 1
    assert any(name.startswith(str(master)) for name in added_names(w))
    # it is context, never an FDE source: not in the coverage documents nor in the sources
    assert [r["document_version_id"] for r in sources(w.sessions, kv_id)] == [doc]
    assert [e.document_version_id for e in coverage(v1).documents] == [doc]


def test_e3_8b_a_tenant_wide_master_document_reaches_the_projects_dataset(w: World) -> None:
    w.docs("RFQ ZEBRA-7731")
    master = w.master_doc("Company policy: quotes are valid for 14 days", project=None)
    w.build_all(w.run_input())
    assert any(name.startswith(str(master)) for name in added_names(w))


def test_e3_8b_the_projects_own_master_document_is_added_too(w: World) -> None:
    w.docs("RFQ ZEBRA-7731")
    mine = w.master_doc("Project rule: use the approved vendor list", project=w.project)
    w.build_all(w.run_input())
    assert any(name.startswith(str(mine)) for name in added_names(w))


@pytest.mark.parametrize(
    "fields",
    [{"parse": "PENDING"}, {"parse": "FAILED"}, {"scan": "INFECTED"}, {"scan": None}],
)
def test_e3_8b_a_master_document_that_is_not_clean_and_parsed_is_not_added(
    w: World, fields: dict[str, Any]
) -> None:
    w.docs("RFQ ZEBRA-7731")
    ignored = w.master_doc("not usable yet", **fields)
    w.build_all(w.run_input())
    assert not any(name.startswith(str(ignored)) for name in added_names(w))
    assert coverage(versions(w.sessions, w.project)[0]).master_documents == 0


def test_e3_8b_another_tenants_and_another_projects_master_documents_are_not_added(
    w: World,
) -> None:
    import uuid

    w.docs("RFQ ZEBRA-7731")
    stranger = w.master_doc("another tenant", tenant=uuid.uuid4())
    elsewhere = w.master_doc("another project", project=uuid.uuid4())
    w.build_all(w.run_input())
    names = added_names(w)
    assert not any(name.startswith(str(stranger)) for name in names)
    assert not any(name.startswith(str(elsewhere)) for name in names)


def test_e3_8b_cognee_refusing_a_master_document_does_not_stop_the_build(w: World) -> None:
    w.docs("RFQ ZEBRA-7731")
    refused = w.master_doc("Policy that Cognee cannot read")
    w.cognee.refuse.add(str(refused))
    w.build_all(w.run_input())
    (v1,) = versions(w.sessions, w.project)
    assert v1.state == "READY" and coverage(v1).master_documents == 0


def test_e3_8b_master_documents_alone_are_not_a_corpus(w: World) -> None:
    w.master_doc("A policy and nothing else")
    with pytest.raises(ApplicationError) as caught:
        w.build(w.run_input())
    assert caught.value.type == EMPTY_CORPUS  # only FDE documents are sources (C3)


def test_e3_8b_the_snapshot_pinned_for_the_build_lists_the_master_document(w: World) -> None:
    w.docs("RFQ ZEBRA-7731")
    master = w.master_doc("Costing standard", title="costing.pdf")
    w.build_all(w.run_input())
    from sbam_api.masters.models import MasterSnapshot
    from sqlalchemy import select

    with w.sessions() as s:
        [snap] = s.scalars(select(MasterSnapshot)).all()
        assert [d["document_id"] for d in snap.documents] == [str(master)]
        assert snap.documents[0]["title"] == "costing.pdf"
