from __future__ import annotations

from dataclasses import replace
from typing import Protocol
from uuid import UUID

from bot_financeiro.application.credit_cards import CreditCardRepository
from bot_financeiro.application.payables import PayableRepository
from bot_financeiro.domain.credit_cards import (
    CardPurchase,
    InstallmentPurchase,
    InvoiceFinancialCharge,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import (
    BillPayment,
    CashExpense,
    Income,
    InvoicePayment,
    Transfer,
)
from bot_financeiro.domain.payables import FinancialCharge, PayableOpened


class ChangeDependencyContext(Protocol):
    credit_cards: CreditCardRepository
    payables: PayableRepository


class PayableChangePolicy:
    @staticmethod
    def blockers(current, proposed, context: ChangeDependencyContext) -> tuple[str, ...]:
        if isinstance(current, BillPayment):
            payable = context.payables.get(current.payable_id)
            if payable is None:
                return ("linked payable does not exist",)
            proposed_amount = (
                Money("0", current.amount.currency) if proposed is None else proposed.amount
            )
            try:
                payable.ensure_payment_change_allowed(
                    original_amount=current.amount,
                    proposed_amount=proposed_amount,
                )
            except ValueError as error:
                return (str(error),)
        if isinstance(current, (PayableOpened, FinancialCharge)):
            payable_id = (
                current.event_id if isinstance(current, PayableOpened) else current.payable_id
            )
            payable = context.payables.get(payable_id)
            if payable is None:
                return ("linked payable does not exist",)
            after = Money("0", current.amount.currency) if proposed is None else proposed.amount
            try:
                payable.ensure_debt_fact_change_allowed(
                    original_amount=current.amount,
                    proposed_amount=after,
                )
            except ValueError as error:
                return (str(error),)
        return ()


class CreditCardChangePolicy:
    @staticmethod
    def blockers(current, proposed, context: ChangeDependencyContext) -> tuple[str, ...]:
        if isinstance(current, InvoicePayment):
            card = context.credit_cards.get(current.card_id)
            if card is None:
                return ("linked credit card does not exist",)
            proposed_applied = (
                Money("0", current.applied_amount.currency)
                if proposed is None
                else proposed.applied_amount
            )
            try:
                card.ensure_invoice_payment_change_allowed(current, proposed_applied)
            except ValueError as error:
                return (str(error),)
        if isinstance(current, (CardPurchase, InstallmentPurchase, InvoiceFinancialCharge)):
            card = context.credit_cards.get(current.card_id)
            if card is None:
                return ("linked credit card does not exist",)
            try:
                card.ensure_financial_fact_change_allowed(current)
            except ValueError as error:
                return (str(error),)
        return ()


def financial_fact_change_blockers(
    current,
    proposed,
    context: ChangeDependencyContext,
) -> tuple[str, ...]:
    return (
        *PayableChangePolicy.blockers(current, proposed, context),
        *CreditCardChangePolicy.blockers(current, proposed, context),
    )


def normalize_financial_fact_replacement(target_id: UUID, current, replacement):
    if isinstance(
        replacement,
        (
            Income,
            CashExpense,
            Transfer,
            BillPayment,
            InvoicePayment,
            PayableOpened,
            FinancialCharge,
            CardPurchase,
            InvoiceFinancialCharge,
        ),
    ):
        description = getattr(replacement, "description", None)
        if description is None:
            description = getattr(current, "description", None)
        return replace(
            replacement,
            original_event_id=target_id,
            description=description,
        )
    if isinstance(replacement, InstallmentPurchase):
        raise ValueError("installment purchase corrections use their specific preview flow")
    return replacement
