"""WG-20 (#768): the `build_workflow_ui` stage, the `ui-copy` prompt and the UI_BUNDLE evidence.

The store is in memory and storage is a temp directory; the model is a fake that returns a canned
`UiCopy` or raises what `LlmClient` would raise. No model is ever called. The evidence test reads
the stored bundle back the way the sandbox harness receives it and runs the existing UI_BUNDLE check
from #857 on its digest.
"""

import asyncio
import hashlib
import io
import tarfile
import uuid
from collections.abc import Mapping
from pathlib import Path
from typing import Any

import pytest
from sbam_api.builds.workflow_ui import WorkflowUiRecord, packaged_digest, packaged_files
from sbam_api.manifests import ui_bundle as rules
from sbam_api.manifests.repository import GateResultRepository
from sbam_api.storage.base import StorageBackend, StoredObject
from sbam_api.storage.local import LocalDiskBackend
from sbam_contracts.build_dashboard import GateName, GateStatus
from sbam_contracts.manifest import SkillManifest, WorkflowManifest
from sbam_contracts.pipeline import WorkflowRef
from sbam_workers.build.ui_bundle import SubCopy, UiBundleError, UiCopy, load_ui_template
from sbam_workers.build.ui_stage import (
    ALIAS,
    PROMPT,
    UI_GATE_FAILED,
    LlmCopier,
    UiInputs,
    build_for,
)
from sbam_workers.llm.budget import BudgetExceeded
from sbam_workers.llm.client import LlmResult, LlmTransportError
from sbam_workers.llm.reask import SchemaInvalidAfterRetries
from sbam_workers.llm.registry import PromptRegistry
from test_manifest import workflow
from test_wg12_workflow_registry import CTX, WORKFLOW_ID, World, server_entry
from test_wg20_ui_bundle import SERVER, ms01, quote_workflow, sealed

SKILL = "MS01.1_Investor_Auth_Manager"
TENANT, PROJECT = uuid.uuid4(), uuid.uuid4()


class MemoryUiStore:
    def __init__(self, storage: StorageBackend, inputs: UiInputs | UiBundleError) -> None:
        self.storage = storage
        self.inputs = inputs
        self.saved: list[WorkflowUiRecord] = []
        self.failures: list[UiBundleError] = []

    def load(self, inp: WorkflowRef) -> UiInputs:
        if isinstance(self.inputs, UiBundleError):
            raise self.inputs
        return self.inputs

    def recorded(self, inp: WorkflowRef, digest: str) -> uuid.UUID | None:
        for n, row in enumerate(self.saved):
            if row.digest == digest:
                return uuid.UUID(int=n + 1)
        return None

    def save(self, inp: WorkflowRef, record: WorkflowUiRecord, stored: StoredObject) -> uuid.UUID:
        self.saved.append(record)
        return uuid.UUID(int=len(self.saved))

    def failed(self, inp: WorkflowRef, error: UiBundleError) -> uuid.UUID:
        self.failures.append(error)
        return uuid.uuid4()


class FakeCopier:
    def __init__(self, reply: UiCopy | None) -> None:
        self.reply = reply
        self.calls: list[Mapping[str, str]] = []

    async def __call__(
        self, inp: WorkflowRef, context: Mapping[str, str], temporal_workflow_id: str
    ) -> UiCopy | None:
        self.calls.append(context)
        return self.reply


def ref() -> WorkflowRef:
    return WorkflowRef(
        tenant_id=TENANT,
        project_id=PROJECT,
        run_id=uuid.uuid4(),
        correlation_id="cid-wg20",
        workflow_id=WORKFLOW_ID,
    )


def inputs(skills: list[SkillManifest] | None = None) -> UiInputs:
    return UiInputs("Investor Onboarding", SERVER, skills or [sealed(ms01())])


def run(store: MemoryUiStore, copier: FakeCopier) -> Any:
    return asyncio.run(build_for(store, copier, ref()))


# --- the stage -----------------------------------------------------------------------------------


