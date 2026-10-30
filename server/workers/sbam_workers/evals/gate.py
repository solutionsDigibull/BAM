"""The regression gate runner (E15.5): a golden set through the model, against the baseline.

    uv run python -m sbam_workers.evals.gate --task ocr            # compare, exit 0/1/2/3
    uv run python -m sbam_workers.evals.gate --task ocr --record   # write a baseline for review

Exit codes: 0 PASS, 1 REGRESSED, 2 INFRA_ERROR (the provider failed: never a pass), 3 NO_BASELINE.

Only OCR has a golden set so far. The pages go through the production path (`ocr.read_page` over a
real `LlmClient`), so the prompt, the image handling and the schema checks are the ones a document
gets. Every run builds its own client and call store: the client caches answers, and runs that
shared one would return the first run's answers and average nothing.
"""

import argparse
import asyncio
import sys
import uuid
from collections.abc import Awaitable, Callable, Sequence
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import httpx
from pydantic import AnyHttpUrl
from sbam_api.db.session import make_engine
from sbam_api.llm.models import LlmCall, LlmDerivative
from sbam_contracts.pipeline import StageRef
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from sbam_workers.evals.baseline import (
    Baseline,
    Decision,
    Verdict,
    decide,
    load_baseline,
    load_gate_config,
    write_baseline,
)
from sbam_workers.evals.golden import (
    BASELINE_DIR,
    EVALS_DIR,
    GOLDEN_DIR,
    OcrItem,
    Task,
    load_ocr,
    load_tasks,
)
from sbam_workers.evals.render import render_page
from sbam_workers.evals.scoring import ItemResult, score_ocr, task_score
from sbam_workers.ingest import ocr
from sbam_workers.ingest.ocr import OcrDeps, OcrRun
from sbam_workers.ingest.rasterize import open_source
from sbam_workers.llm.client import LlmClient, LlmTransportError
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.settings import WorkerSettings

OcrReader = Callable[[bytes, str], Awaitable[str]]  # (image bytes, media type) -> the text read
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


def live_reader_factory(bifrost_url: str | None = None) -> Callable[[], OcrReader]:
    """Readers on the real `ocr.read_page` and Bifrost, with a throwaway call store."""

    def make() -> OcrReader:
        engine = make_engine(
            "sqlite://",
            poolclass=StaticPool,
            connect_args={"check_same_thread": False},
            execution_options={"schema_translate_map": {"sbam": None}},
        )
        LlmCall.metadata.create_all(engine, tables=[LlmCall.__table__, LlmDerivative.__table__])  # type: ignore[list-item]
        sessions = sessionmaker(bind=engine)
        extra = {"bifrost_url": AnyHttpUrl(bifrost_url)} if bifrost_url else {}
        conf = WorkerSettings(
            _env_file=None,  # type: ignore[call-arg]
            database_url="sqlite://",
            llm_timeout_seconds=120,
            **extra,  # type: ignore[arg-type]
        )
        client = LlmClient(
            conf,
            PromptRegistry(),
            SqlCallStore(sessions),
            LangfuseTracer(conf),
            httpx.AsyncClient(),
        )
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
    config = load_gate_config(evals_dir)
    items = load_ocr(golden_dir).items
    count = runs or config.runs
    try:
        results = await run_ocr(make_reader, items, count)
    except InfraError as exc:
        return GateReport(Decision(Verdict.INFRA_ERROR, str(exc)), None, [])
    score = task_score(results)
    if record:
        alias = next(e.alias for e in load_tasks(golden_dir) if e.task is Task.OCR)
        try:
            path = write_baseline(
                Baseline(
                    task=Task.OCR,
                    score=round(score, 4),
                    items=len(items),
                    runs=count,
                    alias=alias,
                    prompt_sha256=prompt_shas(Task.OCR, golden_dir),
                    recorded_on=today().date(),
                    note="recorded with --record; review before merging",
                ),
                baseline_dir,
            )
        except OSError as exc:  # sixty paid calls must not vanish into a stack trace
            reason = f"scored {score:.4f} but could not write the baseline: {type(exc).__name__}"
            return GateReport(Decision(Verdict.INFRA_ERROR, reason), score, results)
        return GateReport(
            Decision(Verdict.PASS, f"baseline {score:.3f} written to {path.name}"), score, results
        )
    baseline = load_baseline(Task.OCR, baseline_dir)
    return GateReport(decide(baseline, score, config.threshold), score, results)


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
    parser.add_argument("--task", choices=[Task.OCR.value], default=Task.OCR.value)
    parser.add_argument("--runs", type=int, default=None, help="override gate.json")
    parser.add_argument(
        "--record", action="store_true", help="write a baseline instead of comparing"
    )
    parser.add_argument("--bifrost-url", default=None)
    args = parser.parse_args(argv)
    report = asyncio.run(
        run_gate(live_reader_factory(args.bifrost_url), record=args.record, runs=args.runs)
    )
    print(render_report(report))
    return EXIT[report.decision.verdict]


if __name__ == "__main__":
    sys.exit(main())
