from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.credit_cards import CreditCardOperations
from bot_financeiro.application.financial_changes import (
    ExpenseBreakdown,
    FinancialChangeOperations,
)
from bot_financeiro.application.financial_operations import FinancialOperations
from bot_financeiro.application.payables import PayableOperations
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.credit_cards import CardPurchase, InvoiceFinancialCharge
from bot_financeiro.domain.financial_changes import AccountExpenseRefund
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import BillPayment, CashExpense
from bot_financeiro.domain.payables import FinancialCharge, PayableOpened
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount
from bot_financeiro.persistence.categories import SqlAlchemyFinancialCategoryRepository
from bot_financeiro.persistence.credit_cards import SqlAlchemyCreditCardUnitOfWork
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.financial_change_unit_of_work import (
    SqlAlchemyFinancialChangeUnitOfWork,
)
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialOperationUnitOfWork,
)
from bot_financeiro.persistence.payables import SqlAlchemyPayableUnitOfWork
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork

ACCOUNT_ID = UUID("d725d08d-a74b-451a-97f3-b1d459747893")
OTHER_ACCOUNT_ID = UUID("549887f1-8a5a-43e7-b621-20096e35fdc9")
CATEGORY_ID = UUID("b9c8c69b-65cb-43c1-b153-890f3ad26a96")


def metadata(record_id: str, day: int, hour: int = 12) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, hour, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def allocation(amount: str, record_id: str, day: int = 2) -> Allocation:
    value = Money(amount, "BRL")
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    return Allocation.create(
        total=value,
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(CATEGORY_ID, value),),
        catalog=catalog,
        metadata=metadata(record_id, day),
    )


def services(*accounts: ResourceAccount):
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        account_repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        for account in accounts:
            account_repository.add(account)
        category_repository = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        for category in catalog.categories:
            category_repository.add(category)
        unit_of_work.commit()
    return (
        FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions)),
        FinancialChangeOperations(lambda: SqlAlchemyFinancialChangeUnitOfWork(sessions)),
        CreditCardOperations(lambda: SqlAlchemyCreditCardUnitOfWork(sessions)),
        PayableOperations(lambda: SqlAlchemyPayableUnitOfWork(sessions)),
    )


def checking(account_id: UUID, amount: str = "1000.00") -> ResourceAccount:
    return ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money(amount, "BRL"),
        metadata=metadata(str(account_id), 1),
    )


def test_cash_expense_correction_has_inert_preview_and_atomic_confirmation() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation("80.00", "78f06631-05d9-419c-b059-c91ab6cb0d61"),
    )

    proposal = changes.preview_correction(
        original.movement_id,
        replacement,
        reason="Valor correto do comprovante",
    )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("900.00", "BRL")
    assert proposal.impact.description
    assert proposal.impact.balance_impacts[0].net_change == Money("20.00", "BRL")
    assert proposal.impact.expense.gross_before == Money("100.00", "BRL")
    assert proposal.impact.expense.gross_after == Money("80.00", "BRL")

    changes.confirm_correction(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("920.00", "BRL")
    current = changes.get_history(original.movement_id).current
    assert current.amount == replacement.amount
    assert current.event_id == original.event_id
    assert changes.expense_breakdown(original.movement_id).gross_expense == Money("80.00", "BRL")


def test_stale_proposal_and_repeated_cancellation_are_rejected() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation("90.00", "78f06631-05d9-419c-b059-c91ab6cb0d61"),
    )
    stale = changes.preview_correction(original.movement_id, replacement, reason="Corrigir")
    cancellation = changes.preview_cancellation(original.movement_id, reason="Não ocorreu")
    changes.confirm_cancellation(
        cancellation,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    with pytest.raises(ValueError, match="obsolete"):
        changes.confirm_correction(
            stale,
            metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 6),
        )
    with pytest.raises(ValueError, match="terminal"):
        changes.preview_cancellation(original.movement_id, reason="De novo")


