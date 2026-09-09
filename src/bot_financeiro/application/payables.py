from __future__ import annotations

from collections.abc import Callable
from datetime import date
from types import TracebackType
from typing import Protocol
from uuid import UUID

from bot_financeiro.application.categories import (
    AllocationRepository,
    FinancialCategoryRepository,
)
from bot_financeiro.application.financial_operations import FinancialMovementRepository
from bot_financeiro.application.resource_accounts import ResourceAccountRepository
from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.categories import Allocation
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import BillPayment
from bot_financeiro.domain.payables import FinancialCharge, Payable


class PayableRepository(Protocol):
    def add(self, payable: Payable) -> None: ...

    def get(self, payable_id: UUID) -> Payable | None: ...

    def save(self, payable: Payable) -> None: ...


class PayableUnitOfWork(Protocol):
    accounts: ResourceAccountRepository
    categories: FinancialCategoryRepository
    allocations: AllocationRepository
    movements: FinancialMovementRepository
    payables: PayableRepository

    def __enter__(self) -> PayableUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class PayableOperations:
    def __init__(self, unit_of_work_factory: Callable[[], PayableUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_payable(self, *, allocation: Allocation, due_date: date) -> Payable:
        with self._unit_of_work_factory() as unit_of_work:
            payable = Payable.open(
                allocation=allocation,
                due_date=due_date,
                catalog=unit_of_work.categories.catalog(),
            )
            unit_of_work.allocations.add(allocation)
            unit_of_work.payables.add(payable)
            unit_of_work.commit()
        return payable

    def get_payable(self, payable_id: UUID) -> Payable | None:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.payables.get(payable_id)

    def pay_payable(
        self,
        *,
        payable_id: UUID,
        account_id: UUID,
        amount: Money,
        metadata: RecordMetadata,
    ) -> BillPayment:
        with self._unit_of_work_factory() as unit_of_work:
            payable = unit_of_work.payables.get(payable_id)
            account = unit_of_work.accounts.get(account_id)
            if payable is None:
                raise LookupError(f"payable {payable_id} does not exist")
            if account is None:
                raise LookupError(f"resource account {account_id} does not exist")
            payment, updated_payable, updated_account = payable.pay(
                account=account,
                amount=amount,
                metadata=metadata,
            )
            unit_of_work.movements.add(payment)
            unit_of_work.payables.save(updated_payable)
            unit_of_work.accounts.save(updated_account)
            unit_of_work.commit()
        return payment

    def add_financial_charge(
        self,
        *,
        payable_id: UUID,
        allocation: Allocation,
    ) -> FinancialCharge:
        with self._unit_of_work_factory() as unit_of_work:
            payable = unit_of_work.payables.get(payable_id)
            if payable is None:
                raise LookupError(f"payable {payable_id} does not exist")
            charge, updated_payable = payable.add_financial_charge(
                allocation=allocation,
                catalog=unit_of_work.categories.catalog(),
            )
            unit_of_work.allocations.add(allocation)
            unit_of_work.payables.save(updated_payable)
            unit_of_work.commit()
        return charge
