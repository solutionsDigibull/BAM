"""`llm_calls`: one row per call attempt, failures included (TD §5.4, §7.8, issue E6.1).

A row records what was asked (alias, prompt id/version/sha256, context manifest), who answered (the
model that really answered, which after a Bifrost failover is the fallback), what it cost and how it
ended. `(temporal_workflow_id, activity_id, activity_attempt)` is the replay key: a SUCCEEDED row
for it holds the
validated output, so an activity replayed after a worker crash reuses it and is not billed twice.
"""

from decimal import Decimal
from enum import StrEnum
from typing import Any

from sqlalchemy import JSON, Index, Integer, Numeric, String, Text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column

from sbam_api.db.base import Base, ProjectOwned, status_check

Json = JSON().with_variant(JSONB(), "postgresql")


class LlmCallStatus(StrEnum):
    SUCCEEDED = "SUCCEEDED"
    SCHEMA_INVALID = "SCHEMA_INVALID"
    FAILED = "FAILED"


class LlmCall(ProjectOwned, Base):
    __tablename__ = "llm_calls"
    __table_args__ = (
        status_check(LlmCallStatus),
        Index(
            "ix_llm_calls_temporal_workflow_id_activity_id", "temporal_workflow_id", "activity_id"
        ),
    )

    alias: Mapped[str] = mapped_column(String(40))
    model: Mapped[str] = mapped_column(String(200))
    prompt_id: Mapped[str] = mapped_column(String(100))
    prompt_version: Mapped[str] = mapped_column(String(40))
    prompt_sha256: Mapped[str] = mapped_column(String(64))
    tokens_in: Mapped[int] = mapped_column(Integer, default=0)
    tokens_out: Mapped[int] = mapped_column(Integer, default=0)
    cost_usd: Mapped[Decimal] = mapped_column(Numeric(12, 6), default=Decimal(0))
    latency_ms: Mapped[int] = mapped_column(Integer, default=0)
    attempt: Mapped[int] = mapped_column(Integer)  # 1-based: the call, then each schema re-ask
    status: Mapped[str] = mapped_column(String(20))
    trace_id: Mapped[str] = mapped_column(String(64))
    context_manifest: Mapped[dict[str, Any]] = mapped_column(Json, default=dict)
    correlation_id: Mapped[str] = mapped_column(String(100))
    # The replay key: which activity, and which Temporal attempt of it, made the call.
    temporal_workflow_id: Mapped[str] = mapped_column(String(255))
    activity_id: Mapped[str] = mapped_column(String(255))
    activity_attempt: Mapped[int] = mapped_column(Integer)
    # The validated output of a SUCCEEDED row (platform fields are refused by the output model).
    output: Mapped[dict[str, Any] | None] = mapped_column(Json)
    error: Mapped[str | None] = mapped_column(Text)  # validation locations or transport reason
