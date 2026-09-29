from datetime import UTC, date, datetime
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.credit_cards import CreditCardOperations
from bot_financeiro.application.financial_changes import FinancialChangeOperations
from bot_financeiro.application.financial_operations import FinancialOperations
from bot_financeiro.application.financial_queries import (
    FinancialQueries,
    FinancialRecordType,
    QueryPeriod,
)
from bot_financeiro.application.payables import PayableOperations
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import CashExpense
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
from bot_financeiro.persistence.financial_queries import SqlAlchemyFinancialQuerySource
from bot_financeiro.persistence.payables import SqlAlchemyPayableUnitOfWork
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000501")
EXPENSE_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000502")
EXPENSE_SUBCATEGORY_ID = UUID("00000000-0000-0000-0000-000000000503")


def test_payable_and_installment_purchase_are_expenses_but_their_payments_are_not() -> None:
    sessions, catalog = configured_database()
    payables = PayableOperations(lambda: SqlAlchemyPayableUnitOfWork(sessions))
    cards = CreditCardOperations(lambda: SqlAlchemyCreditCardUnitOfWork(sessions))
    payable = payables.create_payable(
        allocation=allocation(
            catalog,
            "30.00",
            "00000000-0000-0000-0000-000000000511",
            day=2,
        ),
        due_date=date(2026, 9, 20),
        description="Conta de energia",
    )
    payables.pay_payable(
        payable_id=payable.payable_id,
        account_id=ACCOUNT_ID,
        amount=Money("30.00", "BRL"),
        metadata=metadata("00000000-0000-0000-0000-000000000512", day=5),
        description="Pagamento da energia",
    )
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("00000000-0000-0000-0000-000000000513", day=1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation(
            catalog,
            "120.00",
            "00000000-0000-0000-0000-000000000514",
            day=9,
        ),
        installment_count=3,
        description="Curso parcelado",
    )
    cards.pay_invoice(
        card_id=card.card_id,
        closing_date=purchase.installments[0].cycle.closing_date,
        account_id=ACCOUNT_ID,
        amount=Money("40.00", "BRL"),
        metadata=metadata("00000000-0000-0000-0000-000000000515", day=11),
        description="Pagamento da fatura",
    )

    report = FinancialQueries(
        lambda: SqlAlchemyFinancialQuerySource(sessions)
    ).income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert report.gross_expense.amount == Money("150.00", "BRL")
    assert report.net_expense.amount == Money("150.00", "BRL")
    assert {item.record_type for item in report.gross_expense.components} == {
        FinancialRecordType.PAYABLE_OPENED,
        FinancialRecordType.INSTALLMENT_PURCHASE,
    }
    assert {item.record_id for item in report.gross_expense.components} == {
        payable.payable_id,
        purchase.purchase_id,
    }


def test_current_correction_refund_and_cancellation_reconcile_category_totals() -> None:
    sessions, catalog = configured_database()
    finances = FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions))
    changes = FinancialChangeOperations(lambda: SqlAlchemyFinancialChangeUnitOfWork(sessions))
    original = finances.pay_cash_expense(
        ACCOUNT_ID,
        allocation(
            catalog,
            "100.00",
            "00000000-0000-0000-0000-000000000521",
            day=2,
        ),
        description="Compra original",
    )
    refund_proposal = changes.preview_account_refund(
        original.movement_id,
        account_id=ACCOUNT_ID,
        amount=Money("30.00", "BRL"),
    )
    changes.confirm_account_refund(
        refund_proposal,
        metadata("00000000-0000-0000-0000-000000000522", day=3),
    )
    replacement_metadata = RecordMetadata(
        effective_date=date(2026, 9, 2),
        recorded_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID("00000000-0000-0000-0000-000000000523"),
    )
    corrected_allocation = Allocation.create(
        total=Money("120.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(
            AllocationPart(EXPENSE_CATEGORY_ID, Money("60.00", "BRL")),
            AllocationPart(EXPENSE_SUBCATEGORY_ID, Money("60.00", "BRL")),
        ),
        catalog=catalog,
        metadata=replacement_metadata,
    )
    correction = changes.preview_correction(
        original.movement_id,
        CashExpense(
            account_id=ACCOUNT_ID,
            allocation=corrected_allocation,
            description="Compra corrigida",
        ),
        reason="Valor e Rateios corrigidos",
    )
    changes.confirm_correction(
        correction,
        RecordMetadata(
            effective_date=date(2026, 9, 2),
            recorded_at=datetime(2026, 9, 4, 13, tzinfo=UTC),
            origin=RecordOrigin.MANUAL,
            record_id=UUID("00000000-0000-0000-0000-000000000524"),
        ),
    )
    canceled = finances.pay_cash_expense(
        ACCOUNT_ID,
        allocation(
            catalog,
            "50.00",
            "00000000-0000-0000-0000-000000000525",
            day=5,
        ),
        description="Despesa cancelada",
    )
    cancellation = changes.preview_cancellation(
        canceled.movement_id,
        reason="Compra não ocorreu",
    )
    changes.confirm_cancellation(
        cancellation,
        metadata("00000000-0000-0000-0000-000000000526", day=6),
    )

    report = FinancialQueries(
        lambda: SqlAlchemyFinancialQuerySource(sessions)
    ).income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert report.gross_expense.amount == Money("120.00", "BRL")
    assert report.refunds.amount == Money("30.00", "BRL")
    assert report.net_expense.amount == Money("90.00", "BRL")
    assert {item.record_id for item in report.gross_expense.components} == {original.movement_id}
    parent = report.expense_categories[0]
    assert parent.gross.direct.amount == Money("60.00", "BRL")
    assert parent.gross.descendants.amount == Money("60.00", "BRL")
    assert parent.refunds.direct.amount == Money("15.00", "BRL")
    assert parent.refunds.descendants.amount == Money("15.00", "BRL")
    assert parent.net.inclusive.amount == Money("90.00", "BRL")


