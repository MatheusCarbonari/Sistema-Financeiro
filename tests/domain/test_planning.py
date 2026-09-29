from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.money import CurrencyMismatchError, Money
from bot_financeiro.domain.planning import (
    ForecastDirection,
    ForecastStatus,
    PlannedOccurrence,
    Recurrence,
    RecurrenceFrequency,
    SafetyMargin,
)


def metadata(record_id: str, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def test_safety_margin_is_explicit_exact_and_direction_aware() -> None:
    expected = Money("100.00", "BRL")
    fixed = SafetyMargin.fixed(Money("10.00", "BRL"))
    percentage = SafetyMargin.percentage(Decimal("12.345"))

    assert fixed.amount_for(expected) == Money("10.00", "BRL")
    assert percentage.amount_for(expected) == Money("12.35", "BRL")
    assert fixed.conservative_impact(expected, ForecastDirection.OUTFLOW) == Money("-110.00", "BRL")
    assert fixed.conservative_impact(expected, ForecastDirection.INFLOW) == Money("90.00", "BRL")
    assert SafetyMargin.fixed(Money("150.00", "BRL")).conservative_impact(
        expected, ForecastDirection.INFLOW
    ) == Money("0.00", "BRL")


def test_margin_rejects_invalid_values_and_currency() -> None:
    with pytest.raises(ValueError, match="non-negative"):
        SafetyMargin.fixed(Money("-0.01", "BRL"))
    with pytest.raises(ValueError, match="non-negative"):
        SafetyMargin.percentage(Decimal("-1"))
    with pytest.raises(CurrencyMismatchError):
        SafetyMargin.fixed(Money("1.00", "USD")).amount_for(Money("10.00", "BRL"))


def test_occurrence_becomes_overdue_only_after_planned_date() -> None:
    occurrence = PlannedOccurrence.plan(
        description="Registro de teste",
        account_id=UUID("c78897c8-a99a-4f81-8d91-ab3d7e08ae5a"),
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("80.00", "BRL"),
        planned_date=date(2026, 9, 10),
        margin=None,
        metadata=metadata("8474fd4b-6492-4122-bfc6-ee0c5f45e011", 1),
    )

    assert occurrence.status_on(date(2026, 9, 9)) is ForecastStatus.PENDING
    assert occurrence.status_on(date(2026, 9, 10)) is ForecastStatus.PENDING
    assert occurrence.status_on(date(2026, 9, 11)) is ForecastStatus.OVERDUE
    assert occurrence.confirmed_movement_id is None


def test_new_forecast_and_recurrence_must_start_in_the_future() -> None:
    account_id = UUID("c78897c8-a99a-4f81-8d91-ab3d7e08ae5a")
    with pytest.raises(ValueError, match="future"):
        PlannedOccurrence.plan(
            description="Registro de teste",
            account_id=account_id,
            direction=ForecastDirection.OUTFLOW,
            expected_amount=Money("80.00", "BRL"),
            planned_date=date(2026, 9, 1),
            margin=None,
            metadata=metadata("8474fd4b-6492-4122-bfc6-ee0c5f45e011", 1),
        )
    with pytest.raises(ValueError, match="future"):
        Recurrence.create(
            description="Registro de teste",
            account_id=account_id,
            direction=ForecastDirection.OUTFLOW,
            expected_amount=Money("80.00", "BRL"),
            starts_on=date(2026, 9, 1),
            ends_on=None,
            frequency=RecurrenceFrequency.MONTHLY,
            interval=1,
            margin=None,
            metadata=metadata("5cbdb8b1-3dfc-462b-8c62-dcb3eeb2f647", 1),
        )


def test_confirmation_keeps_occurrence_as_forecast_and_records_divergence() -> None:
    occurrence = PlannedOccurrence.plan(
        description="Registro de teste",
        account_id=UUID("c78897c8-a99a-4f81-8d91-ab3d7e08ae5a"),
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("80.00", "BRL"),
        planned_date=date(2026, 9, 10),
        margin=None,
        metadata=metadata("8474fd4b-6492-4122-bfc6-ee0c5f45e011", 1),
    )
    movement_id = UUID("5c3cb278-f084-494b-9580-d27b01d27f94")

    confirmed = occurrence.confirm(
        movement_id=movement_id,
        actual_amount=Money("92.50", "BRL"),
        metadata=metadata("94b6f5c4-3535-4fa4-8618-a2b67d91dd48", 12),
    )

    assert confirmed.occurrence_id == occurrence.occurrence_id
    assert confirmed.expected_amount == Money("80.00", "BRL")
    assert confirmed.confirmed_movement_id == movement_id
    assert confirmed.actual_amount == Money("92.50", "BRL")
    assert confirmed.divergence == Money("12.50", "BRL")
    assert confirmed.status_on(date(2026, 10, 1)) is ForecastStatus.CONFIRMED
    assert confirmed.history[:-1] == occurrence.history
    with pytest.raises(ValueError, match="already confirmed"):
        confirmed.confirm(
            movement_id=UUID("caf759f7-a6fa-4749-a9a7-02e5be127f92"),
            actual_amount=Money("92.50", "BRL"),
            metadata=metadata("159a42f9-4502-4de2-812d-f61716ed3550", 13),
        )


def test_only_future_unconfirmed_occurrence_can_be_revised() -> None:
    occurrence = PlannedOccurrence.plan(
        description="Registro de teste",
        account_id=UUID("c78897c8-a99a-4f81-8d91-ab3d7e08ae5a"),
        direction=ForecastDirection.INFLOW,
        expected_amount=Money("100.00", "BRL"),
        planned_date=date(2026, 9, 20),
        margin=None,
        metadata=metadata("8474fd4b-6492-4122-bfc6-ee0c5f45e011", 1),
    )

    revised = occurrence.revise(
        expected_amount=Money("110.00", "BRL"),
        planned_date=date(2026, 9, 21),
        margin=SafetyMargin.percentage(Decimal("5")),
        reason="Previsao atualizada",
        metadata=metadata("94b6f5c4-3535-4fa4-8618-a2b67d91dd48", 10),
    )

    assert revised.expected_amount == Money("110.00", "BRL")
    assert revised.planned_date == date(2026, 9, 21)
    assert revised.history[:-1] == occurrence.history
    with pytest.raises(ValueError, match="future occurrence"):
        occurrence.revise(
            expected_amount=Money("110.00", "BRL"),
            planned_date=date(2026, 9, 20),
            margin=None,
            reason="Tarde demais",
            metadata=metadata("159a42f9-4502-4de2-812d-f61716ed3550", 20),
        )


def test_monthly_recurrence_clamps_month_end_without_drifting() -> None:
    recurrence = Recurrence.create(
        description="Registro de teste",
        account_id=UUID("c78897c8-a99a-4f81-8d91-ab3d7e08ae5a"),
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("50.00", "BRL"),
        starts_on=date(2027, 1, 31),
        ends_on=None,
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=metadata("5cbdb8b1-3dfc-462b-8c62-dcb3eeb2f647", 1),
    )

    occurrences = recurrence.occurrences_through(date(2027, 4, 30))

    assert tuple(item.planned_date for item in occurrences) == (
        date(2027, 1, 31),
        date(2027, 2, 28),
        date(2027, 3, 31),
        date(2027, 4, 30),
    )
    assert all(item.recurrence_id == recurrence.recurrence_id for item in occurrences)


def test_recurrence_revision_preserves_earlier_segment() -> None:
    recurrence = Recurrence.create(
        description="Registro de teste",
        account_id=UUID("c78897c8-a99a-4f81-8d91-ab3d7e08ae5a"),
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 10, 5),
        ends_on=date(2027, 1, 5),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=metadata("5cbdb8b1-3dfc-462b-8c62-dcb3eeb2f647", 1),
    )

    revised = recurrence.revise_from(
        from_date=date(2026, 12, 5),
        expected_amount=Money("125.00", "BRL"),
        margin=SafetyMargin.fixed(Money("5.00", "BRL")),
        reason="Novo contrato",
        metadata=metadata("94b6f5c4-3535-4fa4-8618-a2b67d91dd48", 10),
    )
    occurrences = revised.occurrences_through(date(2027, 1, 5))

    assert tuple(item.expected_amount for item in occurrences) == (
        Money("100.00", "BRL"),
        Money("100.00", "BRL"),
        Money("125.00", "BRL"),
        Money("125.00", "BRL"),
    )
    assert revised.history[:-1] == recurrence.history


def test_recurrence_revision_can_change_cadence_only_from_selected_occurrence() -> None:
    recurrence = Recurrence.create(
        description="Registro de teste",
        account_id=UUID("c78897c8-a99a-4f81-8d91-ab3d7e08ae5a"),
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 10, 5),
        ends_on=date(2027, 2, 5),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=metadata("5cbdb8b1-3dfc-462b-8c62-dcb3eeb2f647", 1),
    )

    revised = recurrence.revise_from(
        from_date=date(2026, 12, 5),
        expected_amount=Money("100.00", "BRL"),
        margin=None,
        frequency=RecurrenceFrequency.WEEKLY,
        interval=2,
        reason="Novo calendario",
        metadata=metadata("94b6f5c4-3535-4fa4-8618-a2b67d91dd48", 10),
    )

    assert tuple(item.planned_date for item in revised.occurrences_through(date(2027, 1, 5))) == (
        date(2026, 10, 5),
        date(2026, 11, 5),
        date(2026, 12, 5),
        date(2026, 12, 19),
        date(2027, 1, 2),
    )
