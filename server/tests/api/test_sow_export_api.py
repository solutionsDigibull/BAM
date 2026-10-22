"""E6.4 GET P/sow/versions/{v}/export through the real app and auth chain. The Temporal client is
a fake whose workflow result runs the real `export_version` (or hangs, or is down)."""

import asyncio
import io
import re
import uuid
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import docx
import pytest
from sbam_api.sow import exports
from sbam_api.sow.models import SowExport, SowVersion
from sbam_contracts.sow_api import SowExportInput, SowExportResult
from sbam_workers.sow.export.activity import ExportDeps, export_version
from sqlalchemy import func, select, update
from temporalio.service import RPCError, RPCStatusCode
from test_auth import FDE_A2, TENANT_B, problem
from test_sow_api import Harness as SowHarness


class Handle:
    def __init__(self, client: "ExportClient", inp: SowExportInput) -> None:
        self.client, self.inp = client, inp

    async def result(self) -> SowExportResult:
        if self.client.mode == "hang":
            await asyncio.sleep(30)
        return await asyncio.to_thread(export_version, self.client.deps, self.inp)


class ExportClient:
    def __init__(self, deps: ExportDeps) -> None:
        self.deps = deps
        self.mode = "run"
        self.calls: list[dict[str, Any]] = []

    async def start_workflow(self, workflow: str, arg: Any, **kwargs: Any) -> Handle:
        if self.mode == "down":
            raise RPCError("connection refused", RPCStatusCode.UNAVAILABLE, b"")
        self.calls.append({"workflow": workflow, "arg": arg, **kwargs})
        return Handle(self, arg)


class Harness(SowHarness):
    def __init__(self, tmp_path: Path) -> None:
        super().__init__(tmp_path)
        SowExport.__table__.create(self.sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
        self.exporter = ExportClient(ExportDeps(self.sessions, self.storage))
        self.client.app.state.temporal = self.exporter  # type: ignore[attr-defined]

    def export(self, fmt: str = "md", version: int = 1, token: str | None = None) -> Any:
        url = f"/projects/{self.project}/sow/versions/{version}/export?format={fmt}"
        return self.call("GET", url, token or self.fde())

    def download(self, res: Any) -> bytes:
        assert res.status_code == 302, res.text
        got = self.client.get(res.headers["location"])
        assert got.status_code == 200
        return bytes(got.content)

    def exports(self) -> int:
        with self.sessions() as s:
            return int(s.scalar(select(func.count()).select_from(SowExport)) or 0)


@pytest.fixture
def h(tmp_path: Path) -> Iterator[Harness]:
    yield Harness(tmp_path)


def test_e6_4_t03_markdown_export_redirects_to_the_file(h: Harness) -> None:
    res = h.export("md")
    assert res.headers["location"].startswith("/api/v1/") or "://" in res.headers["location"]
    text = h.download(res).decode("utf-8")
    assert len(re.findall(r"^## ", text, re.M)) == 9
    [call] = h.exporter.calls
    assert call["workflow"] == "SowExport" and call["task_queue"] == "sbam-build"
    assert call["id"] == f"sow-export-{h.v1}-md"
    assert call["id_conflict_policy"].name == "USE_EXISTING"


def test_e6_4_t02_docx_export_opens_with_python_docx(h: Harness) -> None:
    document = docx.Document(io.BytesIO(h.download(h.export("docx"))))
    assert len([p for p in document.paragraphs if p.style and p.style.name == "Heading 1"]) == 9


def test_e6_4_t07_a_second_export_is_served_from_storage(h: Harness) -> None:
    first = h.download(h.export("md"))
    second = h.export("md")
    assert h.download(second) == first
    assert len(h.exporter.calls) == 1 and h.exports() == 1


def test_e6_4_t04_an_unknown_format_is_422_on_format(h: Harness) -> None:
    body = problem(h.export("html"), 422, "VALIDATION_ERROR")
    assert [e["path"] for e in body["errors"]] == ["format"]
    assert h.exporter.calls == []


def test_e6_4_t06_other_tenant_and_unassigned_fde_get_404(h: Harness) -> None:
    other = h.realm.token(uuid.uuid4(), tenant_id=[str(TENANT_B)])
    for token in (other, h.realm.token(FDE_A2)):
        problem(h.export("pdf", token=token), 404, "NOT_FOUND")
    problem(h.export("md", version=9), 404, "NOT_FOUND")
    assert h.exporter.calls == []


def test_e6_4_a_failed_generation_is_409(h: Harness) -> None:
    with h.sessions() as s:
        s.execute(update(SowVersion).values(state="GENERATION_FAILED"))
        s.commit()
    problem(h.export("md"), 409, "CONFLICT")
    assert h.exporter.calls == []


def test_e6_4_tampered_content_is_409(h: Harness) -> None:
    with h.sessions() as s:
        s.execute(update(SowVersion).values(sha256="0" * 64))
        s.commit()
    problem(h.export("md"), 409, "HASH_MISMATCH")


def test_e6_4_a_render_over_the_wait_is_503_with_retry_after(
    h: Harness, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(exports, "EXPORT_WAIT_SECONDS", 0.05)
    h.exporter.mode = "hang"
    res = h.export("md")
    problem(res, 503, "EXPORT_TIMEOUT")
    assert res.headers["Retry-After"] == "30"


def test_e6_4_temporal_down_is_503(h: Harness) -> None:
    h.exporter.mode = "down"
    problem(h.export("md"), 503, "TEMPORAL_UNAVAILABLE")
