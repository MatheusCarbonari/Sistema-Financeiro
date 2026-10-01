from datetime import UTC, date, datetime
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.credit_cards import CreditCardOperations
from bot_financeiro.application.financial_changes import FinancialChangeOperations
from bot_financeiro.application.financial_operations import FinancialOperations
from bot_financeiro.application.financial_queries import FinancialQueries, QueryPeriod
from bot_financeiro.application.payables import PayableOperations
from bot_financeiro.application.planning import PlanningOperations
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.credit_cards import CardPurchase, InvoiceComponentKind
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.planning import ForecastDirection, RecurrenceFrequency
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
from bot_financeiro.persistence.planning import SqlAlchemyPlanningUnitOfWork
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork

FIRST_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000701")
SECOND_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000702")
INCOME_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000703")
EXPENSE_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000704")


def test_card_and_payable_positions_are_rehydrated_through_the_query_facade() -> None:
    sessions, catalog = configured_database()
    cards = CreditCardOperations(lambda: SqlAlchemyCreditCardUnitOfWork(sessions))
    payables = PayableOperations(lambda: SqlAlchemyPayableUnitOfWork(sessions))
    changes = FinancialChangeOperations(lambda: SqlAlchemyFinancialChangeUnitOfWork(sessions))
    card = cards.create_card(
        name="Cartão persistido",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("500.00", "BRL"),
        metadata=metadata(710, day=1),
    )
    purchase = cards.record_installment_purchase(
        card_id=card.card_id,
        allocation=allocation(catalog, "90.00", 711, day=2),
        installment_count=3,
        description="Compra parcelada persistida",
    )
    simple_purchase = cards.record_purchase(
        card_id=card.card_id,
        allocation=allocation(catalog, "100.00", 713, day=3),
        description="Compra simples persistida",
    )
    replacement = CardPurchase(
        card_id=card.card_id,
        allocation=allocation(catalog, "100.00", 719, day=3),
        invoice_closing_date=simple_purchase.invoice_closing_date,
        description="Compra simples corrigida",
    )
    correction = changes.preview_correction(
        simple_purchase.purchase_id,
        replacement,
        reason="Descrição vigente",
    )
    correction_recorded_at = datetime(2026, 9, 3, 18, tzinfo=UTC)
    changes.confirm_correction(
        correction,
        RecordMetadata(
            effective_date=date(2026, 9, 3),
            recorded_at=correction_recorded_at,
            origin=RecordOrigin.MANUAL,
            record_id=UUID("00000000-0000-0000-0000-000000000721"),
        ),
    )
    cards.add_financial_charge(
        card_id=card.card_id,
        closing_date=simple_purchase.invoice_closing_date,
        allocation=allocation(catalog, "20.00", 714, day=4),
        description="Encargo persistido",
    )
    cards.pay_invoice(
        card_id=card.card_id,
        closing_date=simple_purchase.invoice_closing_date,
        account_id=FIRST_ACCOUNT_ID,
        amount=Money("50.00", "BRL"),
        metadata=metadata(715, day=5),
        description="Pagamento persistido",
    )
    refund = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=simple_purchase.purchase_id,
        amount=Money("20.00", "BRL"),
    )
    changes.confirm_card_refund(refund, metadata(716, day=6))
    cards.grant_card_credit(
        card_id=card.card_id,
        amount=Money("30.00", "BRL"),
        description="Crédito persistido",
        reason="Crédito real",
        metadata=metadata(717, day=7),
    )
    cards.apply_card_credit(
        card_id=card.card_id,
        closing_date=simple_purchase.invoice_closing_date,
        amount=Money("10.00", "BRL"),
        metadata=metadata(718, day=8),
    )
    payable = payables.create_payable(
        allocation=allocation(catalog, "40.00", 712, day=3),
        due_date=date(2026, 9, 10),
        description="Conta persistida",
    )
    queries = FinancialQueries(lambda: SqlAlchemyFinancialQuerySource(sessions))

    card_position = queries.credit_card_position(
        card_id=card.card_id,
        on_date=date(2026, 9, 20),
    )
    payable_report = queries.payables_report(
        on_date=date(2026, 9, 20),
        currency="BRL",
    )

    assert card_position.principal_pending.amount == Money("110.00", "BRL")
    assert card_position.financial_charges_pending.amount == Money("20.00", "BRL")
    assert card_position.net_debt.amount == Money("130.00", "BRL")
    assert card_position.available_card_credit.amount == Money("20.00", "BRL")
    assert card_position.available_limit == Money("390.00", "BRL")
    assert [len(invoice.installments) for invoice in card_position.invoices] == [1, 1, 1]
    assert all(
        invoice.installments[0].purchase_id == purchase.purchase_id
        for invoice in card_position.invoices
    )
    corrected_component = next(
        component
        for invoice in card_position.invoices
        for component in invoice.components
        if component.kind is InvoiceComponentKind.PURCHASE
        and component.purchase_id == simple_purchase.purchase_id
    )
    assert corrected_component.description == "Compra simples corrigida"
    assert corrected_component.recorded_at == correction_recorded_at
    assert payable_report.total_due.amount == Money("40.00", "BRL")
    assert payable_report.payables[0].payable_id == payable.payable_id


