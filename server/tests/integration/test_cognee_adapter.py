"""E5.1 Cognee adapter against the real worker-knowledge (Cognee 1.6.2) and Bifrost.

A driver runs inside worker-knowledge (`docker compose exec`), so Cognee uses its real config,
the cognee_data volume and the internal network. Its scope guard allows the two test projects:
the database check (T03) is covered by the unit tests.

T02 (graph), T05, T06 and T07 need Cognee's two Bifrost aliases (`sbam-knowledge`, `sbam-embed`).
Until Bifrost and the mock serve them they skip, saying so; T04 and the dataset-level part of T02
need no LLM and always run. The aliases arrive with #259 (E5.7).
"""

import json
import urllib.error
import urllib.request
import uuid
from typing import Any

import pytest

from .conftest import Compose, port

pytestmark = pytest.mark.integration

DRIVER = r"""
import asyncio, json, os, sys, tempfile, uuid
from pathlib import Path
from sbam_api.storage.base import Kind, StorageScope
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.knowledge import ItemSource, KnowledgeItem, ProjectScope
from sbam_workers.knowledge.cognee_adapter import CogneeAdapter, dataset_name
from sbam_workers.knowledge.cognee_backend import CogneeLibrary

class Allow:
    def project_in_tenant(self, scope): return True
    def eligible_versions(self, scope, ids): return ids

def scope_of(tenant, project):
    return ProjectScope(tenant_id=uuid.UUID(tenant), project_id=uuid.UUID(project))

op = sys.argv[1]
scope = scope_of(sys.argv[2], sys.argv[3])
where = StorageScope(scope.tenant_id, scope.project_id)
storage = LocalDiskBackend(Path(tempfile.mkdtemp()))
lib = CogneeLibrary()
adapter = CogneeAdapter(lib, Allow(), storage, os.environ["COGNEE_USER_SECRET"])

async def main():
    out = {}
    try:
        if op == "ensure":
            out["dataset"] = await adapter.ensure_dataset(scope)
        elif op == "peek":  # this scope's user reads another project's dataset
            other = dataset_name(scope_of(sys.argv[4], sys.argv[5]))
            out["names"] = sorted(await lib.item_names(adapter._principal(scope), other))
        elif op == "build":
            obj = storage.put(sys.argv[4].encode(), where, Kind.DERIVED, "text/markdown")
            item = KnowledgeItem(
                source=ItemSource.DOCUMENT,
                source_id=uuid.uuid4(),
                sha256=obj.sha256,
                storage_key=obj.storage_key,
            )
            out["add"] = (await adapter.add(scope, [item])).model_dump()
            await adapter.cognify(scope)
        elif op == "search":
            out["hits"] = [h.text for h in await adapter.search(scope, sys.argv[4], 10)]
        elif op == "totals":
            page = await adapter.graph_page(scope, None, 500)
            out["totals"] = [page.total_nodes, page.total_edges]
            out["nodes"] = [json.dumps(n.model_dump(), default=str) for n in page.nodes]
        elif op == "cognify":
            await adapter.cognify(scope)
        elif op == "delete":
            await adapter.delete_dataset(scope)
    except Exception as exc:
        out["error"] = type(exc).__name__
    print("RESULT " + json.dumps(out))

asyncio.run(main())
"""


def drive(compose: Compose, *args: str, timeout: int = 600) -> dict[str, Any]:
    run = compose("exec", "-T", "worker-knowledge", "python", "-c", DRIVER, *args, timeout=timeout)
    if run.returncode != 0 and "RESULT " not in run.stdout:
        if "is not running" in run.stderr or "No such service" in run.stderr:
            pytest.skip("worker-knowledge is not running")
        pytest.fail(f"driver failed: {run.stderr[-2000:]}")
    line = next(x for x in run.stdout.splitlines() if x.startswith("RESULT "))
    return dict(json.loads(line.removeprefix("RESULT ")))


def ids() -> tuple[str, str]:
    return str(uuid.uuid4()), str(uuid.uuid4())


