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
from bot_financeiro.domain.credit_cards import CreditCard
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import Income
from bot_financeiro.domain.planning import (
    ForecastDirection,
    Recurrence,
    RecurrenceFrequency,
)
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount


def metadata(record_id: str, *, day: int = 1) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def test_new_income_requires_and_normalizes_its_financial_description() -> None:
    category_id = UUID("00000000-0000-0000-0000-000000000101")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0", "BRL"),
        metadata=metadata("00000000-0000-0000-0000-000000000001"),
    )
    allocation = Allocation.create(
        total=Money("1000", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("1000", "BRL")),),
        catalog=catalog,
        metadata=metadata("00000000-0000-0000-0000-000000000002", day=2),
    )

    income, _ = Income.receive(
        account=account,
        allocation=allocation,
        catalog=catalog,
        description="  Salário de setembro  ",
    )

    assert income.description == "Salário de setembro"

    with pytest.raises(ValueError, match="financial description is required"):
        Income.receive(
            account=account,
            allocation=allocation,
            catalog=catalog,
            description="   ",
        )


def test_legacy_income_without_description_can_still_be_reconstructed() -> None:
    category_id = UUID("00000000-0000-0000-0000-000000000101")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("1000", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("1000", "BRL")),),
        catalog=catalog,
        metadata=metadata("00000000-0000-0000-0000-000000000002", day=2),
    )

    restored = Income(
        account_id=UUID("00000000-0000-0000-0000-000000000001"),
        allocation=allocation,
        description=None,
    )

    assert restored.description is None


def test_installments_use_the_purchase_description_without_own_descriptions() -> None:
    category_id = UUID("00000000-0000-0000-0000-000000000102")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Eletrônicos",
        nature=FinancialNature.EXPENSE,
    )
    card = CreditCard.open(
        name="Cartão",
        currency="BRL",
        closing_day=20,
        due_day=28,
        limit=Money("5000", "BRL"),
        metadata=metadata("00000000-0000-0000-0000-000000000010"),
    )
    allocation = Allocation.create(
        total=Money("1000", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("1000", "BRL")),),
        catalog=catalog,
        metadata=metadata("00000000-0000-0000-0000-000000000011", day=2),
    )

    purchase, card = card.purchase_in_installments(
        allocation=allocation,
        installment_count=3,
        catalog=catalog,
        description="Notebook",
    )
    corrected_allocation = Allocation.create(
        total=Money("900", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("900", "BRL")),),
        catalog=catalog,
        metadata=metadata("00000000-0000-0000-0000-000000000012", day=3),
    )
    correction, corrected_card = card.correct_installment_purchase(
        purchase_id=purchase.purchase_id,
        corrected_allocation=corrected_allocation,
        remaining_installment_count=3,
        description="Notebook corrigido",
        reason="Descrição e valor corrigidos",
        metadata=metadata("00000000-0000-0000-0000-000000000013", day=3),
        catalog=catalog,
    )

    assert purchase.description == "Notebook"
    assert all(not hasattr(installment, "description") for installment in purchase.installments)
    assert correction.description == "Notebook corrigido"
    assert corrected_card.description_for_purchase(purchase.purchase_id) == "Notebook corrigido"
    assert purchase.description == "Notebook"


def test_generated_occurrence_inherits_the_current_recurrence_description() -> None:
    recurrence = Recurrence.create(
        account_id=UUID("00000000-0000-0000-0000-000000000001"),
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100", "BRL"),
        starts_on=date(2026, 10, 1),
        ends_on=None,
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        description="Assinatura",
        metadata=metadata("00000000-0000-0000-0000-000000000020"),
    )

    revised = recurrence.revise_from(
        from_date=date(2026, 11, 1),
        expected_amount=Money("100", "BRL"),
        margin=None,
        description="Assinatura premium",
        reason="Plano alterado",
        metadata=metadata("00000000-0000-0000-0000-000000000021", day=2),
    )
    occurrences = revised.occurrences_through(date(2026, 11, 1))

    assert recurrence.description == "Assinatura"
    assert occurrences[0].description == "Assinatura"
    assert occurrences[1].description == "Assinatura premium"
    assert revised.history[0].description == "Assinatura"
    assert revised.history[1].description == "Assinatura premium"