def test_wg20_stage_stores_the_bundle_hashed_and_a_retry_is_the_same_row(tmp_path: Path) -> None:
    store = MemoryUiStore(LocalDiskBackend(tmp_path), inputs())
    copier = FakeCopier(None)
    first, again = run(store, copier), run(store, copier)
    assert first == again and len(store.saved) == 1  # same bytes: no second row
    [record] = store.saved
    assert record.copy_source == "manifest" and record.entry.workflow_server == SERVER
    assert {"index.html", "src/App.tsx", "src/workflow.generated.ts"} <= set(record.files)
    files = packaged_files(store.storage, record)
    assert files is not None and {p: hashlib.sha256(d).hexdigest() for p, d in files.items()} == (
        record.files
    )
    assert packaged_digest(store.storage, record) == record.entry.digest


def test_wg20_stage_uses_the_models_text_and_drops_what_it_may_not_say(tmp_path: Path) -> None:
    reply = UiCopy(
        sub_skills={
            SKILL: SubCopy(
                summary="Check the investor's one-time code.",
                field_help={
                    "mobile_number": "Open https://evil.example.net/x",
                    "otp_payload": "6 digits",
                },
            ),
            "MS09.9_Invented": SubCopy(summary="Not a skill of this workflow."),
        },
        review_notes={"MS01_Investor_Registration_Access": "Confirm <b>the</b> login result."},
    )
    store = MemoryUiStore(LocalDiskBackend(tmp_path), inputs())
    run(store, FakeCopier(reply))
    [record] = store.saved
    assert record.copy_source == "model"
    files = packaged_files(store.storage, record)
    assert files is not None
    text = files["src/workflow.generated.ts"].decode()
    assert "Check the investor's one-time code." in text and "6 digits" in text
    assert "evil.example.net" not in text and "Invented" not in text and "<b>" not in text


def test_wg20_stage_asks_the_model_with_ids_and_names_only(tmp_path: Path) -> None:
    store = MemoryUiStore(LocalDiskBackend(tmp_path), inputs(quote_workflow()))
    copier = FakeCopier(None)
    run(store, copier)
    [context] = copier.calls
    assert list(context) == ["workflow"]
    assert "MS02.1_Supplier_Pick" in context["workflow"] and "bom_file" in context["workflow"]


def test_wg20_stage_records_a_workflow_it_cannot_build_and_goes_on(tmp_path: Path) -> None:
    for error in (
        UiBundleError("SERVER_NOT_BUILT", "the Workflow's server is not stored yet"),
        UiBundleError("SKILL_NOT_BUILT", "MS01.1 has no manifest"),
    ):
        store = MemoryUiStore(LocalDiskBackend(tmp_path), error)
        run(store, FakeCopier(None))
        assert store.failures == [error] and store.saved == []


