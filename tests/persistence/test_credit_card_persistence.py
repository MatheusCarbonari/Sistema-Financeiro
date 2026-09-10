from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.credit_cards import CreditCard
from bot_financeiro.domain.money import Money
from bot_financeiro.persistence.credit_cards import SqlAlchemyCreditCardRepository
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


def metadata() -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, 1),
        recorded_at=datetime(2026, 9, 1, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID("3bb5caee-8c94-4ec1-bc60-72be0033a880"),
    )


def test_migration_0005_preserves_existing_resource_account_data() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    account_id = "b2a8c084-2630-4945-9304-0b41349c42a0"
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0004")
        connection.exec_driver_sql(
            """
            INSERT INTO resource_accounts (
                id, name, account_type, currency, balance_minor_units,
                status, allows_negative_balance
            ) VALUES (?, 'Conta anterior', 'checking', 'BRL', 10000, 'active', 1)
            """,
            (account_id,),
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
            (account_id, account_id),
        )

        command.upgrade(config, "head")
        account = connection.exec_driver_sql(
            "SELECT name, balance_minor_units FROM resource_accounts WHERE id = ?",
            (account_id,),
        ).one()
        movement_columns = {
            row[1]
            for row in connection.exec_driver_sql(
                "PRAGMA table_info(financial_movements)"
            )
        }

    assert account == ("Conta anterior", 10000)
    assert {
        "card_id",
        "invoice_closing_date",
        "applied_amount_minor_units",
    } <= movement_columns


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE credit_cards SET name = 'Alterado'",
        "DELETE FROM credit_cards",
        "UPDATE credit_card_history_entries SET kind = 'altered'",
        "DELETE FROM credit_card_history_entries",
    ],
)
def test_database_keeps_credit_card_history_append_only(statement: str) -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata(),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        SqlAlchemyCreditCardRepository(unit_of_work.session).add(card)
        unit_of_work.commit()

    with pytest.raises(IntegrityError, match="credit card history is append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)


def test_database_rejects_malformed_credit_card_history_event() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata(),
    )
    with SqlAlchemyUnitOfWork(sessionmaker(bind=engine)) as unit_of_work:
        SqlAlchemyCreditCardRepository(unit_of_work.session).add(card)
        unit_of_work.commit()

    with pytest.raises(IntegrityError, match="ck_credit_card_history"):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO credit_card_history_entries (
                    id, card_id, sequence, kind, amount_minor_units, currency,
                    effective_date, recorded_at, origin
                ) VALUES (?, ?, 1, 'purchase', 1000, 'BRL',
                    '2026-09-02', '2026-09-02T12:00:00+00:00', 'manual')
                """,
                (
                    "3b4514f1-f3b6-4739-99de-98b85beecf96",
                    str(card.card_id),
                ),
            )
