"""`artifacts` (TD §7.8): one row per stored object a project keeps, keyed by its storage key.

Storage is content-addressed, so identical bytes for the same scope and kind share one key and
one row: re-storing a bundle finds the row instead of adding a second one (E11.3).
"""

from enum import StrEnum

from sqlalchemy import BigInteger, Boolean, String, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, ProjectOwned, status_check


class ArtifactKind(StrEnum):
    CODE_BUNDLE = "CODE_BUNDLE"  # E11.3: the assembled Sub-Skill tar


class Artifact(ProjectOwned, Base):
    __tablename__ = "artifacts"
    __table_args__ = (
        status_check(ArtifactKind, column="kind"),
        UniqueConstraint("tenant_id", "storage_key"),
    )

    kind: Mapped[str] = mapped_column(String(30))
    storage_key: Mapped[str] = mapped_column(String(1024))
    sha256: Mapped[str] = mapped_column(String(64))
    size: Mapped[int] = mapped_column(BigInteger)
    content_type: Mapped[str] = mapped_column(String(255))
    is_frozen: Mapped[bool] = mapped_column(Boolean, default=False)
