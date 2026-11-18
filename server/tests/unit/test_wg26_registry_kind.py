"""WG-26 (#860, ADR 0032): registry entries have a kind, staging (Commit) or production (later).

Reuses the world of the WG-12 tests: a frozen, certified Workflow in SQLite and a fake Bifrost.
"""

from typing import Any

import pytest
from sbam_api.core.conventions import ApiError
from sbam_api.db.repository import TenantContext
from sbam_api.workflow_registry import service
from sbam_api.workflow_registry.repository import RegisteredWorkflowRepository
from sbam_contracts.workflow_registry import RegisteredWorkflow, RegistryKind, RegistryStatus
from test_wg12_workflow_registry import (
    CTX,
    DIGEST,
    LATER,
    NEW_DIGEST,
    OTHER_TENANT,
    PROJECT,
    V1_0,
    V1_1,
    WORKFLOW_ID,
    FakeBifrost,
    World,
)

STG_V1_0 = V1_0 + "_stg"
STG_V1_1 = V1_1 + "_stg"
STAGING, PRODUCTION = RegistryKind.STAGING, RegistryKind.PRODUCTION


def stage(world: World, fake: FakeBifrost, **kwargs: Any) -> RegisteredWorkflow:
    return world.register(fake, kind=STAGING, **kwargs)


def rows_of(world: World, kind: RegistryKind) -> dict[str, str]:
    return {r.version: r.status for r in world.rows() if r.kind == kind.value}


def lookup(world: World, kind: RegistryKind | None, version: str | None = None) -> Any:
    with world.sessions() as session:
        return service.lookup(session, CTX, WORKFLOW_ID, version, kind)


def test_wg26_t01_commit_registers_a_staging_entry_with_the_certified_digest() -> None:
    world, fake = World(), FakeBifrost()
    manifest = world.freeze()
    done = stage(world, fake)
    assert done.kind is STAGING and done.status is RegistryStatus.REGISTERED
    assert done.registry_name == STG_V1_0  # a separate Bifrost entry
    assert done.image_digest == DIGEST == manifest.dump()["mcp_server"]["image_digest"]
    assert done.manifest_hash == manifest.integrity.content_sha256  # no rebuild
    assert fake.address == f"http://{STG_V1_0}:8000/mcp"


def test_wg26_t02_a_production_key_cannot_see_a_staging_entry() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    stage(world, fake)
    with pytest.raises(ApiError) as caught:
        lookup(world, PRODUCTION)
    assert caught.value.status == 404
    with pytest.raises(ApiError) as by_version:
        lookup(world, PRODUCTION, "1.0.0")
    assert by_version.value.status == 404
    with world.sessions() as session:
        assert service.names_for_key(session, CTX, PRODUCTION) == []  # nothing for SRIM's key
        assert service.names_for_key(session, CTX, STAGING) == [STG_V1_0]
    assert lookup(world, STAGING).kind is STAGING


def test_wg26_t03_registering_the_same_staging_version_again_is_a_no_op() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    first = stage(world, fake)
    writes = list(fake.writes())
    again = stage(world, fake, now=LATER)
    assert (again.registry_name, again.image_digest) == (first.registry_name, first.image_digest)
    assert again.kind is STAGING and again.version == first.version
    assert fake.writes() == writes  # Bifrost is not called
    assert len(world.rows()) == 1


def test_wg26_t04_a_newer_version_supersedes_the_older_staging_entry_never_deletes_it() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    stage(world, fake)
    world.freeze("1.0.1", NEW_DIGEST, build_no=2)
    new = stage(world, fake, now=LATER)
    assert new.version == "1.0.1" and new.kind is STAGING
    assert rows_of(world, STAGING) == {"1.0.1": "REGISTERED", "1.0.0": "SUPERSEDED"}
    assert fake.listed[STG_V1_0].disabled and not fake.listed[STG_V1_1].disabled
    old = lookup(world, STAGING, "1.0.0")
    assert old.status is RegistryStatus.SUPERSEDED and old.superseded_by == "1.0.1"
    assert lookup(world, STAGING).version == "1.0.1"  # no version: the current one
    assert len(world.rows()) == 2  # never deleted


def test_wg26_t05_a_staging_entry_does_not_supersede_the_production_one() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake, kind=PRODUCTION)
    world.freeze("1.0.1", NEW_DIGEST, build_no=2)
    stage(world, fake, now=LATER)
    assert rows_of(world, PRODUCTION) == {"1.0.0": "REGISTERED"}
    assert not fake.listed[V1_0].disabled
    assert lookup(world, PRODUCTION).version == "1.0.0"  # SRIM still gets its own version
    assert lookup(world, STAGING).version == "1.0.1"


def test_wg26_t06_one_staging_entry_per_version_and_both_kinds_can_share_a_version() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    stage(world, fake)
    world.register(fake, kind=PRODUCTION)  # promotion later: same digest, own row and name
    kinds = {(r.version, r.kind, r.registry_name, r.image_digest) for r in world.rows()}
    assert kinds == {
        ("1.0.0", "staging", STG_V1_0, DIGEST),
        ("1.0.0", "production", V1_0, DIGEST),
    }
    with world.sessions() as session:
        repo = RegisteredWorkflowRepository(session, CTX)
        assert repo.of_version(WORKFLOW_ID, "1.0.0", STAGING) is not None
        assert repo.of_version(WORKFLOW_ID, "1.0.0", PRODUCTION) is not None


def test_wg26_t07_lookup_with_no_kind_shows_staging_first_then_production() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    world.register(fake, kind=PRODUCTION)
    assert lookup(world, None).kind is PRODUCTION
    stage(world, fake)
    assert lookup(world, None).kind is STAGING
    assert lookup(world, None, "1.0.0").kind is STAGING


def test_wg26_t08_the_stg_name_is_a_valid_registry_name_within_64() -> None:
    name = service.registry_name_for("wf_" + "a" * 70, "12.0.345", STAGING)
    assert len(name) <= 64 and name.endswith("_v12_345_stg") and "-" not in name
    assert service.registry_name_for("wf_x", "1.0.0") == "wf_x_v1_0"  # production is unchanged


def test_wg26_t09_a_staging_entry_of_another_tenant_is_404() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    stage(world, fake)
    other = TenantContext(OTHER_TENANT, PROJECT)
    for kind in (STAGING, PRODUCTION, None):
        with world.sessions() as session, pytest.raises(ApiError) as caught:
            service.lookup(session, other, WORKFLOW_ID, None, kind)
        assert caught.value.status == 404
    with world.sessions() as session:
        assert service.names_for_key(session, other, STAGING) == []


def test_wg26_t10_reconcile_treats_a_staging_row_like_any_other() -> None:
    world, fake = World(), FakeBifrost()
    world.freeze()
    stage(world, fake)
    fake.listed.clear()  # Bifrost lost its database
    with world.sessions() as session:
        report = service.reconcile(session, CTX, fake)
        session.commit()
    assert report.reregistered == [STG_V1_0] and STG_V1_0 in fake.listed


def test_wg26_t11_the_kind_is_part_of_the_contract_and_a_bad_kind_is_refused() -> None:
    assert {k.value for k in RegistryKind} == {"staging", "production"}
    with pytest.raises(ValueError):
        RegistryKind("preview")
