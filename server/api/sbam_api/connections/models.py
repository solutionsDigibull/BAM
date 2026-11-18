"""`project_connections` (ADR 0030, decision 1; #764): one row per environment variable.

A row holds the entry's metadata and, once the FDE has typed a value, the value **encrypted**
(`ciphertext`, `key_id`, `masked_tail`: all null or all set, enforced by a CHECK). Nothing here is a
plaintext value; the masked tail is the last 2 or 4 characters of a long value and empty for a short
one (an accepted residual, ADR 0030). An entry that has no value and no FDE-written metadata has no
row: the expected names are computed from the Sub-Skills.

`value_version` counts value changes. A Test connection stores the version it tested in
`tested_value_version`, so a changed value (or a changed `test_config`) is untested again.
"""

from datetime import datetime
from typing import Any

from sbam_contracts.connections import ConnectionSource, ConnectionTestMode, ConnectionTestOutcome
from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    Integer,
    LargeBinary,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.certifications.models import Json
from sbam_api.connections.crypto import Sealed
from sbam_api.db.base import Base, ProjectOwned, status_check


class ConnectionRow(ProjectOwned, Base):
    __tablename__ = "project_connections"
    __table_args__ = (
        status_check(ConnectionSource, "source"),
        status_check(ConnectionTestMode, "test_mode"),
        status_check(ConnectionTestOutcome, "last_test_code"),
        CheckConstraint(
            "(ciphertext IS NULL AND key_id IS NULL AND masked_tail IS NULL)"
            " OR (ciphertext IS NOT NULL AND key_id IS NOT NULL AND masked_tail IS NOT NULL)",
            name="value_all_or_none",
        ),
        CheckConstraint("value_version >= 0", name="value_version_not_negative"),
        UniqueConstraint("tenant_id", "project_id", "name"),
    )

    name: Mapped[str] = mapped_column(String(128))  # an environment variable name
    source: Mapped[str] = mapped_column(String(20), default=ConnectionSource.FDE.value)
    purpose: Mapped[str] = mapped_column(String(500), default="")
    where_to_get: Mapped[str] = mapped_column(String(500), default="")
    required: Mapped[bool] = mapped_column(Boolean, default=True)
    ciphertext: Mapped[bytes | None] = mapped_column(LargeBinary)  # nonce || ct || tag
    key_id: Mapped[str | None] = mapped_column(String(32))
    masked_tail: Mapped[str | None] = mapped_column(String(4))
    value_version: Mapped[int] = mapped_column(Integer, default=0)
    value_set_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    value_set_by: Mapped[str | None] = mapped_column(String(255))
    test_mode: Mapped[str] = mapped_column(String(10), default=ConnectionTestMode.HTTP.value)
    test_config: Mapped[dict[str, Any] | None] = mapped_column(Json)  # never a value
    tested_value_version: Mapped[int | None] = mapped_column(Integer)
    last_test_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
    last_test_code: Mapped[str | None] = mapped_column(String(20))

    @property
    def has_value(self) -> bool:
        return self.ciphertext is not None

    def sealed(self) -> Sealed | None:
        """What `crypto` opens, or None while no value is set."""
        if self.ciphertext is None or self.key_id is None:
            return None
        return Sealed(self.ciphertext, self.key_id)

    def __repr__(self) -> str:  # never the ciphertext
        return f"ConnectionRow(name={self.name!r}, has_value={self.has_value})"
