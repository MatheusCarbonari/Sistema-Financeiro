from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.credit_cards import CardPurchase, CreditCard, InvoiceStatus
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import InvoicePayment
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount


def metadata(record_id: str, *, effective_date: date) -> RecordMetadata:
    return RecordMetadata(
        effective_date=effective_date,
        recorded_at=datetime(2026, 9, 9, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def expense_catalog() -> tuple[CategoryCatalog, UUID]:
    category_id = UUID("847ec07b-cad2-4277-a5e8-c6e98e6e4f75")
    return (
        CategoryCatalog.empty().add_category(
            category_id=category_id,
            name="Alimentação",
            nature=FinancialNature.EXPENSE,
        ),
        category_id,
    )


def expense(
    amount: str,
    *,
    effective_date: date,
    record_id: str,
    catalog: CategoryCatalog,
    category_id: UUID,
) -> Allocation:
    money = Money(amount, "BRL")
    return Allocation.create(
        total=money,
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, money),),
        catalog=catalog,
        metadata=metadata(record_id, effective_date=effective_date),
    )


def test_card_purchase_recognizes_expense_and_debt_without_resource_account() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata(
            "cbabb54d-8f88-46a0-9b40-9b8277b1e3c8",
            effective_date=date(2026, 9, 1),
        ),
    )
    allocation = expense(
        "200.00",
        effective_date=date(2026, 9, 9),
        record_id="2464aebe-6570-4ddc-807d-9bd3c42c5ad7",
        catalog=catalog,
        category_id=category_id,
    )

    purchase, updated = card.purchase(allocation=allocation, catalog=catalog)
    invoice = updated.invoice_for_purchase(purchase.purchase_id)

    assert purchase.is_expense is True
    assert invoice.principal_due == Money("200.00", "BRL")
    assert invoice.amount_due == Money("200.00", "BRL")
    assert invoice.status_on(date(2026, 9, 9)) is InvoiceStatus.OPEN
    assert updated.available_limit == Money("800.00", "BRL")
    assert card.available_limit == Money("1000.00", "BRL")


def test_purchase_on_closing_day_stays_in_cycle_and_status_changes_after_boundaries() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata(
            "2e6c8298-640a-4193-ac5a-dc9ddbb8a3c3",
            effective_date=date(2026, 9, 1),
        ),
    )
    allocation = expense(
        "50.00",
        effective_date=date(2026, 9, 10),
        record_id="86f49982-4cb0-422e-a7e9-96b6192d9a97",
        catalog=catalog,
        category_id=category_id,
    )
    _, card = card.purchase(allocation=allocation, catalog=catalog)
    invoice = card.invoice_for_purchase(allocation.allocation_id)

    assert invoice.cycle.closing_date == date(2026, 9, 10)
    assert invoice.cycle.due_date == date(2026, 9, 17)
    assert invoice.status_on(date(2026, 9, 10)) is InvoiceStatus.OPEN
    assert invoice.status_on(date(2026, 9, 11)) is InvoiceStatus.CLOSED
    assert invoice.status_on(date(2026, 9, 17)) is InvoiceStatus.CLOSED
    assert invoice.status_on(date(2026, 9, 18)) is InvoiceStatus.OVERDUE


def test_billing_days_are_clamped_to_month_end() -> None:
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=31,
        due_day=31,
        limit=None,
        metadata=metadata(
            "60146305-388f-41a9-8e52-750ca26d64cd",
            effective_date=date(2028, 2, 1),
        ),
    )

    cycle = card.cycle_for(date(2028, 2, 29))

    assert cycle.closing_date == date(2028, 2, 29)
    assert cycle.due_date == date(2028, 3, 31)


def test_invoice_assignment_correction_is_explicit_and_preserves_previous_assignment() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata(
            "c7370ef0-4655-48c1-94d0-543569690c50",
            effective_date=date(2026, 9, 1),
        ),
    )
    allocation = expense(
        "75.00",
        effective_date=date(2026, 9, 9),
        record_id="9f75826e-2e57-4fab-9fc2-a8e9269eb68c",
        catalog=catalog,
        category_id=category_id,
    )
    purchase, card = card.purchase(allocation=allocation, catalog=catalog)

    correction, corrected = card.correct_invoice_assignment(
        purchase_id=purchase.purchase_id,
        target_closing_date=date(2026, 10, 10),
        reason="A operadora lançou após o fechamento",
        metadata=metadata(
            "8beb9781-b99b-43f2-904f-5d5202655b2e",
            effective_date=date(2026, 9, 12),
        ),
    )

    assert correction.previous_closing_date == date(2026, 9, 10)
    assert correction.target_closing_date == date(2026, 10, 10)
    assert correction.reason == "A operadora lançou após o fechamento"
    assert card.invoice(date(2026, 9, 10)).principal_due == Money("75.00", "BRL")
    assert corrected.invoice(date(2026, 9, 10)).principal_due == Money("0.00", "BRL")
    assert corrected.invoice(date(2026, 10, 10)).principal_due == Money("75.00", "BRL")
    assert corrected.history[-1] == correction
    assert tuple(invoice.cycle.closing_date for invoice in corrected.invoices()) == (
        date(2026, 10, 10),
    )


