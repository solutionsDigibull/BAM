"""E4.2 against the Compose stack: real tusd with its hooks, the real API, Postgres and Temporal.

The FDE login is not part of this test: the upload slot and its signed token are written the way
`POST /projects/{id}/uploads` writes them, then everything after that is real: tusd's creation and
patch requests, its hook scripts, the API's hook endpoint, storage, and the Temporal start.
"""

import asyncio
import base64
import hashlib
import os
import time
import urllib.error
import urllib.request
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from typing import Any

import pytest
from sbam_api.core.ids import uuid7
from sbam_api.db.session import make_engine, make_sessionmaker
from sbam_api.identity.models import Tenant
from sbam_api.ingest import tokens
from sbam_api.ingest.models import Document, DocumentVersion, UploadReservation
from sbam_api.projects.models import Project
from sbam_api.temporal.client import connect
from sbam_api.temporal.models import TemporalRun
from sqlalchemy import delete, select
from sqlalchemy.orm import Session, sessionmaker

from .conftest import Compose, port
from .test_db import url
from .test_profiles import env_value

pytestmark = pytest.mark.integration

TUSD = f"http://127.0.0.1:{port('TUSD_PORT', 1080)}/files/"
TEMPORAL = f"127.0.0.1:{port('TEMPORAL_PORT', 7233)}"
THREE_MB = 3 * 1024 * 1024


class Scope:
    """One throwaway tenant and project, removed afterwards."""

    def __init__(self) -> None:
        self.tenant, self.project, self.user = uuid7(), uuid7(), uuid7()
        self.secret = env_value("TUSD_HOOK_SECRET")
        self.sessions: sessionmaker[Session] = make_sessionmaker(make_engine(url()))
        with self.sessions() as s:
            s.add(Tenant(id=self.tenant, name=f"e4-2-{self.tenant}"))
            s.flush()
            s.add(
                Project(
                    id=self.project,
                    tenant_id=self.tenant,
                    name="uploads",
                    state="IDLE",
                    status="ACTIVE",
                )
            )
            s.commit()

    def slot(self, name: str, size: int) -> str:
        """An announced file and the token for it (what the token endpoint would return)."""
        batch = uuid7()
        with self.sessions() as s:
            s.add(
                UploadReservation(
                    tenant_id=self.tenant,
                    project_id=self.project,
                    batch_id=batch,
                    user_id=self.user,
                    name=name,
                    size=size,
                    mime="application/pdf",
                    status="RESERVED",
                    expires_at=datetime.now(UTC) + timedelta(minutes=15),
                )
            )
            s.commit()
        claims = tokens.UploadClaims(
            self.tenant, self.project, self.user, batch, int(time.time()) + 900
        )
        return tokens.issue(self.secret, claims)

    def versions(self) -> list[DocumentVersion]:
        with self.sessions() as s:
            return list(
                s.scalars(select(DocumentVersion).where(DocumentVersion.project_id == self.project))
            )

    def wait_for_versions(self, count: int, seconds: int = 60) -> list[DocumentVersion]:
        deadline = time.monotonic() + seconds
        while len(found := self.versions()) < count and time.monotonic() < deadline:
            time.sleep(1)
        return found

    def drop(self) -> None:
        with self.sessions() as s:
            for model in (TemporalRun, DocumentVersion, Document, UploadReservation, Project):
                s.execute(delete(model).where(model.tenant_id == self.tenant))
            s.execute(delete(Tenant).where(Tenant.id == self.tenant))
            s.commit()


@pytest.fixture
def scope() -> Iterator[Scope]:
    s = Scope()
    try:
        yield s
    finally:
        s.drop()


def _b64(text: str) -> str:
    return base64.b64encode(text.encode()).decode()


def tus(
    method: str, url_: str, headers: dict[str, str] | None = None, data: bytes | None = None
) -> tuple[int, dict[str, str], bytes]:
    request = urllib.request.Request(  # noqa: S310 - the stack's own http://127.0.0.1 tusd
        url_, data=data, method=method, headers={"Tus-Resumable": "1.0.0", **(headers or {})}
    )
    try:
        with urllib.request.urlopen(request, timeout=60) as res:  # noqa: S310
            return res.status, dict(res.headers), res.read()
    except urllib.error.HTTPError as err:
        return err.code, dict(err.headers), err.read()


def create(scope: Scope, token: str, name: str, size: int, project: uuid.UUID | None = None) -> Any:
    pid = _b64(str(project or scope.project))
    meta = f"token {_b64(token)},filename {_b64(name)},project_id {pid}"
    return tus("POST", TUSD, {"Upload-Length": str(size), "Upload-Metadata": meta})


