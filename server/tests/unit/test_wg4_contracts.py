"""WG-4 (#752): the contracts of ADR 0028 (source_repos, the Workflow server entry, the Bifrost
gateway value, the repository decision and the token budget).

The point of the first tests is that nothing existing moves: the verbatim ISA14 sample still
validates and a manifest built before this change keeps the hash it was frozen with.
"""

import copy
import json
import re
from datetime import UTC, datetime
from typing import Any

import pytest
from fixtures import fixture_path
from pydantic import ValidationError
from sbam_contracts.budget import (
    DEFAULT_KNOWLEDGE_TOKENS,
    DEFAULT_SUB_SKILL_TOKENS,
    DEFAULT_WORKFLOW_TOKENS,
    BudgetCaps,
    BudgetLevel,
    BudgetScope,
    BudgetState,
)
from sbam_contracts.isatvon14 import SubSkill
from sbam_contracts.manifest import (
    BIFROST_GATEWAY,
    ISA14_SRIM_DEFAULTS,
    SBAM_SRIM_DEFAULTS,
    TEMPORAL_ROUTER,
    Isa14Manifest,
    McpServerEntry,
    SkillManifest,
    WorkflowManifest,
)
from sbam_contracts.repo_decision import RepoDecision
from test_manifest import isa14_sample, manifest, workflow

REPO: dict[str, Any] = {
    "url": "https://github.com/example-org/pricing-kit",
    "ref": "v1.4.2",
    "licence": "MIT",
    "reason": "It already prices a quote from a part list.",
}


def with_repos(repos: list[dict[str, Any]], data: dict[str, Any] | None = None) -> dict[str, Any]:
    out = copy.deepcopy(data if data is not None else manifest())
    out["isatvon_definition"]["t_tech_stack"]["source_repos"] = repos
    return out


# --- nothing that exists moves --------------------------------------------------------------------


def test_wg4_t01_the_isa14_sample_still_validates_and_round_trips() -> None:
    parsed = Isa14Manifest.model_validate(isa14_sample())
    assert parsed.dump() == isa14_sample()


def test_wg4_t02_an_old_frozen_manifest_keeps_its_hash() -> None:
    data = manifest()
    parsed = SkillManifest.model_validate(data)
    assert parsed.computed_hash() == data["governance"]["deterministic_hash"]
    assert "source_repos" not in json.dumps(parsed.dump())  # unset keys stay out of the hashed form


def test_wg4_t02_an_old_workflow_manifest_keeps_its_hash() -> None:
    sealed = WorkflowManifest.sealed(workflow())
    assert sealed.computed_hash() == sealed.integrity.content_sha256
    assert "mcp_server" not in sealed.dump()


# --- source_repos ---------------------------------------------------------------------------------


def test_wg4_t03_an_unknown_key_in_source_repos_is_rejected() -> None:
    with pytest.raises(ValidationError):
        SkillManifest.sealed(with_repos([{**REPO, "branch": "main"}]))


def test_wg4_t04_a_licence_outside_the_allowlist_is_accepted_as_data() -> None:
    sealed = SkillManifest.sealed(with_repos([{**REPO, "licence": "GPL-3.0-only"}]))
    repos = sealed.isatvon_definition.t_tech_stack.source_repos
    assert repos is not None and repos[0].licence == "GPL-3.0-only"  # the fetcher enforces policy


def test_wg4_source_repos_are_covered_by_the_hash() -> None:
    plain = SkillManifest.sealed(manifest())
    with_one = SkillManifest.sealed(with_repos([REPO]))
    other_commit = SkillManifest.sealed(with_repos([{**REPO, "commit": "a" * 40}]))
    hashes = {m.governance.deterministic_hash for m in (plain, with_one, other_commit)}
    assert len(hashes) == 3


def test_wg4_the_flat_isa14_shape_carries_source_repos_too() -> None:
    sample = with_repos(
        [REPO, {**REPO, "url": "https://gitlab.com/group/sub/project"}], isa14_sample()
    )
    assert Isa14Manifest.model_validate(sample).dump() == sample


def test_wg4_the_structured_isatvon_carries_source_repos() -> None:
    raw = json.loads(fixture_path("isa14", "ms01_1_structured.json").read_text(encoding="utf-8"))
    raw["isatvon_definition"]["t_tech_stack"]["source_repos"] = [REPO]
    repos = SubSkill.model_validate(raw).isatvon_definition.t_tech_stack.source_repos
    assert repos is not None and repos[0].url == REPO["url"]


