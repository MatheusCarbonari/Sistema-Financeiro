from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.financial_operations import FinancialOperations
from bot_financeiro.application.payables import PayableOperations
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount
from bot_financeiro.persistence.categories import SqlAlchemyFinancialCategoryRepository
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialOperationUnitOfWork,
)
from bot_financeiro.persistence.payables import SqlAlchemyPayableUnitOfWork
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


def metadata(record_id: str, *, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def operations_with(
    account: ResourceAccount,
    catalog: CategoryCatalog,
) -> tuple[PayableOperations, FinancialOperations, Engine]:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        accounts = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        accounts.add(account)
        for category in catalog.categories:
            categories.add(category)
        unit_of_work.commit()
    return (
        PayableOperations(lambda: SqlAlchemyPayableUnitOfWork(sessions)),
        FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions)),
        engine,
    )


def test_creating_payable_persists_expense_and_debt_without_moving_account_balance() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("d9b6a37c-9cdf-41cc-bca4-788f405bb2dc", day=1),
    )
    category_id = UUID("3390ca29-edcc-4f9a-8a4a-5add50db8783")
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
        metadata=metadata("20540d8f-8362-423a-b46f-b3ad87635772", day=2),
    )
    payables, finances, _ = operations_with(account, catalog)

    payable = payables.create_payable(
        allocation=allocation,
        due_date=date(2026, 9, 10),
    )

    assert payables.get_payable(payable.payable_id) == payable
    assert payable.amount_due == Money("300.00", "BRL")
    assert payable.history[0].is_expense is True
    assert finances.account_statement(account.account_id).balance == Money("1000.00", "BRL")
    assert finances.list_movements() == ()


def test_partial_payment_atomically_reduces_payable_and_resource_account() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("21e5ea13-8e20-490b-8b36-6fc1d32be67b", day=1),
    )
    category_id = UUID("ea969cd1-9a7a-49c9-9f5d-758df87ed9b2")
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
        metadata=metadata("8b3aa351-7f08-46a4-b437-d7a70b2359cc", day=2),
    )
    payables, finances, _ = operations_with(account, catalog)
    payable = payables.create_payable(
        allocation=allocation,
        due_date=date(2026, 9, 10),
    )
    payment_metadata = metadata("9d57c2cd-c7be-42a1-bd18-bc7444cadfc0", day=5)

    payment = payables.pay_payable(
        payable_id=payable.payable_id,
        account_id=account.account_id,
        amount=Money("100.00", "BRL"),
        metadata=payment_metadata,
    )

    restored = payables.get_payable(payable.payable_id)
    assert restored is not None
    assert restored.amount_due == Money("200.00", "BRL")
    assert restored.status_on(date(2026, 9, 5)).value == "partially_paid"
    assert restored.status_on(date(2026, 9, 11)).value == "overdue"
    assert finances.account_statement(account.account_id).balance == Money("900.00", "BRL")
    assert finances.get_movement(payment.movement_id) == payment
    assert payment.is_expense is False


def test_financial_charge_is_persisted_as_separate_expense_without_account_movement() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("b25aaf26-0c56-463e-89f6-3235f33324b3", day=1),
    )
    principal_category_id = UUID("a2037c14-f703-49d8-a39b-dd22d55dfbb5")
    charge_category_id = UUID("a6c6009a-6ad0-49f3-8106-36461b91251b")
    catalog = CategoryCatalog.empty().add_category(
        category_id=principal_category_id,
        name="Moradia",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=charge_category_id,
        name="Encargos financeiros",
        nature=FinancialNature.EXPENSE,
    )
    principal = Allocation.create(
        total=Money("300.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(principal_category_id, Money("300.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("20990ca5-ac45-4561-9747-bc2cb5a734a0", day=2),
    )
    charge_allocation = Allocation.create(
        total=Money("20.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(charge_category_id, Money("20.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("6c17c9dd-9e25-4a90-9243-2785704173e8", day=11),
    )
    payables, finances, _ = operations_with(account, catalog)
    payable = payables.create_payable(
        allocation=principal,
        due_date=date(2026, 9, 10),
    )

    charge = payables.add_financial_charge(
        payable_id=payable.payable_id,
        allocation=charge_allocation,
    )

    restored = payables.get_payable(payable.payable_id)
    assert restored is not None
    assert charge.payable_id == payable.payable_id
    assert charge.is_expense is True
    assert charge.event_id != payable.history[0].event_id
    assert restored.amount_due == Money("320.00", "BRL")
    assert restored.history == (payable.history[0], charge)
    assert finances.account_statement(account.account_id).balance == Money("1000.00", "BRL")
    assert finances.list_movements() == ()


def test_payment_rolls_back_movement_and_debt_when_account_update_fails() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("d8e30a95-c429-4665-8189-f29684524fc9", day=1),
    )
    category_id = UUID("4f701b41-b18a-4fb7-ab9e-c8861a53276d")
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
        metadata=metadata("0a85bf40-445d-4448-b070-8c15342235f7", day=2),
    )
    payables, finances, engine = operations_with(account, catalog)
    payable = payables.create_payable(
        allocation=allocation,
        due_date=date(2026, 9, 10),
    )
    payment_metadata = metadata("c602c339-99b2-4d8d-83b1-f951496d21ae", day=5)
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_resource_account_update
            BEFORE UPDATE ON resource_accounts
            BEGIN
                SELECT RAISE(ABORT, 'simulated account persistence failure');
            END
            """
        )

    with pytest.raises(IntegrityError, match="simulated account persistence failure"):
        payables.pay_payable(
            payable_id=payable.payable_id,
            account_id=account.account_id,
            amount=Money("100.00", "BRL"),
            metadata=payment_metadata,
        )

    restored = payables.get_payable(payable.payable_id)
    assert restored is not None
    assert restored.amount_due == Money("300.00", "BRL")
    assert len(restored.history) == 1
    assert finances.get_movement(payment_metadata.record_id) is None
    assert finances.account_statement(account.account_id).balance == Money("1000.00", "BRL")


def test_payment_history_must_reconcile_with_its_financial_movement() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("943ba661-176c-414b-894d-6cbb91dd47c7", day=1),
    )
    category_id = UUID("22e83595-28a4-43c1-bae6-1ae68131eda2")
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
        metadata=metadata("99e56675-6059-4f5a-bcce-4b56ee2fa84b", day=2),
    )
    payables, _, engine = operations_with(account, catalog)
    payable = payables.create_payable(
        allocation=allocation,
        due_date=date(2026, 9, 10),
    )
    payment = payables.pay_payable(
        payable_id=payable.payable_id,
        account_id=account.account_id,
        amount=Money("100.00", "BRL"),
        metadata=metadata("02e787ec-30c5-49d2-84a0-aecfd6f5b12a", day=5),
    )
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER payable_history_entries_no_update")
        connection.exec_driver_sql(
            "UPDATE payable_history_entries SET amount_minor_units = 1 WHERE id = ?",
            (str(payment.movement_id),),
        )

    with pytest.raises(ValueError, match="diverges from its financial movement"):
        payables.get_payable(payable.payable_id)