def patch(location: str, offset: int, chunk: bytes) -> int:
    headers = {"Content-Type": "application/offset+octet-stream", "Upload-Offset": str(offset)}
    return tus("PATCH", location, headers, chunk)[0]


def upload(scope: Scope, name: str, data: bytes) -> None:
    token = scope.slot(name, len(data))
    status, headers, _ = create(scope, token, name, len(data))
    assert status == 201, headers
    assert patch(headers["Location"], 0, data) == 204


def test_e4_2_t06_upload_resumes_after_a_dropped_connection(scope: Scope) -> None:
    # readable text: random bytes would fail the type check, which is not what this tests
    line = b"Quote line with the parts and the prices, 0123456789\n"
    data = (line * (THREE_MB // len(line) + 1))[:THREE_MB]
    token = scope.slot("resume.txt", len(data))
    status, headers, _ = create(scope, token, "resume.txt", len(data))
    assert status == 201
    location, cut = headers["Location"], int(len(data) * 0.6)
    assert patch(location, 0, data[:cut]) == 204  # the connection "drops" at 60%
    assert scope.versions() == []  # nothing is registered before the upload is complete
    _, head, _ = tus("HEAD", location)
    offset = int(head["Upload-Offset"])
    assert offset == cut
    assert patch(location, offset, data[offset:]) == 204
    (version,) = scope.wait_for_versions(1)
    assert version.state in ("UPLOADED", "SCANNING", "PARSING", "PARSED")  # may be done already
    assert version.sha256 == hashlib.sha256(data).hexdigest()  # single version, matching hash
    assert str(version.id) == location.rsplit("/", 1)[1]


def test_e4_2_t08_token_of_another_project_is_refused_by_tusd(scope: Scope) -> None:
    token = scope.slot("a.pdf", 1024)
    status, _, body = create(scope, token, "a.pdf", 1024, project=uuid7())
    assert status == 403 and b'"code": "FORBIDDEN"' in body
    time.sleep(2)
    assert scope.versions() == []


def test_e4_2_t07_forged_token_is_refused_by_tusd(scope: Scope) -> None:
    token = scope.slot("a.pdf", 1024)
    status, _, _ = create(scope, token[:-3] + "AAA", "a.pdf", 1024)
    assert status == 403


def test_e4_2_tusd_refuses_over_15_mb_and_serves_no_downloads(scope: Scope) -> None:
    token = scope.slot("big.pdf", 15_728_641)
    assert create(scope, token, "big.pdf", 15_728_641)[0] == 413
    data = b"%PDF" + os.urandom(1024)
    token = scope.slot("ok.pdf", len(data))
    _, headers, _ = create(scope, token, "ok.pdf", len(data))
    assert patch(headers["Location"], 0, data) == 204
    scope.wait_for_versions(1)  # let post-finish finish before the fixture removes the rows
    assert tus("GET", headers["Location"])[0] in (404, 405)  # -disable-download
    assert tus("DELETE", headers["Location"])[0] in (404, 405)  # -disable-termination


def test_e4_2_t10_temporal_down_keeps_the_upload_and_ingestion_starts_later(
    scope: Scope, compose: Compose
) -> None:
    first, second = os.urandom(2048), os.urandom(2048)
    assert compose("stop", "temporal").returncode == 0
    try:
        upload(scope, "first.pdf", first)
        (version,) = scope.wait_for_versions(1)
        assert version.state == "UPLOADED" and version.ingestion_started_at is None
    finally:
        compose("start", "temporal")
        deadline = time.monotonic() + 180  # until the API sees Temporal again
        while time.monotonic() < deadline and not _temporal_ok():
            time.sleep(3)
    upload(scope, "second.pdf", second)  # the next upload starts the waiting version as well
    versions = scope.wait_for_versions(2)
    deadline = time.monotonic() + 60
    while any(v.ingestion_started_at is None for v in versions) and time.monotonic() < deadline:
        time.sleep(1)
        versions = scope.versions()
    assert all(v.ingestion_started_at is not None for v in versions)
    assert asyncio.run(_executions(versions)) == {f"ingest-{v.id}" for v in versions}


def _temporal_ok() -> bool:
    try:
        asyncio.run(connect(TEMPORAL, "sbam"))
    except RuntimeError:
        return False
    return True


async def _executions(versions: list[DocumentVersion]) -> set[str]:
    client = await connect(TEMPORAL, "sbam")
    found = set()
    for v in versions:
        description = await client.get_workflow_handle(f"ingest-{v.id}").describe()
        found.add(description.id)
    return found
