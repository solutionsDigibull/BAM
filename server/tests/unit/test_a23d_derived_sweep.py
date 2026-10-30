"""#533 (A-23d): the sweep deletes derived files no version or master document references, only
when they are old, only in `derived/`, and never one that is claimed while the sweep runs."""

import os
import uuid
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sbam_api.audit.models import AuditLog
from sbam_api.identity.models import Tenant
from sbam_api.ingest import derived_sweep
from sbam_api.ingest.derived_sweep import GRACE, sweep_derived_files
from sbam_api.ingest.repository import NO_PROJECT
from sbam_api.masters.models import MasterDocument
from sbam_api.projects.models import Project
from sbam_api.storage.base import Kind, ListedObject, StorageScope, StorageUnavailable
from sbam_api.storage.local import LocalDiskBackend
from sqlalchemy import select
from test_parsing import PROJECT, TENANT, Harness, h, root  # noqa: F401 - their fixtures

# the helpers below name their harness `w`: `h` is the imported fixture

NOW = datetime.now(UTC)
OLD = NOW - GRACE - timedelta(hours=1)


def scope(project: uuid.UUID = PROJECT) -> StorageScope:
    return StorageScope(TENANT, project)


def put(w: Harness, data: bytes, *, age: timedelta = timedelta(days=3), kind: Kind = Kind.DERIVED,
        project: uuid.UUID = PROJECT) -> str:  # fmt: skip
    stored = w.storage.put(data, scope(project), kind, "x/y")
    path = w.storage.root / stored.storage_key  # type: ignore[attr-defined]
    when = (NOW - age).timestamp()
    path.chmod(0o644)
    os.utime(path, (when, when))
    return stored.storage_key


def exists(w: Harness, key: str) -> bool:
    return w.storage.exists(key)


@pytest.fixture
def world(h: Harness) -> Harness:  # noqa: F811
    bind = h.sessions.kw["bind"]
    for table in (Tenant.__table__, Project.__table__):
        table.create(bind, checkfirst=True)  # type: ignore[attr-defined]
    with h.sessions() as s:
        s.add(Tenant(id=TENANT, name="T"))
        s.flush()
        s.add(Project(id=PROJECT, tenant_id=TENANT, name="P", status="ACTIVE", state="IDLE"))
        s.commit()
    return h


def run(w: Harness, now: datetime = NOW) -> derived_sweep.SweepResult:
    assert isinstance(w.storage, LocalDiskBackend)
    with w.sessions() as s:
        return sweep_derived_files(s, w.storage, now, "c-sweep")


def version_with(w: Harness, md: str | None, js: str | None, state: str = "PARSED") -> None:
    ref = w.add("a.md", b"x" * 10)
    with w.sessions() as s:
        row = w.version(ref)
        merged = s.merge(row)
        merged.derived_md_key, merged.derived_json_key, merged.state = md, js, state
        s.commit()


def test_a23d_t01_an_old_unreferenced_derived_file_is_deleted(world: Harness) -> None:
    orphan = put(world, b"orphan text")
    result = run(world)
    assert not exists(world, orphan)
    assert (result.deleted, result.failed) == (1, 0)


def test_a23d_t02_referenced_files_stay(world: Harness) -> None:
    md, js = put(world, b"md"), put(world, b"json")
    version_with(world, md, js)
    assert run(world).deleted == 0
    assert exists(world, md) and exists(world, js)


def test_a23d_t03_a_master_document_keeps_its_files(world: Harness) -> None:
    key = put(world, b"master md", project=NO_PROJECT)
    with world.sessions() as s:
        s.add(
            MasterDocument(
                tenant_id=TENANT, title="r", filetype_row_label="Filetype #12",
                intent_class="Policy / rules", master_type="RULES",
                route_source="DEFAULT_BY_FILETYPE", storage_key="k", sha256="0" * 64, size=1,
                mime="text/plain", derived_md_key=key,
            )
        )  # fmt: skip
        s.commit()
    assert run(world).deleted == 0 and exists(world, key)
    orphan = put(world, b"unreferenced master file", project=NO_PROJECT)
    assert run(world).deleted == 1 and not exists(world, orphan) and exists(world, key)


def test_a23d_t04_a_young_file_is_left_alone(world: Harness) -> None:
    young = put(world, b"just stored", age=timedelta(hours=1))
    result = run(world)
    assert exists(world, young) and (result.deleted, result.young) == (0, 1)
    assert run(world, NOW + GRACE).deleted == 1  # a day later it is old enough


