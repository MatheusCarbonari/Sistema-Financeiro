from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.resource_accounts import ResourceAccountRepository
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.resource_accounts import (
    SqlAlchemyResourceAccountRepository,
)
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


def metadata(record_id: str, *, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def migrated_database() -> Engine:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    return engine


def test_repository_round_trip_preserves_account_policy_and_immutable_history() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=migrated_database()))
    account = ResourceAccount.open(
        name="Conta personalizada",
        account_type=AccountType.CUSTOM,
        currency="BRL",
        initial_balance=Money("80.00", "BRL"),
        metadata=metadata("e7192166-8e92-441e-b7ce-8121a653002b", day=1),
        custom_allows_negative_balance=False,
    )

    with unit_of_work:
        repository: ResourceAccountRepository = SqlAlchemyResourceAccountRepository(
            unit_of_work.session
        )
        repository.add(account)
        unit_of_work.commit()

    adjusted = account.adjust_balance(
        description="Registro de teste",
        actual_balance=Money("77.45", "BRL"),
        reason="Conciliação com o saldo do banco",
        metadata=metadata("2ccb862b-1834-40cc-9411-41d1944f55fa", day=2),
    )
    with unit_of_work:
        repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        repository.save(adjusted)
        unit_of_work.commit()

    with unit_of_work:
        repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        restored = repository.get(account.account_id)
        missing = repository.get(UUID("54aa5cd8-329e-4dac-bb05-8fa49ea2cf93"))

    assert restored == adjusted
    assert restored is not None
    assert restored.allows_negative_balance is False
    assert restored.history[1].reason == "Conciliação com o saldo do banco"
    assert restored.history[1].difference == Money("-2.55", "BRL")
    assert missing is None


def test_repository_changes_are_rolled_back_atomically_on_failure() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=migrated_database()))
    account = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("20.00", "BRL"),
        metadata=metadata("52b87118-a45a-4118-b710-e591108fd4b8", day=1),
    )

    with pytest.raises(IntegrityError):
        with unit_of_work:
            repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
            repository.add(account)
            repository.add(account)
            unit_of_work.commit()

    with unit_of_work:
        repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        restored = repository.get(account.account_id)

    assert restored is None


def test_repository_rejects_rewriting_persisted_history() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=migrated_database()))
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("10.00", "BRL"),
        metadata=metadata("8467a60c-56bf-4870-86e7-8c7f66e02e26", day=1),
    )
    with unit_of_work:
        repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        repository.add(account)
        unit_of_work.commit()

    rewritten_entry = replace(
        account.history[0],
        difference=Money("12.00", "BRL"),
        balance=Money("12.00", "BRL"),
    )
    rewritten = replace(
        account,
        balance=Money("12.00", "BRL"),
        history=(rewritten_entry,),
    )

    with pytest.raises(ValueError, match="persisted history"):
        with unit_of_work:
            repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
            repository.save(rewritten)


def test_database_prevents_deleting_an_account_that_has_history() -> None:
    engine = migrated_database()
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))
    account = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("1dbc8916-2b8f-43a6-87a4-0fbc82123862", day=1),
    )
    with unit_of_work:
        repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        repository.add(account)
        unit_of_work.commit()

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "DELETE FROM resource_accounts WHERE id = ?", (str(account.account_id),)
            )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE account_history_entries SET reason = 'adulterado'",
        "DELETE FROM account_history_entries",
    ],
)
def test_database_rejects_changes_to_existing_history(statement: str) -> None:
    engine = migrated_database()
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))
    account = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("4ee07fbc-d925-4827-8232-0cc035df795f", day=1),
    )
    with unit_of_work:
        repository = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        repository.add(account)
        unit_of_work.commit()

    with pytest.raises(IntegrityError, match="account history is append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)