def test_transfer_cancellation_updates_both_accounts_but_not_its_fee() -> None:
    financial, changes, _, _ = services(
        checking(ACCOUNT_ID), checking(OTHER_ACCOUNT_ID, "100.00")
    )
    result = financial.transfer(
        source_account_id=ACCOUNT_ID,
        destination_account_id=OTHER_ACCOUNT_ID,
        amount=Money("200.00", "BRL"),
        metadata=metadata("446008fb-e949-49f5-ac7e-ef5f1cb829b0", 2),
        fee_allocation=allocation("5.00", "78f06631-05d9-419c-b059-c91ab6cb0d61"),
    )

    proposal = changes.preview_cancellation(result.transfer.movement_id, reason="Conta errada")
    assert {impact.net_change for impact in proposal.impact.balance_impacts} == {
        Money("200.00", "BRL"),
        Money("-200.00", "BRL"),
    }
    changes.confirm_cancellation(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("995.00", "BRL")
    assert financial.account_statement(OTHER_ACCOUNT_ID).balance == Money("100.00", "BRL")
    assert changes.get_history(result.fee_expense.movement_id).current == result.fee_expense


def test_transfer_cancellation_preview_exposes_destination_balance_blocker() -> None:
    wallet = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata(str(OTHER_ACCOUNT_ID), 1),
    )
    financial, changes, _, _ = services(checking(ACCOUNT_ID), wallet)
    result = financial.transfer(
        source_account_id=ACCOUNT_ID,
        destination_account_id=OTHER_ACCOUNT_ID,
        amount=Money("100.00", "BRL"),
        metadata=metadata("446008fb-e949-49f5-ac7e-ef5f1cb829b0", 2),
    )
    financial.pay_cash_expense(
        OTHER_ACCOUNT_ID,
        allocation("100.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=3),
    )

    proposal = changes.preview_cancellation(result.transfer.movement_id, reason="Não ocorreu")

    assert proposal.impact.blockers == (
        "resource account would reject the financial change: "
        "negative balance is not allowed for this account",
    )
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_cancellation(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
        )
    assert financial.account_statement(ACCOUNT_ID).balance == Money("900.00", "BRL")
    assert financial.account_statement(OTHER_ACCOUNT_ID).balance == Money("0.00", "BRL")


def test_account_refund_keeps_gross_refunded_and_net_expense_separate() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    proposal = changes.preview_account_refund(
        original.movement_id,
        account_id=ACCOUNT_ID,
        amount=Money("30.00", "BRL"),
    )
    changes.confirm_account_refund(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    breakdown = changes.expense_breakdown(original.movement_id)
    assert breakdown.gross_expense == Money("100.00", "BRL")
    assert breakdown.refunded_amount == Money("30.00", "BRL")
    assert breakdown.net_expense == Money("70.00", "BRL")
    assert financial.account_statement(ACCOUNT_ID).balance == Money("930.00", "BRL")

    blocked = changes.preview_cancellation(original.movement_id, reason="Não ocorreu")
    assert blocked.impact.blockers
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_cancellation(
            blocked,
            metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 6),
        )


def test_account_refund_preview_exposes_archived_destination_blocker() -> None:
    archived = ResourceAccount.open(
        name="Conta encerrada",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata(str(OTHER_ACCOUNT_ID), 1),
    ).archive()
    financial, changes, _, _ = services(checking(ACCOUNT_ID), archived)
    original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )

    proposal = changes.preview_account_refund(
        original.movement_id,
        account_id=OTHER_ACCOUNT_ID,
        amount=Money("30.00", "BRL"),
    )

    assert proposal.impact.blockers == (
        "resource account would reject the refund: archived account rejects new movements",
    )
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_account_refund(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
        )


def test_failed_correction_rolls_back_history_allocation_and_account_effect() -> None:
    wallet = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("100.00", "BRL"),
        metadata=metadata(str(ACCOUNT_ID), 1),
    )
    financial, changes, _, _ = services(wallet)
    original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("80.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation("150.00", "78f06631-05d9-419c-b059-c91ab6cb0d61"),
    )
    proposal = changes.preview_correction(original.movement_id, replacement, reason="Corrigir")

    assert proposal.impact.blockers == (
        "resource account would reject the financial change: "
        "negative balance is not allowed for this account",
    )
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_correction(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
        )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("20.00", "BRL")
    assert changes.get_history(original.movement_id).current == original


def test_correction_preview_preserves_existing_refund_in_expense_impact() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    refund = changes.preview_account_refund(
        original.movement_id,
        account_id=ACCOUNT_ID,
        amount=Money("30.00", "BRL"),
    )
    changes.confirm_account_refund(
        refund,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation("80.00", "78f06631-05d9-419c-b059-c91ab6cb0d61"),
    )

    proposal = changes.preview_correction(
        original.movement_id,
        replacement,
        reason="Valor bruto corrigido",
    )

    assert proposal.impact.expense is not None
    assert proposal.impact.expense.gross_before == Money("100.00", "BRL")
    assert proposal.impact.expense.gross_after == Money("80.00", "BRL")
    assert proposal.impact.expense.refunded_before == Money("30.00", "BRL")
    assert proposal.impact.expense.refunded_after == Money("30.00", "BRL")


