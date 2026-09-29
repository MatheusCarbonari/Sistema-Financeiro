from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import datetime
from typing import Protocol
from uuid import UUID

from bot_financeiro.domain.audit import RecordMetadata, ensure_registration_order
from bot_financeiro.domain.categories import CategoryCatalog
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.resource_account import (
    AccountHistoryEntryKind,
    ResourceAccount,
)


@dataclass(frozen=True, slots=True)
class AccountEffect:
    account_id: UUID
    amount: Money


@dataclass(frozen=True, slots=True)
class DebtEffect:
    owner_id: UUID
    amount: Money


@dataclass(frozen=True, slots=True)
class AccountExpenseRefund:
    original_expense_id: UUID
    account_id: UUID
    amount: Money
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if self.amount.amount <= 0:
            raise ValueError("refund amount must be positive")

    @property
    def movement_id(self) -> UUID:
        return self.metadata.record_id

    @property
    def event_id(self) -> UUID:
        return self.movement_id

    @property
    def is_income(self) -> bool:
        return False

    @property
    def is_expense(self) -> bool:
        return False

    @property
    def reduces_expense(self) -> bool:
        return True

    @property
    def account_effects(self) -> tuple[AccountEffect, ...]:
        return (AccountEffect(self.account_id, self.amount),)

    @property
    def debt_effects(self) -> tuple[DebtEffect, ...]:
        return ()

    @classmethod
    def receive(
        cls,
        *,
        original_expense_id: UUID,
        account: ResourceAccount,
        amount: Money,
        metadata: RecordMetadata,
    ) -> tuple[AccountExpenseRefund, ResourceAccount]:
        refund = cls(
            original_expense_id=original_expense_id,
            account_id=account.account_id,
            amount=amount,
            metadata=metadata,
        )
        updated_account = account.record_movement(
            kind=AccountHistoryEntryKind.ACCOUNT_EXPENSE_REFUND,
            difference=amount,
            movement_metadata=metadata,
        )
        return refund, updated_account


@dataclass(frozen=True, slots=True)
class ExpenseRefundPosition:
    gross_expense: Money
    refunded_amount: Money

    def __post_init__(self) -> None:
        if self.gross_expense.currency != self.refunded_amount.currency:
            raise ValueError("expense and refund totals must use the same currency")
        if self.refunded_amount.amount < 0:
            raise ValueError("refunded amount cannot be negative")
        if self.refunded_amount > self.gross_expense:
            raise ValueError("refunded amount cannot exceed the expense")

    def ensure_can_refund(self, requested: Money, *, receiving_currency: str) -> None:
        if requested.currency != self.gross_expense.currency:
            raise ValueError("refund currency differs from the expense")
        if receiving_currency != requested.currency:
            raise ValueError("refund currency differs from the receiving channel")
        if requested.amount <= 0:
            raise ValueError("refund amount must be positive")
        if self.refunded_amount + requested > self.gross_expense:
            raise ValueError("refund exceeds the current expense")

    def ensure_expense_can_change_to(self, proposed: Money) -> None:
        if proposed.currency != self.gross_expense.currency:
            raise ValueError("correction must preserve currency")
        if self.refunded_amount > proposed:
            raise ValueError("existing refunds exceed the proposed expense")

    def ensure_expense_can_be_canceled(self) -> None:
        if self.refunded_amount.amount > 0:
            raise ValueError("expense already has refunds")


def ensure_financial_fact_correction(
    current,
    replacement,
    catalog: CategoryCatalog,
) -> None:
    if type(current) is not type(replacement):
        raise ValueError("correction must preserve the financial fact type")
    if current.amount.currency != replacement.amount.currency:
        raise ValueError("correction must preserve currency")
    if not hasattr(replacement, "ensure_correction_of"):
        raise ValueError("this financial fact requires its specific correction handler")
    replacement.ensure_correction_of(current, catalog)


class FinancialFactIdentity(Protocol):
    @property
    def event_id(self) -> UUID: ...

    @property
    def metadata(self) -> RecordMetadata: ...


@dataclass(frozen=True, slots=True)
class FinancialFactCorrected[FinancialFact: FinancialFactIdentity]:
    target_id: UUID
    previous_version_id: UUID
    replacement: FinancialFact
    reason: str
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("correction reason is required")

    @property
    def change_id(self) -> UUID:
        return self.metadata.record_id


@dataclass(frozen=True, slots=True)
class FinancialFactCanceled:
    target_id: UUID
    previous_version_id: UUID
    reason: str
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("cancellation reason is required")

    @property
    def change_id(self) -> UUID:
        return self.metadata.record_id


FactChange = FinancialFactCorrected[FinancialFactIdentity] | FinancialFactCanceled


@dataclass(frozen=True, slots=True)
class FinancialFactHistory[FinancialFact: FinancialFactIdentity]:
    original: FinancialFact
    events: tuple[FinancialFactCorrected[FinancialFact] | FinancialFactCanceled, ...] = ()

    def __post_init__(self) -> None:
        target_id = self.original.event_id
        version_id = target_id
        canceled = False
        seen_ids: set[UUID] = set()
        previous_metadata = self.original.metadata
        for event in self.events:
            if event.change_id in seen_ids:
                raise ValueError("financial change identities must be unique")
            seen_ids.add(event.change_id)
            if event.target_id != target_id:
                raise ValueError("financial change belongs to another fact")
            if event.previous_version_id != version_id:
                raise ValueError("financial change does not follow the current version")
            if canceled:
                raise ValueError("cancellation is terminal")
            ensure_registration_order(event.metadata, previous_metadata)
            previous_metadata = event.metadata
            version_id = event.change_id
            canceled = isinstance(event, FinancialFactCanceled)

    @property
    def target_id(self) -> UUID:
        return self.original.event_id

    @property
    def version_id(self) -> UUID:
        return self.target_id if not self.events else self.events[-1].change_id

    @property
    def is_canceled(self) -> bool:
        return bool(self.events and isinstance(self.events[-1], FinancialFactCanceled))

    @property
    def current(self) -> FinancialFact | None:
        if self.is_canceled:
            return None
        for event in reversed(self.events):
            if isinstance(event, FinancialFactCorrected):
                return event.replacement
        return self.original

    def append_correction(
        self, correction: FinancialFactCorrected[FinancialFact]
    ) -> FinancialFactHistory[FinancialFact]:
        if self.is_canceled:
            raise ValueError("cancellation is terminal")
        return replace(self, events=(*self.events, correction))

    def append_cancellation(
        self, cancellation: FinancialFactCanceled
    ) -> FinancialFactHistory[FinancialFact]:
        if self.is_canceled:
            raise ValueError("cancellation is terminal")
        return replace(self, events=(*self.events, cancellation))

    def state_recorded_at(self, moment: datetime) -> FinancialFact | None:
        current: FinancialFact | None = self.original
        for event in self.events:
            if event.metadata.recorded_at > moment:
                break
            if isinstance(event, FinancialFactCanceled):
                current = None
            else:
                current = event.replacement
        return current
