from __future__ import annotations

from dataclasses import dataclass
from datetime import date
from uuid import UUID

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.categories import Allocation, CategoryCatalog, FinancialNature
from bot_financeiro.domain.descriptions import (
    normalize_financial_description,
    validate_persisted_financial_description,
)
from bot_financeiro.domain.financial_changes import AccountEffect, AccountExpenseRefund, DebtEffect
from bot_financeiro.domain.money import CurrencyMismatchError, Money
from bot_financeiro.domain.resource_account import AccountHistoryEntryKind, ResourceAccount


def _require_positive(amount: Money) -> None:
    if amount.amount <= 0:
        raise ValueError("realized movement amount must be positive")


@dataclass(frozen=True, slots=True)
class Income:
    account_id: UUID
    allocation: Allocation
    description: str | None = None
    original_event_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.INCOME:
            raise ValueError("income requires an income allocation")
        validate_persisted_financial_description(self.description)
        _require_positive(self.amount)

    @property
    def movement_id(self) -> UUID:
        return self.original_event_id or self.allocation.allocation_id

    @property
    def event_id(self) -> UUID:
        return self.movement_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def metadata(self) -> RecordMetadata:
        return self.allocation.metadata

    @property
    def is_income(self) -> bool:
        return True

    @property
    def is_expense(self) -> bool:
        return False

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
        account: ResourceAccount,
        allocation: Allocation,
        catalog: CategoryCatalog,
        description: str,
    ) -> tuple[Income, ResourceAccount]:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        income = cls(
            account_id=account.account_id,
            allocation=allocation,
            description=normalize_financial_description(description),
        )
        updated_account = account.record_movement(
            kind=AccountHistoryEntryKind.INCOME,
            difference=income.amount,
            movement_metadata=income.metadata,
        )
        return income, updated_account

    def ensure_correction_of(self, original: Income, catalog: CategoryCatalog) -> None:
        self.allocation.ensure_categories_compatible(catalog, require_active=True)


@dataclass(frozen=True, slots=True)
class CashExpense:
    account_id: UUID
    allocation: Allocation
    description: str | None = None
    related_transfer_id: UUID | None = None
    original_event_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("cash expense requires an expense allocation")
        validate_persisted_financial_description(self.description)
        _require_positive(self.amount)

    @property
    def movement_id(self) -> UUID:
        return self.original_event_id or self.allocation.allocation_id

    @property
    def event_id(self) -> UUID:
        return self.movement_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def metadata(self) -> RecordMetadata:
        return self.allocation.metadata

    @property
    def is_income(self) -> bool:
        return False

    @property
    def is_expense(self) -> bool:
        return True

    @property
    def account_effects(self) -> tuple[AccountEffect, ...]:
        return (
            AccountEffect(
                self.account_id,
                Money(-self.amount.amount, self.amount.currency),
            ),
        )

    @property
    def debt_effects(self) -> tuple[DebtEffect, ...]:
        return ()

    @classmethod
    def pay(
        cls,
        *,
        account: ResourceAccount,
        allocation: Allocation,
        catalog: CategoryCatalog,
        description: str,
        related_transfer: Transfer | None = None,
    ) -> tuple[CashExpense, ResourceAccount]:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        expense = cls(
            account_id=account.account_id,
            allocation=allocation,
            description=normalize_financial_description(description),
            related_transfer_id=(
                None if related_transfer is None else related_transfer.movement_id
            ),
        )
        updated_account = account.record_movement(
            kind=AccountHistoryEntryKind.CASH_EXPENSE,
            difference=Money(-expense.amount.amount, expense.amount.currency),
            movement_metadata=expense.metadata,
        )
        return expense, updated_account

    def ensure_correction_of(self, original: CashExpense, catalog: CategoryCatalog) -> None:
        if self.related_transfer_id != original.related_transfer_id:
            raise ValueError("a transfer fee remains linked to its original transfer")
        self.allocation.ensure_categories_compatible(catalog, require_active=True)


