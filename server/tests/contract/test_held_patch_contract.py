"""#499 contract: the held ISATVON patch and the FDE's decision on it."""

import uuid
from typing import Any

import pytest
from pydantic import ValidationError
from sbam_contracts.held_patch import (
    MAX_HELD_OPS,
    HeldPatch,
    HeldPatchResponse,
    PatchDecided,
    PatchDecision,
)


def held(**over: Any) -> dict[str, Any]:
    return {
        "sub_skill_id": uuid.uuid4(),
        "content_sha256": "c" * 64,
        "attempt": 1,
        "failed_gate": "run_sandbox_tests",
        "rationale": "loosen the bound",
        "changed_paths": ["/isatvon_definition/v_variables/max_iterations"],
        "ops": [
            {
                "op": "replace",
                "path": "/isatvon_definition/v_variables/max_iterations",
                "value": 9,
            }
        ],
        "isatvon_sha256_before": "sha256:" + "a" * 64,
        "isatvon_sha256_after": "sha256:" + "b" * 64,
        **over,
    }


def test_a20b_a_held_patch_carries_what_the_fde_needs_to_decide() -> None:
    patch = HeldPatch.model_validate(held())
    assert patch.ops[0].op == "replace" and patch.changed_paths and patch.rationale


def test_a20b_nothing_waiting_is_an_empty_response() -> None:
    assert HeldPatchResponse().held is None
    assert HeldPatchResponse(held=HeldPatch.model_validate(held())).held is not None


def test_a20b_an_attempt_starts_at_one_and_the_ops_are_bounded() -> None:
    with pytest.raises(ValidationError):
        HeldPatch.model_validate(held(attempt=0))
    ops = [{"op": "remove", "path": "/isatvon_definition/x"}] * (MAX_HELD_OPS + 1)
    with pytest.raises(ValidationError):
        HeldPatch.model_validate(held(ops=ops))


def test_a20b_an_operation_must_be_a_json_patch_operation() -> None:
    bad = [{"op": "move", "path": "/isatvon_definition/x"}]
    with pytest.raises(ValidationError):
        HeldPatch.model_validate(held(ops=bad))


def test_a20b_a_decision_reason_is_optional_but_not_empty() -> None:
    assert PatchDecision().reason is None
    assert PatchDecision(reason="the bound is too loose").reason
    with pytest.raises(ValidationError):
        PatchDecision(reason="")
    with pytest.raises(ValidationError):
        PatchDecision.model_validate({"reason": "x", "approve": True})


def test_a20b_a_decision_is_accepted_or_rejected_only() -> None:
    assert PatchDecided(sub_skill_id=uuid.uuid4(), build_no=1, decision="ACCEPTED")
    with pytest.raises(ValidationError):
        PatchDecided.model_validate(
            {"sub_skill_id": str(uuid.uuid4()), "build_no": 1, "decision": "MAYBE"}
        )


def test_a20b_the_held_patch_carries_the_content_hash_to_send_as_if_match() -> None:
    assert HeldPatch.model_validate(held()).content_sha256 == "c" * 64
    for bad in ("", "xyz", "C" * 64, "c" * 63):
        with pytest.raises(ValidationError):
            HeldPatch.model_validate(held(content_sha256=bad))