def test_bill_payment_correction_recalculates_account_and_payable_debt() -> None:
    financial, changes, _, payables = services(checking(ACCOUNT_ID))
    payable = payables.create_payable(
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
        due_date=date(2026, 9, 20),
    )
    payment = payables.pay_payable(
        payable_id=payable.payable_id,
        account_id=ACCOUNT_ID,
        amount=Money("60.00", "BRL"),
        metadata=metadata("78f06631-05d9-419c-b059-c91ab6cb0d61", 4),
    )
    replacement = BillPayment(
        payable_id=payable.payable_id,
        account_id=ACCOUNT_ID,
        amount=Money("40.00", "BRL"),
        metadata=metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 4),
    )

    proposal = changes.preview_correction(payment.movement_id, replacement, reason="Valor pago")
    changes.confirm_correction(
        proposal,
        metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 5),
    )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("960.00", "BRL")
    assert payables.get_payable(payable.payable_id).amount_due == Money("60.00", "BRL")
    current = changes.get_history(payment.movement_id).current
    assert current.amount == replacement.amount
    assert current.event_id == payment.event_id


def test_payable_opening_correction_versions_expense_and_recalculates_debt() -> None:
    _, changes, _, payables = services(checking(ACCOUNT_ID))
    payable = payables.create_payable(
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
        due_date=date(2026, 9, 20),
    )
    replacement = PayableOpened(
        allocation=allocation("80.00", "78f06631-05d9-419c-b059-c91ab6cb0d61"),
        due_date=date(2026, 9, 22),
    )

    proposal = changes.preview_correction(payable.payable_id, replacement, reason="Nota corrigida")
    changes.confirm_correction(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    current = payables.get_payable(payable.payable_id)
    assert current.payable_id == payable.payable_id
    assert current.original_amount == Money("80.00", "BRL")
    assert current.amount_due == Money("80.00", "BRL")
    assert current.opening.due_date == date(2026, 9, 22)


def test_payable_expense_can_be_refunded_into_a_resource_account() -> None:
    financial, changes, _, payables = services(checking(ACCOUNT_ID))
    payable = payables.create_payable(
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
        due_date=date(2026, 9, 20),
    )
    proposal = changes.preview_account_refund(
        payable.payable_id,
        account_id=ACCOUNT_ID,
        amount=Money("25.00", "BRL"),
    )
    changes.confirm_account_refund(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("1025.00", "BRL")
    assert changes.expense_breakdown(payable.payable_id) == ExpenseBreakdown(
        gross_expense=Money("100.00", "BRL"),
        refunded_amount=Money("25.00", "BRL"),
        net_expense=Money("75.00", "BRL"),
    )


def test_invoice_payment_cancellation_recalculates_cash_and_invoice_debt() -> None:
    financial, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    payment = cards.pay_invoice(
        card_id=card.card_id,
        closing_date=purchase.invoice_closing_date,
        account_id=ACCOUNT_ID,
        amount=Money("100.00", "BRL"),
        metadata=metadata("78f06631-05d9-419c-b059-c91ab6cb0d61", 12),
    )

    proposal = changes.preview_cancellation(payment.movement_id, reason="Pagamento estornado")
    assert proposal.impact.debt_impacts[0].net_change == Money("100.00", "BRL")
    changes.confirm_cancellation(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 13),
    )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("1000.00", "BRL")
    assert cards.get_invoice(
        card_id=card.card_id, closing_date=purchase.invoice_closing_date
    ).amount_due == Money("100.00", "BRL")
    assert changes.get_history(payment.movement_id).current is None


def test_invoice_payment_with_linked_credit_blocks_unsafe_cancellation() -> None:
    financial, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    payment = cards.pay_invoice(
        card_id=card.card_id,
        closing_date=purchase.invoice_closing_date,
        account_id=ACCOUNT_ID,
        amount=Money("120.00", "BRL"),
        metadata=metadata("78f06631-05d9-419c-b059-c91ab6cb0d61", 12),
        create_credit_from_excess=True,
    )

    proposal = changes.preview_cancellation(payment.movement_id, reason="Estornar")

    assert proposal.impact.blockers == ("invoice payment has linked card credit",)
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_cancellation(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 13),
        )
    assert financial.account_statement(ACCOUNT_ID).balance == Money("880.00", "BRL")


