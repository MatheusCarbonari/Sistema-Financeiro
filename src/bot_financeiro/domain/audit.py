from dataclasses import dataclass, field
from datetime import date, datetime
from enum import StrEnum
from typing import Self
from uuid import UUID, uuid4


def _require_timezone(value: datetime, field_name: str) -> None:
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError(f"{field_name} requires timezone information")


class RecordOrigin(StrEnum):
    MANUAL = "manual"


class AuditAction(StrEnum):
    REGISTERED = "registered"
    CORRECTED = "corrected"
    CANCELED = "canceled"


@dataclass(frozen=True, slots=True)
class RecordMetadata:
    effective_date: date
    recorded_at: datetime
    origin: RecordOrigin
    record_id: UUID = field(default_factory=uuid4)

    def __post_init__(self) -> None:
        _require_timezone(self.recorded_at, "recorded_at")


@dataclass(frozen=True, slots=True)
class AuditEntry:
    record_id: UUID
    action: AuditAction
    occurred_at: datetime
    reason: str | None = None

    def __post_init__(self) -> None:
        _require_timezone(self.occurred_at, "occurred_at")
        if self.action is not AuditAction.REGISTERED and not (self.reason or "").strip():
            raise ValueError("corrections and cancellations require a reason")


@dataclass(frozen=True, slots=True)
class AuditTrail:
    metadata: RecordMetadata
    entries: tuple[AuditEntry, ...]

    @classmethod
    def start(cls, metadata: RecordMetadata) -> Self:
        registration = AuditEntry(
            record_id=metadata.record_id,
            action=AuditAction.REGISTERED,
            occurred_at=metadata.recorded_at,
        )
        return cls(metadata=metadata, entries=(registration,))

    def append(
        self,
        action: AuditAction,
        *,
        occurred_at: datetime,
        reason: str,
    ) -> Self:
        if action is AuditAction.REGISTERED:
            raise ValueError("registration is created when an AuditTrail starts")
        entry = AuditEntry(
            record_id=self.metadata.record_id,
            action=action,
            occurred_at=occurred_at,
            reason=reason,
        )
        return type(self)(metadata=self.metadata, entries=(*self.entries, entry))
