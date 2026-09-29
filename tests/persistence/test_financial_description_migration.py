from datetime import UTC, date, datetime
from uuid import UUID

from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.financial_changes import FinancialChangeOperations
from bot_financeiro.application.financial_operations import FinancialOperations
from bot_financeiro.application.financial_queries import (
    FinancialQueries,
    FinancialSearchFilters,
    QueryPeriod,
)
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import Income
from bot_financeiro.domain.planning import ForecastDirection
from bot_financeiro.domain.resource_account import (
    AccountHistoryEntryKind,
    AccountType,
    ResourceAccount,
)
from bot_financeiro.persistence.categories import SqlAlchemyFinancialCategoryRepository
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.financial_change_unit_of_work import (
    SqlAlchemyFinancialChangeUnitOfWork,
)
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialOperationUnitOfWork,
)
from bot_financeiro.persistence.financial_queries import SqlAlchemyFinancialQuerySource
from bot_financeiro.persistence.planning import SqlAlchemyPlanningRepository
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


def test_migration_0009_preserves_legacy_records_without_inventing_descriptions() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")

    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0008")
        for account_id, name in (("source", "Origem"), ("destination", "Destino")):
            connection.exec_driver_sql(
                "INSERT INTO resource_accounts "
                "(id, name, account_type, currency, balance_minor_units, status, "
                "allows_negative_balance) VALUES (?, ?, 'checking', 'BRL', 0, "
                "'active', 1)",
                (account_id, name),
            )
        connection.exec_driver_sql(
            "INSERT INTO financial_movements "
            "(id, kind, amount_minor_units, currency, account_id, source_account_id, "
            "destination_account_id, allocation_id, related_transfer_id, payable_id, "
            "card_id, invoice_closing_date, applied_amount_minor_units, "
            "original_expense_id, effective_date, recorded_at, origin) VALUES "
            "('legacy', 'transfer', 1000, 'BRL', NULL, 'source', 'destination', "
            "NULL, NULL, NULL, NULL, NULL, NULL, NULL, '2026-09-01', "
            "'2026-09-01T12:00:00+00:00', 'manual')"
        )

        command.upgrade(config, "0009")

        columns = {
            row[1] for row in connection.exec_driver_sql("PRAGMA table_info(financial_movements)")
        }
        legacy_description = connection.exec_driver_sql(
            "SELECT description FROM financial_movements WHERE id = 'legacy'"
        ).scalar_one()

    assert "description" in columns
    assert legacy_description is None


def test_described_movement_round_trips_through_public_financial_queries() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    account_id = UUID("00000000-0000-0000-0000-000000000001")
    category_id = UUID("00000000-0000-0000-0000-000000000101")
    movement_id = UUID("00000000-0000-0000-0000-000000000201")
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
        metadata=_metadata(account_id, 1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        SqlAlchemyFinancialCategoryRepository(unit_of_work.session).add(
            catalog.require(category_id)
        )
        SqlAlchemyResourceAccountRepository(unit_of_work.session).add(account)
        unit_of_work.commit()
    allocation = Allocation.create(
        total=Money("1000", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("1000", "BRL")),),
        catalog=catalog,
        metadata=_metadata(movement_id, 2),
    )
    FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions)).receive_income(
        account_id, allocation, description="Salário de setembro"
    )

    queries = FinancialQueries(lambda: SqlAlchemyFinancialQuerySource(sessions))
    results = queries.search_movements(FinancialSearchFilters(description="setembro"))
    statement = queries.account_statement(account_id=account_id)
    report = queries.income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert [result.description for result in results] == ["Salário de setembro"]
    assert statement.closing_balance.amount == Money("1000", "BRL")
    assert report.income.amount == Money("1000", "BRL")
    assert report.gross_expense.amount == Money("0", "BRL")