def test_unsettled_card_purchase_can_be_corrected_without_losing_identity() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    replacement = CardPurchase(
        card_id=card.card_id,
        allocation=allocation("80.00", "78f06631-05d9-419c-b059-c91ab6cb0d61"),
        invoice_closing_date=purchase.invoice_closing_date,
    )

    proposal = changes.preview_correction(purchase.purchase_id, replacement, reason="Valor")
    changes.confirm_correction(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    current = cards.get_card(card.card_id)
    corrected = next(
        event for event in current.history if isinstance(event, CardPurchase)
    )
    assert corrected.purchase_id == purchase.purchase_id
    assert current.invoice_for_purchase(purchase.purchase_id).principal_due == Money(
        "80.00", "BRL"
    )


def test_card_refund_preview_reuses_card_refund_and_never_creates_income() -> None:
    financial, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )

    proposal = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.event_id,
        amount=Money("40.00", "BRL"),
    )
    assert cards.get_card(card.card_id).invoice_for_purchase(
        purchase.event_id
    ).principal_due == Money("100.00", "BRL")
    refund = changes.confirm_card_refund(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    assert refund.is_income is False
    assert cards.get_card(card.card_id).invoice_for_purchase(
        purchase.event_id
    ).principal_due == Money("60.00", "BRL")
    breakdown = changes.card_expense_breakdown(card.card_id, purchase.event_id)
    assert breakdown.gross_expense == Money("100.00", "BRL")
    assert breakdown.refunded_amount == Money("40.00", "BRL")
    assert breakdown.net_expense == Money("60.00", "BRL")
    assert financial.account_statement(ACCOUNT_ID).balance == Money("1000.00", "BRL")


def test_installment_purchase_refund_uses_card_credit_across_invoice_cycles() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
        installment_count=3,
    )

    proposal = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        amount=Money("40.00", "BRL"),
    )
    changes.confirm_card_refund(
        proposal,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 5),
    )

    assert cards.get_card(card.card_id).available_limit == Money("940.00", "BRL")
    assert changes.card_expense_breakdown(
        card.card_id, purchase.purchase_id
    ).net_expense == Money("60.00", "BRL")


def test_installment_correction_preview_is_inert_and_freezes_affected_cycle() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
        installment_count=3,
    )
    corrected = allocation("120.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=9)

    proposal = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=corrected,
        remaining_installment_count=2,
        reason="Valor confirmado pela operadora",
        effective_date=date(2026, 9, 11),
    )

    assert cards.get_card(card.card_id).expense_for_purchase(purchase.purchase_id).total == Money(
        "100.00", "BRL"
    )
    assert tuple(item.amount for item in proposal.installment_impact.frozen) == (
        Money("33.34", "BRL"),
    )
    confirmation_metadata = metadata(
        "f3a91670-5604-4a53-919d-b0adbcc86c1d", 11, hour=13
    )
    correction = changes.confirm_installment_purchase_correction(
        proposal, confirmation_metadata
    )

    restored = cards.get_card(card.card_id)
    assert restored is not None
    assert restored.expense_for_purchase(purchase.purchase_id).total == Money("120.00", "BRL")
    assert tuple(
        item.amount for item in restored.installments_for_purchase(purchase.purchase_id)
    ) == (Money("33.34", "BRL"), Money("43.33", "BRL"), Money("43.33", "BRL"))
    assert changes.card_expense_breakdown(
        card.card_id, purchase.purchase_id
    ) == ExpenseBreakdown(
        gross_expense=Money("120.00", "BRL"),
        refunded_amount=Money("0.00", "BRL"),
        net_expense=Money("120.00", "BRL"),
    )
    assert correction.metadata == confirmation_metadata


def test_installment_correction_preview_requires_a_reason() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("6d3f715e-65f9-4f86-856c-11e3194c916f", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "435c59d0-f603-4ab7-9f53-a993399c683c", day=9),
        installment_count=2,
    )

    with pytest.raises(ValueError, match="reason is required"):
        changes.preview_installment_purchase_correction(
            card_id=card.card_id,
            purchase_id=purchase.purchase_id,
            corrected_allocation=allocation(
                "110.00", "5cde053c-b81c-4867-a1fd-68cfc1659cb2", day=9
            ),
            remaining_installment_count=2,
            reason="   ",
            effective_date=date(2026, 9, 11),
        )


def test_installment_correction_preview_can_extend_into_a_new_future_cycle() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
        installment_count=2,
    )

    proposal = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=allocation(
            "110.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=9
        ),
        remaining_installment_count=2,
        reason="Valor confirmado",
        effective_date=date(2026, 9, 11),
    )

    assert tuple(
        item.cycle.closing_date for item in proposal.installment_impact.replacement
    ) == (date(2026, 10, 10), date(2026, 11, 10))


def test_installment_correction_rejects_a_stale_proposal() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
        installment_count=3,
    )
    proposal = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=allocation(
            "120.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=9
        ),
        remaining_installment_count=2,
        reason="Valor confirmado",
        effective_date=date(2026, 9, 11),
    )
    cards.grant_card_credit(
        card_id=card.card_id,
        amount=Money("1.00", "BRL"),
        reason="Crédito real",
        metadata=metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 12),
    )

    with pytest.raises(ValueError, match="obsolete"):
        changes.confirm_installment_purchase_correction(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 11),
        )


def test_installment_refund_uses_the_current_corrected_expense_value() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
        installment_count=3,
    )
    correction = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=allocation(
            "120.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=9
        ),
        remaining_installment_count=2,
        reason="Valor confirmado",
        effective_date=date(2026, 9, 11),
    )
    changes.confirm_installment_purchase_correction(
        correction,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 11),
    )

    refund = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        amount=Money("110.00", "BRL"),
    )
    assert not refund.impact.blockers
    changes.confirm_card_refund(
        refund,
        metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 12),
    )

    assert changes.card_expense_breakdown(
        card.card_id, purchase.purchase_id
    ).net_expense == Money("10.00", "BRL")