@pytest.mark.parametrize(
    "url",
    [
        "http://github.com/example-org/pricing-kit",  # not https
        "https://user:token@github.com/example-org/pricing-kit",  # credentials in the URL
        "https://github.com/example-org/pricing-kit?token=abc",  # a query
        "https://github.com/example-org/pricing-kit#readme",  # a fragment
        "ssh://git@github.com/example-org/pricing-kit",
        "git@github.com:example-org/pricing-kit.git",
        "https://github.com/example org/pricing-kit",
    ],
)
def test_wg4_a_repository_url_is_https_with_no_credentials_query_or_fragment(url: str) -> None:
    with pytest.raises(ValidationError):
        SkillManifest.sealed(with_repos([{**REPO, "url": url}]))


@pytest.mark.parametrize("commit", ["abc1234", "A" * 40, "g" * 40, "a" * 39, "a" * 41])
def test_wg4_a_commit_is_a_full_lower_case_hex_object_name(commit: str) -> None:
    with pytest.raises(ValidationError):
        SkillManifest.sealed(with_repos([{**REPO, "commit": commit}]))


def test_wg4_a_sha256_commit_is_accepted_and_the_list_is_bounded() -> None:
    SkillManifest.sealed(with_repos([{**REPO, "commit": "a" * 64}]))
    with pytest.raises(ValidationError):
        SkillManifest.sealed(
            with_repos([{**REPO, "url": f"https://github.com/example-org/r{i}"} for i in range(11)])
        )


# --- the gateway value ----------------------------------------------------------------------------


def test_wg4_isa14_defaults_untouched_sbam_differs_in_router_and_gateway() -> None:
    assert ISA14_SRIM_DEFAULTS.gateway.route_via == "MCP_Jungle_Gateway_Reverse_Proxy"
    # the legacy sample, verbatim: frozen manifests keep it
    assert ISA14_SRIM_DEFAULTS.orchestration.router == "Dify_Dynamic_Master_Router"
    assert SBAM_SRIM_DEFAULTS.gateway.route_via == BIFROST_GATEWAY == "Bifrost_MCP_Gateway"
    assert SBAM_SRIM_DEFAULTS.orchestration.router == TEMPORAL_ROUTER
    assert TEMPORAL_ROUTER == "Temporal_Workflow_Orchestrator"
    old = ISA14_SRIM_DEFAULTS.model_dump(mode="json")
    new = SBAM_SRIM_DEFAULTS.model_dump(mode="json")
    assert {k: v for k, v in new.items() if k not in ("gateway", "orchestration")} == {
        k: v for k, v in old.items() if k not in ("gateway", "orchestration")
    }
    assert new["orchestration"]["trigger"] == old["orchestration"]["trigger"]  # only router moves


def test_adr0031_no_default_names_dify() -> None:
    assert "dify" not in SBAM_SRIM_DEFAULTS.model_dump_json().lower()


def test_adr0031_the_isa14_sample_with_dify_still_validates() -> None:
    sample = isa14_sample()
    assert Isa14Manifest.model_validate(sample)  # legacy and frozen manifests keep validating


def test_wg4_a_manifest_with_the_bifrost_gateway_validates() -> None:
    data = copy.deepcopy(manifest())
    table = data["srim_runtime_definition"]["persistence"]["table"]
    data["srim_runtime_definition"] = SBAM_SRIM_DEFAULTS.runtime_definition(table).dump()
    assert SkillManifest.sealed(data).srim_runtime_definition.gateway.route_via == BIFROST_GATEWAY


# --- the Workflow's one MCP server ----------------------------------------------------------------


def tool(skill: str, role: str) -> dict[str, Any]:
    found = re.match(r"MS(\d{2})\.(\d+)_", skill)
    assert found is not None
    return {
        "name": f"MS{found[1]}_{found[2]}__{role}",
        "skill_id": skill,
        "role": role,
        "input_schema": {"type": "object"},
        "output_schema": {"type": "object"},
    }


def server(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "name": "wf_northwind_rfq_v1_0",
        "version": "1.0.0",
        "image_digest": "sha256:" + "a" * 64,
        "tools": [
            tool("MS01.1_Capture_RFQ", "action"),
            tool("MS01.1_Capture_RFQ", "check"),
            tool("MS01.2_Check_Country", "action"),
            tool("MS01.2_Check_Country", "check"),
        ],
    }
    return {**base, **over}