@pytest.fixture(scope="module")
def cognee_aliases() -> None:
    """Skip unless Bifrost answers an embedding request for Cognee's alias."""
    body = json.dumps({"model": "sbam-embed", "input": ["probe"]}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port('BIFROST_PORT', 8081)}/v1/embeddings",
        body,
        {"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30):  # noqa: S310 - fixed local URL
            return
    except (urllib.error.URLError, OSError) as exc:
        pytest.skip(f"Bifrost does not serve Cognee's aliases yet, #259 ({type(exc).__name__})")


def test_e5_1_t04_access_control_off_worker_refuses_to_start(compose: Compose) -> None:
    run = compose(
        "run", "--rm", "--no-deps", "-e", "ENABLE_BACKEND_ACCESS_CONTROL=false",
        "worker-knowledge", timeout=300,
    )  # fmt: skip
    assert run.returncode == 3, run.stderr[-1000:]
    assert "health FAILED COGNEE_ACL_DISABLED" in run.stderr


def test_e5_1_t02_project_user_cannot_open_another_projects_dataset(compose: Compose) -> None:
    a, b = ids(), ids()
    assert "dataset" in drive(compose, "ensure", *a)
    assert "dataset" in drive(compose, "ensure", *b)
    assert drive(compose, "peek", *b, *a) == {"error": "DatasetNotFound"}
    assert drive(compose, "peek", *a, *a) == {"names": []}
    drive(compose, "delete", *a)
    drive(compose, "delete", *b)


@pytest.mark.usefixtures("cognee_aliases")
def test_e5_1_t02_canary_of_project_a_is_never_visible_to_project_b(compose: Compose) -> None:
    a, b = ids(), ids()
    assert "error" not in drive(compose, "build", *a, "Supplier Mouser quoted ZEBRA-7731 for PCBA.")
    assert "error" not in drive(compose, "build", *b, "Supplier Arrow quoted cable harnesses.")
    assert any("ZEBRA-7731" in h for h in drive(compose, "search", *a, "ZEBRA-7731")["hits"])
    assert not any("ZEBRA-7731" in h for h in drive(compose, "search", *b, "ZEBRA-7731")["hits"])
    assert not any("ZEBRA-7731" in n for n in drive(compose, "totals", *b)["nodes"])
    drive(compose, "delete", *a)
    drive(compose, "delete", *b)


@pytest.mark.usefixtures("cognee_aliases")
def test_e5_1_t05_cognee_calls_reach_bifrost(compose: Compose) -> None:
    a = ids()
    since = compose("exec", "-T", "bifrost", "date", "-u", "+%Y-%m-%dT%H:%M:%SZ").stdout.strip()
    assert "error" not in drive(compose, "build", *a, "Routing probe for the gateway.")
    logs = compose("logs", "--since", since or "5m", "bifrost").stdout
    assert "sbam-knowledge" in logs or "sbam-embed" in logs
    assert "openrouter.ai" not in compose("exec", "-T", "worker-knowledge", "printenv").stdout
    drive(compose, "delete", *a)


@pytest.mark.usefixtures("cognee_aliases")
def test_e5_1_t06_graph_survives_a_worker_restart(compose: Compose) -> None:
    a = ids()
    assert "error" not in drive(compose, "build", *a, "Supplier Mouser quoted QUOKKA-4419.")
    before = drive(compose, "totals", *a)["totals"]
    assert before[0] > 0
    assert compose("restart", "worker-knowledge", timeout=300).returncode == 0
    assert drive(compose, "totals", *a)["totals"] == before
    assert any("QUOKKA-4419" in h for h in drive(compose, "search", *a, "QUOKKA-4419")["hits"])
    drive(compose, "delete", *a)


@pytest.mark.usefixtures("cognee_aliases")
def test_e5_1_t07_bifrost_down_is_retryable(compose: Compose) -> None:
    a = ids()
    assert "dataset" in drive(compose, "ensure", *a)
    assert compose("stop", "bifrost").returncode == 0
    try:
        assert drive(compose, "cognify", *a, timeout=900) == {"error": "CogneeUnavailable"}
    finally:
        compose("start", "bifrost")
    drive(compose, "delete", *a)


def test_e5_1_t05_worker_holds_no_openrouter_key(compose: Compose) -> None:
    env = compose("exec", "-T", "worker-knowledge", "printenv")
    if env.returncode != 0:
        pytest.skip("worker-knowledge is not running")
    assert "OPENROUTER" not in env.stdout
    assert "ENABLE_BACKEND_ACCESS_CONTROL=true" in env.stdout
    assert "LLM_ENDPOINT=http://bifrost:8080/v1" in env.stdout
