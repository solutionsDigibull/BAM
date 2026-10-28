"""Code viewer reads (E11.5, TD §9.3): attempts, one file of an attempt's bundle, a diff of two.

Everything is read from the stored attempt: its `files` map says which paths exist, and the bundle
in storage supplies the bytes. A `path` the map does not hold is a 404 and is never used to open
anything. A Workflow, Sub-Skill, build or attempt outside this tenant and project is a 404 too, as
the repositories scope every query.
"""

import difflib
import hashlib
import io
import re
import tarfile
import uuid
from typing import Final

from sbam_contracts.code_view import (
    MAX_DIFF_BYTES,
    MAX_VIEW_BYTES,
    CodeAttemptList,
    CodeAttemptSummary,
    CodeDiff,
    CodeFileRef,
    CodeFileView,
    CodeFinding,
    LineRange,
)
from sqlalchemy.orm import Session

from sbam_api.builds.models import CodeAttempt
from sbam_api.builds.repository import CodeAttemptRepository
from sbam_api.core.auth import CurrentUser
from sbam_api.core.conventions import not_found
from sbam_api.storage.base import StorageBackend
from sbam_api.workflows.repository import WorkflowRepository

_MARKER: Final = re.compile(r"^[ \t]*# (>>>|<<<) LLM_BODY:\w+[ \t]*$")


def _findings(row: CodeAttempt, path: str | None = None) -> list[CodeFinding]:
    out: list[CodeFinding] = []
    for raw in row.findings or []:
        if path is not None and raw.get("file") != path:
            continue
        line = raw.get("line")
        out.append(
            CodeFinding(
                gate=str(raw.get("gate", "")),
                code=str(raw.get("code", raw.get("gate", ""))),
                file=raw.get("file"),
                line=line if isinstance(line, int) and line >= 1 else None,
                col=raw.get("col") if isinstance(raw.get("col"), int) else None,
                message=str(raw.get("message", "")),
            )
        )
    return out


def _summary(row: CodeAttempt) -> CodeAttemptSummary:
    return CodeAttemptSummary(
        build_no=row.build_no,
        cycle=row.cycle,
        attempt=row.attempt,
        status=row.status,
        template_name=row.template_name,
        template_version=row.template_version,
        correction_reason=row.correction_reason,
        files=[CodeFileRef(path=p, sha256=h) for p, h in sorted((row.files or {}).items())],
        findings=_findings(row),
    )


def _rows(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    build_no: int | None,
    cycle: int | None,
) -> list[CodeAttempt]:
    ctx = user.context(project_id)
    if WorkflowRepository(session, ctx).get(workflow_id) is None:
        raise not_found()
    return CodeAttemptRepository(session, ctx).of_sub_skill(
        workflow_id, sub_skill_id, build_no, cycle
    )


def attempts(
    session: Session,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    build_no: int | None,
    cycle: int | None,
) -> CodeAttemptList:
    rows = _rows(session, user, project_id, workflow_id, sub_skill_id, build_no, cycle)
    return CodeAttemptList(items=[_summary(r) for r in rows])


def _pick(rows: list[CodeAttempt], n: int) -> CodeAttempt:
    for row in rows:
        if row.attempt == n:
            return row
    raise not_found()


def _read_file(storage: StorageBackend, row: CodeAttempt, path: str) -> bytes:
    """The bytes of `path` in the attempt's bundle; 404 unless the stored file map lists it."""
    files = row.files or {}
    if not row.bundle_key or path not in files:
        raise not_found()
    try:
        data = storage.get(row.bundle_key)
    except FileNotFoundError:
        raise not_found() from None
    with tarfile.open(fileobj=io.BytesIO(data), mode="r:") as tar:
        try:
            member = tar.getmember(path)
        except KeyError:
            raise not_found() from None
        handle = tar.extractfile(member) if member.isfile() else None
        if handle is None:
            raise not_found()
        raw = handle.read()
    if hashlib.sha256(raw).hexdigest() != files[path]:
        raise not_found()  # the stored bundle no longer matches its file map
    return raw


def slots_of(text: str) -> list[LineRange]:
    """1-based inclusive line ranges strictly between each `LLM_BODY` open and close marker."""
    out: list[LineRange] = []
    start: int | None = None
    for number, line in enumerate(text.split("\n"), start=1):
        match = _MARKER.match(line)
        if match is None:
            continue
        if match[1] == ">>>":
            start = number + 1
        elif start is not None:
            if number - 1 >= start:
                out.append(LineRange(start=start, end=number - 1))
            start = None
    return out


def file_view(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    n: int,
    path: str,
    build_no: int | None,
    cycle: int | None,
) -> CodeFileView:
    row = _pick(_rows(session, user, project_id, workflow_id, sub_skill_id, build_no, cycle), n)
    raw = _read_file(storage, row, path)
    text = raw[:MAX_VIEW_BYTES].decode("utf-8", errors="replace")
    return CodeFileView(
        path=path,
        sha256=(row.files or {})[path],
        content=text,
        truncated=len(raw) > MAX_VIEW_BYTES,
        slots=slots_of(text),
        findings=_findings(row, path),
    )


def diff(
    session: Session,
    storage: StorageBackend,
    user: CurrentUser,
    project_id: uuid.UUID,
    workflow_id: uuid.UUID,
    sub_skill_id: uuid.UUID,
    n: int,
    m: int,
    path: str,
    build_no: int | None,
    cycle: int | None,
) -> CodeDiff:
    rows = _rows(session, user, project_id, workflow_id, sub_skill_id, build_no, cycle)
    a, b = _pick(rows, n), _pick(rows, m)
    left = _read_file(storage, a, path).decode("utf-8", errors="replace")
    right = _read_file(storage, b, path).decode("utf-8", errors="replace")
    text = "".join(
        difflib.unified_diff(
            left.splitlines(keepends=True),
            right.splitlines(keepends=True),
            fromfile=f"attempt {n}/{path}",
            tofile=f"attempt {m}/{path}",
        )
    )
    encoded = text.encode()
    truncated = len(encoded) > MAX_DIFF_BYTES
    if truncated:
        text = encoded[:MAX_DIFF_BYTES].decode("utf-8", errors="ignore")
    return CodeDiff(
        from_attempt=n,
        to_attempt=m,
        path=path,
        changed=bool(text),
        unified=text,
        truncated=truncated,
    )
