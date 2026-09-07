from __future__ import annotations

from dataclasses import dataclass
from uuid import UUID

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.categories import Allocation, CategoryCatalog, FinancialNature
from bot_financeiro.domain.money import CurrencyMismatchError, Money
from bot_financeiro.domain.resource_account import AccountHistoryEntryKind, ResourceAccount


def _require_positive(amount: Money) -> None:
    if amount.amount <= 0:
        raise ValueError("realized movement amount must be positive")


@dataclass(frozen=True, slots=True)
class Income:
    account_id: UUID
    allocation: Allocation

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.INCOME:
            raise ValueError("income requires an income allocation")
        _require_positive(self.amount)

    @property
    def movement_id(self) -> UUID:
        return self.allocation.allocation_id

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

    @classmethod
    def receive(
        cls,
        *,
        account: ResourceAccount,
        allocation: Allocation,
        catalog: CategoryCatalog,
    ) -> tuple[Income, ResourceAccount]:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        income = cls(account_id=account.account_id, allocation=allocation)
        updated_account = account.record_movement(
            kind=AccountHistoryEntryKind.INCOME,
            difference=income.amount,
            movement_metadata=income.metadata,
        )
        return income, updated_account


@dataclass(frozen=True, slots=True)
class CashExpense:
    account_id: UUID
    allocation: Allocation
    related_transfer_id: UUID | None = None

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("cash expense requires an expense allocation")
        _require_positive(self.amount)

    @property
    def movement_id(self) -> UUID:
        return self.allocation.allocation_id

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

    @classmethod
    def pay(
        cls,
        *,
        account: ResourceAccount,
        allocation: Allocation,
        catalog: CategoryCatalog,
        related_transfer: Transfer | None = None,
    ) -> tuple[CashExpense, ResourceAccount]:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        expense = cls(
            account_id=account.account_id,
            allocation=allocation,
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


@dataclass(frozen=True, slots=True)
class Transfer:
    source_account_id: UUID
    destination_account_id: UUID
    amount: Money
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if self.source_account_id == self.destination_account_id:
            raise ValueError("transfer requires different accounts")
        _require_positive(self.amount)

    @property
    def movement_id(self) -> UUID:
        return self.metadata.record_id

    @property
    def is_income(self) -> bool:
        return False

    @property
    def is_expense(self) -> bool:
        return False

    @classmethod
    def execute(
        cls,
        *,
        source: ResourceAccount,
        destination: ResourceAccount,
        amount: Money,
        metadata: RecordMetadata,
    ) -> tuple[Transfer, ResourceAccount, ResourceAccount]:
        if source.currency != destination.currency:
            raise CurrencyMismatchError("transfer accounts must use the same currency")
        transfer = cls(
            source_account_id=source.account_id,
            destination_account_id=destination.account_id,
            amount=amount,
            metadata=metadata,
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


FinancialMovement = Income | CashExpense | Transfer