def test_installment_account_refund_uses_the_current_corrected_expense_value() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
        installment_count=3,
    )
    correction = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=allocation(
            "120.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=9
        ),
        remaining_installment_count=2,
        reason="Valor confirmado",
        effective_date=date(2026, 9, 11),
    )
    changes.confirm_installment_purchase_correction(
        correction,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 11),
    )

    refund = changes.preview_account_refund(
        purchase.purchase_id,
        account_id=ACCOUNT_ID,
        amount=Money("110.00", "BRL"),
    )
    assert not refund.impact.blockers
    changes.confirm_account_refund(
        refund,
        metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 12),
    )

    assert changes.expense_breakdown(purchase.purchase_id) == ExpenseBreakdown(
        gross_expense=Money("120.00", "BRL"),
        refunded_amount=Money("110.00", "BRL"),
        net_expense=Money("10.00", "BRL"),
    )


def test_installment_account_refund_rejects_preview_from_before_a_correction() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=2),
        installment_count=2,
    )
    stale_refund = changes.preview_account_refund(
        purchase.purchase_id,
        account_id=ACCOUNT_ID,
        amount=Money("90.00", "BRL"),
    )
    correction = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=allocation(
            "120.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=2
        ),
        remaining_installment_count=2,
        reason="Valor confirmado",
        effective_date=date(2026, 9, 3),
    )
    changes.confirm_installment_purchase_correction(
        correction,
        metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 3),
    )

    with pytest.raises(ValueError, match="obsolete"):
        changes.confirm_account_refund(
            stale_refund,
            metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 4),
        )


def test_installment_correction_preview_blocks_total_below_existing_refund() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=2),
        installment_count=2,
    )
    refund = changes.preview_account_refund(
        purchase.purchase_id,
        account_id=ACCOUNT_ID,
        amount=Money("30.00", "BRL"),
    )
    changes.confirm_account_refund(
        refund,
        metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 3),
    )

    proposal = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=allocation(
            "20.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=2
        ),
        remaining_installment_count=2,
        reason="Valor confirmado",
        effective_date=date(2026, 9, 4),
    )

    assert proposal.impact.blockers == ("existing refunds exceed the proposed expense",)
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_installment_purchase_correction(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 4),
        )


def test_installment_correction_preview_exposes_frozen_cycle_blocker() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
        installment_count=3,
    )
    cards.pay_invoice(
        card_id=card.card_id,
        closing_date=date(2026, 9, 10),
        account_id=ACCOUNT_ID,
        amount=Money("33.34", "BRL"),
        metadata=metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 11),
    )

    proposal = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=allocation(
            "20.00", "78f06631-05d9-419c-b059-c91ab6cb0d61", day=9
        ),
        remaining_installment_count=2,
        reason="Valor confirmado",
        effective_date=date(2026, 9, 12),
    )

    assert proposal.impact.blockers == (
        "corrected total cannot be lower than frozen installments",
    )
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_installment_purchase_correction(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 12),
        )


def test_invoice_assignment_correction_requires_inert_preview_and_confirmation() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
    )

    proposal = changes.preview_invoice_assignment_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        target_closing_date=date(2026, 10, 10),
        reason="Fatura confirmada pela operadora",
        effective_date=date(2026, 9, 11),
    )

    assert cards.get_card(card.card_id).invoice_for_purchase(
        purchase.purchase_id
    ).cycle.closing_date == date(2026, 9, 10)
    confirmation_metadata = metadata(
        "975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 11, hour=13
    )
    correction = changes.confirm_invoice_assignment_correction(
        proposal,
        confirmation_metadata,
    )
    assert cards.get_card(card.card_id).invoice_for_purchase(
        purchase.purchase_id
    ).cycle.closing_date == date(2026, 10, 10)
    assert correction.metadata == confirmation_metadata


def test_invoice_assignment_preview_blocks_a_cycle_with_consumed_payment() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("a27b7213-92f4-46b4-9516-d72bcd3f9534", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0", day=9),
    )
    cards.pay_invoice(
        card_id=card.card_id,
        closing_date=date(2026, 9, 10),
        account_id=ACCOUNT_ID,
        amount=Money("20.00", "BRL"),
        metadata=metadata("8520b8ee-1636-40dc-9e4f-b685720e4111", 12),
    )

    proposal = changes.preview_invoice_assignment_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        target_closing_date=date(2026, 10, 10),
        reason="Fatura confirmada pela operadora",
        effective_date=date(2026, 9, 13),
    )

    assert proposal.impact.blockers == ("invoice assignment has settlement dependencies",)
    with pytest.raises(ValueError, match="blocked"):
        changes.confirm_invoice_assignment_correction(
            proposal,
            metadata("975e5800-4fa9-48e5-bba2-d6064c3fc2b4", 13),
        )