def test_wg4_a_workflow_server_entry_validates_and_is_covered_by_the_hash() -> None:
    McpServerEntry.model_validate(server())
    plain = WorkflowManifest.sealed(workflow())
    one = WorkflowManifest.sealed({**workflow(), "mcp_server": server()})
    other = WorkflowManifest.sealed(
        {**workflow(), "mcp_server": server(image_digest="sha256:" + "b" * 64)}
    )
    assert (
        len(
            {
                plain.integrity.content_sha256,
                one.integrity.content_sha256,
                other.integrity.content_sha256,
            }
        )
        == 3
    )
    assert one.dump()["mcp_server"]["name"] == "wf_northwind_rfq_v1_0"


@pytest.mark.parametrize(
    "name", ["wf-northwind", "Wf_northwind", "wf_", "northwind", "wf_a b", "wf__x"]
)
def test_wg4_a_server_name_has_no_hyphen_space_or_capital(name: str) -> None:
    with pytest.raises(ValidationError):
        McpServerEntry.model_validate(server(name=name))


@pytest.mark.parametrize("version", ["1.1.0", "1.0", "v1.0.0", "1.0.0-rc1"])
def test_wg4_a_server_version_is_major_0_patch(version: str) -> None:
    with pytest.raises(ValidationError):
        McpServerEntry.model_validate(server(version=version))


def test_wg4_every_skill_needs_both_an_action_and_a_check_tool() -> None:
    only_action = [tool("MS01.1_Capture_RFQ", "action"), tool("MS01.2_Check_Country", "action")]
    with pytest.raises(ValidationError, match="both an action and a check"):
        McpServerEntry.model_validate(server(tools=only_action))


def test_wg4_tool_names_are_unique_and_follow_their_skill_and_role() -> None:
    twice = [tool("MS01.1_Capture_RFQ", "action")] * 2
    with pytest.raises(ValidationError):
        McpServerEntry.model_validate(server(tools=twice))
    wrong_role = {**tool("MS01.1_Capture_RFQ", "check"), "name": "MS01_1__action"}
    with pytest.raises(ValidationError, match="is not the check tool"):
        McpServerEntry.model_validate(
            server(tools=[tool("MS01.1_Capture_RFQ", "action"), wrong_role])
        )
    wrong_skill = {**tool("MS01.1_Capture_RFQ", "check"), "name": "MS02_1__check"}
    with pytest.raises(ValidationError):
        McpServerEntry.model_validate(
            server(tools=[tool("MS01.1_Capture_RFQ", "action"), wrong_skill])
        )


def test_wg4_a_tool_name_has_no_dot() -> None:
    dotted = {**tool("MS01.1_Capture_RFQ", "action"), "name": "MS01.1__action"}
    with pytest.raises(ValidationError):
        McpServerEntry.model_validate(server(tools=[dotted, tool("MS01.1_Capture_RFQ", "check")]))


def test_wg4_registration_leaves_the_registry_id_empty_until_it_happens() -> None:
    assert McpServerEntry.model_validate(server()).registry_id is None
    assert McpServerEntry.model_validate(server(registry_id="reg-1")).registry_id == "reg-1"


# --- the repository decision ----------------------------------------------------------------------


def decision(**over: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "sub_skill_id": "MS01.1_Capture_RFQ",
        "outcome": "REPO",
        "candidates": [
            {
                "url": REPO["url"],
                "source": "ADMIN_AGENTS",
                "score": 0.82,
                "licence": "MIT",
                "reasons": ["covers the inputs"],
            },
            {
                "url": "https://github.com/example-org/other-kit",
                "source": "ADMIN_TOOLING",
                "score": 0.41,
            },
        ],
        "chosen": {"url": REPO["url"]},
        "decided_by": "AUTO",
        "decided_at": datetime(2026, 11, 16, 9, 0, tzinfo=UTC).isoformat(),
        "reason": "One clear winner by score.",
    }
    return {**base, **over}


def test_wg4_a_repo_decision_validates() -> None:
    parsed = RepoDecision.model_validate_json(json.dumps(decision()))
    assert parsed.chosen is not None and parsed.chosen.url == REPO["url"]