def test_a23d_t05_only_derived_is_ever_touched(world: Harness) -> None:
    kept = [
        put(world, b"upload", kind=Kind.UPLOADS),
        put(world, b"okf", kind=Kind.OKF),
        put(world, b"code", kind=Kind.CODE),
        put(world, b"export", kind=Kind.EXPORTS),
    ]
    stray = world.storage.root / f"{TENANT}/{PROJECT}/derived/not-a-key.txt"  # type: ignore[attr-defined]
    stray.parent.mkdir(parents=True, exist_ok=True)
    stray.write_text("stray")
    run(world)
    assert all(exists(world, k) for k in kept) and stray.exists()


def test_a23d_t06_a_file_claimed_while_the_sweep_runs_is_kept(world: Harness) -> None:
    racing = put(world, b"claimed in the middle")
    real = world.storage.list_keys  # type: ignore[attr-defined]

    def list_then_claim(prefix: str) -> Iterator[ListedObject]:
        for item in real(prefix):
            version_with(world, item.storage_key, None)  # a parse commits its key now
            yield item

    world.storage.list_keys = list_then_claim  # type: ignore[attr-defined]
    assert run(world).deleted == 0 and exists(world, racing)


def test_a23d_t07_a_purged_or_rejected_reference_does_not_keep_a_file(world: Harness) -> None:
    key = put(world, b"purged version text")
    version_with(world, key, None, state="PURGED")
    assert run(world).deleted == 1 and not exists(world, key)


def test_a23d_t08_a_second_run_deletes_nothing_more_and_audits_once(world: Harness) -> None:
    put(world, b"one"), put(world, b"two")
    assert run(world).deleted == 2
    assert run(world).deleted == 0
    with world.sessions() as s:
        rows = [r for r in s.scalars(select(AuditLog)) if r.action == "DERIVED_FILES_SWEPT"]
    assert len(rows) == 1 and rows[0].after is not None and rows[0].after["deleted"] == 2
    assert rows[0].tenant_id is None and "key" not in str(rows[0].after)  # counts only


def test_a23d_t09_a_failing_delete_is_counted_and_does_not_stop_the_others(
    world: Harness,
) -> None:
    keys = [put(world, f"orphan {n}".encode()) for n in range(3)]
    real = world.storage.delete

    def flaky(key: str) -> None:
        if key == sorted(keys)[0]:
            raise StorageUnavailable()
        real(key)

    world.storage.delete = flaky  # type: ignore[method-assign]
    result = run(world)
    assert (result.deleted, result.failed) == (2, 1)
    assert exists(world, sorted(keys)[0])


def test_a23d_t10_the_run_is_bounded(world: Harness, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(derived_sweep, "MAX_DELETES", 2)
    keys = [put(world, f"orphan {n}".encode()) for n in range(5)]
    assert run(world).deleted == 2
    assert run(world).deleted == 2 and run(world).deleted == 1
    assert not any(exists(world, k) for k in keys)


def test_a23d_t11_storing_identical_content_again_refreshes_its_age(world: Harness) -> None:
    key = put(world, b"reused content")  # an old orphan
    again = world.storage.put(b"reused content", scope(), Kind.DERIVED, "x/y")
    assert again.storage_key == key
    mtime = datetime.fromtimestamp(
        (world.storage.root / key).stat().st_mtime, UTC  # type: ignore[attr-defined]
    )
    assert NOW - mtime < timedelta(minutes=5)
    assert run(world).deleted == 0 and exists(world, key)  # a parse reusing it is safe


def test_a23d_t12_a_listing_failure_is_counted_not_raised(world: Harness) -> None:
    def broken(prefix: str) -> Iterator[ListedObject]:
        raise StorageUnavailable()
        yield  # pragma: no cover

    world.storage.list_keys = broken  # type: ignore[attr-defined]
    assert run(world).failed >= 1


def test_a23d_t13_the_backend_lists_only_its_prefix_and_valid_keys(
    world: Harness, tmp_path: Path
) -> None:
    mine = put(world, b"mine")
    other = put(world, b"other project", project=uuid.uuid4())
    listed = [o.storage_key for o in world.storage.list_keys(f"{TENANT}/{PROJECT}/derived/")]  # type: ignore[attr-defined]
    assert listed == [mine] and other not in listed
    assert list(world.storage.list_keys("../../etc/")) == []  # type: ignore[attr-defined]
    assert list(world.storage.list_keys(f"{TENANT}/{uuid.uuid4()}/derived/")) == []  # type: ignore[attr-defined]
