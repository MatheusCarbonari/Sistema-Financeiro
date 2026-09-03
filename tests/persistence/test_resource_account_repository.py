from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import create_engine
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.resource_accounts import ResourceAccountRepository
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount
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
    engine = create_engine("sqlite+pysqlite:///:memory:")
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
