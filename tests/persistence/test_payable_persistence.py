from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.payables import Payable
from bot_financeiro.persistence.categories import (
    SqlAlchemyAllocationRepository,
    SqlAlchemyFinancialCategoryRepository,
)
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.payables import SqlAlchemyPayableRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


def migrated_database() -> Engine:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine


def metadata(record_id: str, *, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def persisted_payable(engine: Engine) -> Payable:
    sessions = sessionmaker(bind=engine)
    unit_of_work = SqlAlchemyUnitOfWork(sessions)
    category_id = UUID("9f81a09d-99d2-448e-aa3c-7bb3a535146b")
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
        metadata=metadata("580472fa-3d7d-4cdd-9272-9106137223d1", day=2),
    )
    payable = Payable.open(
        description="Registro de teste",
        allocation=allocation,
        due_date=date(2026, 9, 10),
        catalog=catalog,
    )
    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        payables = SqlAlchemyPayableRepository(unit_of_work.session)
        categories.add(catalog.require(category_id))
        allocations.add(allocation)
        payables.add(payable)
        unit_of_work.commit()
    return payable


def test_migration_0004_preserves_existing_movements_and_account_history() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    source_id = "a96a61b9-2c75-46aa-b69a-af9433c6f7fb"
    destination_id = "c0be3497-fc54-4145-8918-13bac75c66c7"
    movement_id = "0bbb0c5d-443f-449a-926a-7ca09d97623d"
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0003")
        for account_id, name, balance in (
            (source_id, "Origem", 90000),
            (destination_id, "Destino", 110000),
        ):
            connection.exec_driver_sql(
                """
                INSERT INTO resource_accounts (
                    id, name, account_type, currency, balance_minor_units,
                    status, allows_negative_balance
                ) VALUES (?, ?, 'checking', 'BRL', ?, 'active', 1)
                """,
                (account_id, name, balance),
            )
            connection.exec_driver_sql(
                """
                INSERT INTO account_history_entries (
                    id, account_id, sequence, kind, difference_minor_units,
                    balance_minor_units, currency, effective_date, recorded_at,
                    origin, reason, movement_id
                ) VALUES (?, ?, 0, 'initial_balance', 100000, 100000, 'BRL', ?, ?,
                    'manual', NULL, NULL)
                """,
                (account_id, account_id, "2026-09-01", "2026-09-01T12:00:00+00:00"),
            )
        connection.exec_driver_sql(
            """
            INSERT INTO financial_movements (
                id, kind, amount_minor_units, currency, source_account_id,
                destination_account_id, effective_date, recorded_at, origin
            ) VALUES (?, 'transfer', 10000, 'BRL', ?, ?, ?, ?, 'manual')
            """,
            (
                movement_id,
                source_id,
                destination_id,
                "2026-09-02",
                "2026-09-02T12:00:00+00:00",
            ),
        )
        for entry_id, account_id, kind, difference, balance in (
            ("319bb47a-f8c3-482e-a247-db817779e9da", source_id, "transfer_out", -10000, 90000),
            (
                "414b4398-5381-48e7-803b-f787521834c0",
                destination_id,
                "transfer_in",
                10000,
                110000,
            ),
        ):
            connection.exec_driver_sql(
                """
                INSERT INTO account_history_entries (
                    id, account_id, sequence, kind, difference_minor_units,
                    balance_minor_units, currency, effective_date, recorded_at,
                    origin, reason, movement_id
                ) VALUES (?, ?, 1, ?, ?, ?, 'BRL', ?, ?, 'manual', NULL, ?)
                """,
                (
                    entry_id,
                    account_id,
                    kind,
                    difference,
                    balance,
                    "2026-09-02",
                    "2026-09-02T12:00:00+00:00",
                    movement_id,
                ),
            )

        command.upgrade(config, "head")
        movement = connection.exec_driver_sql(
            "SELECT kind, amount_minor_units, payable_id FROM financial_movements"
        ).one()
        history_count = connection.exec_driver_sql(
            "SELECT COUNT(*) FROM account_history_entries"
        ).scalar_one()

    assert movement == ("transfer", 10000, None)
    assert history_count == 4

    with pytest.raises(IntegrityError, match="financial movement history is append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql("UPDATE financial_movements SET amount_minor_units = 1")


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE payables SET due_date = '2026-09-20'",
        "DELETE FROM payables",
        "UPDATE payable_history_entries SET amount_minor_units = 1",
        "DELETE FROM payable_history_entries",
    ],
)
def test_database_preserves_payable_history_as_append_only(statement: str) -> None:
    engine = migrated_database()
    persisted_payable(engine)

    with pytest.raises(IntegrityError, match="payable history is append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)


def test_repository_rejects_rewriting_payable_opening() -> None:
    engine = migrated_database()
    payable = persisted_payable(engine)
    changed_opening = replace(payable.opening, due_date=date(2026, 9, 20))
    rewritten = Payable(history=(changed_opening,))
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))

    with pytest.raises(ValueError, match="history is immutable"):
        with unit_of_work:
            payables = SqlAlchemyPayableRepository(unit_of_work.session)
            payables.save(rewritten)


def test_repository_rejects_history_amount_that_diverges_from_its_allocation() -> None:
    engine = migrated_database()
    payable = persisted_payable(engine)
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER payable_history_entries_no_update")
        connection.exec_driver_sql(
            "UPDATE payable_history_entries SET amount_minor_units = 1 WHERE id = ?",
            (str(payable.payable_id),),
        )
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))

    with pytest.raises(ValueError, match="diverges from its domain event"):
        with unit_of_work:
            payables = SqlAlchemyPayableRepository(unit_of_work.session)
            payables.get(payable.payable_id)
