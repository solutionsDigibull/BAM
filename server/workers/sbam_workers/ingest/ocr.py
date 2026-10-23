"""The `ocr_document` activity (E4.8, TD §4 T5): the vision model reads the pages that have no text.

Runs after `parse_document` when the parse left a version PARSING with pages in
`parse_report.ocr_needed_pages` (a PDF page with fewer than 20 characters of text, or every frame
of an image). `ocr_version` is the whole step and takes its dependencies as arguments, so tests run
it with a fake model, a temp-dir storage and SQLite. The activity only builds the real ones.

For a version in PARSING with a CLEAN scan:
  * each page in `ocr_needed_pages` is rendered to a PNG (200 DPI, at most 3000 px and 5 MB, see
    `rasterize`) and sent to `sbam-ocr`, one page per call, with the document version id on the
    call. Only that project's page goes in a call, never pages of several documents or projects;
  * the answer is an `OcrPage` for the page that was sent. A wrong page number or an answer that
    does not fit the schema is re-asked (at most 3 times); after that the page is unreadable and
    the document goes on. A page the model calls unreadable, or answers with no text, is the same;
  * a transport error (the provider is down after the client's own retries) is raised and the
    activity is retried; after the last attempt the workflow calls `mark_ocr_failed`;
  * the model's text is stored as a block with `ocr="llm"` (replacing a short text layer of that
    page), an unreadable page as an empty block with `unreadable=True`, `doc.md` and `doc.json` are
    rewritten (content-addressed, so a rewrite is the same object) and the report lists
    `ocr_pages` and `unreadable_pages`;
  * the version is PARSED when at least one page has text, otherwise PARSE_FAILED with `EMPTY`.
The page text is data: the only thing taken from the model's answer is the text, stored as a block.
A status, a flag or a field the model invents never reaches the version (the schema forbids extra
fields, and state changes only through the state machine).

One LLM scope per page (`<activity id>:page-<n>`), so the replay cache cannot hand page 1's answer
to page 2. Nothing here logs page text or file names.
"""

import asyncio
import logging
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from functools import lru_cache
from typing import Any, Protocol

import sbam_api.ingest.models  # noqa: F401 - registers DocumentVersion for the state machine
from sbam_api.audit.repository import record
from sbam_api.db.repository import TenantContext
from sbam_api.state_machine.service import SYSTEM_ACTOR_ID
from sbam_api.storage.factory import make_backend
from sbam_contracts.api import ErrorCode
from sbam_contracts.documents import (
    MAX_OCR_PAGES,
    Block,
    BlockType,
    OcrPage,
    ParsedDocument,
    ParseOutcome,
    ParseReport,
    Table,
)
from sbam_contracts.pipeline import StageRef
from sbam_contracts.runs import TaskQueue
from sbam_contracts.state_machines import DocumentVersionState as S
from sqlalchemy.orm import sessionmaker
from temporalio import activity
from temporalio.exceptions import ApplicationError

from sbam_workers import mirror
from sbam_workers.ingest.guard import require_clean
from sbam_workers.ingest.loaders import Heartbeat, Parsed, ParseError, no_heartbeat
from sbam_workers.ingest.parsing import (
    NOT_PARSEABLE,
    ParseDeps,
    ParseFailure,
    _fail,
    _gone,
    _load,
    _outcome,
    store_and_finish,
)
from sbam_workers.ingest.rasterize import PageSource, PageUnreadable, open_source
from sbam_workers.llm.client import (
    ContextTooLarge,
    LlmClient,
    LlmImage,
    LlmRequestRejected,
    LlmResult,
)
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from sbam_workers.llm.store import LlmScope, SqlCallStore
from sbam_workers.llm.trace import LangfuseTracer
from sbam_workers.registry import activity_for
from sbam_workers.settings import WorkerSettings

log = logging.getLogger("sbam_workers.ingest.ocr")

ALIAS = "sbam-ocr"
PROMPT = "ocr-page"
MAX_WARNINGS = 20


class Reader(Protocol):
    """The part of `LlmClient` this step uses (a fake in the tests)."""

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[OcrPage],
        context: Mapping[str, str],
        scope: LlmScope,
        *,
        validate: Callable[[OcrPage], tuple[str, ...]] | None = None,
        images: Sequence[LlmImage] = (),
        subject: Mapping[str, str] | None = None,
    ) -> LlmResult[OcrPage]: ...


@dataclass(frozen=True)
class OcrDeps:
    sessions: sessionmaker[Any]
    storage: Any
    reader: Reader
    open_source: Callable[[bytes, str], PageSource] = open_source

    @property
    def parse(self) -> ParseDeps:
        return ParseDeps(self.sessions, self.storage)


@dataclass(frozen=True)
class OcrRun:
    """Which activity attempt this is: the key of the LLM replay cache."""

    workflow_id: str
    activity_id: str
    attempt: int = 1


