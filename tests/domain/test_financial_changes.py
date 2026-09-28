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
from bot_financeiro.domain.financial_changes import (
    AccountExpenseRefund,
    FinancialFactCanceled,
    FinancialFactCorrected,
    FinancialFactHistory,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import CashExpense
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount

CATEGORY_ID = UUID("b9c8c69b-65cb-43c1-b153-890f3ad26a96")
ACCOUNT_ID = UUID("d725d08d-a74b-451a-97f3-b1d459747893")


def metadata(record_id: str, day: int, hour: int = 12) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, hour, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def expense(amount: str, record_id: str, day: int) -> CashExpense:
    value = Money(amount, "BRL")
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=value,
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(CATEGORY_ID, value),),
        catalog=catalog,
        metadata=metadata(record_id, day),
    )
    return CashExpense(account_id=ACCOUNT_ID, allocation=allocation)


def test_correction_versions_fact_and_reconstructs_each_recorded_state() -> None:
    original = expense("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", 1)
    replacement = expense("80.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", 1)
    correction = FinancialFactCorrected(
        target_id=original.movement_id,
        previous_version_id=original.movement_id,
        replacement=replacement,
        reason="Valor duplicado no comprovante",
        metadata=metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    history = FinancialFactHistory(original=original).append_correction(correction)

    assert history.original == original
    assert history.current == replacement
    assert history.version_id == correction.change_id
    assert history.state_recorded_at(datetime(2026, 9, 4, 23, tzinfo=UTC)) == original
    assert history.state_recorded_at(datetime(2026, 9, 5, 13, tzinfo=UTC)) == replacement
    assert history.events == (correction,)


def test_cancellation_is_terminal_and_keeps_original_reconstructible() -> None:
    original = expense("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", 1)
    cancellation = FinancialFactCanceled(
        target_id=original.movement_id,
        previous_version_id=original.movement_id,
        reason="Registro não aconteceu",
        metadata=metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )
    history = FinancialFactHistory(original=original).append_cancellation(cancellation)

    assert history.original == original
    assert history.current is None
    assert history.is_canceled is True
    assert history.state_recorded_at(datetime(2026, 9, 4, 23, tzinfo=UTC)) == original
    with pytest.raises(ValueError, match="terminal"):
        history.append_cancellation(
            FinancialFactCanceled(
                target_id=original.movement_id,
                previous_version_id=cancellation.change_id,
                reason="Cancelar novamente",
                metadata=metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 6),
            )
        )


def test_account_refund_increases_resources_without_becoming_income() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("900.00", "BRL"),
        metadata=metadata("d725d08d-a74b-451a-97f3-b1d459747893", 1),
    )

    refund, updated = AccountExpenseRefund.receive(
        original_expense_id=UUID("446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
        account=account,
        amount=Money("30.00", "BRL"),
        metadata=metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    assert updated.balance == Money("930.00", "BRL")
    assert refund.is_income is False
    assert refund.is_expense is False
    assert refund.reduces_expense is True
    assert updated.history[-1].movement_id == refund.movement_id


def test_first_change_cannot_be_recorded_before_the_original_fact() -> None:
    original = expense("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", 1)
    correction = FinancialFactCorrected(
        target_id=original.event_id,
        previous_version_id=original.event_id,
        replacement=expense("80.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", 1),
        reason="Valor correto",
        metadata=RecordMetadata(
            effective_date=date(2026, 9, 1),
            recorded_at=datetime(2026, 9, 1, 11, tzinfo=UTC),
            origin=RecordOrigin.MANUAL,
            record_id=UUID("975e5800-4fa9-48e5-bba2-d6064c3fc2b4"),
        ),
    )

    with pytest.raises(ValueError, match="registration order"):
        FinancialFactHistory(original=original, events=(correction,))
