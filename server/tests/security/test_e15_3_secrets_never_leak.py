"""E15.3 update 2026-11-18 (#137, ADR 0030): a secret value never appears in logs, traces,
manifests or the export.

The WG-18 Commit harness (tests/api/test_wg18_commit_api.py) with WG-16's connections: a Workflow
whose Sub-Skill names `LEDGER_API_KEY`; the FDE stores a known value through the connections
route, tests it, and the whole Commit (recheck, freeze, register, start Staging) runs. Then the
value is looked for in:

* every log record, with all of its structured fields (DEBUG and up, API and worker loggers);
* every span the process tracer exported (names, attributes, events);
* every Workflow manifest row (the harness keeps no Sub-Skill manifests), the registry entry,
  the audit log and the Staging start request.

Control: the one sanctioned reader (`connections.inject.resolve_env`, used at container start)
still returns the value, so its absence everywhere else is not because nothing was stored.

The Workflow export package (#133) is checked the same way: the secret is stored for the exported
Workflow, a frozen version is exported, and neither the zip bytes nor any decompressed entry holds
it; the package's `.env.example` names the variable with no value.
"""

import io
import json
import logging
import sys
import uuid
import zipfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import pytest
from opentelemetry.sdk.trace.export import SimpleSpanProcessor
from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter
from sqlalchemy import select

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "api"))

import test_e14_5_export_api as e145
import test_wg16_connections_api as wg16
import test_wg18_commit_api as wg18
from sbam_api.audit.models import AuditLog
from sbam_api.connections.crypto import KeyRing
from sbam_api.connections.inject import resolve_env
from sbam_api.core.telemetry import tracer_provider
from sbam_api.manifests.models import WorkflowManifestRow
from sbam_api.workflow_registry.models import RegisteredWorkflowRow
from sbam_workers.exports import activities as export_activities
from test_project_workspace import Harness
from test_wg12_workflow_registry import TOOLS, FakeBifrost

h = wg18.h  # the Commit harness (connections included)
client = wg18.client  # the fake Temporal client the export route starts its run on
SECRET = wg16.SECRET  # a fixed test value; it is never a real key
LEDGER = wg16.LEDGER


@pytest.fixture(scope="module")
def spans() -> InMemorySpanExporter:
    exporter = InMemorySpanExporter()
    return exporter


@pytest.fixture
def traced(h: Harness, spans: InMemorySpanExporter) -> Iterator[InMemorySpanExporter]:
    provider = tracer_provider(h.client.app.state.settings)  # type: ignore[attr-defined]
    processor = SimpleSpanProcessor(spans)
    provider.add_span_processor(processor)
    spans.clear()
    yield spans
    processor.shutdown()


def rows_json(h: Harness, model: type[Any], *columns: str) -> str:
    with h.sessions() as s:
        found = s.scalars(select(model)).all()
        return json.dumps([[getattr(r, c) for c in columns] for r in found], default=str)


def committed_with_a_secret(h: Harness) -> wg18.FakeRuntime:
    """The FDE sets and tests LEDGER_API_KEY, then the whole Commit runs."""
    wg18.seed(h, LEDGER)
    res = wg16.put_value(h, LEDGER, SECRET)
    assert res.status_code in (200, 201), res.text
    assert wg16.set_test_target(h, LEDGER).status_code == 200
    tested = h.call("POST", f"{wg16.base(h)}/{LEDGER}/test", h.fde())
    assert tested.json()["outcome"] == "OK", tested.text
    sha = wg18.certify(h, recorded=True, imaged=True)
    runtime = wg18.FakeRuntime()
    result = wg18.run_all(wg18.steps(h, FakeBifrost(TOOLS), runtime), wg18.commit_input(h, sha))
    assert result.registry_name.endswith("_stg")
    return runtime


def test_e15_3_a_secret_is_never_in_logs_traces_manifests_or_the_registry(
    h: Harness, traced: InMemorySpanExporter, caplog: pytest.LogCaptureFixture
) -> None:
    caplog.set_level(logging.DEBUG)

    runtime = committed_with_a_secret(h)

    # control: the value is stored and the container-start path can still read it
    keyring = KeyRing.parse(wg16.KEYS, allow_placeholder=True)
    with h.sessions() as s:
        ctx = wg18.ctx(h)
        env = resolve_env(
            s, ctx, wg16.WORKFLOW, keyring=keyring, actor_id="t", purpose="t", run_id=uuid.uuid4()
        )
        s.rollback()  # the control's own audit row is not part of what is checked
    assert env[LEDGER].get_secret_value() == SECRET

    logs = "\n".join(json.dumps(r.__dict__, default=str) for r in caplog.records)
    exported = traced.get_finished_spans()
    trace_dump = json.dumps(
        [
            [
                s.name,
                dict(s.attributes or {}),
                [(e.name, dict(e.attributes or {})) for e in s.events],
            ]
            for s in exported
        ],
        default=str,
    )
    places = {
        "logs": logs,
        "traces": trace_dump,
        "workflow manifests": rows_json(h, WorkflowManifestRow, "manifest", "sha256"),
        "registry": rows_json(h, RegisteredWorkflowRow, "registry_name", "tools", "owner"),
        "audit": rows_json(h, AuditLog, "action", "before", "after"),
        "staging start": json.dumps([r.model_dump(mode="json") for r in runtime.requests]),
    }
    assert caplog.records and exported, "nothing was captured: the check would be vacuous"
    assert rows_json(h, WorkflowManifestRow, "id") != "[]"
    assert [r.secret_names for r in runtime.requests] == [[LEDGER]]  # names travel, values don't
    leaked = sorted(where for where, text in places.items() if SECRET in text)
    assert leaked == []


def test_e15_3_a_secret_is_never_in_the_export_package(
    h: Harness, client: wg18.FakeClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(export_activities, "notify_project", lambda *_, **__: 1)
    e145.frozen(h)
    wg16.put_value(h, LEDGER, SECRET)
    keyring = KeyRing.parse(wg16.KEYS, allow_placeholder=True)
    with h.sessions() as s:  # control: the value is stored and readable where it is meant to be
        env = resolve_env(
            s,
            wg18.ctx(h),
            wg16.WORKFLOW,
            keyring=keyring,
            actor_id="t",
            purpose="t",
            run_id=uuid.uuid4(),
        )
        s.rollback()
    assert env[LEDGER].get_secret_value() == SECRET
    accepted = e145.post(h)
    assert accepted.status_code == 202, accepted.text
    result = e145.run(h, client)
    raw = wg18.storage(h).get(result.storage_key)
    archive = zipfile.ZipFile(io.BytesIO(raw))
    assert SECRET.encode() not in raw
    leaked = [name for name in archive.namelist() if SECRET.encode() in archive.read(name)]
    assert leaked == []
    env_example = archive.read(".env.example").decode()
    assert f"{LEDGER}=" in env_example.splitlines()  # the name, never the value
