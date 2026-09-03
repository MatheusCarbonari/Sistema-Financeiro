from datetime import date, datetime, timedelta, timezone
from uuid import UUID

import pytest

from bot_financeiro.domain.audit import (
    AuditAction,
    AuditTrail,
    RecordMetadata,
    RecordOrigin,
)


def test_record_metadata_keeps_retroactive_effective_date_separate_from_recorded_moment() -> None:
    record_id = UUID("b7fa62f2-dca5-4cba-83fa-f02c803acf5c")
    sao_paulo_offset = timezone(timedelta(hours=-3))
    recorded_at = datetime(2026, 9, 3, 10, 30, tzinfo=sao_paulo_offset)

    metadata = RecordMetadata(
        effective_date=date(2026, 8, 10),
        recorded_at=recorded_at,
        origin=RecordOrigin.MANUAL,
        record_id=record_id,
    )

    assert metadata.record_id == record_id
    assert metadata.effective_date == date(2026, 8, 10)
    assert metadata.recorded_at == recorded_at
    assert metadata.origin is RecordOrigin.MANUAL


def test_recorded_moment_requires_timezone_information() -> None:
    with pytest.raises(ValueError, match="timezone"):
        RecordMetadata(
            effective_date=date(2026, 9, 3),
            recorded_at=datetime(2026, 9, 3, 10, 30),
            origin=RecordOrigin.MANUAL,
        )


def test_audit_trail_appends_correction_and_cancellation_without_rewriting_history() -> None:
    tz = timezone(timedelta(hours=-3))
    metadata = RecordMetadata(
        effective_date=date(2026, 8, 10),
        recorded_at=datetime(2026, 9, 1, 9, 0, tzinfo=tz),
        origin=RecordOrigin.MANUAL,
        record_id=UUID("b7fa62f2-dca5-4cba-83fa-f02c803acf5c"),
    )

    original = AuditTrail.start(metadata)
    corrected = original.append(
        AuditAction.CORRECTED,
        occurred_at=datetime(2026, 9, 2, 10, 0, tzinfo=tz),
        reason="Corrigir data informada",
    )
    canceled = corrected.append(
        AuditAction.CANCELED,
        occurred_at=datetime(2026, 9, 3, 11, 0, tzinfo=tz),
        reason="Registro duplicado",
    )

    assert [entry.action for entry in original.entries] == [AuditAction.REGISTERED]
    assert [entry.action for entry in corrected.entries] == [
        AuditAction.REGISTERED,
        AuditAction.CORRECTED,
    ]
    assert [entry.action for entry in canceled.entries] == [
        AuditAction.REGISTERED,
        AuditAction.CORRECTED,
        AuditAction.CANCELED,
    ]
    assert all(entry.record_id == metadata.record_id for entry in canceled.entries)