@dataclass(frozen=True, slots=True)
class Transfer:
    source_account_id: UUID
    destination_account_id: UUID
    amount: Money
    metadata: RecordMetadata
    description: str | None = None
    original_event_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.source_account_id == self.destination_account_id:
            raise ValueError("transfer requires different accounts")
        validate_persisted_financial_description(self.description)
        _require_positive(self.amount)

    @property
    def movement_id(self) -> UUID:
        return self.original_event_id or self.metadata.record_id

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
    def account_effects(self) -> tuple[AccountEffect, ...]:
        return (
            AccountEffect(
                self.source_account_id,
                Money(-self.amount.amount, self.amount.currency),
            ),
            AccountEffect(self.destination_account_id, self.amount),
        )

    @property
    def debt_effects(self) -> tuple[DebtEffect, ...]:
        return ()

    @classmethod
    def execute(
        cls,
        *,
        source: ResourceAccount,
        destination: ResourceAccount,
        amount: Money,
        metadata: RecordMetadata,
        description: str,
    ) -> tuple[Transfer, ResourceAccount, ResourceAccount]:
        if source.currency != destination.currency:
            raise CurrencyMismatchError("transfer accounts must use the same currency")
        transfer = cls(
            source_account_id=source.account_id,
            destination_account_id=destination.account_id,
            amount=amount,
            metadata=metadata,
            description=normalize_financial_description(description),
        )
        updated_source = source.record_movement(
            kind=AccountHistoryEntryKind.TRANSFER_OUT,
            difference=Money(-amount.amount, amount.currency),
            movement_metadata=metadata,
        )
        updated_destination = destination.record_movement(
            kind=AccountHistoryEntryKind.TRANSFER_IN,
            difference=amount,
            movement_metadata=metadata,
        )
        return transfer, updated_source, updated_destination

    def ensure_correction_of(self, original: Transfer, catalog: CategoryCatalog) -> None:
        del original, catalog


@dataclass(frozen=True, slots=True)
class BillPayment:
    payable_id: UUID
    account_id: UUID
    amount: Money
    metadata: RecordMetadata
    description: str | None = None
    original_event_id: UUID | None = None

    def __post_init__(self) -> None:
        validate_persisted_financial_description(self.description)
        _require_positive(self.amount)

    @property
    def movement_id(self) -> UUID:
        return self.original_event_id or self.metadata.record_id

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
    def account_effects(self) -> tuple[AccountEffect, ...]:
        return (
            AccountEffect(
                self.account_id,
                Money(-self.amount.amount, self.amount.currency),
            ),
        )

    @property
    def debt_effects(self) -> tuple[DebtEffect, ...]:
        return (
            DebtEffect(
                self.payable_id,
                Money(-self.amount.amount, self.amount.currency),
            ),
        )

    def ensure_correction_of(self, original: BillPayment, catalog: CategoryCatalog) -> None:
        del catalog
        if self.payable_id != original.payable_id:
            raise ValueError("bill payment correction must preserve its payable")


@dataclass(frozen=True, slots=True)
class InvoicePayment:
    card_id: UUID
    invoice_closing_date: date
    account_id: UUID
    amount: Money
    applied_amount: Money
    metadata: RecordMetadata
    description: str | None = None
    original_event_id: UUID | None = None

    def __post_init__(self) -> None:
        validate_persisted_financial_description(self.description)
        _require_positive(self.amount)
        if self.applied_amount.amount < 0:
            raise ValueError("invoice payment applied amount cannot be negative")
        if self.applied_amount.currency != self.amount.currency:
            raise CurrencyMismatchError("invoice payment values must use the same currency")
        if self.applied_amount > self.amount:
            raise ValueError("invoice payment applied amount cannot exceed cash payment")

    @property
    def movement_id(self) -> UUID:
        return self.original_event_id or self.metadata.record_id

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
    def account_effects(self) -> tuple[AccountEffect, ...]:
        return (
            AccountEffect(
                self.account_id,
                Money(-self.amount.amount, self.amount.currency),
            ),
        )

    @property
    def debt_effects(self) -> tuple[DebtEffect, ...]:
        return (
            DebtEffect(
                self.card_id,
                Money(-self.applied_amount.amount, self.applied_amount.currency),
            ),
        )

    def ensure_correction_of(self, original: InvoicePayment, catalog: CategoryCatalog) -> None:
        del catalog
        if self.card_id != original.card_id:
            raise ValueError("invoice payment correction must preserve its card")
        if self.invoice_closing_date != original.invoice_closing_date:
            raise ValueError("invoice payment correction must preserve its invoice")


FinancialMovement = (
    Income | CashExpense | Transfer | BillPayment | InvoicePayment | AccountExpenseRefund
)