@dataclass(frozen=True)
class Pending:
    """What the OCR step needs from a version that is waiting for it."""

    key: str  # the stored file
    detected_type: str
    loader_id: str
    json_key: str | None  # the text pass's doc.json (none for an image)
    report: ParseReport


@dataclass(frozen=True)
class Page:
    """The result for one page: text, or why there is none."""

    number: int
    markdown: str = ""
    warning: str | None = None

    @property
    def readable(self) -> bool:
        return bool(self.markdown.strip())


def _scope(inp: StageRef, run: OcrRun, number: int) -> LlmScope:
    return LlmScope(
        tenant_id=inp.tenant_id,
        project_id=inp.project_id,
        correlation_id=inp.correlation_id,
        temporal_workflow_id=run.workflow_id,
        activity_id=f"{run.activity_id}:page-{number}",
        attempt=run.attempt,
    )


async def read_page(deps: OcrDeps, inp: StageRef, run: OcrRun, source: PageSource, n: int) -> Page:
    """One page. A transport error propagates (the activity is retried); everything else that goes
    wrong with this page leaves it unreadable and the document goes on."""
    try:
        png = await asyncio.to_thread(source.render, n)
    except PageUnreadable:
        return Page(n, warning=f"page {n} could not be turned into an image")

    def right_page(answer: OcrPage) -> tuple[str, ...]:
        return () if answer.page == n else ("page: wrong_page",)  # code only, never the value

    try:
        result = await deps.reader.run(
            ALIAS,
            PROMPT,
            OcrPage,
            {"page_number": str(n)},
            _scope(inp, run, n),
            validate=right_page,
            images=[LlmImage(name=f"page-{n}", media_type="image/png", data=png)],
            subject={"document_version_id": str(inp.ref_id)},
        )
    except SchemaInvalidAfterRetries:
        return Page(n, warning=f"page {n}: the model's answer never matched the schema")
    except (LlmRequestRejected, ContextTooLarge):
        return Page(n, warning=f"page {n}: the model could not take this page")
    answer = result.output
    if answer.unreadable:
        return Page(n, warning=f"page {n} is blank or too poor to read")
    if not answer.markdown.strip():
        return Page(n, warning=f"page {n}: the model returned no text")
    return Page(n, markdown=answer.markdown.strip())


async def ocr_version(
    deps: OcrDeps, inp: StageRef, run: OcrRun, beat: Heartbeat = no_heartbeat
) -> ParseOutcome:
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    waiting = await asyncio.to_thread(_pending, deps, ctx, inp)
    if isinstance(waiting, ParseOutcome):  # an earlier attempt finished it
        return waiting
    report = waiting.report

    if len(report.ocr_needed_pages) > MAX_OCR_PAGES:
        failed = ParseReport(warnings=[f"more than {MAX_OCR_PAGES} pages need OCR"])
        return await asyncio.to_thread(_fail, deps.parse, ctx, inp, ErrorCode.PARSE_ERROR, failed)
    try:
        data = await asyncio.to_thread(deps.storage.get, waiting.key)
    except FileNotFoundError:
        raise _gone("stored file") from None
    try:
        source = await asyncio.to_thread(deps.open_source, data, waiting.detected_type)
    except ParseError as exc:
        failed = ParseReport(warnings=[str(exc)[:200]])
        return await asyncio.to_thread(_fail, deps.parse, ctx, inp, exc.code, failed)

    pages: list[Page] = []
    try:
        for n in report.ocr_needed_pages:
            beat()
            pages.append(await read_page(deps, inp, run, source, n))
    finally:
        source.close()
    return await asyncio.to_thread(_finish, deps, ctx, inp, waiting, pages)


def _pending(deps: OcrDeps, ctx: TenantContext, inp: StageRef) -> ParseOutcome | Pending:
    with deps.sessions() as session:
        version = _load(session, ctx, inp.ref_id)
        require_clean(version)
        if version.state in (S.PARSED.value, S.PARSE_FAILED.value):
            return _outcome(version)
        report = ParseReport.model_validate(version.parse_report) if version.parse_report else None
        if (
            version.state != S.PARSING.value
            or not version.detected_type
            or report is None
            or not report.ocr_needed_pages
        ):
            raise ApplicationError(
                "nothing to read by OCR for this version", type=NOT_PARSEABLE, non_retryable=True
            )
        return Pending(
            key=version.storage_key,
            detected_type=version.detected_type,
            loader_id=version.filetype_row_label or version.detected_type,
            json_key=version.derived_json_key,
            report=report,
        )


def _existing(deps: OcrDeps, json_key: str | None) -> tuple[list[Block], list[Table]]:
    """The blocks and tables the text pass stored (none for an image)."""
    if not json_key:
        return [], []
    try:
        document = ParsedDocument.model_validate_json(deps.storage.get(json_key))
    except FileNotFoundError:
        raise _gone("parsed text") from None
    return document.blocks, document.tables


