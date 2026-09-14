from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date
from types import TracebackType
from typing import Protocol
from uuid import UUID

from bot_financeiro.application.financial_operations import FinancialMovementRepository
from bot_financeiro.application.resource_accounts import ResourceAccountRepository
from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.money import CurrencyMismatchError, Money
from bot_financeiro.domain.movements import (
    BillPayment,
    CashExpense,
    Income,
    InvoicePayment,
)
from bot_financeiro.domain.planning import (
    ForecastDirection,
    ForecastStatus,
    PlannedOccurrence,
    Recurrence,
    RecurrenceFrequency,
    SafetyMargin,
)
from bot_financeiro.domain.resource_account import AccountStatus


class PlanningRepository(Protocol):
    def add_occurrence(self, occurrence: PlannedOccurrence) -> None: ...

    def get_occurrence(self, occurrence_id: UUID) -> PlannedOccurrence | None: ...

    def save_occurrence(self, occurrence: PlannedOccurrence) -> None: ...

    def list_standalone_occurrences(self) -> tuple[PlannedOccurrence, ...]: ...

    def apply_history(self, occurrence: PlannedOccurrence) -> PlannedOccurrence: ...

    def add_recurrence(self, recurrence: Recurrence) -> None: ...

    def get_recurrence(self, recurrence_id: UUID) -> Recurrence | None: ...

    def save_recurrence(self, recurrence: Recurrence) -> None: ...

    def list_recurrences(self) -> tuple[Recurrence, ...]: ...

    def movement_is_linked(self, movement_id: UUID) -> bool: ...

    def occurrence_event_dates(self, recurrence_id: UUID) -> tuple[date, ...]: ...

    def occurrence_source_dates(self, recurrence_id: UUID) -> tuple[date, ...]: ...


class PlanningUnitOfWork(Protocol):
    accounts: ResourceAccountRepository
    movements: FinancialMovementRepository
    planning: PlanningRepository

    def __enter__(self) -> PlanningUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class PlanningProjectionLine:
    occurrence_id: UUID
    planned_date: date
    status: ForecastStatus
    direction: ForecastDirection
    expected_amount: Money
    margin_amount: Money
    normal_impact: Money
    conservative_impact: Money


@dataclass(frozen=True, slots=True)
class PlanningProjection:
    account_id: UUID
    through_date: date
    current_balance: Money
    lines: tuple[PlanningProjectionLine, ...]
    projected_balance: Money
    conservative_projected_balance: Money


