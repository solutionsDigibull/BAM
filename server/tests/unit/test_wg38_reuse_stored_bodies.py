"""WG-38 (#925): a build restarted after BUILD_ERROR (the FDE's `recover`) starts from the bodies
the failed build stored: copied to the new build number, no model call. Without the `recover`
event, or when it is older than the stored bodies, nothing is copied."""

import asyncio
from datetime import timedelta

from sbam_api.audit.models import AuditLog
from sbam_api.builds.models import CodeAttemptStatus
from sbam_api.db.base import utcnow
from sbam_contracts.build import BuildStageInput
from sbam_workers.build.bodies import BodiesInputs
from sbam_workers.build.codegen import LoadedTemplate
from test_codegen_bodies import (
    activity_setup,
    attempts_of,
    env,
    stage_input,
    template,  # noqa: F401 - the module-scoped fixture
)


def mark_recovered(sessions: object, inp: object, offset: int = 5) -> None:
    with sessions() as s:  # type: ignore[operator]
        s.add(
            AuditLog(
                tenant_id=inp.tenant_id,  # type: ignore[attr-defined]
                actor_id="fde-1",
                action="recover",
                entity_type="sub_skill",
                entity_id=str(inp.sub_skill_id),  # type: ignore[attr-defined]
                correlation_id="corr-1",
                at=utcnow() + timedelta(seconds=offset),
            )
        )
        s.commit()


def test_wg38_t10_a_recovered_build_reuses_the_stored_bodies_without_a_model_call(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    activity_impl, bifrost, sessions, _ = activity_setup(template, "ms01_1")
    first = stage_input(build_no=1)
    asyncio.run(env().run(activity_impl.run, first))
    assert len(bifrost.requests) == 1
    AuditLog.__table__.create(sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
    mark_recovered(sessions, first)
    second = stage_input(
        build_no=2,
        tenant_id=first.tenant_id,
        project_id=first.project_id,
        sub_skill_id=first.sub_skill_id,
        workflow_id=first.workflow_id,
    )
    result = asyncio.run(env().run(activity_impl.run, second))
    assert len(bifrost.requests) == 1  # no paid call
    old, new = sorted(attempts_of(sessions, second), key=lambda r: r.build_no)
    assert new.id == result.id and new.build_no == 2
    assert new.bodies == old.bodies and new.llm_call_ids == old.llm_call_ids
    assert new.status == CodeAttemptStatus.BODIES_GENERATED and new.findings == []
    again = asyncio.run(env(2).run(activity_impl.run, second))  # a retried stage: the same row
    assert again.id == result.id and len(bifrost.requests) == 1
    assert len(attempts_of(sessions, second)) == 2


def second_build(first: BuildStageInput) -> BuildStageInput:
    return stage_input(
        build_no=2,
        tenant_id=first.tenant_id,
        project_id=first.project_id,
        sub_skill_id=first.sub_skill_id,
        workflow_id=first.workflow_id,
    )


def test_wg38_t11_without_a_recover_event_nothing_is_copied(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    activity_impl, _, sessions, attempts = activity_setup(template, "ms01_1")
    first = stage_input(build_no=1)
    asyncio.run(env().run(activity_impl.run, first))
    AuditLog.__table__.create(sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
    given = BodiesInputs(template, {}, None, [])  # type: ignore[arg-type]
    assert attempts.reuse_stored(second_build(first), given) is None  # an FDE rebuild after review
    assert len(attempts_of(sessions, first)) == 1


def test_wg38_t12_a_recover_older_than_the_stored_bodies_is_not_a_recovery(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    activity_impl, _, sessions, attempts = activity_setup(template, "ms01_1")
    AuditLog.__table__.create(sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
    first = stage_input(build_no=1)
    mark_recovered(sessions, first, offset=-60)  # before the attempt below was written
    asyncio.run(env().run(activity_impl.run, first))
    given = BodiesInputs(template, {}, None, [])  # type: ignore[arg-type]
    assert attempts.reuse_stored(second_build(first), given) is None


def test_wg38_t13_only_the_first_pass_of_a_build_is_copied(
    template: LoadedTemplate,  # noqa: F811
) -> None:
    activity_impl, _, sessions, attempts = activity_setup(template, "ms01_1")
    first = stage_input(build_no=1)
    asyncio.run(env().run(activity_impl.run, first))
    AuditLog.__table__.create(sessions.kw["bind"], checkfirst=True)  # type: ignore[attr-defined]
    mark_recovered(sessions, first)
    given = BodiesInputs(template, {}, None, [])  # type: ignore[arg-type]
    later = stage_input(
        build_no=2,
        cycle=1,
        tenant_id=first.tenant_id,
        project_id=first.project_id,
        sub_skill_id=first.sub_skill_id,
        workflow_id=first.workflow_id,
    )
    assert attempts.reuse_stored(later, given) is None
    assert attempts.reuse_stored(second_build(first), given) is not None