def test_partial_invoice_payment_reduces_account_debt_and_principal_without_new_expense() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata(
            "410b659c-cb66-4050-ab17-b625330d2ee9",
            effective_date=date(2026, 9, 1),
        ),
    )
    purchase, card = card.purchase(
        allocation=expense(
            "300.00",
            effective_date=date(2026, 9, 9),
            record_id="3ddf1385-0e5b-462b-a13e-d4db0f2e4de8",
            catalog=catalog,
            category_id=category_id,
        ),
        catalog=catalog,
    )
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(
            "5f54b3d7-f616-439d-aef1-92743e2ab5cc",
            effective_date=date(2026, 9, 1),
        ),
    )

    payment, paid_card, paid_account = card.pay_invoice(
        closing_date=purchase.invoice_closing_date,
        account=account,
        amount=Money("100.00", "BRL"),
        metadata=metadata(
            "6390b1a9-7ee8-4214-b3af-3bde7963f8b6",
            effective_date=date(2026, 9, 17),
        ),
    )

    assert isinstance(payment, InvoicePayment)
    assert payment.is_income is False
    assert payment.is_expense is False
    assert paid_account.balance == Money("900.00", "BRL")
    invoice = paid_card.invoice(purchase.invoice_closing_date)
    assert invoice.principal_due == Money("200.00", "BRL")
    assert invoice.amount_due == Money("200.00", "BRL")
    assert invoice.status_on(date(2026, 9, 17)) is InvoiceStatus.PARTIALLY_PAID
    assert invoice.status_on(date(2026, 9, 18)) is InvoiceStatus.OVERDUE
    assert paid_card.available_limit == Money("800.00", "BRL")
    assert card.available_limit == Money("700.00", "BRL")


def test_invoice_payment_cannot_exceed_debt_without_explicit_credit() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata(
            "b092d41b-6b05-45c9-96c4-5220407d29b4",
            effective_date=date(2026, 9, 1),
        ),
    )
    purchase, card = card.purchase(
        allocation=expense(
            "100.00",
            effective_date=date(2026, 9, 9),
            record_id="ba114eb7-429e-4a09-89cf-d73894490b68",
            catalog=catalog,
            category_id=category_id,
        ),
        catalog=catalog,
    )
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(
            "625b7647-125e-4631-8072-e10cb5b7954b",
            effective_date=date(2026, 9, 1),
        ),
    )

    with pytest.raises(ValueError, match="explicit card credit"):
        card.pay_invoice(
            closing_date=purchase.invoice_closing_date,
            account=account,
            amount=Money("110.00", "BRL"),
            metadata=metadata(
                "fd56890a-2d7d-4883-b29c-200f24c7e890",
                effective_date=date(2026, 9, 17),
            ),
        )


def test_financial_charge_is_separate_expense_and_does_not_consume_limit() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata(
            "b55c9c6a-b104-4e00-a897-eeaf376681f5",
            effective_date=date(2026, 9, 1),
        ),
    )
    purchase, card = card.purchase(
        allocation=expense(
            "300.00",
            effective_date=date(2026, 9, 9),
            record_id="2d6b8461-2cfc-4a4f-91e5-aad9e8f02a1f",
            catalog=catalog,
            category_id=category_id,
        ),
        catalog=catalog,
    )
    charge_allocation = expense(
        "20.00",
        effective_date=date(2026, 9, 18),
        record_id="c3b2bd46-ee1f-475e-8eaa-d79785b8830c",
        catalog=catalog,
        category_id=category_id,
    )

    charge, charged = card.add_financial_charge(
        closing_date=purchase.invoice_closing_date,
        allocation=charge_allocation,
        catalog=catalog,
    )

    invoice = charged.invoice(purchase.invoice_closing_date)
    assert charge.is_expense is True
    assert invoice.principal_due == Money("300.00", "BRL")
    assert invoice.financial_charges_due == Money("20.00", "BRL")
    assert invoice.amount_due == Money("320.00", "BRL")
    assert charged.available_limit == Money("700.00", "BRL")