class PlanningOperations:
    def __init__(self, unit_of_work_factory: Callable[[], PlanningUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_forecast(
        self,
        *,
        account_id: UUID,
        direction: ForecastDirection,
        expected_amount: Money,
        planned_date: date,
        margin: SafetyMargin | None,
        metadata: RecordMetadata,
    ) -> PlannedOccurrence:
        occurrence = PlannedOccurrence.plan(
            account_id=account_id,
            direction=direction,
            expected_amount=expected_amount,
            planned_date=planned_date,
            margin=margin,
            metadata=metadata,
        )
        with self._unit_of_work_factory() as unit_of_work:
            self._require_account(unit_of_work, account_id, expected_amount.currency)
            unit_of_work.planning.add_occurrence(occurrence)
            unit_of_work.commit()
        return occurrence

    def create_recurrence(
        self,
        *,
        account_id: UUID,
        direction: ForecastDirection,
        expected_amount: Money,
        starts_on: date,
        ends_on: date | None,
        frequency: RecurrenceFrequency,
        interval: int,
        margin: SafetyMargin | None,
        metadata: RecordMetadata,
    ) -> Recurrence:
        recurrence = Recurrence.create(
            account_id=account_id,
            direction=direction,
            expected_amount=expected_amount,
            starts_on=starts_on,
            ends_on=ends_on,
            frequency=frequency,
            interval=interval,
            margin=margin,
            metadata=metadata,
        )
        with self._unit_of_work_factory() as unit_of_work:
            self._require_account(unit_of_work, account_id, expected_amount.currency)
            unit_of_work.planning.add_recurrence(recurrence)
            unit_of_work.commit()
        return recurrence

    def get_recurrence(self, recurrence_id: UUID | None) -> Recurrence:
        if recurrence_id is None:
            raise LookupError("occurrence does not belong to a recurrence")
        with self._unit_of_work_factory() as unit_of_work:
            recurrence = unit_of_work.planning.get_recurrence(recurrence_id)
            if recurrence is None:
                raise LookupError(f"recurrence {recurrence_id} does not exist")
            return recurrence

    def list_occurrences(
        self, *, account_id: UUID, through_date: date
    ) -> tuple[PlannedOccurrence, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            self._require_account_exists(unit_of_work, account_id)
            return self._occurrences(unit_of_work, account_id, through_date)

    def revise_occurrence(
        self,
        *,
        occurrence: PlannedOccurrence,
        expected_amount: Money,
        planned_date: date,
        margin: SafetyMargin | None,
        reason: str,
        metadata: RecordMetadata,
    ) -> PlannedOccurrence:
        with self._unit_of_work_factory() as unit_of_work:
            current = self._current_occurrence(unit_of_work, occurrence)
            if current.recurrence_id is not None:
                horizon = max(current.planned_date, planned_date)
                siblings = self._occurrences(
                    unit_of_work, current.account_id, horizon
                )
                if any(
                    item.recurrence_id == current.recurrence_id
                    and item.occurrence_id != current.occurrence_id
                    and item.planned_date == planned_date
                    for item in siblings
                ):
                    raise ValueError(
                        "occurrence revision conflicts with another recurrence date"
                    )
            revised = current.revise(
                expected_amount=expected_amount,
                planned_date=planned_date,
                margin=margin,
                reason=reason,
                metadata=metadata,
            )
            unit_of_work.planning.save_occurrence(revised)
            unit_of_work.commit()
        return revised

    def revise_recurrence_from(
        self,
        *,
        recurrence_id: UUID,
        from_date: date,
        expected_amount: Money,
        margin: SafetyMargin | None,
        frequency: RecurrenceFrequency | None = None,
        interval: int | None = None,
        reason: str,
        metadata: RecordMetadata,
    ) -> Recurrence:
        with self._unit_of_work_factory() as unit_of_work:
            recurrence = unit_of_work.planning.get_recurrence(recurrence_id)
            if recurrence is None:
                raise LookupError(f"recurrence {recurrence_id} does not exist")
            if (frequency is not None or interval is not None) and any(
                event_date >= from_date
                for event_date in unit_of_work.planning.occurrence_event_dates(
                    recurrence_id
                )
            ):
                raise ValueError(
                    "recurrence cadence cannot replace occurrences with preserved history"
                )
            revised = recurrence.revise_from(
                from_date=from_date,
                expected_amount=expected_amount,
                margin=margin,
                frequency=frequency,
                interval=interval,
                reason=reason,
                metadata=metadata,
            )
            unit_of_work.planning.save_recurrence(revised)
            unit_of_work.commit()
        return revised

    def confirm_occurrence(
        self,
        *,
        occurrence: PlannedOccurrence,
        movement_id: UUID,
        metadata: RecordMetadata,
    ) -> PlannedOccurrence:
        with self._unit_of_work_factory() as unit_of_work:
            current = self._current_occurrence(unit_of_work, occurrence)
            movement = unit_of_work.movements.get(movement_id)
            if movement is None:
                raise LookupError(f"financial movement {movement_id} does not exist")
            if unit_of_work.planning.movement_is_linked(movement_id):
                raise ValueError("movement already confirms another occurrence")
            expected_direction = (
                ForecastDirection.INFLOW
                if isinstance(movement, Income)
                else ForecastDirection.OUTFLOW
                if isinstance(movement, (CashExpense, BillPayment, InvoicePayment))
                else None
            )
            if expected_direction is None or expected_direction is not current.direction:
                raise ValueError("realized movement direction conflicts with forecast direction")
            if movement.account_id != current.account_id:
                raise ValueError("realized movement account conflicts with forecast account")
            confirmed = current.confirm(
                movement_id=movement_id,
                actual_amount=movement.amount,
                metadata=metadata,
            )
            unit_of_work.planning.save_occurrence(confirmed)
            unit_of_work.commit()
        return confirmed

    def confirm_future_amount_update(
        self,
        *,
        confirmed_occurrence: PlannedOccurrence,
        reason: str,
        metadata: RecordMetadata,
    ) -> Recurrence:
        with self._unit_of_work_factory() as unit_of_work:
            current = self._current_occurrence(unit_of_work, confirmed_occurrence)
            if current.confirmation is None:
                raise ValueError("future update requires a confirmed occurrence")
            if current.recurrence_id is None:
                raise ValueError("future update requires a recurring occurrence")
            recurrence = unit_of_work.planning.get_recurrence(current.recurrence_id)
            if recurrence is None:
                raise LookupError(f"recurrence {current.recurrence_id} does not exist")
            from_date = recurrence.next_date_after(
                max(current.planned_date, metadata.effective_date)
            )
            if from_date is None:
                raise ValueError("recurrence has no future occurrences to update")
            if from_date <= metadata.effective_date:
                raise ValueError("future update cannot rewrite past occurrences")
            assert current.actual_amount is not None
            revised = recurrence.revise_from(
                from_date=from_date,
                expected_amount=current.actual_amount,
                margin=current.margin,
                reason=reason,
                metadata=metadata,
            )
            unit_of_work.planning.save_recurrence(revised)
            unit_of_work.commit()
        return revised

    def project_account_balance(
        self,
        *,
        account_id: UUID,
        through_date: date,
        as_of: date,
    ) -> PlanningProjection:
        with self._unit_of_work_factory() as unit_of_work:
            account = self._require_account_exists(unit_of_work, account_id)
            occurrences = [
                item
                for item in self._occurrences(unit_of_work, account_id, through_date)
                if item.confirmation is None
            ]
            lines = tuple(
                PlanningProjectionLine(
                    occurrence_id=item.occurrence_id,
                    planned_date=item.planned_date,
                    status=item.status_on(as_of),
                    direction=item.direction,
                    expected_amount=item.expected_amount,
                    margin_amount=item.margin_amount(),
                    normal_impact=item.normal_impact(),
                    conservative_impact=item.conservative_impact(),
                )
                for item in occurrences
            )
            projected = account.balance
            conservative = account.balance
            for line in lines:
                projected += line.normal_impact
                conservative += line.conservative_impact
            return PlanningProjection(
                account_id=account_id,
                through_date=through_date,
                current_balance=account.balance,
                lines=lines,
                projected_balance=projected,
                conservative_projected_balance=conservative,
            )

    def _occurrences(
        self, unit_of_work: PlanningUnitOfWork, account_id: UUID, through_date: date
    ) -> tuple[PlannedOccurrence, ...]:
        standalone = [
            item
            for item in unit_of_work.planning.list_standalone_occurrences()
            if item.account_id == account_id and item.planned_date <= through_date
        ]
        generated = [
            occurrence
            for recurrence in unit_of_work.planning.list_recurrences()
            if recurrence.account_id == account_id
            for occurrence in recurrence.occurrences_through(
                max(
                    (through_date, *unit_of_work.planning.occurrence_source_dates(
                        recurrence.recurrence_id
                    ))
                )
            )
        ]
        current = [*standalone]
        current.extend(unit_of_work.planning.apply_history(item) for item in generated)
        within_horizon = [item for item in current if item.planned_date <= through_date]
        return tuple(
            sorted(within_horizon, key=lambda item: (item.planned_date, item.occurrence_id))
        )

    @staticmethod
    def _require_account_exists(unit_of_work: PlanningUnitOfWork, account_id: UUID):
        account = unit_of_work.accounts.get(account_id)
        if account is None:
            raise LookupError(f"resource account {account_id} does not exist")
        return account

    @classmethod
    def _require_account(
        cls, unit_of_work: PlanningUnitOfWork, account_id: UUID, currency: str
    ) -> None:
        account = cls._require_account_exists(unit_of_work, account_id)
        if account.status is AccountStatus.ARCHIVED:
            raise ValueError("archived account rejects new forecasts")
        if account.currency != currency:
            raise CurrencyMismatchError("forecast must use the account currency")

    @staticmethod
    def _current_occurrence(
        unit_of_work: PlanningUnitOfWork, supplied: PlannedOccurrence
    ) -> PlannedOccurrence:
        if supplied.recurrence_id is None:
            current = unit_of_work.planning.get_occurrence(supplied.occurrence_id)
            if current is None:
                raise LookupError(f"occurrence {supplied.occurrence_id} does not exist")
        else:
            recurrence = unit_of_work.planning.get_recurrence(supplied.recurrence_id)
            if recurrence is None:
                raise LookupError(f"recurrence {supplied.recurrence_id} does not exist")
            original = supplied.history[0]
            candidates = recurrence.occurrences_through(original.planned_date)
            current = next(
                (item for item in candidates if item.occurrence_id == supplied.occurrence_id),
                None,
            )
            if current is None:
                raise LookupError(f"occurrence {supplied.occurrence_id} does not exist")
            current = unit_of_work.planning.apply_history(current)
        if current != supplied:
            raise ValueError("occurrence changed since it was read")
        return current
