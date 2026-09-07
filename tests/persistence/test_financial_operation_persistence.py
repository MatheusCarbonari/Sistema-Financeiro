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
from bot_financeiro.domain.movements import CashExpense, Income
from bot_financeiro.domain.resource_account import (
    AccountHistoryEntryKind,
    AccountType,
    ResourceAccount,
)
from bot_financeiro.persistence.categories import (
    SqlAlchemyAllocationRepository,
    SqlAlchemyFinancialCategoryRepository,
)
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialMovementRepository,
)
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
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


def test_account_history_rejects_an_unregistered_movement_reference() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=migrated_database()))
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("20.00", "BRL"),
        metadata=metadata("65cfe603-fccc-4720-a7b6-a0aa7b3201dd", day=1),
    )
    with unit_of_work:
        accounts = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        accounts.add(account)
        unit_of_work.commit()

    movement_metadata = metadata("b0a093f3-6583-45c6-8594-4a1dff68f601", day=2)
    forged = account.record_movement(
        kind=AccountHistoryEntryKind.INCOME,
        difference=Money("5.00", "BRL"),
        movement_metadata=movement_metadata,
    )

    with pytest.raises(IntegrityError):
        with unit_of_work:
            accounts = SqlAlchemyResourceAccountRepository(unit_of_work.session)
            accounts.save(forged)
            unit_of_work.commit()


def test_database_rejects_a_transfer_without_both_linked_accounts() -> None:
    engine = migrated_database()

    with pytest.raises(IntegrityError):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                """
                INSERT INTO financial_movements (
                    id, kind, amount_minor_units, currency, effective_date, recorded_at, origin
                ) VALUES (?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    "30226869-c7bf-4cc7-8607-ec9d3c64fd1f",
                    "transfer",
                    100,
                    "BRL",
                    "2026-09-02",
                    "2026-09-02T12:00:00+00:00",
                    "manual",
                ),
            )


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE financial_movements SET amount_minor_units = 200",
        "DELETE FROM financial_movements",
    ],
)
def test_database_preserves_financial_movements_as_append_only(statement: str) -> None:
    engine = migrated_database()
    with engine.begin() as connection:
        for account_id, name in (
            ("201c8802-516f-4a37-9e7a-1457234a3db6", "Origem"),
            ("63f49f78-779e-4f68-b28b-d9f5a30eff35", "Destino"),
        ):
            connection.exec_driver_sql(
                """
                INSERT INTO resource_accounts (
                    id, name, account_type, currency, balance_minor_units,
                    status, allows_negative_balance
                ) VALUES (?, ?, 'checking', 'BRL', 0, 'active', 1)
                """,
                (account_id, name),
            )
        connection.exec_driver_sql(
            """
            INSERT INTO financial_movements (
                id, kind, amount_minor_units, currency, source_account_id,
                destination_account_id, effective_date, recorded_at, origin
            ) VALUES (?, 'transfer', 100, 'BRL', ?, ?, ?, ?, 'manual')
            """,
            (
                "f1055fef-9b74-45e0-92ea-20e9122a0240",
                "201c8802-516f-4a37-9e7a-1457234a3db6",
                "63f49f78-779e-4f68-b28b-d9f5a30eff35",
                "2026-09-02",
                "2026-09-02T12:00:00+00:00",
            ),
        )

    with pytest.raises(IntegrityError, match="financial movement history is append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)


def test_migration_0003_preserves_existing_account_history() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0002")
        connection.exec_driver_sql(
            """
            INSERT INTO resource_accounts (
                id, name, account_type, currency, balance_minor_units,
                status, allows_negative_balance
            ) VALUES (?, 'Conta existente', 'checking', 'BRL', 1000, 'active', 1)
            """,
            ("80dcc70f-7a8b-464a-898b-051d0916770e",),
        )
        connection.exec_driver_sql(
            """
            INSERT INTO account_history_entries (
                id, account_id, sequence, kind, difference_minor_units,
                balance_minor_units, currency, effective_date, recorded_at, origin, reason
            ) VALUES (?, ?, 0, 'initial_balance', 1000, 1000, 'BRL', ?, ?, 'manual', NULL)
            """,
            (
                "80dcc70f-7a8b-464a-898b-051d0916770e",
                "80dcc70f-7a8b-464a-898b-051d0916770e",
                "2026-09-01",
                "2026-09-01T12:00:00+00:00",
            ),
        )
        command.upgrade(config, "head")
        row = connection.exec_driver_sql(
            "SELECT difference_minor_units, movement_id FROM account_history_entries"
        ).one()

    assert row == (1000, None)


def test_repository_rejects_a_movement_that_diverges_from_its_allocation() -> None:
    engine = migrated_database()
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("8298a888-82f3-412c-aadb-94d7f18006c9", day=1),
    )
    category_id = UUID("ae74e75a-b194-47a1-94c8-a79645bd9f8f")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("10.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("10.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("b544e3c6-a499-4c36-a265-ab4512df4c25", day=2),
    )
    income = Income(account_id=account.account_id, allocation=allocation)
    with unit_of_work:
        accounts = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        movements = SqlAlchemyFinancialMovementRepository(unit_of_work.session)
        accounts.add(account)
        categories.add(catalog.require(category_id))
        allocations.add(allocation)
        movements.add(income)
        unit_of_work.commit()
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER financial_movements_no_update")
        connection.exec_driver_sql(
            "UPDATE financial_movements SET amount_minor_units = 999 WHERE id = ?",
            (str(income.movement_id),),
        )

    with pytest.raises(ValueError, match="diverges from its allocation"):
        with unit_of_work:
            movements = SqlAlchemyFinancialMovementRepository(unit_of_work.session)
            movements.get(income.movement_id)


def test_repository_rejects_a_fee_linked_to_something_other_than_a_transfer() -> None:
    engine = migrated_database()
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("cb5f2944-f482-473c-96f8-0b4aff01d64a", day=1),
    )
    income_category_id = UUID("91654f6a-62f7-4ba8-9156-ab76c326da31")
    expense_category_id = UUID("0a8989cd-fe65-46d6-99e2-e7dad0711c18")
    catalog = CategoryCatalog.empty().add_category(
        category_id=income_category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    catalog = catalog.add_category(
        category_id=expense_category_id,
        name="Tarifas bancárias",
        nature=FinancialNature.EXPENSE,
    )
    income_allocation = Allocation.create(
        total=Money("10.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(income_category_id, Money("10.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("c03b7a1c-edfb-42a1-87fc-98870362d28d", day=2),
    )
    fee_allocation = Allocation.create(
        total=Money("1.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(expense_category_id, Money("1.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("6a7ca57c-8b79-4c4f-a07a-1c934a049009", day=2),
    )
    income = Income(account_id=account.account_id, allocation=income_allocation)
    invalid_fee = CashExpense(
        account_id=account.account_id,
        allocation=fee_allocation,
        related_transfer_id=income.movement_id,
    )
    with unit_of_work:
        accounts = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        movements = SqlAlchemyFinancialMovementRepository(unit_of_work.session)
        accounts.add(account)
        for category in catalog.categories:
            categories.add(category)
        allocations.add(income_allocation)
        allocations.add(fee_allocation)
        movements.add(income)
        unit_of_work.commit()

    with pytest.raises(ValueError, match="fee must reference a transfer"):
        with unit_of_work:
            movements = SqlAlchemyFinancialMovementRepository(unit_of_work.session)
            movements.add(invalid_fee)