def assemble(existing: list[Block], pages: Sequence[Page]) -> list[Block]:
    """The text blocks of the pages the model did not touch, plus the model's page blocks, by page.
    A readable OCR page replaces any short text of that page; an unreadable one keeps it and adds
    an empty `unreadable` block."""
    by_page = {p.number: p for p in pages}
    blocks: list[Block] = []
    for block in existing:
        page = by_page.get(block.page or 0)
        if page is not None and page.readable:
            continue  # the model's text replaces the short text layer of this page
        blocks.append(block)
    for page in pages:
        if page.readable:
            blocks.append(
                Block(type=BlockType.PARAGRAPH, text=page.markdown, page=page.number, ocr="llm")
            )
        else:
            blocks.append(
                Block(
                    type=BlockType.PARAGRAPH, text="", page=page.number, ocr="llm", unreadable=True
                )
            )
    return sorted(blocks, key=lambda b: (b.page or 0, b.unreadable))


def blocks_markdown(blocks: Sequence[Block]) -> str:
    """`doc.md` from the final blocks: a comment per page, then its text."""
    out: list[str] = []
    page: int | None = None
    for block in blocks:
        if block.page != page:
            page = block.page
            tag = " unreadable" if block.unreadable else " (OCR)" if block.ocr else ""
            out.append(f"<!-- page {page}{tag} -->\n")
        if block.text:
            out.append(f"{block.text}\n")
    return "\n".join(out)


def _finish(
    deps: OcrDeps,
    ctx: TenantContext,
    inp: StageRef,
    waiting: Pending,
    pages: Sequence[Page],
    *,
    code: ErrorCode = ErrorCode.EMPTY,
) -> ParseOutcome:
    existing, tables = _existing(deps, waiting.json_key)
    blocks = assemble(existing, pages)
    report = waiting.report
    warnings = [p.warning for p in pages if p.warning]
    shown = warnings[:MAX_WARNINGS]
    if len(warnings) > len(shown):
        shown.append(f"{len(warnings) - len(shown)} more pages could not be read")
    final = report.model_copy(
        update={
            "ocr_pages": [p.number for p in pages if p.readable],
            "unreadable_pages": [p.number for p in pages if not p.readable],
            "warnings": [*report.warnings, *shown],
        }
    )
    if not any(b.text for b in blocks):  # no page has any text: nothing was read
        return _fail(deps.parse, ctx, inp, code, final)
    parsed = Parsed(blocks=blocks, tables=tables, markdown=blocks_markdown(blocks), report=final)
    return store_and_finish(deps.parse, ctx, inp, waiting.loader_id, parsed)


def mark_failed(deps: OcrDeps, failure: ParseFailure) -> ParseOutcome:
    """The workflow gives up on the model (retries used up, or the timeout): every page still to
    read is unreadable. The version is PARSED when some page has text, else PARSE_FAILED with the
    code (OCR_UNAVAILABLE, or TIMEOUT)."""
    inp = failure.ref
    ctx = TenantContext(inp.tenant_id, inp.project_id)
    waiting = _pending(deps, ctx, inp)
    if isinstance(waiting, ParseOutcome):  # finished in the meantime: keep that answer
        return waiting
    pages = [
        Page(n, warning=f"page {n}: {failure.code.value}") for n in waiting.report.ocr_needed_pages
    ]
    outcome = _finish(deps, ctx, inp, waiting, pages, code=failure.code)
    if outcome.parsed:  # some pages have text: the document goes on, and the gap is on record
        with deps.sessions() as session:
            record(
                session,
                tenant_id=inp.tenant_id,
                actor_id=SYSTEM_ACTOR_ID,
                action="DOCUMENT_OCR_UNAVAILABLE",
                entity_type="document_version",
                entity_id=str(inp.ref_id),
                correlation_id=inp.correlation_id,
                after={"code": failure.code.value, "pages": len(pages)},
            )
            session.commit()
    return outcome


@lru_cache(maxsize=1)
def _deps() -> OcrDeps:
    settings = WorkerSettings()  # type: ignore[call-arg]  # values come from the environment
    sessions = mirror.sessions()
    reader = LlmClient(settings, PromptRegistry(), SqlCallStore(sessions), LangfuseTracer(settings))
    return OcrDeps(sessions=sessions, storage=make_backend(settings), reader=reader)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="ocr_document")
async def ocr_document(inp: StageRef) -> ParseOutcome:
    info = activity.info()
    run = OcrRun(info.workflow_id or "", info.activity_id, info.attempt)
    return await ocr_version(_deps(), inp, run, activity.heartbeat)


@activity_for(TaskQueue.INGEST)
@activity.defn(name="mark_ocr_failed")
def mark_ocr_failed(failure: ParseFailure) -> ParseOutcome:
    return mark_failed(_deps(), failure)
