"""E13.2 orphan reaper: only a VM of ours, of this environment, that has outlived its own TTL plus
the grace period is destroyed. Everything else on a shared host is left alone."""

import asyncio
from datetime import UTC, datetime, timedelta

import pytest
from sbam_contracts.sandbox import VM_TAG_KEY
from sbam_workers.sandbox.backend import VmInfo
from sbam_workers.sandbox.reaper import (
    DEFAULT_TTL_S,
    ENVIRONMENT_TAG,
    TTL_TAG,
    orphans,
    reap,
    reap_schedule,
)
from temporalio.client import ScheduleOverlapPolicy

NOW = datetime(2026, 10, 30, 12, 0, tzinfo=UTC)
GRACE = timedelta(minutes=5)


def vm(vm_id: str, age_s: float | None, **tags: str) -> VmInfo:
    started = None if age_s is None else NOW - timedelta(seconds=age_s)
    base = {VM_TAG_KEY: "run-1", ENVIRONMENT_TAG: "staging", TTL_TAG: "600"}
    return VmInfo(vm_id, started, {**base, **tags})


class FakeBackend:
    def __init__(self, vms: list[VmInfo], fail: set[str] | None = None) -> None:
        self.vms, self.fail = vms, fail or set()
        self.destroyed: list[str] = []

    async def list_vms(self) -> list[VmInfo]:
        return self.vms

    async def destroy(self, vm_id: str) -> None:
        if vm_id in self.fail:
            raise RuntimeError("kill refused")
        self.destroyed.append(vm_id)


def run(backend: FakeBackend, environment: str = "staging"):  # type: ignore[no-untyped-def]
    return asyncio.run(reap(backend, NOW, GRACE, environment))  # type: ignore[arg-type]


def test_e13_2_t07_a_vm_older_than_its_ttl_plus_grace_is_destroyed() -> None:
    backend = FakeBackend([vm("old", 600 + 301)])
    result = run(backend)
    assert backend.destroyed == ["old"] and result.destroyed == 1 and result.failed == 0


def test_e13_2_a_vm_inside_its_ttl_plus_grace_is_never_touched() -> None:
    backend = FakeBackend([vm("running", 600), vm("slow", 600 + 299)])
    assert run(backend).destroyed == 0 and backend.destroyed == []


@pytest.mark.parametrize(
    "stranger",
    [
        VmInfo("someone-elses", NOW - timedelta(days=3), {}),  # no tag: not ours
        VmInfo(
            "other-env", NOW - timedelta(days=3), {VM_TAG_KEY: "r", ENVIRONMENT_TAG: "production"}
        ),
        VmInfo("no-env", NOW - timedelta(days=3), {VM_TAG_KEY: "r"}),
    ],
    ids=["untagged", "other-environment", "no-environment"],
)
def test_e13_2_a_vm_that_is_not_ours_is_left_alone_however_old(stranger: VmInfo) -> None:
    backend = FakeBackend([stranger])
    result = run(backend)
    assert backend.destroyed == [] and result.listed == 1 and result.ours == 0


def test_e13_2_a_vm_with_no_start_time_is_counted_not_killed() -> None:
    backend = FakeBackend([vm("mystery", None)])
    result = run(backend)
    assert backend.destroyed == [] and result.unreadable == 1 and result.ours == 1


def test_e13_2_a_naive_start_time_is_read_as_utc() -> None:
    naive = VmInfo(
        "n",
        (NOW - timedelta(hours=5)).replace(tzinfo=None),
        {VM_TAG_KEY: "r", ENVIRONMENT_TAG: "staging", TTL_TAG: "600"},
    )
    assert [v.vm_id for v in orphans([naive], NOW, GRACE, "staging")[0]] == ["n"]


@pytest.mark.parametrize("bad", ["", "soon"])
def test_e13_2_an_unreadable_ttl_falls_back_to_the_default(bad: str) -> None:
    inside = vm("y", DEFAULT_TTL_S + 100, **{TTL_TAG: bad})  # past 600 s, inside the default
    outside = vm("o", DEFAULT_TTL_S + 301, **{TTL_TAG: bad})
    found = orphans([inside, outside], NOW, GRACE, "staging")[0]
    assert [v.vm_id for v in found] == ["o"]


@pytest.mark.parametrize("bad", ["-5", "0"])
def test_e13_2_a_non_positive_ttl_is_clamped_to_one_second_never_trusted(bad: str) -> None:
    found = orphans([vm("z", 200, **{TTL_TAG: bad})], NOW, GRACE, "staging")[0]
    assert found == []  # inside the grace period: a bad tag never makes a young VM an orphan
    found = orphans([vm("z", 302, **{TTL_TAG: bad})], NOW, GRACE, "staging")[0]
    assert [v.vm_id for v in found] == ["z"]


def test_e13_2_one_vm_that_will_not_die_does_not_stop_the_others() -> None:
    backend = FakeBackend([vm("stuck", 9999), vm("fine", 9999)], fail={"stuck"})
    result = run(backend)
    assert backend.destroyed == ["fine"] and result.failed == 1 and result.destroyed == 1


def test_e13_2_the_schedule_runs_every_five_minutes_and_never_overlaps() -> None:
    schedule = reap_schedule()
    assert schedule.spec.intervals[0].every == timedelta(minutes=5)
    assert schedule.policy is not None and schedule.policy.overlap is ScheduleOverlapPolicy.SKIP
