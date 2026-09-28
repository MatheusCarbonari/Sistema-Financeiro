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
from bot_financeiro.domain.credit_cards import (
    CardCashFlow,
    CardCreditApplied,
    CardCreditGranted,
    CardPurchase,
    CreditCard,
    InstallmentPurchase,
    Invoice,
    InvoiceFinancialCharge,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import InvoicePayment


class CreditCardRepository(Protocol):
    def add(self, card: CreditCard) -> None: ...

    def get(self, card_id: UUID) -> CreditCard | None: ...

    def save(self, card: CreditCard) -> None: ...


class CreditCardUnitOfWork(Protocol):
    accounts: ResourceAccountRepository
    categories: FinancialCategoryRepository
    allocations: AllocationRepository
    movements: FinancialMovementRepository
    credit_cards: CreditCardRepository

    def __enter__(self) -> CreditCardUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


class CreditCardOperations:
    def __init__(self, unit_of_work_factory: Callable[[], CreditCardUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def create_card(
        self,
        *,
        name: str,
        currency: str,
        closing_day: int,
        due_day: int,
        limit: Money | None,
        metadata: RecordMetadata,
    ) -> CreditCard:
        card = CreditCard.open(
            name=name,
            currency=currency,
            closing_day=closing_day,
            due_day=due_day,
            limit=limit,
            metadata=metadata,
        )
        with self._unit_of_work_factory() as unit_of_work:
            unit_of_work.credit_cards.add(card)
            unit_of_work.commit()
        return card

    def get_card(self, card_id: UUID) -> CreditCard | None:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.credit_cards.get(card_id)

    def record_purchase(self, *, card_id: UUID, allocation: Allocation) -> CardPurchase:
        with self._unit_of_work_factory() as unit_of_work:
            card = self._card(unit_of_work, card_id)
            purchase, updated = card.purchase(
                allocation=allocation,
                catalog=unit_of_work.categories.catalog(),
            )
            unit_of_work.allocations.add(allocation)
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
        return purchase

    def record_installment_purchase(
        self,
        *,
        card_id: UUID,
        allocation: Allocation,
        installment_count: int,
    ) -> InstallmentPurchase:
        with self._unit_of_work_factory() as unit_of_work:
            card = self._card(unit_of_work, card_id)
            purchase, updated = card.purchase_in_installments(
                allocation=allocation,
                installment_count=installment_count,
                catalog=unit_of_work.categories.catalog(),
            )
            unit_of_work.allocations.add(allocation)
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
        return purchase

    def pay_invoice(
        self,
        *,
        card_id: UUID,
        closing_date: date,
        account_id: UUID,
        amount: Money,
        metadata: RecordMetadata,
        create_credit_from_excess: bool = False,
    ) -> InvoicePayment:
        with self._unit_of_work_factory() as unit_of_work:
            card = self._card(unit_of_work, card_id)
            account = unit_of_work.accounts.get(account_id)
            if account is None:
                raise LookupError(f"resource account {account_id} does not exist")
            payment, updated_card, updated_account = card.pay_invoice(
                closing_date=closing_date,
                account=account,
                amount=amount,
                metadata=metadata,
                create_credit_from_excess=create_credit_from_excess,
            )
            unit_of_work.movements.add(payment)
            unit_of_work.credit_cards.save(updated_card)
            unit_of_work.accounts.save(updated_account)
            unit_of_work.commit()
        return payment

    def add_financial_charge(
        self,
        *,
        card_id: UUID,
        closing_date: date,
        allocation: Allocation,
    ) -> InvoiceFinancialCharge:
        with self._unit_of_work_factory() as unit_of_work:
            card = self._card(unit_of_work, card_id)
            charge, updated = card.add_financial_charge(
                closing_date=closing_date,
                allocation=allocation,
                catalog=unit_of_work.categories.catalog(),
            )
            unit_of_work.allocations.add(allocation)
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
        return charge

    def grant_card_credit(
        self,
        *,
        card_id: UUID,
        amount: Money,
        reason: str,
        metadata: RecordMetadata,
    ) -> CardCreditGranted:
        with self._unit_of_work_factory() as unit_of_work:
            card = self._card(unit_of_work, card_id)
            credit, updated = card.grant_card_credit(
                amount=amount,
                reason=reason,
                metadata=metadata,
            )
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
        return credit

    def apply_card_credit(
        self,
        *,
        card_id: UUID,
        closing_date: date,
        amount: Money,
        metadata: RecordMetadata,
    ) -> CardCreditApplied:
        with self._unit_of_work_factory() as unit_of_work:
            card = self._card(unit_of_work, card_id)
            applied, updated = card.apply_card_credit(
                closing_date=closing_date,
                amount=amount,
                metadata=metadata,
            )
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
        return applied

    def get_invoice(self, *, card_id: UUID, closing_date: date) -> Invoice:
        with self._unit_of_work_factory() as unit_of_work:
            return self._card(unit_of_work, card_id).invoice(closing_date)

    def list_invoices(self, *, card_id: UUID) -> tuple[Invoice, ...]:
        with self._unit_of_work_factory() as unit_of_work:
            return self._card(unit_of_work, card_id).invoices()

    def get_cash_flow(
        self,
        *,
        card_id: UUID,
        start_date: date,
        end_date: date,
    ) -> CardCashFlow:
        with self._unit_of_work_factory() as unit_of_work:
            return self._card(unit_of_work, card_id).cash_flow(
                start_date=start_date,
                end_date=end_date,
            )

    @staticmethod
    def _card(unit_of_work: CreditCardUnitOfWork, card_id: UUID) -> CreditCard:
        card = unit_of_work.credit_cards.get(card_id)
        if card is None:
            raise LookupError(f"credit card {card_id} does not exist")
        return card