def test_account_refund_is_rejected_by_generic_change_flows() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    expense = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "446008fb-e949-49f5-ac7e-ef5f1cb829b0"),
    )
    refund_proposal = changes.preview_account_refund(
        expense.event_id,
        account_id=ACCOUNT_ID,
        amount=Money("20.00", "BRL"),
    )
    refund = changes.confirm_account_refund(
        refund_proposal,
        metadata("c92c51c6-99ab-4f04-81a2-188b7dd2905f", 5),
    )
    replacement = AccountExpenseRefund(
        original_expense_id=expense.event_id,
        account_id=ACCOUNT_ID,
        amount=Money("10.00", "BRL"),
        metadata=metadata("4f11039d-cbb7-4382-8460-96a17bfdbd60", 6),
    )

    with pytest.raises(ValueError, match="refund.*cannot be corrected"):
        changes.preview_correction(refund.event_id, replacement, reason="Valor informado")
    with pytest.raises(ValueError, match="refund.*cannot be canceled"):
        changes.preview_cancellation(refund.event_id, reason="Lançamento indevido")


def test_account_refund_rejects_registration_before_original_without_effects() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    expense = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "83ae1bb6-9f8a-41ed-9326-199f893ff2f2", day=4),
    )
    proposal = changes.preview_account_refund(
        expense.event_id,
        account_id=ACCOUNT_ID,
        amount=Money("20.00", "BRL"),
    )

    with pytest.raises(ValueError, match="registration order"):
        changes.confirm_account_refund(
            proposal,
            metadata("4c289fe8-a00f-422a-8db9-b5a4b301775b", 3),
        )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("900.00", "BRL")
    assert changes.expense_breakdown(expense.event_id).refunded_amount == Money(
        "0.00", "BRL"
    )


def test_card_specific_correction_rejects_out_of_order_confirmation() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("138cd50f-d9b7-4ecf-964a-72f7703ed514", 1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "67b3d6af-0e7c-4050-bfe3-eb53652cfbb6", day=9),
        installment_count=2,
    )
    proposal = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.event_id,
        corrected_allocation=allocation(
            "110.00", "b6e42239-63cc-40a4-9a07-99aa0f2a87b8", day=9
        ),
        remaining_installment_count=2,
        reason="Valor correto",
        effective_date=date(2026, 9, 11),
    )

    with pytest.raises(ValueError, match="registration order"):
        changes.confirm_installment_purchase_correction(
            proposal,
            RecordMetadata(
                effective_date=date(2026, 9, 11),
                recorded_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
                origin=RecordOrigin.MANUAL,
                record_id=UUID("ca97b60d-e28a-4a18-ae77-1217df810bf6"),
            ),
        )

    assert cards.get_card(card.card_id).expense_for_purchase(purchase.event_id).total == Money(
        "100.00", "BRL"
    )


def test_invoice_assignment_and_card_refund_reject_out_of_order_confirmation() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("1a2a5578-18f5-40c1-97db-e70238140706", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "0e18ab23-c369-48a2-82b1-b55a6503e818", day=9),
    )
    assignment = changes.preview_invoice_assignment_correction(
        card_id=card.card_id,
        purchase_id=purchase.event_id,
        target_closing_date=date(2026, 10, 10),
        reason="Fatura correta",
        effective_date=date(2026, 9, 11),
    )
    out_of_order_assignment = RecordMetadata(
        effective_date=date(2026, 9, 11),
        recorded_at=datetime(2026, 9, 8, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID("2480e8ed-7f53-4d41-a065-f18244c103c6"),
    )

    with pytest.raises(ValueError, match="registration order"):
        changes.confirm_invoice_assignment_correction(
            assignment,
            out_of_order_assignment,
        )

    refund = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.event_id,
        amount=Money("20.00", "BRL"),
    )
    with pytest.raises(ValueError, match="registration order"):
        changes.confirm_card_refund(
            refund,
            RecordMetadata(
                effective_date=date(2026, 9, 10),
                recorded_at=datetime(2026, 9, 8, 13, tzinfo=UTC),
                origin=RecordOrigin.MANUAL,
                record_id=UUID("e70bcbb4-b457-40b7-bb20-5dfba2bd9d4e"),
            ),
        )

    restored = cards.get_card(card.card_id)
    assert restored.invoice_for_purchase(purchase.event_id).cycle.closing_date == date(
        2026, 9, 10
    )
    assert changes.card_expense_breakdown(
        card.card_id, purchase.event_id
    ).refunded_amount == Money("0.00", "BRL")


