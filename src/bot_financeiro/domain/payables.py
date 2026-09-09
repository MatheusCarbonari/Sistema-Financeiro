from __future__ import annotations

from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from uuid import UUID

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.categories import Allocation, CategoryCatalog, FinancialNature
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

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("payable opening requires an expense allocation")
        if self.allocation.total.amount <= 0:
            raise ValueError("payable original amount must be positive")

    @property
    def event_id(self) -> UUID:
        return self.allocation.allocation_id

    @property
    def is_expense(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class FinancialCharge:
    payable_id: UUID
    allocation: Allocation

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("financial charge requires an expense allocation")
        if self.allocation.total.amount <= 0:
            raise ValueError("financial charge amount must be positive")

    @property
    def event_id(self) -> UUID:
        return self.allocation.allocation_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def is_expense(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Payable:
    history: tuple[PayableOpened | BillPayment | FinancialCharge, ...]

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
    ) -> Payable:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        return cls(history=(PayableOpened(allocation=allocation, due_date=due_date),))

    @property
    def payable_id(self) -> UUID:
        return self.history[0].event_id

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
    ) -> tuple[BillPayment, Payable, ResourceAccount]:
        payment = BillPayment(
            payable_id=self.payable_id,
            account_id=account.account_id,
            amount=amount,
            metadata=metadata,
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
    ) -> tuple[FinancialCharge, Payable]:
        if self.amount_due.amount == 0:
            raise ValueError("paid payable rejects new financial charges")
        allocation.ensure_categories_compatible(catalog, require_active=True)
        charge = FinancialCharge(payable_id=self.payable_id, allocation=allocation)
        return charge, replace(self, history=(*self.history, charge))

    def ensure_append_only_successor_of(self, previous: Payable) -> None:
        previous_size = len(previous.history)
        if (
            self.payable_id != previous.payable_id
            or len(self.history) < previous_size
            or self.history[:previous_size] != previous.history
        ):
            raise ValueError("persisted payable history is immutable")

    def status_on(self, on_date: date) -> PayableStatus:
        if self.amount_due.amount == 0:
            return PayableStatus.PAID
        if on_date > self.opening.due_date:
            return PayableStatus.OVERDUE
        if any(isinstance(event, BillPayment) for event in self.history[1:]):
            return PayableStatus.PARTIALLY_PAID
        return PayableStatus.PENDING