def test_wg20_stage_records_a_bundle_that_breaks_the_pattern(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from sbam_workers.build import ui_stage

    broken = lambda files, template, hosts: type(  # noqa: E731
        "V", (), {"passed": False, "findings": [{"code": "UI_HOST_UNDECLARED"}]}
    )()
    monkeypatch.setattr(ui_stage, "check_ui_bundle_files", broken)
    store = MemoryUiStore(LocalDiskBackend(tmp_path), inputs())
    run(store, FakeCopier(None))
    assert [f.code for f in store.failures] == [UI_GATE_FAILED] and store.saved == []


# --- the model call ------------------------------------------------------------------------------


class FakeLlm:
    def __init__(self, reply: UiCopy | Exception) -> None:
        self.reply = reply
        self.calls: list[tuple[str, str]] = []

    async def run(
        self,
        alias: str,
        prompt_id: str,
        output_type: type[UiCopy],
        context: Any,
        scope: Any,
        **kw: Any,
    ) -> Any:
        self.calls.append((alias, prompt_id))
        assert kw["validate"](UiCopy(review_notes={"MS99_Nope": "x"})) == (
            "UNKNOWN_SKILL:MS99_Nope",
        )
        if isinstance(self.reply, Exception):
            raise self.reply
        return LlmResult(self.reply, uuid.uuid4(), "trace", 1)


def ask(llm: FakeLlm) -> UiCopy | None:
    context = {
        "workflow": '{"skills": [{"id": "MS01_A", "sub_skills": [{"skill_id": "MS01.1_A"}]}]}'
    }
    return asyncio.run(LlmCopier(llm)(ref(), context, "wf-1"))  # type: ignore[arg-type]


def test_wg20_copier_asks_the_pinned_prompt_through_the_alias() -> None:
    llm = FakeLlm(UiCopy())
    assert ask(llm) == UiCopy() and llm.calls == [(ALIAS, PROMPT)]
    prompt = PromptRegistry().get(PROMPT)
    assert prompt.alias == ALIAS == "sbam-codegen" and "three panes" in prompt.text.lower()


def test_wg20_copier_falls_back_when_the_model_cannot_answer_in_the_schema() -> None:
    assert ask(FakeLlm(SchemaInvalidAfterRetries(()))) is None


def test_wg20_copier_lets_a_budget_or_transport_failure_through() -> None:
    with pytest.raises(BudgetExceeded):
        ask(FakeLlm(BudgetExceeded("over")))
    with pytest.raises(LlmTransportError):
        ask(FakeLlm(LlmTransportError("down")))


# --- UI_BUNDLE evidence (the check from #857 run the way the sandbox harness does) ----------


def manifest_with(record: WorkflowUiRecord) -> WorkflowManifest:
    served = WorkflowManifest.sealed(workflow())
    from sbam_workers.build.workflow_server import with_server

    return WorkflowManifest.sealed(
        {**with_server(served, server_entry()).dump(), "ui_bundle": record.entry.dump()}
    )


def test_wg20_the_stored_bundle_earns_a_passed_ui_bundle_row(tmp_path: Path) -> None:
    store = MemoryUiStore(LocalDiskBackend(tmp_path), inputs())
    run(store, FakeCopier(None))
    [record] = store.saved
    manifest = manifest_with(record)
    assert manifest.requires_ui_bundle and manifest.ui_bundle == record.entry
    world = World()
    with world.sessions() as session:
        found = packaged_digest(store.storage, record)
        row = rules.record_ui_bundle_gate(session, CTX, WORKFLOW_ID, manifest, found)
        session.commit()
        assert row.status == GateStatus.PASSED.value and row.gate == GateName.UI_BUNDLE.value
        rows = GateResultRepository(session, CTX).workflow_rows(WORKFLOW_ID, "UI_BUNDLE")
        assert rules.certification_reasons(manifest, rows) == []


class Tampered(LocalDiskBackend):
    """Storage whose tar holds one changed file, as a bundle altered after it was recorded."""

    def get(self, key: str, *, verify: bool = False) -> bytes:
        files = {}
        with tarfile.open(fileobj=io.BytesIO(super().get(key, verify=verify)), mode="r:") as tar:
            for member in tar.getmembers():
                handle = tar.extractfile(member)
                assert handle is not None
                files[member.name] = handle.read()
        files["src/App.tsx"] += b"\n// tampered\n"
        buffer = io.BytesIO()
        with tarfile.open(fileobj=buffer, mode="w") as tar:
            for name, data in sorted(files.items()):
                info = tarfile.TarInfo(name)
                info.size = len(data)
                tar.addfile(info, io.BytesIO(data))
        return buffer.getvalue()


def test_wg20_a_bundle_changed_in_storage_fails_the_evidence(tmp_path: Path) -> None:
    store = MemoryUiStore(LocalDiskBackend(tmp_path), inputs())
    run(store, FakeCopier(None))
    [record] = store.saved
    manifest = manifest_with(record)
    altered = Tampered(tmp_path)
    assert packaged_files(altered, record) is None  # the file no longer matches its recorded hash
    found = packaged_digest(altered, record)
    assert found is None
    verdict = rules.check_ui_bundle(manifest, found)
    assert verdict.status is GateStatus.FAILED
    assert verdict.findings[0]["code"] == "UI_BUNDLE_NOT_FOUND"
    world = World()
    with world.sessions() as session:
        row = rules.record_ui_bundle_gate(session, CTX, WORKFLOW_ID, manifest, found)
        session.commit()
        rows = GateResultRepository(session, CTX).workflow_rows(WORKFLOW_ID, "UI_BUNDLE")
        assert row.status == "FAILED"
        assert rules.certification_reasons(manifest, rows) == [rules.FAILED]


def test_wg20_the_template_the_stage_uses_is_the_released_one(tmp_path: Path) -> None:
    store = MemoryUiStore(LocalDiskBackend(tmp_path), inputs())
    run(store, FakeCopier(None))
    assert store.saved[0].template.sha256 == load_ui_template().sha256