def test_refund_registration_order_is_shared_across_account_and_card_channels() -> None:
    financial, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("330308c1-8992-4323-b6d4-96fc0a7d55d8", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "b8c73114-8876-4a1d-8c72-17ae9957516b", day=9),
    )
    card_refund = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.event_id,
        amount=Money("20.00", "BRL"),
    )
    changes.confirm_card_refund(
        card_refund,
        metadata("15fb3806-07a3-4940-aa0d-f212ae366f48", 12),
    )
    account_refund = changes.preview_account_refund(
        purchase.event_id,
        account_id=ACCOUNT_ID,
        amount=Money("10.00", "BRL"),
    )

    with pytest.raises(ValueError, match="registration order"):
        changes.confirm_account_refund(
            account_refund,
            metadata("6eb6ce58-cada-49b2-a492-2560f3625292", 11),
        )
    assert financial.account_statement(ACCOUNT_ID).balance == Money("1000.00", "BRL")

    financial, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("622c6458-16f5-4158-a713-df2981683bbe", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "09230af6-d965-4404-a038-7bef7b975ff8", day=9),
    )
    account_refund = changes.preview_account_refund(
        purchase.event_id,
        account_id=ACCOUNT_ID,
        amount=Money("20.00", "BRL"),
    )
    changes.confirm_account_refund(
        account_refund,
        metadata("210e619c-814c-42a3-b175-7cf8bf615049", 12),
    )
    card_refund = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.event_id,
        amount=Money("10.00", "BRL"),
    )

    with pytest.raises(ValueError, match="registration order"):
        changes.confirm_card_refund(
            card_refund,
            metadata("7c0eb9bc-9f67-41a0-8074-4ba0a6a25750", 11),
        )
    assert changes.card_expense_breakdown(
        card.card_id, purchase.event_id
    ).refunded_amount == Money("20.00", "BRL")


def test_purchase_with_invoice_assignment_correction_blocks_cancellation() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("e7c48cfa-f8a8-44f6-866a-dd9f22a78ccd", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "58962324-287f-4a6c-a857-a3aa50331ab4", day=9),
    )
    assignment = changes.preview_invoice_assignment_correction(
        card_id=card.card_id,
        purchase_id=purchase.event_id,
        target_closing_date=date(2026, 10, 10),
        reason="Fatura confirmada pela operadora",
        effective_date=date(2026, 9, 11),
    )
    changes.confirm_invoice_assignment_correction(
        assignment,
        metadata("3ae0d7ab-a985-4435-b194-7a46c2676ad1", 11),
    )

    proposal = changes.preview_cancellation(
        purchase.event_id,
        reason="Compra cancelada",
    )

    assert proposal.impact.is_blocked
    assert "invoice assignment" in proposal.impact.blockers[0]
    with pytest.raises(ValueError, match="invoice assignment"):
        changes.confirm_cancellation(
            proposal,
            metadata("6acb73e8-ebf0-4a95-9980-035a8d92e7c2", 12),
        )


def test_movement_queries_return_current_versions_and_omit_cancellations() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    corrected_original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "07588bb7-4041-47d7-8f36-24c7a7273d6d"),
    )
    canceled = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("30.00", "7ea71839-cc69-4888-b869-429863357f23"),
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation("80.00", "3e502fd8-f796-4b3a-a658-c956a8f49e5f"),
    )
    correction = changes.preview_correction(
        corrected_original.event_id,
        replacement,
        reason="Valor correto",
    )
    changes.confirm_correction(
        correction,
        metadata("3608fd53-ee7b-4ca2-bbbe-eb1c55adf0a2", 5),
    )
    cancellation = changes.preview_cancellation(canceled.event_id, reason="Duplicado")
    changes.confirm_cancellation(
        cancellation,
        metadata("1d0fc620-ad8e-4de0-933a-3f35e0df797f", 6),
    )

    current = financial.get_movement(corrected_original.event_id)
    assert current.amount == Money("80.00", "BRL")
    assert current.event_id == corrected_original.event_id
    assert financial.get_movement(canceled.event_id) is None
    assert financial.list_movements() == (current,)
    assert changes.get_history(canceled.event_id).original == canceled

    second_replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation("70.00", "84399da2-1861-4411-84a9-840645789b85"),
    )
    second_proposal = changes.preview_correction(
        current.event_id,
        second_replacement,
        reason="Segundo valor corrigido",
    )
    changes.confirm_correction(
        second_proposal,
        metadata("38545dd7-76b1-4697-88a8-64b84702751a", 7),
    )

    assert financial.get_movement(corrected_original.event_id).amount == Money(
        "70.00", "BRL"
    )
    assert len(changes.get_history(corrected_original.event_id).events) == 2