def test_refund_reduces_expense_and_principal_then_creates_non_income_credit() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata(
            "ab6e89af-abd1-462c-bfff-6676076851a4",
            effective_date=date(2026, 9, 1),
        ),
    )
    purchase, card = card.purchase(
        allocation=expense(
            "300.00",
            effective_date=date(2026, 9, 9),
            record_id="193fb0a4-5007-427a-a156-824a85891467",
            catalog=catalog,
            category_id=category_id,
        ),
        catalog=catalog,
    )
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(
            "91e64ec7-478d-459b-bb28-d646aab1c911",
            effective_date=date(2026, 9, 1),
        ),
    )
    _, card, _ = card.pay_invoice(
        closing_date=purchase.invoice_closing_date,
        account=account,
        amount=Money("100.00", "BRL"),
        metadata=metadata(
            "c96b1e83-7846-48f2-b0f8-545d041164c2",
            effective_date=date(2026, 9, 17),
        ),
    )

    refund, refunded = card.refund_purchase(
        purchase_id=purchase.purchase_id,
        amount=Money("250.00", "BRL"),
        metadata=metadata(
            "0a9cce5b-7372-430c-9d53-c5bf6f2e3d5a",
            effective_date=date(2026, 9, 20),
        ),
    )

    assert refund.is_income is False
    assert refund.reduces_expense is True
    assert refund.principal_reduction == Money("200.00", "BRL")
    assert refunded.invoice(purchase.invoice_closing_date).amount_due == Money(
        "0.00", "BRL"
    )
    assert refunded.card_credit_balance == Money("50.00", "BRL")
    assert refunded.available_limit == Money("1000.00", "BRL")


def test_explicit_invoice_overpayment_creates_card_credit() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata(
            "765f8f09-a074-478a-af0d-ab913dc2acd9",
            effective_date=date(2026, 9, 1),
        ),
    )
    purchase, card = card.purchase(
        allocation=expense(
            "100.00",
            effective_date=date(2026, 9, 9),
            record_id="6daf4378-71c4-49f6-8844-29a49a40b351",
            catalog=catalog,
            category_id=category_id,
        ),
        catalog=catalog,
    )
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(
            "b0df9378-072b-46ea-adf1-5a5deeb2dbb6",
            effective_date=date(2026, 9, 1),
        ),
    )

    payment, paid_card, paid_account = card.pay_invoice(
        closing_date=purchase.invoice_closing_date,
        account=account,
        amount=Money("110.00", "BRL"),
        metadata=metadata(
            "53451216-5a86-4744-84ed-13df7f1be950",
            effective_date=date(2026, 9, 17),
        ),
        create_credit_from_excess=True,
    )

    assert payment.applied_amount == Money("100.00", "BRL")
    assert paid_card.invoice(purchase.invoice_closing_date).status_on(
        date(2026, 9, 17)
    ) is InvoiceStatus.PAID
    assert paid_card.card_credit_balance == Money("10.00", "BRL")
    assert paid_account.balance == Money("890.00", "BRL")


def test_rehydrated_purchase_cannot_bypass_calculated_invoice_cycle() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata(
            "28744284-3015-4a84-b3aa-7f513af54279",
            effective_date=date(2026, 9, 1),
        ),
    )
    allocation = expense(
        "50.00",
        effective_date=date(2026, 9, 9),
        record_id="081cc7bc-d950-4b12-b72c-afc8568ff047",
        catalog=catalog,
        category_id=category_id,
    )
    invalid_purchase = CardPurchase(
        card_id=card.card_id,
        allocation=allocation,
        invoice_closing_date=date(2026, 9, 15),
    )

    with pytest.raises(ValueError, match="calculated invoice"):
        CreditCard(history=(*card.history, invalid_purchase))


def test_purchase_above_limit_is_rejected_by_operation_and_rehydration() -> None:
    catalog, category_id = expense_catalog()
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("100.00", "BRL"),
        metadata=metadata(
            "3724e345-17c5-4f45-b480-c639cf979dac",
            effective_date=date(2026, 9, 1),
        ),
    )
    allocation = expense(
        "100.01",
        effective_date=date(2026, 9, 9),
        record_id="8ac00cc3-52bf-4019-9f55-e1cb616cb1cb",
        catalog=catalog,
        category_id=category_id,
    )

    with pytest.raises(ValueError, match="available limit"):
        card.purchase(allocation=allocation, catalog=catalog)

    forged = CardPurchase(
        card_id=card.card_id,
        allocation=allocation,
        invoice_closing_date=date(2026, 9, 10),
    )
    with pytest.raises(ValueError, match="history exceeds"):
        CreditCard(history=(*card.history, forged))
