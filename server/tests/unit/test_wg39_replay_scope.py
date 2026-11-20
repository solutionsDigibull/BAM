"""WG-39 (#926), the check requested after #930: a stage that reuses a fixed Temporal workflow id
and an activity id the replay key alone cannot tell apart must not hand an earlier run's answer to
the next one. Fixed here: the UI copy (keyed by its input) and the run id in the activities."""

from pathlib import Path
from typing import Any

from sbam_workers.build.ui_stage import _digest, copy_context


def spec(skill_id: str, name: str) -> dict[str, Any]:
    sub = {"id": f"{skill_id}.1", "name": name, "summary": "s", "inputs": []}
    return {"skills": [{"id": skill_id, "name": name, "subs": [sub]}]}


WORKERS = Path(__file__).resolve().parents[2] / "workers" / "sbam_workers"


def test_wg39_t50_the_ui_copy_key_follows_its_input_and_is_stable_for_a_retry() -> None:
    one = copy_context("Quote", spec("MS01", "Intake"))
    same = copy_context("Quote", spec("MS01", "Intake"))
    other = copy_context("Quote", spec("MS02", "Price"))
    assert _digest(one) == _digest(same) != _digest(other)


def test_wg39_t51_sow_and_isatvon_activities_key_the_replay_on_the_run_id() -> None:
    for name in ("sow/generate.py", "isatvon/macros.py", "isatvon/sub_skills.py"):
        text = (WORKERS / name).read_text(encoding="utf-8")
        assert 'activity_id=f"{info.workflow_run_id}:{info.activity_id}"' in text, name
        assert "activity_id=info.activity_id," not in text, name
