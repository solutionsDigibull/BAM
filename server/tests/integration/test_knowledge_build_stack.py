"""#371: a knowledge build runs end to end on the Compose stack.

`worker-knowledge` exports `masters.jsonl` and stores the OKF bundle through `StorageBackend`, so
its storage mount must be writable. With a read-only mount every build failed with `[Errno 30]
Read-only file system`; no test ran a real build, so nobody saw it.

A document goes in as in test_parse_stack.py, then `KnowledgeRebuild` (what POST P/knowledge/rebuild
starts) runs on the real worker, Cognee and Bifrost. It needs Cognee's two Bifrost aliases like the
other live Cognee tests, and skips without them. With the mock provider no money is spent.
"""

import asyncio
import time
import uuid
from collections.abc import Iterator

import pytest
from sbam_api.core.ids import uuid7
from sbam_api.ingest.models import Document, DocumentVersion, UploadReservation
from sbam_api.knowledge.models import KnowledgeSource, KnowledgeVersion
from sbam_api.notifications.models import Notification
from sbam_api.temporal.client import connect
from sbam_api.temporal.models import TemporalRun
from sbam_api.temporal.service import StartSpec, start_run
from sbam_contracts.knowledge import KNOWLEDGE_REBUILD, knowledge_workflow_id
from sbam_contracts.runs import RunInput, SubjectType, TaskQueue
from sqlalchemy import delete, select

from .conftest import Compose
from .test_cognee_adapter import cognee_aliases, drive  # noqa: F401 - the alias fixture skips
from .test_ocr_stack import WAIT, finished
from .test_scan_stack import upload
from .test_uploads_stack import TEMPORAL, Scope

pytestmark = pytest.mark.integration

BUILD_WAIT = 900  # seconds: Cognee's graph extraction runs as real model calls


@pytest.fixture
def scope() -> Iterator[Scope]:
    s = Scope()
    try:
        yield s
    finally:
        # The build's master snapshot is append-only and points at the project, so the tenant
        # and the project stay behind (as in the masters tests); everything else is removed.
        with s.sessions() as session:
            for model in (
                KnowledgeSource,
                KnowledgeVersion,
                Notification,
                TemporalRun,
                DocumentVersion,
                Document,
                UploadReservation,
            ):
                session.execute(delete(model).where(model.tenant_id == s.tenant))
            session.commit()


def version_of(scope: Scope) -> KnowledgeVersion | None:
    with scope.sessions() as session:
        return session.scalars(
            select(KnowledgeVersion).where(KnowledgeVersion.project_id == scope.project)
        ).first()


async def start_build(scope: Scope) -> None:
    client = await connect(TEMPORAL, "sbam")
    spec = StartSpec(
        workflow=KNOWLEDGE_REBUILD,
        workflow_id=knowledge_workflow_id(scope.project),
        queue=TaskQueue.KNOWLEDGE,
        subject_type=SubjectType.PROJECT,
        subject_id=scope.project,
        stage="knowledge",
        input=RunInput(
            tenant_id=scope.tenant,
            project_id=scope.project,
            run_id=uuid7(),
            correlation_id=str(uuid7()),
        ),
    )
    await start_run(client, scope.sessions, spec, with_search_attributes=False)


@pytest.mark.usefixtures("cognee_aliases")
def test_e5_371_a_knowledge_build_reaches_ready_and_stores_the_okf_bundle(
    scope: Scope, compose: Compose
) -> None:
    document: DocumentVersion = finished(
        scope, upload(scope, "rfq.txt", b"RFQ ZEBRA-7731: 500 PCBA from supplier Mouser."), WAIT
    )
    assert document.state == "PARSED", (document.state, document.parse_code)
    try:
        asyncio.run(start_build(scope))
        deadline = time.monotonic() + BUILD_WAIT
        while time.monotonic() < deadline:
            built = version_of(scope)
            if built is not None and built.state in ("READY", "FAILED"):
                break
            time.sleep(5)
        built = version_of(scope)
        assert built is not None
        # the read-only mount showed up here as FAILED with an OSError behind it
        assert built.state == "READY", (built.state, built.error_reason)
        assert built.master_snapshot_id is not None  # masters.jsonl was exported
        assert built.okf_bundle_key, "the OKF bundle was not stored"
        stored = compose(
            "exec", "-T", "worker-knowledge", "test", "-f", f"/data/storage/{built.okf_bundle_key}"
        )
        assert stored.returncode == 0, "the bundle is not on the storage volume"
    finally:
        drive(compose, "delete", str(scope.tenant), str(scope.project))


def test_e5_371_worker_knowledge_can_write_to_the_storage_volume(compose: Compose) -> None:
    """The cheap check behind the build: the mount is read-write (no model, no Cognee call)."""
    probe = f"/data/storage/.tmp/e5-371-{uuid.uuid4().hex}"
    wrote = compose("exec", "-T", "worker-knowledge", "sh", "-c", f"touch {probe} && rm {probe}")
    if "is not running" in wrote.stderr or "No such service" in wrote.stderr:
        pytest.skip("worker-knowledge is not running")
    assert wrote.returncode == 0, wrote.stderr[-500:]
