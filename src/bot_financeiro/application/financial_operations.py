from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from types import TracebackType
from typing import Protocol
from uuid import UUID

from bot_financeiro.application.categories import (
    AllocationRepository,
    FinancialCategoryRepository,
)
from bot_financeiro.application.resource_accounts import ResourceAccountRepository
from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.categories import Allocation
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import CashExpense, FinancialMovement, Income, Transfer
from bot_financeiro.domain.resource_account import AccountStatement


class FinancialMovementRepository(Protocol):
    def add(self, movement: FinancialMovement) -> None: ...

    def get(self, movement_id: UUID) -> FinancialMovement | None: ...

    def list_all(self) -> tuple[FinancialMovement, ...]: ...


class FinancialOperationUnitOfWork(Protocol):
    accounts: ResourceAccountRepository
    categories: FinancialCategoryRepository
    allocations: AllocationRepository
    movements: FinancialMovementRepository

    def __enter__(self) -> FinancialOperationUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class TransferResult:
    transfer: Transfer
    fee_expense: CashExpense | None = None


class FinancialOperations:
    def __init__(
        self,
        unit_of_work_factory: Callable[[], FinancialOperationUnitOfWork],
    ) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def receive_income(
        self, account_id: UUID, allocation: Allocation, *, description: str
    ) -> Income:
        with self._unit_of_work_factory() as unit_of_work:
            account = unit_of_work.accounts.get(account_id)
            if account is None:
                raise LookupError(f"resource account {account_id} does not exist")
            income, updated_account = Income.receive(
                account=account,
                allocation=allocation,
                catalog=unit_of_work.categories.catalog(),
                description=description,
            )
            unit_of_work.allocations.add(allocation)
            unit_of_work.movements.add(income)
            unit_of_work.accounts.save(updated_account)
            unit_of_work.commit()
        return income

    def pay_cash_expense(
        self, account_id: UUID, allocation: Allocation, *, description: str
    ) -> CashExpense:
        with self._unit_of_work_factory() as unit_of_work:
            account = unit_of_work.accounts.get(account_id)
            if account is None:
                raise LookupError(f"resource account {account_id} does not exist")
            expense, updated_account = CashExpense.pay(
                account=account,
                allocation=allocation,
                catalog=unit_of_work.categories.catalog(),
                description=description,
            )
            unit_of_work.allocations.add(allocation)
            unit_of_work.movements.add(expense)
            unit_of_work.accounts.save(updated_account)
            unit_of_work.commit()
        return expense

    def transfer(
        self,
        *,
        source_account_id: UUID,
        destination_account_id: UUID,
        amount: Money,
        metadata: RecordMetadata,
        description: str,
        fee_allocation: Allocation | None = None,
        fee_description: str | None = None,
    ) -> TransferResult:
        with self._unit_of_work_factory() as unit_of_work:
            source = unit_of_work.accounts.get(source_account_id)
            destination = unit_of_work.accounts.get(destination_account_id)
            if source is None:
                raise LookupError(f"resource account {source_account_id} does not exist")
            if destination is None:
                raise LookupError(f"resource account {destination_account_id} does not exist")
            transfer, updated_source, updated_destination = Transfer.execute(
                source=source,
                destination=destination,
                amount=amount,
                metadata=metadata,
                description=description,
            )
            fee_expense = None
            if fee_allocation is not None:
                if fee_description is None:
                    raise ValueError("transfer fee requires a financial description")
                fee_expense, updated_source = CashExpense.pay(
                    account=updated_source,
                    allocation=fee_allocation,
                    catalog=unit_of_work.categories.catalog(),
                    description=fee_description,
                    related_transfer=transfer,
                )
            unit_of_work.movements.add(transfer)
            if fee_expense is not None:
                unit_of_work.allocations.add(fee_allocation)
                unit_of_work.movements.add(fee_expense)
            unit_of_work.accounts.save(updated_source)
            unit_of_work.accounts.save(updated_destination)
            unit_of_work.commit()
        return TransferResult(transfer=transfer, fee_expense=fee_expense)

    def account_statement(self, account_id: UUID) -> AccountStatement:
        with self._unit_of_work_factory() as unit_of_work:
            account = unit_of_work.accounts.get(account_id)
            if account is None:
                raise LookupError(f"resource account {account_id} does not exist")
            return account.statement()

    def get_movement(self, movement_id: UUID) -> FinancialMovement | None:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.movements.get(movement_id)

    def list_movements(self) -> tuple[FinancialMovement, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.movements.list_all()
