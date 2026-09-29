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
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import BillPayment
from bot_financeiro.domain.payables import (
    FinancialCharge,
    Payable,
    PayableOpened,
    PayableStatus,
)
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount


def metadata(record_id: str, *, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def test_opening_payable_recognizes_expense_without_changing_resource_account_balance() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("59342d8d-a246-447d-bcb6-2aecaa318b4d", day=1),
    )
    category_id = UUID("76711fa4-0bf0-4942-961f-a37edc49fe89")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Moradia",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("300.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("300.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("45620d40-8275-4e95-b369-63c1ee75b931", day=2),
    )

    payable = Payable.open(
        description="Registro de teste",
        allocation=allocation,
        due_date=date(2026, 9, 10),
        catalog=catalog,
    )

    assert account.balance == Money("1000.00", "BRL")
    assert payable.payable_id == allocation.allocation_id
    assert payable.original_amount == Money("300.00", "BRL")
    assert payable.amount_due == Money("300.00", "BRL")
    assert payable.status_on(date(2026, 9, 2)) is PayableStatus.PENDING
    assert len(payable.history) == 1
    assert isinstance(payable.history[0], PayableOpened)
    assert payable.history[0].is_expense is True


def test_passing_due_date_only_changes_calculated_status() -> None:
    category_id = UUID("86ff79e3-30a2-40fc-8407-729d61dc65ec")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Serviços",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("80.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("80.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("9d38d8e3-e0de-446d-8fa3-ec0d2056265f", day=2),
    )
    payable = Payable.open(
        description="Registro de teste",
        allocation=allocation,
        due_date=date(2026, 9, 10),
        catalog=catalog,
    )

    assert payable.status_on(date(2026, 9, 10)) is PayableStatus.PENDING
    assert payable.status_on(date(2026, 9, 11)) is PayableStatus.OVERDUE
    assert payable.amount_due == Money("80.00", "BRL")
    assert payable.history == (payable.history[0],)


def test_partial_payment_reduces_debt_and_account_balance_without_new_expense() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("e0443ed5-833c-40a9-a79c-d78f83e43188", day=1),
    )
    category_id = UUID("caa6d181-617d-454f-b2b6-34b58013f129")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Moradia",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("300.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("300.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("c907a4f7-fcc9-4db7-a7ce-d5d98c5a3278", day=2),
    )
    payable = Payable.open(
        description="Registro de teste",
        allocation=allocation,
        due_date=date(2026, 9, 10),
        catalog=catalog,
    )
    payment_metadata = metadata("b81d12d3-91aa-443b-8b32-ff52ca0dc62f", day=5)

    payment, partially_paid, updated_account = payable.pay(
        account=account,
        amount=Money("100.00", "BRL"),
        metadata=payment_metadata,
        description="Pagamento da conta",
    )

    assert isinstance(payment, BillPayment)
    assert payment.is_expense is False
    assert payment.payable_id == payable.payable_id
    assert updated_account.balance == Money("900.00", "BRL")
    assert partially_paid.amount_due == Money("200.00", "BRL")
    assert partially_paid.status_on(date(2026, 9, 5)) is PayableStatus.PARTIALLY_PAID
    assert partially_paid.history == (payable.history[0], payment)
    assert payable.amount_due == Money("300.00", "BRL")
    assert account.balance == Money("1000.00", "BRL")