def test_charges_card_purchase_and_card_refund_use_recognition_not_cash_flow() -> None:
    sessions, catalog = configured_database()
    payables = PayableOperations(lambda: SqlAlchemyPayableUnitOfWork(sessions))
    cards = CreditCardOperations(lambda: SqlAlchemyCreditCardUnitOfWork(sessions))
    changes = FinancialChangeOperations(lambda: SqlAlchemyFinancialChangeUnitOfWork(sessions))
    payable = payables.create_payable(
        allocation=allocation(
            catalog,
            "40.00",
            "00000000-0000-0000-0000-000000000531",
            day=2,
        ),
        due_date=date(2026, 9, 20),
        description="Serviço contratado",
    )
    payables.add_financial_charge(
        payable_id=payable.payable_id,
        allocation=allocation(
            catalog,
            "5.00",
            "00000000-0000-0000-0000-000000000532",
            day=3,
        ),
        description="Juros do serviço",
    )
    card = cards.create_card(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("00000000-0000-0000-0000-000000000533", day=1),
    )
    purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation(
            catalog,
            "100.00",
            "00000000-0000-0000-0000-000000000534",
            day=4,
        ),
        description="Compra no cartão",
    )
    cards.add_financial_charge(
        card_id=card.card_id,
        closing_date=purchase.invoice_closing_date,
        allocation=allocation(
            catalog,
            "7.00",
            "00000000-0000-0000-0000-000000000535",
            day=5,
        ),
        description="Encargo da fatura",
    )
    proposal = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        amount=Money("20.00", "BRL"),
    )
    refund = changes.confirm_card_refund(
        proposal,
        metadata("00000000-0000-0000-0000-000000000536", day=6),
    )

    report = FinancialQueries(
        lambda: SqlAlchemyFinancialQuerySource(sessions)
    ).income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert report.gross_expense.amount == Money("152.00", "BRL")
    assert report.refunds.amount == Money("20.00", "BRL")
    assert report.net_expense.amount == Money("132.00", "BRL")
    assert {item.record_type for item in report.gross_expense.components} == {
        FinancialRecordType.PAYABLE_OPENED,
        FinancialRecordType.PAYABLE_FINANCIAL_CHARGE,
        FinancialRecordType.CARD_PURCHASE,
        FinancialRecordType.INVOICE_FINANCIAL_CHARGE,
    }
    assert len(report.refunds.components) == 1
    refund_component = report.refunds.components[0]
    assert refund_component.record_id == refund.event_id
    assert refund_component.record_type is FinancialRecordType.CARD_REFUND
    assert refund_component.description == "Compra no cartão"


def configured_database():
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=EXPENSE_CATEGORY_ID,
            name="Despesas",
            nature=FinancialNature.EXPENSE,
        )
        .add_category(
            category_id=EXPENSE_SUBCATEGORY_ID,
            name="Detalhadas",
            nature=FinancialNature.EXPENSE,
            parent_id=EXPENSE_CATEGORY_ID,
        )
    )
    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(str(ACCOUNT_ID), day=1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        for category in catalog.categories:
            categories.add(category)
        SqlAlchemyResourceAccountRepository(unit_of_work.session).add(account)
        unit_of_work.commit()
    return sessions, catalog


def allocation(
    catalog: CategoryCatalog,
    amount: str,
    record_id: str,
    *,
    day: int,
) -> Allocation:
    value = Money(amount, "BRL")
    return Allocation.create(
        total=value,
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(EXPENSE_CATEGORY_ID, value),),
        catalog=catalog,
        metadata=metadata(record_id, day=day),
    )


def metadata(record_id: str, *, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )
