"""#720: the FDE's goal is extra context for the Cognee knowledge build (one `goal.md` item, pruned
and replaced when the goal changes, never a document of the coverage) and for SOW generation (a
`goal` data block, its sha256 in the context manifest). No goal: nothing changes."""

import asyncio
import hashlib
from pathlib import Path

import pytest
from knowledge_db import World, versions
from llm_db import FakeBifrost, completion
from sbam_api.projects.models import Project
from sbam_contracts.knowledge import KnowledgeCoverage
from sbam_workers.knowledge.build import goal_markdown
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.sow.context import Bundle, build_context
from test_knowledge_build import keys_of
from test_sow_generate import Gen, fake_embed

GOAL = "Build an AI agent that automates quoting for sales managers at SMB manufacturers in India"
OTHER = "Build an AI agent that automates RFQ responses for procurement teams in Europe"


def set_goal(w: World, goal: str | None) -> None:
    with w.sessions() as s:
        project = s.get(Project, w.project)
        assert project is not None
        project.goal = goal
        s.commit()


def goal_keys(w: World) -> set[str]:
    """Dataset items owned by the project itself: only the goal is (item key `<source_id>_<sha>`)."""
    return {k for k in keys_of(w) if k.startswith(f"{w.project}_")}


def key_of(w: World, goal: str) -> str:
    return f"{w.project}_{hashlib.sha256(goal_markdown(goal)).hexdigest()}"


# --- the knowledge build ------------------------------------------------------------------------


def test_720_t10_the_goal_is_added_to_the_projects_dataset_as_context(tmp_path: Path) -> None:
    w = World(tmp_path)
    (doc,) = w.docs("RFQ ZEBRA-7731")
    set_goal(w, GOAL)
    w.build_all(w.run_input())
    (v1,) = versions(w.sessions, w.project)
    assert v1.state == "READY"
    assert goal_keys(w) == {key_of(w, GOAL)}
    coverage = KnowledgeCoverage.model_validate(v1.coverage)
    assert [e.document_version_id for e in coverage.documents] == [doc]  # never a source


def test_720_t11_no_goal_adds_nothing(tmp_path: Path) -> None:
    w = World(tmp_path)
    w.docs("RFQ ZEBRA-7731")
    w.build_all(w.run_input())
    assert goal_keys(w) == set()


def test_720_t12_a_changed_goal_replaces_the_old_one_on_the_next_build(tmp_path: Path) -> None:
    w = World(tmp_path)
    w.docs("RFQ ZEBRA-7731")
    set_goal(w, GOAL)
    w.build_all(w.run_input())
    set_goal(w, OTHER)
    w.build_all(w.run_input())
    assert goal_keys(w) == {key_of(w, OTHER)}  # the old goal was pruned
    set_goal(w, None)
    w.build_all(w.run_input())
    assert goal_keys(w) == set()


def test_720_t13_cognee_refusing_the_goal_does_not_fail_the_build(tmp_path: Path) -> None:
    w = World(tmp_path)
    w.docs("RFQ ZEBRA-7731")
    set_goal(w, GOAL)
    w.cognee.refuse.add(str(w.project))
    w.build_all(w.run_input())
    (v1,) = versions(w.sessions, w.project)
    assert v1.state == "READY" and goal_keys(w) == set()


# --- SOW generation -----------------------------------------------------------------------------


@pytest.fixture
def g(tmp_path: Path) -> Gen:
    return Gen(World(tmp_path))


def test_720_t20_the_sow_is_generated_with_the_goal_as_a_data_block(g: Gen) -> None:
    set_goal(g.w, GOAL)  # read when the SOW is generated, not when the knowledge was built
    assert g.bundle().goal == GOAL
    bifrost = FakeBifrost([completion(g.answer())])
    sc = g.scope()
    g.run(bifrost, sc=sc)
    assert GOAL in bifrost.requests[0]["messages"][1]["content"]
    [row] = g.calls(sc)
    assert row.context_manifest["_extra"]["goal_sha256"] == hashlib.sha256(GOAL.encode()).hexdigest()


def test_720_t21_without_a_goal_the_sow_context_is_unchanged(g: Gen) -> None:
    assert g.bundle().goal is None
    bifrost = FakeBifrost([completion(g.answer())])
    sc = g.scope()
    g.run(bifrost, sc=sc)
    [row] = g.calls(sc)
    assert "goal_sha256" not in row.context_manifest["_extra"]


def test_720_t22_the_goal_block_is_context_never_a_citable_source() -> None:
    context = asyncio.run(
        build_context(Bundle([], [], [], "", GOAL), fake_embed, budget_tokens=100_000)
    )
    assert context.blocks["goal"] == GOAL
    assert context.citable == {}  # nothing to cite: the goal is not a document
    plain = asyncio.run(build_context(Bundle([], [], [], ""), fake_embed, budget_tokens=100_000))
    assert "goal" not in plain.blocks and "goal_sha256" not in plain.manifest


def test_720_t23_sow_generate_1_2_0_is_newest_and_documents_the_goal_block() -> None:
    registry = PromptRegistry()
    newest = registry.get("sow-generate")
    assert newest.version == "1.2.0"
    assert "- `goal`" in newest.text and "never cite it" in newest.text
    assert registry.get("sow-generate", "1.1.0").sha256 == (
        "361e9e19bad8b319c1f935f14c0d1bc6353da9c4b37ca37aa507653b08f0a50f"
    )  # a released version is never edited