def test_migrated_legacy_planning_can_be_restored_revised_and_generated() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    account_id = "00000000-0000-0000-0000-000000000401"
    occurrence_id = UUID("00000000-0000-0000-0000-000000000402")
    recurrence_id = UUID("00000000-0000-0000-0000-000000000403")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0008")
        connection.exec_driver_sql(
            "INSERT INTO resource_accounts "
            "(id, name, account_type, currency, balance_minor_units, status, "
            "allows_negative_balance) VALUES (?, 'Conta', 'checking', 'BRL', 0, "
            "'active', 1)",
            (account_id,),
        )
        connection.exec_driver_sql(
            "INSERT INTO planned_occurrences "
            "(id, account_id, direction, amount_minor_units, currency, planned_date, "
            "margin_kind, margin_value, effective_date, recorded_at, origin) VALUES "
            "(?, ?, 'outflow', 10000, 'BRL', '2026-10-01', NULL, NULL, "
            "'2026-09-01', '2026-09-01T12:00:00+00:00', 'manual')",
            (str(occurrence_id), account_id),
        )
        connection.exec_driver_sql(
            "INSERT INTO planning_recurrences "
            "(id, account_id, direction, amount_minor_units, currency, starts_on, "
            "ends_on, frequency, interval, margin_kind, margin_value, effective_date, "
            "recorded_at, origin) VALUES (?, ?, 'outflow', 5000, 'BRL', "
            "'2026-10-01', NULL, 'monthly', 1, NULL, NULL, '2026-09-01', "
            "'2026-09-01T12:00:00+00:00', 'manual')",
            (str(recurrence_id), account_id),
        )
        command.upgrade(config, "0009")

    sessions = sessionmaker(bind=engine)
    with sessions() as session:
        repository = SqlAlchemyPlanningRepository(session)
        occurrence = repository.get_occurrence(occurrence_id)
        recurrence = repository.get_recurrence(recurrence_id)

    assert occurrence is not None
    assert recurrence is not None
    assert occurrence.description is None
    assert recurrence.description is None
    assert recurrence.occurrences_through(date(2026, 10, 1))[0].description is None

    revised = occurrence.revise(
        expected_amount=Money("110", "BRL"),
        planned_date=date(2026, 10, 2),
        margin=None,
        description="Conta prevista legada",
        reason="Completar dados legados",
        metadata=RecordMetadata(
            effective_date=date(2026, 9, 2),
            recorded_at=datetime(2026, 9, 2, 12, tzinfo=UTC),
            origin=RecordOrigin.MANUAL,
            record_id=UUID("00000000-0000-0000-0000-000000000404"),
        ),
    )
    assert revised.description == "Conta prevista legada"
    assert revised.direction is ForecastDirection.OUTFLOW


def test_legacy_movement_can_be_queried_and_corrected_without_a_description() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    account_id = UUID("00000000-0000-0000-0000-000000000301")
    category_id = UUID("00000000-0000-0000-0000-000000000302")
    movement_id = UUID("00000000-0000-0000-0000-000000000303")
    replacement_id = UUID("00000000-0000-0000-0000-000000000304")
    correction_id = UUID("00000000-0000-0000-0000-000000000305")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    account = ResourceAccount.open(
        name="Conta legada",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100", "BRL"),
        metadata=_metadata(account_id, 1),
    )
    allocation = Allocation.create(
        total=Money("50", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("50", "BRL")),),
        catalog=catalog,
        metadata=_metadata(movement_id, 2),
    )
    legacy_income = Income(
        account_id=account_id,
        allocation=allocation,
        description=None,
    )
    with SqlAlchemyFinancialOperationUnitOfWork(sessions) as unit_of_work:
        unit_of_work.categories.add(catalog.require(category_id))
        unit_of_work.accounts.add(account)
        unit_of_work.commit()
    account = account.record_movement(
        kind=AccountHistoryEntryKind.INCOME,
        difference=Money("50", "BRL"),
        movement_metadata=allocation.metadata,
    )
    with SqlAlchemyFinancialOperationUnitOfWork(sessions) as unit_of_work:
        unit_of_work.allocations.add(allocation)
        unit_of_work.movements.add(legacy_income)
        unit_of_work.accounts.save(account)
        unit_of_work.commit()

    queries = FinancialQueries(lambda: SqlAlchemyFinancialQuerySource(sessions))
    legacy_results = queries.search_movements(FinancialSearchFilters())
    legacy_statement = queries.account_statement(account_id=account_id)

    assert [result.description for result in legacy_results] == [None]
    assert legacy_statement.closing_balance.amount == Money("150", "BRL")

    replacement_metadata = RecordMetadata(
        effective_date=date(2026, 9, 2),
        recorded_at=datetime(2026, 9, 3, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=replacement_id,
    )
    replacement_allocation = Allocation.create(
        total=Money("60", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("60", "BRL")),),
        catalog=catalog,
        metadata=replacement_metadata,
    )
    replacement = Income(
        account_id=account_id,
        allocation=replacement_allocation,
        description="Salário legado corrigido",
    )
    changes = FinancialChangeOperations(lambda: SqlAlchemyFinancialChangeUnitOfWork(sessions))
    proposal = changes.preview_correction(
        movement_id,
        replacement,
        reason="Completar descrição e valor",
    )
    changes.confirm_correction(
        proposal,
        RecordMetadata(
            effective_date=date(2026, 9, 2),
            recorded_at=datetime(2026, 9, 4, 12, tzinfo=UTC),
            origin=RecordOrigin.MANUAL,
            record_id=correction_id,
        ),
    )

    history = changes.get_history(movement_id)
    current_results = queries.search_movements(FinancialSearchFilters(description="corrigido"))
    current_statement = queries.account_statement(account_id=account_id)

    assert history.original.description is None
    assert history.current is not None
    assert history.current.description == "Salário legado corrigido"
    assert [result.description for result in current_results] == ["Salário legado corrigido"]
    assert current_statement.closing_balance.amount == Money("160", "BRL")


def _metadata(record_id: UUID, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=record_id,
    )