def test_financial_charge_is_a_separate_expense_that_increases_amount_due() -> None:
    principal_category_id = UUID("97e18d31-cc50-48af-b008-da31235df445")
    charge_category_id = UUID("6c01afaf-9b39-469a-b044-f6e1ba450db1")
    catalog = CategoryCatalog.empty().add_category(
        category_id=principal_category_id,
        name="Moradia",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=charge_category_id,
        name="Encargos financeiros",
        nature=FinancialNature.EXPENSE,
    )
    principal = Allocation.create(
        total=Money("300.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(principal_category_id, Money("300.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("51d66046-d38b-4457-9768-a2cbf4e24baa", day=2),
    )
    payable = Payable.open(
        description="Registro de teste",
        allocation=principal,
        due_date=date(2026, 9, 10),
        catalog=catalog,
    )
    charge_allocation = Allocation.create(
        total=Money("20.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(charge_category_id, Money("20.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("2c3e5c23-8605-4d4f-9170-169f44f01ab6", day=11),
    )

    charge, charged = payable.add_financial_charge(
        description="Registro de teste",
        allocation=charge_allocation,
        catalog=catalog,
    )

    assert isinstance(charge, FinancialCharge)
    assert charge.is_expense is True
    assert charge.payable_id == payable.payable_id
    assert charge.allocation == charge_allocation
    assert charged.original_amount == Money("300.00", "BRL")
    assert charged.amount_due == Money("320.00", "BRL")
    assert charged.status_on(date(2026, 9, 9)) is PayableStatus.PENDING
    assert charged.history == (payable.history[0], charge)


def payable_with_account() -> tuple[Payable, ResourceAccount, CategoryCatalog, UUID]:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("229b1054-8b8c-46b1-9cef-38c9a64495e9", day=1),
    )
    category_id = UUID("e0c82287-609f-4e51-b697-f52b220226e3")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Moradia",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("300.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("300.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("c325fb84-7a67-4246-b66f-5b4b25950c0a", day=2),
    )
    return (
        Payable.open(
            description="Registro de teste",
            allocation=allocation,
            due_date=date(2026, 9, 10),
            catalog=catalog,
        ),
        account,
        catalog,
        category_id,
    )


def test_total_payment_marks_payable_as_paid_without_repeating_principal_expense() -> None:
    payable, account, _, _ = payable_with_account()

    payment, paid, updated_account = payable.pay(
        account=account,
        amount=Money("300.00", "BRL"),
        metadata=metadata("4f4e555b-7491-4753-9928-e39935262319", day=5),
        description="Pagamento da conta",
    )

    assert paid.amount_due == Money("0.00", "BRL")
    assert paid.status_on(date(2026, 9, 20)) is PayableStatus.PAID
    assert updated_account.balance == Money("700.00", "BRL")
    assert tuple(event.is_expense for event in paid.history) == (True, False)
    assert payment.is_expense is False


def test_payment_cannot_exceed_amount_due() -> None:
    payable, account, _, _ = payable_with_account()

    with pytest.raises(ValueError, match="exceed"):
        payable.pay(
            account=account,
            amount=Money("300.01", "BRL"),
            metadata=metadata("61245513-6372-48cb-9f22-7456cf1cb7db", day=5),
            description="Pagamento da conta",
        )

    assert payable.amount_due == Money("300.00", "BRL")
    assert account.balance == Money("1000.00", "BRL")


def test_paid_payable_rejects_new_financial_charge() -> None:
    payable, account, catalog, category_id = payable_with_account()
    _, paid, _ = payable.pay(
        account=account,
        amount=Money("300.00", "BRL"),
        metadata=metadata("a9279e87-b3d6-48d2-b9a2-cdc92a9fdab8", day=5),
        description="Pagamento da conta",
    )
    charge = Allocation.create(
        total=Money("1.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("1.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("1659a8c5-f2e6-4158-8e64-578b53fc3fa6", day=11),
    )

    with pytest.raises(ValueError, match="paid payable"):
        paid.add_financial_charge(
            description="Registro de teste", allocation=charge, catalog=catalog
        )


def test_amount_due_combines_original_charge_and_payment_exactly() -> None:
    payable, account, catalog, category_id = payable_with_account()
    charge_allocation = Allocation.create(
        total=Money("20.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("20.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("e3bf60f8-7931-47ca-a3a4-596d03b2c2e9", day=4),
    )
    charge, charged = payable.add_financial_charge(
        description="Registro de teste",
        allocation=charge_allocation,
        catalog=catalog,
    )

    payment, current, _ = charged.pay(
        account=account,
        amount=Money("100.00", "BRL"),
        metadata=metadata("ba4a2ba9-50e4-449c-bb3f-3f1dde1e7516", day=5),
        description="Pagamento da conta",
    )

    assert current.amount_due == Money("220.00", "BRL")
    assert current.history == (payable.history[0], charge, payment)


def test_payable_and_financial_charge_require_expense_allocations() -> None:
    income_category_id = UUID("a7dd56ef-a695-4ddc-b8ee-5f28f1b9b572")
    catalog = CategoryCatalog.empty().add_category(
        category_id=income_category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    income_allocation = Allocation.create(
        total=Money("50.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(income_category_id, Money("50.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("151e4d85-491e-41ef-8292-f002d3303942", day=2),
    )

    with pytest.raises(ValueError, match="expense allocation"):
        Payable.open(
            description="Registro de teste",
            allocation=income_allocation,
            due_date=date(2026, 9, 10),
            catalog=catalog,
        )

    payable, _, expense_catalog, _ = payable_with_account()
    with pytest.raises(ValueError, match="expense allocation"):
        payable.add_financial_charge(
            description="Registro de teste",
            allocation=income_allocation,
            catalog=CategoryCatalog(categories=(*expense_catalog.categories, *catalog.categories)),
        )
