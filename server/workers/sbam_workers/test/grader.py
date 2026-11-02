"""The rubric grader (E13.6, #638): `llm-rubric` verdicts from the `sbam-promptfoo` sidecar.

`SidecarGrader` implements `judge.Grader`. For each case that carries a rubric it hands the sidecar
the rubric and the text the Sub-Skill produced (the outputs of the case's last pass), through the
shared job directory, and reads back one verdict per case. The sidecar runs Promptfoo with a
provider that reaches only Bifrost (alias `sbam-rubric`); this side never calls a model itself, so
the "LLM calls go through Bifrost with an alias" rule holds.

    <jobs>/<id>/request.json   {"alias": "sbam-rubric", "cases": [{"name", "rubric", "output"}]}
    <jobs>/<id>/READY          written last: the sidecar may take the job
    <jobs>/<id>/result.json    {"verdicts": {"<name>": true|false}, "error": null|"..."} then DONE

Anything that is not a clean answer for every case asked about (no directory, a timeout, a crash,
an `error`, a result that is not JSON, a verdict missing for a case, a verdict that is not a
boolean) raises `PromptfooInfraError`: `judge.grade_with_retries` asks again up to three times and
then testing is blocked, with no correction spent (E13.6-T07). A grader that cannot answer is never
read as "the rubric was not met".

Messages carry case names and counts, never the outputs or the rubric text.
"""

import asyncio
import json
import shutil
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from pathlib import Path
from typing import Any, Final

from sbam_contracts.promptfoo import AssertionType, SuiteCase
from sbam_contracts.sandbox import RunnerOutputs

from sbam_workers.test.judge import PromptfooInfraError, evidence_of

ALIAS: Final = "sbam-rubric"
MAX_OUTPUT_CHARS: Final = 20_000  # what the grader reads of one case's outputs


def rubric_of(case: SuiteCase) -> str | None:
    for assertion in case.assertions:
        if assertion.type is AssertionType.LLM_RUBRIC and isinstance(assertion.value, str):
            return assertion.value
    return None


def output_text(outputs: Mapping[str, Any] | None) -> str:
    return json.dumps(outputs or {}, sort_keys=True, ensure_ascii=False)[:MAX_OUTPUT_CHARS]


def request_for(cases: Sequence[SuiteCase], outputs: RunnerOutputs) -> dict[str, Any]:
    by_name = {c.case_id: c for c in outputs.cases}
    asked = []
    for case in cases:
        rubric = rubric_of(case)
        raw = by_name.get(case.name)
        if rubric is None or raw is None:
            raise PromptfooInfraError(f"case {case.name} has nothing to grade")
        asked.append(
            {
                "name": case.name,
                "rubric": rubric,
                "output": output_text(evidence_of(raw).outputs),
            }
        )
    return {"alias": ALIAS, "cases": asked}


def verdicts_of(result: Mapping[str, Any], names: Sequence[str]) -> dict[str, bool]:
    if result.get("error"):
        raise PromptfooInfraError(f"the grader sidecar says {str(result['error'])[:80]}")
    given = result.get("verdicts")
    if not isinstance(given, dict):
        raise PromptfooInfraError("the grader sidecar gave no verdicts")
    verdicts: dict[str, bool] = {}
    for name in names:
        verdict = given.get(name)
        if not isinstance(verdict, bool):
            raise PromptfooInfraError(f"the grader sidecar gave no verdict for case {name}")
        verdicts[name] = verdict
    return verdicts


class SidecarGrader:
    """Jobs for the `sbam-promptfoo` container, through the shared `jobs_dir`."""

    def __init__(
        self,
        jobs_dir: Path,
        timeout: float = 300.0,
        poll: float = 0.2,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ) -> None:
        self.jobs_dir, self.timeout, self.poll = jobs_dir, timeout, poll
        self.clock, self.sleep = clock, sleep

    def grade(self, cases: Sequence[SuiteCase], outputs: RunnerOutputs) -> dict[str, bool]:
        if not self.jobs_dir.is_dir():  # never invent the directory and wait for a runner
            raise PromptfooInfraError("the grader jobs directory is not mounted")
        request = request_for(cases, outputs)
        job = self.jobs_dir / uuid.uuid4().hex
        try:
            job.mkdir()
            (job / "request.json").write_text(json.dumps(request), encoding="utf-8")
            (job / "READY").touch()  # last: the sidecar takes a job only once it is complete
            deadline = self.clock() + self.timeout
            while not (job / "DONE").exists():
                if self.clock() > deadline:
                    raise PromptfooInfraError("the grader sidecar did not answer in time")
                self.sleep(self.poll)
            result = json.loads((job / "result.json").read_text(encoding="utf-8"))
        except OSError as exc:
            raise PromptfooInfraError(
                f"the grader jobs directory failed: {type(exc).__name__}"
            ) from None
        except ValueError:
            raise PromptfooInfraError(
                "the grader sidecar answered with something unreadable"
            ) from None
        finally:
            shutil.rmtree(job, ignore_errors=True)
        if not isinstance(result, dict):
            raise PromptfooInfraError("the grader sidecar answered with something unreadable")
        return verdicts_of(result, [c["name"] for c in request["cases"]])

    async def __call__(self, cases: Sequence[SuiteCase], outputs: RunnerOutputs) -> dict[str, bool]:
        return await asyncio.to_thread(self.grade, cases, outputs)
