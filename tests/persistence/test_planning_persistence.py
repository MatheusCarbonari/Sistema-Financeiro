from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.planning import (
    ForecastDirection,
    PlannedOccurrence,
    Recurrence,
    RecurrenceFrequency,
    SafetyMargin,
)
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.planning import SqlAlchemyPlanningRepository
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork

ACCOUNT_ID = UUID("db99bd0d-94ee-418c-bf7f-cf1da797f611")


def record_metadata(record_id: str, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def test_migration_0007_preserves_existing_data() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0006")
        connection.exec_driver_sql(
            """
            INSERT INTO resource_accounts (
                id, name, account_type, currency, balance_minor_units,
                status, allows_negative_balance
            ) VALUES (?, 'Conta anterior', 'checking', 'BRL', 10000, 'active', 1)
            """,
            (str(ACCOUNT_ID),),
        )
        connection.exec_driver_sql(
            """
            INSERT INTO account_history_entries (
                id, account_id, sequence, kind, difference_minor_units,
                balance_minor_units, currency, effective_date, recorded_at,
                origin, reason, movement_id
            ) VALUES (?, ?, 0, 'initial_balance', 10000, 10000, 'BRL',
                '2026-09-01', '2026-09-01T12:00:00+00:00', 'manual', NULL, NULL)
            """,
            (str(ACCOUNT_ID), str(ACCOUNT_ID)),
        )

        command.upgrade(config, "head")

        account = connection.exec_driver_sql(
            "SELECT name, balance_minor_units FROM resource_accounts WHERE id = ?",
            (str(ACCOUNT_ID),),
        ).one()
        planning_tables = {
            row[0]
            for row in connection.exec_driver_sql(
                "SELECT name FROM sqlite_master WHERE type = 'table' AND name LIKE 'planning_%'"
            )
        }

    assert account == ("Conta anterior", 10000)
    assert planning_tables == {
        "planning_recurrences",
        "planning_recurrence_revisions",
        "planning_occurrence_events",
    }


def test_repository_round_trips_occurrence_and_recurrence_histories() -> None:
    engine, sessions = configured_database()
    occurrence = PlannedOccurrence.plan(
        description="Registro de teste",
        account_id=ACCOUNT_ID,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        planned_date=date(2026, 10, 10),
        margin=None,
        metadata=record_metadata("7604d43a-1924-4f1f-aa5b-ebf08563e06e", 1),
    )
    recurrence = Recurrence.create(
        description="Registro de teste",
        account_id=ACCOUNT_ID,
        direction=ForecastDirection.INFLOW,
        expected_amount=Money("200.00", "BRL"),
        starts_on=date(2026, 10, 15),
        ends_on=None,
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=record_metadata("1472e7a2-fdb4-47cb-9101-b7f41b616c42", 1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        repository = SqlAlchemyPlanningRepository(unit_of_work.session)
        repository.add_occurrence(occurrence)
        repository.add_recurrence(recurrence)
        unit_of_work.commit()

    revised_occurrence = occurrence.revise(
        expected_amount=Money("110.00", "BRL"),
        planned_date=date(2026, 10, 11),
        margin=SafetyMargin.fixed(Money("10.00", "BRL")),
        reason="Valor revisto",
        metadata=record_metadata("9b957ff1-2ac2-427c-a43c-d877ea7d8b94", 5),
    )
    revised_recurrence = recurrence.revise_from(
        from_date=date(2026, 11, 15),
        expected_amount=Money("250.00", "BRL"),
        margin=SafetyMargin.percentage("5"),
        frequency=RecurrenceFrequency.WEEKLY,
        interval=2,
        reason="Nova cadencia",
        metadata=record_metadata("7cfc53b4-8f9d-466e-88be-3f58d6e03dca", 5),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        repository = SqlAlchemyPlanningRepository(unit_of_work.session)
        repository.save_occurrence(revised_occurrence)
        repository.save_recurrence(revised_recurrence)
        unit_of_work.commit()

    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        repository = SqlAlchemyPlanningRepository(unit_of_work.session)
        restored_occurrence = repository.get_occurrence(occurrence.occurrence_id)
        restored_recurrence = repository.get_recurrence(recurrence.recurrence_id)

    assert restored_occurrence == revised_occurrence
    assert restored_recurrence == revised_recurrence
    assert tuple(
        item.planned_date for item in restored_recurrence.occurrences_through(date(2026, 12, 15))
    ) == (
        date(2026, 10, 15),
        date(2026, 11, 15),
        date(2026, 11, 29),
        date(2026, 12, 13),
    )
    del engine


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE planned_occurrences SET amount_minor_units = 1",
        "DELETE FROM planned_occurrences",
        "UPDATE planning_recurrences SET interval = 2",
        "DELETE FROM planning_recurrences",
    ],
)
def test_database_keeps_planning_origins_append_only(statement: str) -> None:
    engine, sessions = configured_database()
    occurrence = PlannedOccurrence.plan(
        description="Registro de teste",
        account_id=ACCOUNT_ID,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        planned_date=date(2026, 10, 10),
        margin=None,
        metadata=record_metadata("7604d43a-1924-4f1f-aa5b-ebf08563e06e", 1),
    )
    recurrence = Recurrence.create(
        description="Registro de teste",
        account_id=ACCOUNT_ID,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 10, 10),
        ends_on=None,
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=record_metadata("1472e7a2-fdb4-47cb-9101-b7f41b616c42", 1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        repository = SqlAlchemyPlanningRepository(unit_of_work.session)
        repository.add_occurrence(occurrence)
        repository.add_recurrence(recurrence)
        unit_of_work.commit()

    with pytest.raises(IntegrityError, match="planning history is append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)


def configured_database():
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=record_metadata(str(ACCOUNT_ID), 1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        SqlAlchemyResourceAccountRepository(unit_of_work.session).add(account)
        unit_of_work.commit()
    return engine, sessions