def test_wg4_a_decision_without_a_repository_is_generated_and_has_no_chosen() -> None:
    generated = decision(
        outcome="GENERATED", chosen=None, candidates=[], decided_by="FDE", actor_id="fde-1"
    )
    RepoDecision.model_validate_json(json.dumps(generated))
    with pytest.raises(ValidationError, match="no chosen"):
        RepoDecision.model_validate_json(json.dumps({**generated, "chosen": {"url": REPO["url"]}}))


@pytest.mark.parametrize(
    "over",
    [
        {"chosen": None},  # REPO needs the chosen repository
        {"chosen": {"url": "https://github.com/example-org/not-a-candidate"}},
        {"decided_by": "FDE"},  # the FDE's id is required
        {"decided_at": "2026-11-16T09:00:00"},  # no time zone
        {"candidates": [{"url": REPO["url"], "source": "FDE", "score": 1.5}]},  # score out of range
    ],
)
def test_wg4_an_inconsistent_repo_decision_is_rejected(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        RepoDecision.model_validate_json(json.dumps(decision(**over)))


def test_wg4_a_candidate_appears_once() -> None:
    twice = [decision()["candidates"][0]] * 2
    with pytest.raises(ValidationError, match="once"):
        RepoDecision.model_validate_json(json.dumps(decision(candidates=twice)))


# --- the token budget -----------------------------------------------------------------------------


def test_wg4_the_default_caps_are_those_of_adr_0028() -> None:
    caps = BudgetCaps()
    assert (
        (caps.sub_skill_tokens, caps.workflow_tokens, caps.knowledge_tokens)
        == (
            DEFAULT_SUB_SKILL_TOKENS,
            DEFAULT_WORKFLOW_TOKENS,
            DEFAULT_KNOWLEDGE_TOKENS,
        )
        == (200_000, 2_000_000, 30_000_000)
    )
    assert (caps.first_alert_pct, caps.second_alert_pct) == (50, 80)


@pytest.mark.parametrize(
    ("used", "level"),
    [
        (0, BudgetLevel.OK),
        (99_999, BudgetLevel.OK),
        (100_000, BudgetLevel.FIRST_ALERT),  # 50% of 200,000
        (159_999, BudgetLevel.FIRST_ALERT),
        (160_000, BudgetLevel.SECOND_ALERT),  # 80%
        (199_999, BudgetLevel.SECOND_ALERT),
        (200_000, BudgetLevel.STOPPED),  # the cap itself
        (500_000, BudgetLevel.STOPPED),
    ],
)
def test_wg4_the_level_follows_usage_against_the_cap(used: int, level: BudgetLevel) -> None:
    assert BudgetCaps().level(BudgetScope.SUB_SKILL, used) is level


def test_wg4_each_scope_has_its_own_cap() -> None:
    caps = BudgetCaps()
    assert caps.level(BudgetScope.WORKFLOW, 1_500_000) is BudgetLevel.FIRST_ALERT
    assert caps.level(BudgetScope.SUB_SKILL, 1_500_000) is BudgetLevel.STOPPED
    assert caps.level(BudgetScope.KNOWLEDGE, 25_600_000) is BudgetLevel.SECOND_ALERT


@pytest.mark.parametrize(
    "over",
    [
        {"first_alert_pct": 80, "second_alert_pct": 80},
        {"first_alert_pct": 90, "second_alert_pct": 80},
        {"sub_skill_tokens": 3_000_000},  # above the Workflow cap
        {"workflow_tokens": 0},
        {"knowledge_tokens": 1_000_000_001},
    ],
)
def test_wg4_caps_must_be_ordered_and_positive(over: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        BudgetCaps(**over)


def test_wg4_the_state_is_stopped_exactly_at_the_cap() -> None:
    BudgetState(scope=BudgetScope.WORKFLOW, used=100, cap=1000, level=BudgetLevel.OK)
    BudgetState(scope=BudgetScope.WORKFLOW, used=1000, cap=1000, level=BudgetLevel.STOPPED)
    with pytest.raises(ValidationError):
        BudgetState(scope=BudgetScope.WORKFLOW, used=1000, cap=1000, level=BudgetLevel.OK)
    with pytest.raises(ValidationError):
        BudgetState(scope=BudgetScope.WORKFLOW, used=10, cap=1000, level=BudgetLevel.STOPPED)
