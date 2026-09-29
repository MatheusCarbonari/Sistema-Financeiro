from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from uuid import UUID

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.categories import Allocation, CategoryCatalog, FinancialNature
from bot_financeiro.domain.descriptions import (
    normalize_financial_description,
    validate_persisted_financial_description,
)
from bot_financeiro.domain.financial_changes import AccountEffect, DebtEffect
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import BillPayment
from bot_financeiro.domain.resource_account import AccountHistoryEntryKind, ResourceAccount


class PayableStatus(StrEnum):
    PENDING = "pending"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"


@dataclass(frozen=True, slots=True)
class PayableOpened:
    allocation: Allocation
    due_date: date
    description: str | None = None
    original_event_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("payable opening requires an expense allocation")
        validate_persisted_financial_description(self.description)
        if self.allocation.total.amount <= 0:
            raise ValueError("payable original amount must be positive")

    @property
    def event_id(self) -> UUID:
        return self.original_event_id or self.allocation.allocation_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def metadata(self) -> RecordMetadata:
        return self.allocation.metadata

    @property
    def is_expense(self) -> bool:
        return True

    @property
    def account_effects(self) -> tuple[AccountEffect, ...]:
        return ()

    @property
    def debt_effects(self) -> tuple[DebtEffect, ...]:
        return (DebtEffect(self.event_id, self.amount),)

    def ensure_correction_of(self, original: PayableOpened, catalog: CategoryCatalog) -> None:
        del original
        self.allocation.ensure_categories_compatible(catalog, require_active=True)


@dataclass(frozen=True, slots=True)
class FinancialCharge:
    payable_id: UUID
    allocation: Allocation
    description: str | None = None
    original_event_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("financial charge requires an expense allocation")
        validate_persisted_financial_description(self.description)
        if self.allocation.total.amount <= 0:
            raise ValueError("financial charge amount must be positive")

    @property
    def event_id(self) -> UUID:
        return self.original_event_id or self.allocation.allocation_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def metadata(self) -> RecordMetadata:
        return self.allocation.metadata

    @property
    def is_expense(self) -> bool:
        return True

    @property
    def account_effects(self) -> tuple[AccountEffect, ...]:
        return ()

    @property
    def debt_effects(self) -> tuple[DebtEffect, ...]:
        return (DebtEffect(self.payable_id, self.amount),)

    def ensure_correction_of(self, original: FinancialCharge, catalog: CategoryCatalog) -> None:
        if self.payable_id != original.payable_id:
            raise ValueError("financial charge correction must preserve its payable")
        self.allocation.ensure_categories_compatible(catalog, require_active=True)


@dataclass(frozen=True, slots=True)
class Payable:
    history: tuple[PayableOpened | BillPayment | FinancialCharge, ...]
    identity: UUID | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.history, tuple) or not self.history:
            raise ValueError("payable history must be a non-empty immutable tuple")
        if not isinstance(self.history[0], PayableOpened):
            raise ValueError("payable history must begin with its opening")

        seen_event_ids: set[UUID] = {self.history[0].event_id}
        for event in self.history[1:]:
            if isinstance(event, PayableOpened):
                raise ValueError("payable can be opened only once")
            if event.event_id in seen_event_ids:
                raise ValueError("payable history event identities must be unique")
            seen_event_ids.add(event.event_id)
            if event.payable_id != self.payable_id:
                raise ValueError("payable history event belongs to another payable")
        self._calculate_amount_due()

    @classmethod
    def open(
        cls,
        *,
        allocation: Allocation,
        due_date: date,
        catalog: CategoryCatalog,
        description: str,
    ) -> Payable:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        return cls(
            history=(
                PayableOpened(
                    allocation=allocation,
                    due_date=due_date,
                    description=normalize_financial_description(description),
                ),
            ),
            identity=allocation.allocation_id,
        )

    @property
    def payable_id(self) -> UUID:
        return self.identity or self.history[0].event_id

    @property
    def original_amount(self) -> Money:
        return self.opening.allocation.total

    @property
    def amount_due(self) -> Money:
        return self._calculate_amount_due()

    def _calculate_amount_due(self) -> Money:
        balance = self.original_amount
        for event in self.history[1:]:
            if isinstance(event, BillPayment):
                if event.amount > balance:
                    raise ValueError("bill payment cannot exceed the amount due")
                balance = Money(balance.amount - event.amount.amount, balance.currency)
            else:
                if balance.amount == 0:
                    raise ValueError("paid payable rejects new financial charges")
                balance += event.amount
        return balance

    @property
    def opening(self) -> PayableOpened:
        opening = self.history[0]
        if not isinstance(opening, PayableOpened):
            raise ValueError("payable history must begin with its opening")
        return opening

    def pay(
        self,
        *,
        account: ResourceAccount,
        amount: Money,
        metadata: RecordMetadata,
        description: str,
    ) -> tuple[BillPayment, Payable, ResourceAccount]:
        payment = BillPayment(
            payable_id=self.payable_id,
            account_id=account.account_id,
            amount=amount,
            metadata=metadata,
            description=normalize_financial_description(description),
        )
        if payment.amount > self.amount_due:
            raise ValueError("bill payment cannot exceed the amount due")
        updated_account = account.record_movement(
            kind=AccountHistoryEntryKind.BILL_PAYMENT,
            difference=Money(-payment.amount.amount, payment.amount.currency),
            movement_metadata=payment.metadata,
        )
        return payment, replace(self, history=(*self.history, payment)), updated_account

    def add_financial_charge(
        self,
        *,
        allocation: Allocation,
        catalog: CategoryCatalog,
        description: str,
    ) -> tuple[FinancialCharge, Payable]:
        if self.amount_due.amount == 0:
            raise ValueError("paid payable rejects new financial charges")
        allocation.ensure_categories_compatible(catalog, require_active=True)
        charge = FinancialCharge(
            payable_id=self.payable_id,
            allocation=allocation,
            description=normalize_financial_description(description),
        )
        return charge, replace(self, history=(*self.history, charge))

    def ensure_append_only_successor_of(self, previous: Payable) -> None:
        previous_size = len(previous.history)
        if (
            self.payable_id != previous.payable_id
            or len(self.history) < previous_size
            or self.history[:previous_size] != previous.history
        ):
            raise ValueError("persisted payable history is immutable")

    def ensure_payment_change_allowed(
        self,
        *,
        original_amount: Money,
        proposed_amount: Money,
    ) -> None:
        recalculated = Money(
            self.amount_due.amount + original_amount.amount - proposed_amount.amount,
            self.amount_due.currency,
        )
        if recalculated.amount < 0:
            raise ValueError("corrected payment exceeds the payable debt")

    def ensure_debt_fact_change_allowed(
        self,
        *,
        original_amount: Money,
        proposed_amount: Money,
    ) -> None:
        recalculated = Money(
            self.amount_due.amount - original_amount.amount + proposed_amount.amount,
            self.amount_due.currency,
        )
        if recalculated.amount < 0:
            raise ValueError("change would invalidate payments already consumed by the payable")

    def status_on(self, on_date: date) -> PayableStatus:
        if self.amount_due.amount == 0:
            return PayableStatus.PAID
        if on_date > self.opening.due_date:
            return PayableStatus.OVERDUE
        if any(isinstance(event, BillPayment) for event in self.history[1:]):
            return PayableStatus.PARTIALLY_PAID
        return PayableStatus.PENDING