def test_confirmation_rejects_registration_before_original_without_side_effects() -> None:
    financial, changes, _, _ = services(checking(ACCOUNT_ID))
    original = financial.pay_cash_expense(
        ACCOUNT_ID,
        allocation("100.00", "8bd597dd-624c-4566-a31c-081086b00631", day=2),
    )
    proposal = changes.preview_correction(
        original.event_id,
        CashExpense(
            account_id=ACCOUNT_ID,
            allocation=allocation(
                "80.00", "3b7d99a8-747f-4681-aaeb-da6832a87a13", day=2
            ),
        ),
        reason="Valor correto",
    )

    with pytest.raises(ValueError, match="registration order"):
        changes.confirm_correction(
            proposal,
            RecordMetadata(
                effective_date=date(2026, 9, 1),
                recorded_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
                origin=RecordOrigin.MANUAL,
                record_id=UUID("7d009696-7f5b-4053-b981-074679ca5b6f"),
            ),
        )

    assert financial.account_statement(ACCOUNT_ID).balance == Money("900.00", "BRL")
    assert changes.get_history(original.event_id).events == ()


def test_payable_financial_charge_correction_and_cancellation_recalculate_debt() -> None:
    _, changes, _, payables = services(checking(ACCOUNT_ID))
    payable = payables.create_payable(
        allocation=allocation("100.00", "537d3501-ac20-4787-834c-1f2cab028792"),
        due_date=date(2026, 9, 20),
    )
    charge = payables.add_financial_charge(
        payable_id=payable.payable_id,
        allocation=allocation("20.00", "c142161a-9307-4077-82aa-9724f2442e22", day=4),
    )
    replacement = FinancialCharge(
        payable_id=payable.payable_id,
        allocation=allocation("15.00", "ead1dc98-6d26-4652-a8b4-29d48d698855", day=4),
    )

    correction = changes.preview_correction(
        charge.event_id,
        replacement,
        reason="Encargo corrigido",
    )
    changes.confirm_correction(
        correction,
        metadata("c3c44b42-f18f-4389-b8fe-985372e281fa", 5),
    )

    assert payables.get_payable(payable.payable_id).amount_due == Money("115.00", "BRL")
    assert changes.expense_breakdown(charge.event_id) == ExpenseBreakdown(
        gross_expense=Money("15.00", "BRL"),
        refunded_amount=Money("0.00", "BRL"),
        net_expense=Money("15.00", "BRL"),
    )

    current = changes.get_history(charge.event_id).current
    second_replacement = FinancialCharge(
        payable_id=payable.payable_id,
        allocation=allocation("12.00", "54ce28f2-a862-447f-8625-aa75de15153e", day=5),
    )
    second_correction = changes.preview_correction(
        current.event_id,
        second_replacement,
        reason="Encargo novamente corrigido",
    )
    changes.confirm_correction(
        second_correction,
        metadata("4c412ffb-c30e-457b-9755-857854b6bc51", 6),
    )
    assert payables.get_payable(payable.payable_id).amount_due == Money("112.00", "BRL")

    cancellation = changes.preview_cancellation(
        charge.event_id,
        reason="Encargo removido",
    )
    changes.confirm_cancellation(
        cancellation,
        metadata("5b5abdc4-ccfb-4e10-a00a-a1fed6274e5f", 7),
    )

    assert payables.get_payable(payable.payable_id).amount_due == Money("100.00", "BRL")


def test_invoice_financial_charge_correction_and_cancellation_recalculate_debt() -> None:
    _, changes, cards, _ = services(checking(ACCOUNT_ID))
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=20,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("34def08c-3d1f-405b-8442-21eb1a1845cc", 1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation("100.00", "52cd99e9-156e-4b07-b1b0-5c666d8b058f"),
    )
    charge = cards.add_financial_charge(
        card_id=card.card_id,
        closing_date=purchase.invoice_closing_date,
        allocation=allocation("20.00", "c9d75ff2-6f05-4cd6-bd71-245c2f969be0", day=4),
    )
    replacement = InvoiceFinancialCharge(
        card_id=card.card_id,
        invoice_closing_date=purchase.invoice_closing_date,
        allocation=allocation("15.00", "42386bd7-8e1a-4c01-8040-50bad91333cd", day=4),
    )

    correction = changes.preview_correction(
        charge.event_id,
        replacement,
        reason="Encargo corrigido",
    )
    changes.confirm_correction(
        correction,
        metadata("0b23b819-be17-4432-9b50-4a55b50ba765", 5),
    )

    assert cards.get_invoice(
        card_id=card.card_id,
        closing_date=purchase.invoice_closing_date,
    ).amount_due == Money("115.00", "BRL")
    assert changes.expense_breakdown(charge.event_id).gross_expense == Money(
        "15.00", "BRL"
    )

    cancellation = changes.preview_cancellation(
        charge.event_id,
        reason="Encargo removido",
    )
    changes.confirm_cancellation(
        cancellation,
        metadata("ef006ed7-e7c5-4780-b7d9-bd78bce7af99", 6),
    )

    assert cards.get_invoice(
        card_id=card.card_id,
        closing_date=purchase.invoice_closing_date,
    ).amount_due == Money("100.00", "BRL")
