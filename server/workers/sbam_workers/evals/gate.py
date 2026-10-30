"""The regression gate runner (E15.5): a golden set through the model, against the baseline.

    uv run python -m sbam_workers.evals.gate --task ocr            # compare, exit 0/1/2/3
    uv run python -m sbam_workers.evals.gate --task testgen --record   # write a baseline for review

Exit codes: 0 PASS, 1 REGRESSED, 2 INFRA_ERROR (the provider failed: never a pass), 3 NO_BASELINE.

OCR, test generation and correction have golden sets so far. Each goes through the production path
(`ocr.read_page`, `testgen.generate`, `correct_code`, each over a real `LlmClient`), so the
prompt, the schema checks and the re-asks are the ones the build gets. Every run builds its own
client and call store: the client caches answers, and runs that shared one would return the first
run's answers and average nothing.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
from pydantic import AnyHttpUrl
from sbam_api.db.session import make_engine
from sbam_api.llm.models import LlmCall, LlmDerivative
from sbam_contracts.pipeline import StageRef
from sbam_contracts.testgen import TestgenOutput
from sqlalchemy.orm import Session, sessionmaker
from sqlalchemy.pool import StaticPool

from sbam_workers.build.codegen.bodies import CodeBodies, GeneratedBodies, generate_bodies
from sbam_workers.build.codegen.correction import Correction, correct_code
from sbam_workers.build.codegen.store import LoadedTemplate
from sbam_workers.evals.baseline import (
    Baseline,
    Decision,
    Verdict,
    decide,
    load_baseline,
    load_gate_config,
    write_baseline,
)
from sbam_workers.evals.codegen_set import CodegenCase, build_cases, load_codegen_template
from sbam_workers.evals.correction_set import (
    MUST_KEEP,
    CorrectionEnv,
    build_env,
    findings_of,
    flawed_bodies,
)
from sbam_workers.evals.golden import (
    BASELINE_DIR,
    EVALS_DIR,
    GOLDEN_DIR,
    DomainProfile,
    GoldenCorrectionItem,
    GoldenTestgenItem,
    OcrItem,
    Task,
    build_testgen_isatvon,
    correction_base,
    load_codegen,
    load_correction,
    load_ocr,
    load_tasks,
    load_testgen,
    testgen_base,
)
from sbam_workers.evals.render import render_page
from sbam_workers.evals.scoring import (
    ItemResult,
    score_codegen,
    score_correction,
    score_ocr,
    score_testgen,
    task_score,
)
from sbam_workers.ingest import ocr
from sbam_workers.ingest.ocr import OcrDeps, OcrRun
from sbam_workers.ingest.rasterize import open_source
from sbam_workers.llm.client import (
    ContextTooLarge,
    LlmClient,
    LlmRequestRejected,
    LlmTransportError,
)
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings
from sbam_workers.test import testgen

OcrReader = Callable[[bytes, str], Awaitable[str]]  # (image bytes, media type) -> the text read
# (the Sub-Skill's ISATVON) -> (the model's cases or None if it never gave valid ones, model calls)
TestgenGenerator = Callable[[Mapping[str, Any]], Awaitable[tuple[TestgenOutput | None, int]]]
# (the Sub-Skill, its flawed bodies, the findings) -> the corrector's answer, or None if it never
# gave one the client accepts
CorrectionRunner = Callable[
    [Mapping[str, Any], CodeBodies, list[dict[str, Any]]], Awaitable[Correction | None]
]
# (the Sub-Skill's case) -> the generated bodies, or None if the model never gave any
CodegenRunner = Callable[[CodegenCase], Awaitable[GeneratedBodies | None]]
EXIT = {Verdict.PASS: 0, Verdict.REGRESSED: 1, Verdict.INFRA_ERROR: 2, Verdict.NO_BASELINE: 3}


class InfraError(Exception):
    """The provider failed, so there is no score. The gate reports it as a failure."""


@dataclass(frozen=True)
class GateReport:
    decision: Decision
    score: float | None
    runs: list[list[ItemResult]]


async def run_ocr(
    make_reader: Callable[[], OcrReader], items: Sequence[OcrItem], runs: int
) -> list[list[ItemResult]]:
    """`runs` passes over the items, each with its own reader. Each page is drawn once and every
    run reads the same image."""
    pages = [(item, *render_page(item.render)) for item in items]
    out: list[list[ItemResult]] = []
    for _ in range(runs):
        read = make_reader()
        results: list[ItemResult] = []
        for item, data, media_type in pages:
            try:
                text = await read(data, media_type)
            except LlmTransportError as exc:
                raise InfraError(
                    f"the provider failed on {item.id}: {type(exc).__name__}"
                ) from None
            results.append(score_ocr(item, text))
        out.append(results)
    return out


async def run_testgen(
    make_generator: Callable[[], TestgenGenerator],
    items: Sequence[GoldenTestgenItem],
    base: dict[str, Any],
    domains: dict[str, DomainProfile],
    runs: int,
) -> list[list[ItemResult]]:
    """`runs` passes over the items, each with its own generator (so each with its own client)."""
    documents = [(item, build_testgen_isatvon(item, base, domains)) for item in items]
    out: list[list[ItemResult]] = []
    for _ in range(runs):
        generate = make_generator()
        results: list[ItemResult] = []
        for item, isatvon in documents:
            try:
                output, attempts = await generate(isatvon)
            except LlmTransportError as exc:
                raise InfraError(
                    f"the provider failed on {item.id}: {type(exc).__name__}"
                ) from None
            results.append(
                score_testgen(item.id, domains[item.domain].required_inputs, output, attempts)
            )
        out.append(results)
    return out


def _live_client(bifrost_url: str | None) -> tuple[LlmClient, sessionmaker[Session]]:
    """A real `LlmClient` over Bifrost whose call store is a throwaway in-memory database, and the
    sessions of that database."""
    engine = make_engine(
        "sqlite://",
        poolclass=StaticPool,
        connect_args={"check_same_thread": False},
        execution_options={"schema_translate_map": {"sbam": None}},
    )
    LlmCall.metadata.create_all(engine, tables=[LlmCall.__table__, LlmDerivative.__table__])  # type: ignore[list-item]
    sessions: sessionmaker[Session] = sessionmaker(bind=engine)
    extra = {"bifrost_url": AnyHttpUrl(bifrost_url)} if bifrost_url else {}
    conf = WorkerSettings(
        _env_file=None,  # type: ignore[call-arg]
        database_url="sqlite://",
        llm_timeout_seconds=120,
        **extra,  # type: ignore[arg-type]
    )
    client = LlmClient(
        conf, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(conf), httpx.AsyncClient()
    )
    return client, sessions


def live_reader_factory(bifrost_url: str | None = None) -> Callable[[], OcrReader]:
    """Readers on the real `ocr.read_page` and Bifrost, with a throwaway call store."""

    def make() -> OcrReader:
        client, sessions = _live_client(bifrost_url)
        deps = OcrDeps(sessions=sessions, storage=None, reader=client)

        async def read(data: bytes, media_type: str) -> str:
            stage = StageRef(
                tenant_id=uuid.uuid4(),
                project_id=uuid.uuid4(),
                run_id=uuid.uuid4(),
                correlation_id="eval-gate",
                ref_id=uuid.uuid4(),
            )
            run = OcrRun(f"wf-{uuid.uuid4()}", "act-eval", 1, "run-eval")
            source = open_source(data, "png" if media_type == "image/png" else "jpg")
            try:
                page = await ocr.read_page(deps, stage, run, source, 1)
            finally:
                source.close()
            return page.markdown  # empty for a page the model could not read

        return read

    return make


def live_testgen_factory(bifrost_url: str | None = None) -> Callable[[], TestgenGenerator]:
    """Generators on the real `testgen.generate` (with its re-ask loop) and Bifrost."""

    def make() -> TestgenGenerator:
        client, _ = _live_client(bifrost_url)

        async def generate(isatvon: Mapping[str, Any]) -> tuple[TestgenOutput | None, int]:
            scope = LlmScope(
                uuid.uuid4(), uuid.uuid4(), "eval-gate", f"wf-{uuid.uuid4()}:run", "act-eval"
            )
            try:
                result = await testgen.generate(client, scope, isatvon)
            except SchemaInvalidAfterRetries as exc:
                return None, len(exc.attempts)  # the model never gave valid cases
            except (LlmRequestRejected, ContextTooLarge):
                return None, 0
            return result.output, result.attempts

        return generate

    return make


async def run_correction(
    make_runner: Callable[[], CorrectionRunner],
    items: Sequence[GoldenCorrectionItem],
    base: dict[str, Any],
    env: CorrectionEnv,
    runs: int,
) -> list[list[ItemResult]]:
    """`runs` passes over the flawed bodies, each with its own runner (so its own client). The
    findings are worked out once: they are what the pre-check reports for the flawed bodies."""
    cases = []
    for item in items:
        bodies = flawed_bodies(item, base)
        cases.append((item, bodies, findings_of(bodies, env)))
    out: list[list[ItemResult]] = []
    for _ in range(runs):
        run = make_runner()
        results: list[ItemResult] = []
        for item, bodies, findings in cases:
            try:
                correction = await run(env.sub_skill, bodies, findings)
            except LlmTransportError as exc:
                raise InfraError(
                    f"the provider failed on {item.id}: {type(exc).__name__}"
                ) from None
            results.append(
                score_correction(
                    item.id, bodies.model_dump(), item.defect.slot, correction, MUST_KEEP
                )
            )
        out.append(results)
    return out


def live_correction_factory(
    env: CorrectionEnv, bifrost_url: str | None = None
) -> Callable[[], CorrectionRunner]:
    """Runners on the real `correct_code` (its schema re-asks and pre-checks) and Bifrost."""

    def make() -> CorrectionRunner:
        client, _ = _live_client(bifrost_url)

        async def run(
            sub_skill: Mapping[str, Any], bodies: CodeBodies, findings: list[dict[str, Any]]
        ) -> Correction | None:
            scope = LlmScope(
                uuid.uuid4(), uuid.uuid4(), "eval-gate", f"wf-{uuid.uuid4()}:run", "act-eval"
            )
            try:
                return await correct_code(
                    client,
                    scope,
                    sub_skill=sub_skill,
                    current=bodies,
                    template=env.template,
                    params=env.params,
                    skeleton=env.skeleton,
                    modules=(),
                    findings=findings,
                )
            except SchemaInvalidAfterRetries:
                return None  # the model never gave an answer the schema accepts
            except (LlmRequestRejected, ContextTooLarge):
                return None

        return run

    return make


async def run_codegen(
    make_runner: Callable[[], CodegenRunner], cases: Sequence[CodegenCase], runs: int
) -> list[list[ItemResult]]:
    """`runs` passes over the Sub-Skills, each with its own runner (so its own client)."""
    out: list[list[ItemResult]] = []
    for _ in range(runs):
        run = make_runner()
        results: list[ItemResult] = []
        for case in cases:
            try:
                generated = await run(case)
            except LlmTransportError as exc:
                raise InfraError(
                    f"the provider failed on {case.item.id}: {type(exc).__name__}"
                ) from None
            results.append(score_codegen(case.item.id, generated, case.params["gate_outputs"]))
        out.append(results)
    return out


def live_codegen_factory(
    template: LoadedTemplate, bifrost_url: str | None = None
) -> Callable[[], CodegenRunner]:
    """Runners on the real `generate_bodies` (schema re-asks, pre-check, assembly) and Bifrost."""

    def make() -> CodegenRunner:
        client, _ = _live_client(bifrost_url)

        async def run(case: CodegenCase) -> GeneratedBodies | None:
            scope = LlmScope(
                uuid.uuid4(), uuid.uuid4(), "eval-gate", f"wf-{uuid.uuid4()}:run", "act-eval"
            )
            try:
                return await generate_bodies(
                    client, scope, template, case.params, case.skeleton, ()
                )
            except SchemaInvalidAfterRetries:
                return None  # the model never gave bodies the schema accepts
            except (LlmRequestRejected, ContextTooLarge):
                return None

        return run

    return make


def prompt_shas(task: Task, golden_dir: Path = GOLDEN_DIR) -> dict[str, str]:
    """`prompt id/version -> sha256` of the latest version of every prompt the task uses."""
    registry = PromptRegistry()
    shas: dict[str, str] = {}
    for entry in load_tasks(golden_dir):
        if entry.task is task:
            for prompt_id in entry.prompt_ids:
                prompt = registry.get(prompt_id)
                shas[f"{prompt.id}/{prompt.version}"] = prompt.sha256
    return shas


async def run_task_gate(
    task: Task,
    execute: Callable[[int], Awaitable[list[list[ItemResult]]]],
    *,
    items: int,
    record: bool = False,
    runs: int | None = None,
    golden_dir: Path = GOLDEN_DIR,
    evals_dir: Path = EVALS_DIR,
    baseline_dir: Path = BASELINE_DIR,
    today: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> GateReport:
    """What every task shares: run, score, then record a baseline or compare with the last one.
    `execute(runs)` returns the item results of each run."""
    config = load_gate_config(evals_dir)
    count = runs or config.runs
    try:
        results = await execute(count)
    except InfraError as exc:
        return GateReport(Decision(Verdict.INFRA_ERROR, str(exc)), None, [])
    score = task_score(results)
    if record:
        alias = next(e.alias for e in load_tasks(golden_dir) if e.task is task)
        try:
            path = write_baseline(
                Baseline(
                    task=task,
                    score=round(score, 4),
                    items=items,
                    runs=count,
                    alias=alias,
                    prompt_sha256=prompt_shas(task, golden_dir),
                    recorded_on=today().date(),
                    note="recorded with --record; review before merging",
                ),
                baseline_dir,
            )
        except OSError as exc:  # paid calls must not vanish into a stack trace
            reason = f"scored {score:.4f} but could not write the baseline: {type(exc).__name__}"
            return GateReport(Decision(Verdict.INFRA_ERROR, reason), score, results)
        return GateReport(
            Decision(Verdict.PASS, f"baseline {score:.3f} written to {path.name}"), score, results
        )
    baseline = load_baseline(task, baseline_dir)
    return GateReport(decide(baseline, score, config.threshold), score, results)


async def run_gate(
    make_reader: Callable[[], OcrReader],
    *,
    record: bool = False,
    runs: int | None = None,
    golden_dir: Path = GOLDEN_DIR,
    evals_dir: Path = EVALS_DIR,
    baseline_dir: Path = BASELINE_DIR,
    today: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> GateReport:
    """The OCR gate."""
    items = load_ocr(golden_dir).items
    return await run_task_gate(
        Task.OCR,
        lambda count: run_ocr(make_reader, items, count),
        items=len(items),
        record=record,
        runs=runs,
        golden_dir=golden_dir,
        evals_dir=evals_dir,
        baseline_dir=baseline_dir,
        today=today,
    )


async def run_testgen_gate(
    make_generator: Callable[[], TestgenGenerator],
    *,
    record: bool = False,
    runs: int | None = None,
    golden_dir: Path = GOLDEN_DIR,
    evals_dir: Path = EVALS_DIR,
    baseline_dir: Path = BASELINE_DIR,
    today: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> GateReport:
    """The test-generation gate."""
    loaded = load_testgen(golden_dir)
    items = loaded.items
    base = testgen_base(golden_dir)
    return await run_task_gate(
        Task.TESTGEN,
        lambda count: run_testgen(make_generator, items, base, loaded.domains, count),
        items=len(items),
        record=record,
        runs=runs,
        golden_dir=golden_dir,
        evals_dir=evals_dir,
        baseline_dir=baseline_dir,
        today=today,
    )


async def run_correction_gate(
    make_runner: Callable[[], CorrectionRunner],
    *,
    record: bool = False,
    runs: int | None = None,
    golden_dir: Path = GOLDEN_DIR,
    evals_dir: Path = EVALS_DIR,
    baseline_dir: Path = BASELINE_DIR,
    today: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> GateReport:
    """The correction gate."""
    items = load_correction(golden_dir).items
    base = correction_base(golden_dir)
    env = build_env(base)
    return await run_task_gate(
        Task.CORRECTION,
        lambda count: run_correction(make_runner, items, base, env, count),
        items=len(items),
        record=record,
        runs=runs,
        golden_dir=golden_dir,
        evals_dir=evals_dir,
        baseline_dir=baseline_dir,
        today=today,
    )


async def run_codegen_gate(
    make_runner: Callable[[], CodegenRunner],
    *,
    record: bool = False,
    runs: int | None = None,
    golden_dir: Path = GOLDEN_DIR,
    evals_dir: Path = EVALS_DIR,
    baseline_dir: Path = BASELINE_DIR,
    today: Callable[[], datetime] = lambda: datetime.now(UTC),
) -> GateReport:
    """The code-generation gate."""
    items = load_codegen(golden_dir).items
    shared = load_testgen(golden_dir)
    cases = build_cases(items, testgen_base(golden_dir), shared.domains)
    return await run_task_gate(
        Task.CODEGEN,
        lambda count: run_codegen(make_runner, cases, count),
        items=len(items),
        record=record,
        runs=runs,
        golden_dir=golden_dir,
        evals_dir=evals_dir,
        baseline_dir=baseline_dir,
        today=today,
    )


def render_report(report: GateReport) -> str:
    lines = [f"{report.decision.verdict}: {report.decision.reason}"]
    for number, run in enumerate(report.runs, start=1):
        for result in run:
            if result.missing:
                lines.append(
                    f"  run {number} {result.item_id}: missing {', '.join(result.missing)}"
                )
    return "\n".join(lines)


def main(argv: Sequence[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Run a golden set against the model")
    parser.add_argument(
        "--task",
        choices=[Task.OCR.value, Task.TESTGEN.value, Task.CORRECTION.value, Task.CODEGEN.value],
        default="ocr",
    )
    parser.add_argument("--runs", type=int, default=None, help="override gate.json")
    parser.add_argument(
        "--record", action="store_true", help="write a baseline instead of comparing"
    )
    parser.add_argument("--bifrost-url", default=None)
    args = parser.parse_args(argv)
    if args.task == Task.CODEGEN.value:
        coroutine = run_codegen_gate(
            live_codegen_factory(load_codegen_template(), args.bifrost_url),
            record=args.record,
            runs=args.runs,
        )
    elif args.task == Task.CORRECTION.value:
        coroutine = run_correction_gate(
            live_correction_factory(build_env(correction_base()), args.bifrost_url),
            record=args.record,
            runs=args.runs,
        )
    elif args.task == Task.TESTGEN.value:
        coroutine = run_testgen_gate(
            live_testgen_factory(args.bifrost_url), record=args.record, runs=args.runs
        )
    else:
        coroutine = run_gate(
            live_reader_factory(args.bifrost_url), record=args.record, runs=args.runs
        )
    report = asyncio.run(coroutine)
    print(render_report(report))
    return EXIT[report.decision.verdict]


if __name__ == "__main__":
    sys.exit(main())
