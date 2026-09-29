from __future__ import annotations

import calendar
from dataclasses import dataclass, replace
from datetime import date, timedelta
from decimal import Decimal
from enum import StrEnum
from typing import Self
from uuid import NAMESPACE_URL, UUID, uuid5

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.descriptions import (
    normalize_financial_description,
    validate_persisted_financial_description,
)
from bot_financeiro.domain.money import CurrencyMismatchError, Money


class ForecastDirection(StrEnum):
    INFLOW = "inflow"
    OUTFLOW = "outflow"


class ForecastStatus(StrEnum):
    PENDING = "pending"
    OVERDUE = "overdue"
    CONFIRMED = "confirmed"


class SafetyMarginKind(StrEnum):
    FIXED = "fixed"
    PERCENTAGE = "percentage"


@dataclass(frozen=True, slots=True)
class SafetyMargin:
    kind: SafetyMarginKind
    fixed_amount: Money | None = None
    percentage_rate: Decimal | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.kind, SafetyMarginKind):
            raise ValueError("safety margin kind is invalid")
        if self.kind is SafetyMarginKind.FIXED:
            if self.fixed_amount is None or self.percentage_rate is not None:
                raise ValueError("fixed margin requires only a fixed amount")
            if self.fixed_amount.amount < 0:
                raise ValueError("safety margin must be non-negative")
        elif self.kind is SafetyMarginKind.PERCENTAGE:
            if self.percentage_rate is None or self.fixed_amount is not None:
                raise ValueError("percentage margin requires only a percentage rate")
            if self.percentage_rate < 0:
                raise ValueError("safety margin must be non-negative")

    @classmethod
    def fixed(cls, amount: Money) -> Self:
        return cls(kind=SafetyMarginKind.FIXED, fixed_amount=amount)

    @classmethod
    def percentage(cls, rate: Decimal | str) -> Self:
        if not isinstance(rate, (Decimal, str)):
            raise TypeError("percentage rate must be Decimal or text")
        return cls(
            kind=SafetyMarginKind.PERCENTAGE,
            percentage_rate=Decimal(rate),
        )

    def amount_for(self, expected: Money) -> Money:
        if self.kind is SafetyMarginKind.FIXED:
            assert self.fixed_amount is not None
            if self.fixed_amount.currency != expected.currency:
                raise CurrencyMismatchError("fixed margin must use the forecast currency")
            return self.fixed_amount
        assert self.percentage_rate is not None
        amount = expected.amount * self.percentage_rate / Decimal("100")
        return Money(amount, expected.currency)

    def conservative_impact(self, expected: Money, direction: ForecastDirection) -> Money:
        margin = self.amount_for(expected)
        if direction is ForecastDirection.OUTFLOW:
            return Money(-(expected.amount + margin.amount), expected.currency)
        conservative_inflow = max(expected.amount - margin.amount, Decimal("0"))
        return Money(conservative_inflow, expected.currency)


@dataclass(frozen=True, slots=True)
class OccurrencePlanned:
    expected_amount: Money
    planned_date: date
    margin: SafetyMargin | None
    description: str | None
    metadata: RecordMetadata


@dataclass(frozen=True, slots=True)
class OccurrenceRevised:
    expected_amount: Money
    planned_date: date
    margin: SafetyMargin | None
    description: str | None
    reason: str
    metadata: RecordMetadata


@dataclass(frozen=True, slots=True)
class OccurrenceConfirmed:
    movement_id: UUID
    expected_amount: Money
    actual_amount: Money
    metadata: RecordMetadata

    @property
    def divergence(self) -> Money:
        return Money(
            self.actual_amount.amount - self.expected_amount.amount,
            self.expected_amount.currency,
        )


OccurrenceHistoryEntry = OccurrencePlanned | OccurrenceRevised | OccurrenceConfirmed


