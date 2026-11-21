"""E14.3: `certifications.reused_from_id` and the `reused_from` marker of the manifest view.

The composite foreign key (tenant, project, id) that stops a reused certificate naming a source of
another tenant or project is enforced by Postgres only (SQLite does not check foreign keys); it is
proved on a real database in the PR and by the migration up/down check.
"""

import uuid
from pathlib import Path

import pytest
from sbam_api.certifications.models import CertificationRow
from sbam_api.manifests.service import _reused_from
from sqlalchemy.exc import IntegrityError
from test_certifications_db import Harness


def test_e14_3_a_certificate_names_the_one_it_carried_over(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    source = h.record()
    reused = h.record(subject_id=uuid.uuid4(), reused_from_id=source)
    with h.sessions() as s:
        row = s.get(CertificationRow, reused)
        assert row is not None and row.reused_from_id == source
        earned = s.get(CertificationRow, source)
        assert earned is not None and earned.reused_from_id is None  # earned by its own gates


def test_e14_3_a_certificate_cannot_be_reused_from_itself(tmp_path: Path) -> None:
    h = Harness(tmp_path)
    own = uuid.uuid4()
    with pytest.raises(IntegrityError):
        h.record(id=own, reused_from_id=own)


def test_e14_3_the_manifest_view_reads_the_marker_from_the_certification_block() -> None:
    reused = {"sbam_build_record": {"certification": {"state": "CERTIFIED", "reused_from": "c-1"}}}
    earned = {"sbam_build_record": {"certification": {"state": "CERTIFIED", "reused_from": None}}}
    assert _reused_from(reused) == "c-1"
    assert _reused_from(earned) is None
    assert _reused_from({}) is None  # a draft has no certification block