def test_commitments_expand_recurrences_and_keep_overdue_occurrences() -> None:
    sessions, _ = configured_database()
    planning = PlanningOperations(lambda: SqlAlchemyPlanningUnitOfWork(sessions))
    recurrence = planning.create_recurrence(
        account_id=FIRST_ACCOUNT_ID,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("25.00", "BRL"),
        starts_on=date(2026, 9, 15),
        ends_on=None,
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        description="Assinatura prevista",
        metadata=metadata(720, day=1),
    )

    report = FinancialQueries(lambda: SqlAlchemyFinancialQuerySource(sessions)).commitments_report(
        as_of=date(2026, 9, 20),
        through_date=date(2026, 10, 31),
        currency="BRL",
    )

    assert report.forecast_outflow_total == Money("50.00", "BRL")
    assert [item.planned_date for item in report.forecast_occurrences] == [
        date(2026, 9, 15),
        date(2026, 10, 15),
    ]
    assert all(item.occurrence_id for item in report.forecast_occurrences)
    assert all(item.description == recurrence.description for item in report.forecast_occurrences)


def test_persisted_transfer_is_detailed_globally_and_counted_only_per_account() -> None:
    sessions, catalog = configured_database()
    finances = FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions))
    income = finances.receive_income(
        FIRST_ACCOUNT_ID,
        allocation(
            catalog,
            "100.00",
            730,
            day=2,
            nature=FinancialNature.INCOME,
            category_id=INCOME_CATEGORY_ID,
        ),
        description="Receita realizada",
    )
    transfer_result = finances.transfer(
        source_account_id=FIRST_ACCOUNT_ID,
        destination_account_id=SECOND_ACCOUNT_ID,
        amount=Money("20.00", "BRL"),
        metadata=metadata(731, day=3),
        description="Transferência persistida",
    )
    transfer = transfer_result.transfer
    queries = FinancialQueries(lambda: SqlAlchemyFinancialQuerySource(sessions))
    period = QueryPeriod(date(2026, 9, 1), date(2026, 9, 30))

    global_flow = queries.realized_cash_flow(period=period, currency="BRL")
    destination_flow = queries.realized_cash_flow(
        period=period,
        currency="BRL",
        account_id=SECOND_ACCOUNT_ID,
    )

    assert global_flow.inflows == income.amount
    assert global_flow.outflows == Money("0.00", "BRL")
    assert (
        len([entry for entry in global_flow.entries if entry.record_id == transfer.movement_id])
        == 2
    )
    assert destination_flow.inflows == Money("20.00", "BRL")
    assert destination_flow.net_change == Money("20.00", "BRL")


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
            category_id=INCOME_CATEGORY_ID,
            name="Receitas",
            nature=FinancialNature.INCOME,
        )
        .add_category(
            category_id=EXPENSE_CATEGORY_ID,
            name="Despesas",
            nature=FinancialNature.EXPENSE,
        )
    )
    first = ResourceAccount.open(
        name="Conta A",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(701, day=1),
    )
    second = ResourceAccount.open(
        name="Conta B",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata(702, day=1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        for category in catalog.categories:
            categories.add(category)
        accounts = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        accounts.add(first)
        accounts.add(second)
        unit_of_work.commit()
    return sessions, catalog


def allocation(
    catalog: CategoryCatalog,
    amount: str,
    identity_suffix: int,
    *,
    day: int,
    nature: FinancialNature = FinancialNature.EXPENSE,
    category_id: UUID = EXPENSE_CATEGORY_ID,
) -> Allocation:
    value = Money(amount, "BRL")
    return Allocation.create(
        total=value,
        nature=nature,
        parts=(AllocationPart(category_id, value),),
        catalog=catalog,
        metadata=metadata(identity_suffix, day=day),
    )


def metadata(identity_suffix: int, *, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(f"00000000-0000-0000-0000-{identity_suffix:012d}"),
    )