@dataclass(frozen=True, slots=True)
class PlannedOccurrence:
    occurrence_id: UUID
    account_id: UUID
    direction: ForecastDirection
    expected_amount: Money
    planned_date: date
    margin: SafetyMargin | None
    recurrence_id: UUID | None
    history: tuple[OccurrenceHistoryEntry, ...]
    description: str | None = None

    def __post_init__(self) -> None:
        if self.expected_amount.amount <= 0:
            raise ValueError("forecast amount must be positive")
        if not self.history or not isinstance(self.history[0], OccurrencePlanned):
            raise ValueError("occurrence history must begin with its plan")
        validate_persisted_financial_description(self.description)
        _validate_margin_currency(self.margin, self.expected_amount)

    @property
    def confirmation(self) -> OccurrenceConfirmed | None:
        confirmations = tuple(
            entry for entry in self.history if isinstance(entry, OccurrenceConfirmed)
        )
        if len(confirmations) > 1:
            raise ValueError("occurrence can have only one confirmation")
        return confirmations[0] if confirmations else None

    @property
    def confirmed_movement_id(self) -> UUID | None:
        confirmation = self.confirmation
        return None if confirmation is None else confirmation.movement_id

    @property
    def actual_amount(self) -> Money | None:
        confirmation = self.confirmation
        return None if confirmation is None else confirmation.actual_amount

    @property
    def divergence(self) -> Money | None:
        confirmation = self.confirmation
        return None if confirmation is None else confirmation.divergence

    def status_on(self, today: date) -> ForecastStatus:
        if self.confirmation is not None:
            return ForecastStatus.CONFIRMED
        if today > self.planned_date:
            return ForecastStatus.OVERDUE
        return ForecastStatus.PENDING

    def normal_impact(self) -> Money:
        sign = Decimal("1") if self.direction is ForecastDirection.INFLOW else Decimal("-1")
        return Money(self.expected_amount.amount * sign, self.expected_amount.currency)

    def margin_amount(self) -> Money:
        if self.margin is None:
            return Money("0", self.expected_amount.currency)
        return self.margin.amount_for(self.expected_amount)

    def conservative_impact(self) -> Money:
        if self.margin is None:
            return self.normal_impact()
        return self.margin.conservative_impact(self.expected_amount, self.direction)

    def revise(
        self,
        *,
        expected_amount: Money,
        planned_date: date,
        margin: SafetyMargin | None,
        description: str | None = None,
        reason: str,
        metadata: RecordMetadata,
    ) -> Self:
        if self.confirmation is not None:
            raise ValueError("confirmed occurrence cannot be revised")
        if self.planned_date <= metadata.effective_date:
            raise ValueError("only a future occurrence can be revised")
        if planned_date <= metadata.effective_date:
            raise ValueError("revised date must remain in the future")
        if expected_amount.currency != self.expected_amount.currency:
            raise CurrencyMismatchError("occurrence revision must preserve currency")
        if expected_amount.amount <= 0:
            raise ValueError("forecast amount must be positive")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("occurrence revision reason is required")
        _validate_margin_currency(margin, expected_amount)
        revised_description = (
            self.description
            if description is None
            else normalize_financial_description(description)
        )
        event = OccurrenceRevised(
            expected_amount=expected_amount,
            planned_date=planned_date,
            margin=margin,
            description=revised_description,
            reason=normalized_reason,
            metadata=metadata,
        )
        return replace(
            self,
            expected_amount=expected_amount,
            planned_date=planned_date,
            margin=margin,
            description=revised_description,
            history=(*self.history, event),
        )

    def confirm(
        self,
        *,
        movement_id: UUID,
        actual_amount: Money,
        metadata: RecordMetadata,
    ) -> Self:
        if self.confirmation is not None:
            raise ValueError("occurrence is already confirmed")
        if actual_amount.currency != self.expected_amount.currency:
            raise CurrencyMismatchError("realized movement must use the forecast currency")
        if actual_amount.amount <= 0:
            raise ValueError("realized movement amount must be positive")
        confirmation = OccurrenceConfirmed(
            movement_id=movement_id,
            expected_amount=self.expected_amount,
            actual_amount=actual_amount,
            metadata=metadata,
        )
        return replace(self, history=(*self.history, confirmation))

    @classmethod
    def plan(
        cls,
        *,
        account_id: UUID,
        direction: ForecastDirection,
        expected_amount: Money,
        planned_date: date,
        margin: SafetyMargin | None,
        description: str,
        metadata: RecordMetadata,
        recurrence_id: UUID | None = None,
        occurrence_id: UUID | None = None,
    ) -> Self:
        normalized_description = normalize_financial_description(description)
        return cls.restore(
            account_id=account_id,
            direction=direction,
            expected_amount=expected_amount,
            planned_date=planned_date,
            margin=margin,
            description=normalized_description,
            metadata=metadata,
            recurrence_id=recurrence_id,
            occurrence_id=occurrence_id,
        )

    @classmethod
    def restore(
        cls,
        *,
        account_id: UUID,
        direction: ForecastDirection,
        expected_amount: Money,
        planned_date: date,
        margin: SafetyMargin | None,
        description: str | None,
        metadata: RecordMetadata,
        recurrence_id: UUID | None = None,
        occurrence_id: UUID | None = None,
    ) -> Self:
        """Rehydrate persisted planning data, including pre-description records."""
        if planned_date <= metadata.effective_date:
            raise ValueError("forecast date must be in the future when planned")
        event = OccurrencePlanned(
            expected_amount=expected_amount,
            planned_date=planned_date,
            margin=margin,
            description=description,
            metadata=metadata,
        )
        return cls(
            occurrence_id=metadata.record_id if occurrence_id is None else occurrence_id,
            account_id=account_id,
            direction=direction,
            expected_amount=expected_amount,
            planned_date=planned_date,
            margin=margin,
            recurrence_id=recurrence_id,
            history=(event,),
            description=description,
        )


class RecurrenceFrequency(StrEnum):
    DAILY = "daily"
    WEEKLY = "weekly"
    MONTHLY = "monthly"
    YEARLY = "yearly"


@dataclass(frozen=True, slots=True)
class RecurrenceCreated:
    description: str | None
    metadata: RecordMetadata


@dataclass(frozen=True, slots=True)
class RecurrenceRevisedFrom:
    from_date: date
    expected_amount: Money
    margin: SafetyMargin | None
    frequency: RecurrenceFrequency
    interval: int
    description: str | None
    reason: str
    metadata: RecordMetadata


RecurrenceHistoryEntry = RecurrenceCreated | RecurrenceRevisedFrom


@dataclass(frozen=True, slots=True)
class Recurrence:
    recurrence_id: UUID
    account_id: UUID
    direction: ForecastDirection
    expected_amount: Money
    starts_on: date
    ends_on: date | None
    frequency: RecurrenceFrequency
    interval: int
    margin: SafetyMargin | None
    history: tuple[RecurrenceHistoryEntry, ...]
    description: str | None = None

    def __post_init__(self) -> None:
        if self.expected_amount.amount <= 0:
            raise ValueError("forecast amount must be positive")
        if self.interval < 1:
            raise ValueError("recurrence interval must be positive")
        if self.ends_on is not None and self.ends_on < self.starts_on:
            raise ValueError("recurrence end cannot precede its start")
        if not self.history or not isinstance(self.history[0], RecurrenceCreated):
            raise ValueError("recurrence history must begin with creation")
        validate_persisted_financial_description(self.description)
        _validate_margin_currency(self.margin, self.expected_amount)

    @classmethod
    def create(
        cls,
        *,
        account_id: UUID,
        direction: ForecastDirection,
        expected_amount: Money,
        starts_on: date,
        ends_on: date | None,
        frequency: RecurrenceFrequency,
        interval: int,
        margin: SafetyMargin | None,
        description: str,
        metadata: RecordMetadata,
    ) -> Self:
        normalized_description = normalize_financial_description(description)
        return cls.restore(
            account_id=account_id,
            direction=direction,
            expected_amount=expected_amount,
            starts_on=starts_on,
            ends_on=ends_on,
            frequency=frequency,
            interval=interval,
            margin=margin,
            description=normalized_description,
            metadata=metadata,
        )

    @classmethod
    def restore(
        cls,
        *,
        account_id: UUID,
        direction: ForecastDirection,
        expected_amount: Money,
        starts_on: date,
        ends_on: date | None,
        frequency: RecurrenceFrequency,
        interval: int,
        margin: SafetyMargin | None,
        description: str | None,
        metadata: RecordMetadata,
    ) -> Self:
        """Rehydrate persisted recurrence data, including pre-description records."""
        if starts_on <= metadata.effective_date:
            raise ValueError("recurrence must start in the future when planned")
        return cls(
            recurrence_id=metadata.record_id,
            account_id=account_id,
            direction=direction,
            expected_amount=expected_amount,
            starts_on=starts_on,
            ends_on=ends_on,
            frequency=frequency,
            interval=interval,
            margin=margin,
            history=(
                RecurrenceCreated(
                    description=description,
                    metadata=metadata,
                ),
            ),
            description=description,
        )

    def revise_from(
        self,
        *,
        from_date: date,
        expected_amount: Money,
        margin: SafetyMargin | None,
        frequency: RecurrenceFrequency | None = None,
        interval: int | None = None,
        description: str | None = None,
        reason: str,
        metadata: RecordMetadata,
    ) -> Self:
        if from_date <= metadata.effective_date:
            raise ValueError("only future recurrence occurrences can be revised")
        if from_date not in self._scheduled_dates_through(from_date):
            raise ValueError("recurrence revision must start at a scheduled occurrence")
        if expected_amount.currency != self.expected_amount.currency:
            raise CurrencyMismatchError("recurrence revision must preserve currency")
        if expected_amount.amount <= 0:
            raise ValueError("forecast amount must be positive")
        revised_frequency = self.frequency if frequency is None else frequency
        revised_interval = self.interval if interval is None else interval
        previous_revisions = tuple(
            item for item in self.history if isinstance(item, RecurrenceRevisedFrom)
        )
        if previous_revisions:
            revised_frequency = previous_revisions[-1].frequency if frequency is None else frequency
            revised_interval = previous_revisions[-1].interval if interval is None else interval
            if from_date <= previous_revisions[-1].from_date:
                raise ValueError("recurrence revisions must advance the preserved segment")
        if revised_interval < 1:
            raise ValueError("recurrence interval must be positive")
        normalized_reason = reason.strip()
        if not normalized_reason:
            raise ValueError("recurrence revision reason is required")
        _validate_margin_currency(margin, expected_amount)
        revised_description = (
            self.description
            if description is None
            else normalize_financial_description(description)
        )
        event = RecurrenceRevisedFrom(
            from_date=from_date,
            expected_amount=expected_amount,
            margin=margin,
            frequency=revised_frequency,
            interval=revised_interval,
            description=revised_description,
            reason=normalized_reason,
            metadata=metadata,
        )
        return replace(
            self,
            description=revised_description,
            history=(*self.history, event),
        )

    def occurrences_through(self, through_date: date) -> tuple[PlannedOccurrence, ...]:
        created = self.history[0]
        assert isinstance(created, RecurrenceCreated)
        revisions = self._revisions()
        occurrences: list[PlannedOccurrence] = []
        for planned_date in self._scheduled_dates_through(through_date):
            applicable = [entry for entry in revisions if entry.from_date <= planned_date]
            latest = applicable[-1] if applicable else None
            expected_amount = self.expected_amount if latest is None else latest.expected_amount
            margin = self.margin if latest is None else latest.margin
            description = self.history[0].description if latest is None else latest.description
            source_metadata = created.metadata if latest is None else latest.metadata
            occurrence_id = uuid5(
                NAMESPACE_URL, f"bot-financeiro:{self.recurrence_id}:{planned_date.isoformat()}"
            )
            occurrence_metadata = replace(
                source_metadata,
                record_id=occurrence_id,
            )
            occurrences.append(
                PlannedOccurrence.restore(
                    account_id=self.account_id,
                    direction=self.direction,
                    expected_amount=expected_amount,
                    planned_date=planned_date,
                    margin=margin,
                    description=description,
                    metadata=occurrence_metadata,
                    recurrence_id=self.recurrence_id,
                    occurrence_id=occurrence_id,
                )
            )
        return tuple(occurrences)

    def next_date_after(self, planned_date: date) -> date | None:
        horizon = planned_date
        while horizon.year < 9999:
            horizon = date(min(horizon.year + 10, 9999), 12, 31)
            later = [item for item in self._scheduled_dates_through(horizon) if item > planned_date]
            if later:
                return later[0]
            if self.ends_on is not None and horizon >= self.ends_on:
                return None
        return None

    def _scheduled_dates_through(self, through_date: date) -> tuple[date, ...]:
        limit = min(through_date, self.ends_on) if self.ends_on is not None else through_date
        if limit < self.starts_on:
            return ()
        dates: list[date] = []
        segments = [
            (self.starts_on, self.frequency, self.interval),
            *((item.from_date, item.frequency, item.interval) for item in self._revisions()),
        ]
        for index, (anchor, frequency, interval) in enumerate(segments):
            segment_end = segments[index + 1][0] if index + 1 < len(segments) else None
            position = 0
            while True:
                candidate = _recurring_date(anchor, frequency, interval, position)
                if candidate > limit:
                    return tuple(dates)
                if segment_end is not None and candidate >= segment_end:
                    break
                dates.append(candidate)
                position += 1
        return tuple(dates)

    def _date_at(self, position: int) -> date:
        return _recurring_date(self.starts_on, self.frequency, self.interval, position)

    def _revisions(self) -> tuple[RecurrenceRevisedFrom, ...]:
        return tuple(entry for entry in self.history if isinstance(entry, RecurrenceRevisedFrom))


def _add_months(anchor: date, months: int) -> date:
    absolute_month = anchor.year * 12 + anchor.month - 1 + months
    year, month_index = divmod(absolute_month, 12)
    month = month_index + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _recurring_date(
    anchor: date,
    frequency: RecurrenceFrequency,
    interval: int,
    position: int,
) -> date:
    steps = position * interval
    if frequency is RecurrenceFrequency.DAILY:
        return anchor + timedelta(days=steps)
    if frequency is RecurrenceFrequency.WEEKLY:
        return anchor + timedelta(weeks=steps)
    if frequency is RecurrenceFrequency.MONTHLY:
        return _add_months(anchor, steps)
    return _add_months(anchor, steps * 12)


def _validate_margin_currency(margin: SafetyMargin | None, amount: Money) -> None:
    if margin is not None:
        margin.amount_for(amount)
