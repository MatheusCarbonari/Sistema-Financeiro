from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.financial_operations import FinancialOperations
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import CashExpense, Income, Transfer
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount
from bot_financeiro.persistence.categories import SqlAlchemyFinancialCategoryRepository
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialOperationUnitOfWork,
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


def operations_with(
    account: ResourceAccount,
    catalog: CategoryCatalog,
    *additional_accounts: ResourceAccount,
) -> FinancialOperations:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        accounts = SqlAlchemyResourceAccountRepository(unit_of_work.session)
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        for resource_account in (account, *additional_accounts):
            accounts.add(resource_account)
        for category in catalog.categories:
            categories.add(category)
        unit_of_work.commit()
    return FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions))


def test_receiving_income_increases_one_account_and_preserves_its_allocation() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100.00", "BRL"),
        metadata=metadata("a1571f56-d6e5-4ce9-8815-3fce0c21ce99", day=1),
    )
    category_id = UUID("0bf96ac5-e37a-4d51-9efd-86f1f1a041d3")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("2500.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("2500.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("5d22c67b-46d6-4eef-92b8-960af5b53db0", day=3),
    )
    operations = operations_with(account, catalog)

    income = operations.receive_income(
        account.account_id, allocation, description="Registro de teste"
    )

    assert isinstance(income, Income)
    assert income.account_id == account.account_id
    assert income.amount == Money("2500.00", "BRL")
    assert income.metadata.effective_date == date(2026, 9, 3)
    assert operations.account_statement(account.account_id).balance == Money("2600.00", "BRL")
    assert operations.get_movement(income.movement_id) == income


def test_paying_cash_expense_uses_effective_date_and_reduces_one_account() -> None:
    account = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("80.00", "BRL"),
        metadata=metadata("cb98c366-314a-44a9-ad87-75917f7b5126", day=1),
    )
    category_id = UUID("1cb5bc94-5564-4558-b62a-249f2ba3db3c")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Alimentação",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("25.90", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("25.90", "BRL")),),
        catalog=catalog,
        metadata=metadata("72dc68b3-fcb1-4b44-b8d1-e03579131204", day=5),
    )
    operations = operations_with(account, catalog)

    expense = operations.pay_cash_expense(
        account.account_id, allocation, description="Registro de teste"
    )

    assert isinstance(expense, CashExpense)
    assert expense.metadata.effective_date == date(2026, 9, 5)
    assert operations.account_statement(account.account_id).balance == Money("54.10", "BRL")
    assert operations.get_movement(expense.movement_id) == expense


def test_transfer_moves_the_same_value_without_income_expense_or_net_worth_effect() -> None:
    source = ResourceAccount.open(
        name="Conta corrente",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("6de9d68c-e9fc-4a19-a2bd-ab036160693b", day=1),
    )
    destination = ResourceAccount.open(
        name="Poupança",
        account_type=AccountType.SAVINGS,
        currency="BRL",
        initial_balance=Money("400.00", "BRL"),
        metadata=metadata("d8d201f1-9ed9-42ad-a559-0aa1a983d007", day=1),
    )
    operations = operations_with(source, CategoryCatalog.empty(), destination)

    result = operations.transfer(
        description="Registro de teste",
        source_account_id=source.account_id,
        destination_account_id=destination.account_id,
        amount=Money("275.35", "BRL"),
        metadata=metadata("23ba5b66-da8f-4bd4-8974-6109477a527e", day=8),
    )

    assert operations.account_statement(source.account_id).balance == Money("724.65", "BRL")
    assert operations.account_statement(destination.account_id).balance == Money("675.35", "BRL")
    assert result.transfer.is_income is False
    assert result.transfer.is_expense is False
    assert result.fee_expense is None
    assert operations.get_movement(result.transfer.movement_id) == result.transfer
    assert operations.account_statement(source.account_id).balance + operations.account_statement(
        destination.account_id
    ).balance == Money("1400.00", "BRL")


def test_transfer_rejects_accounts_in_different_currencies_without_changing_them() -> None:
    source = ResourceAccount.open(
        name="Conta Brasil",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100.00", "BRL"),
        metadata=metadata("68ea6d09-5c48-48f4-b70b-4dad84f652d7", day=1),
    )
    destination = ResourceAccount.open(
        name="Conta EUA",
        account_type=AccountType.CHECKING,
        currency="USD",
        initial_balance=Money("50", "USD"),
        metadata=metadata("76cb736b-f331-4c71-b624-33bffb7e0d60", day=1),
    )
    with pytest.raises(ValueError, match="same currency"):
        Transfer.execute(
            description="Registro de teste",
            source=source,
            destination=destination,
            amount=Money("10.00", "BRL"),
            metadata=metadata("9b5552d6-a2af-4f70-9964-4682898ea5e2", day=2),
        )

    assert source.balance == Money("100.00", "BRL")
    assert destination.balance == Money("50", "USD")


def test_transfer_fee_is_a_separate_categorized_cash_expense() -> None:
    source = ResourceAccount.open(
        name="Conta corrente",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100.00", "BRL"),
        metadata=metadata("fbaf875b-21f7-4227-bec0-580dcecbf9db", day=1),
    )
    destination = ResourceAccount.open(
        name="Conta digital",
        account_type=AccountType.DIGITAL,
        currency="BRL",
        initial_balance=Money("10.00", "BRL"),
        metadata=metadata("e525df4c-476c-46d8-bdf6-e84ae91f5e27", day=1),
    )
    category_id = UUID("c238c275-b76f-46f5-b5b2-5943e50adcea")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Tarifas bancárias",
        nature=FinancialNature.EXPENSE,
    )
    fee_allocation = Allocation.create(
        total=Money("2.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("2.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("dc86b13c-f121-46a1-967d-449778820c71", day=4),
    )
    operations = operations_with(source, catalog, destination)

    result = operations.transfer(
        description="Registro de teste",
        source_account_id=source.account_id,
        destination_account_id=destination.account_id,
        amount=Money("20.00", "BRL"),
        metadata=metadata("780424e3-1914-4bbf-b8e9-949fd3431608", day=4),
        fee_allocation=fee_allocation,
        fee_description="Tarifa de transferência",
    )

    assert result.fee_expense is not None
    assert result.fee_expense.is_expense is True
    assert result.fee_expense.movement_id != result.transfer.movement_id
    assert result.fee_expense.related_transfer_id == result.transfer.movement_id
    assert result.fee_expense.allocation == fee_allocation
    assert operations.account_statement(source.account_id).balance == Money("78.00", "BRL")
    assert operations.account_statement(destination.account_id).balance == Money("30.00", "BRL")
    movements = operations.list_movements()
    assert len(movements) == 2
    assert result.transfer in movements
    assert result.fee_expense in movements


def test_income_rejects_an_expense_allocation_without_changing_the_account() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("40.00", "BRL"),
        metadata=metadata("783edc9a-5c4b-4ae4-baf7-7c2fa232119b", day=1),
    )
    category_id = UUID("4f76ceec-a2bb-420c-a8d2-30ba0a62c608")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("15.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("15.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("96bb81f7-b56e-4240-be0b-6803fd0ee3bb", day=2),
    )
    operations = operations_with(account, catalog)

    with pytest.raises(ValueError, match="income allocation"):
        operations.receive_income(account.account_id, allocation, description="Registro de teste")

    assert operations.account_statement(account.account_id).balance == Money("40.00", "BRL")
    assert operations.list_movements() == ()


def test_cash_expense_rejects_an_income_allocation_without_changing_the_account() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("40.00", "BRL"),
        metadata=metadata("6e1a0025-5ef7-45cc-ae1e-f46820c67d50", day=1),
    )
    category_id = UUID("c5f5ee74-c861-442f-892f-c86047b28e1c")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("15.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("15.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("dd54fb35-b38b-4e5e-879d-a3b5c05f20ed", day=2),
    )
    operations = operations_with(account, catalog)

    with pytest.raises(ValueError, match="expense allocation"):
        operations.pay_cash_expense(account.account_id, allocation, description="Registro de teste")

    assert operations.account_statement(account.account_id).balance == Money("40.00", "BRL")
    assert operations.list_movements() == ()


def test_transfer_and_fee_are_rolled_back_together_when_persistence_fails() -> None:
    source = ResourceAccount.open(
        name="Conta corrente",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100.00", "BRL"),
        metadata=metadata("8b9708d4-ce45-47a7-a9eb-a94591c4dd41", day=1),
    )
    destination = ResourceAccount.open(
        name="Conta digital",
        account_type=AccountType.DIGITAL,
        currency="BRL",
        initial_balance=Money("10.00", "BRL"),
        metadata=metadata("78b5ec12-f9d4-4d72-a221-948236d561d3", day=1),
    )
    category_id = UUID("b4592070-7b56-4ddb-a15c-b1e84f4aa659")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Tarifas bancárias",
        nature=FinancialNature.EXPENSE,
    )
    duplicate_fee = Allocation.create(
        total=Money("2.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("2.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("9840cc9f-f43c-4aee-af0a-43c62f86b820", day=2),
    )
    operations = operations_with(source, catalog, destination)
    operations.pay_cash_expense(source.account_id, duplicate_fee, description="Registro de teste")
    transfer_metadata = metadata("e0242eae-de8a-479c-887b-a4f313ff0814", day=3)

    with pytest.raises(IntegrityError):
        operations.transfer(
            description="Registro de teste",
            source_account_id=source.account_id,
            destination_account_id=destination.account_id,
            amount=Money("20.00", "BRL"),
            metadata=transfer_metadata,
            fee_allocation=duplicate_fee,
            fee_description="Tarifa de transferência",
        )

    assert operations.account_statement(source.account_id).balance == Money("98.00", "BRL")
    assert operations.account_statement(destination.account_id).balance == Money("10.00", "BRL")
    assert operations.get_movement(transfer_metadata.record_id) is None


def test_account_statement_recalculates_balance_from_reconciled_history() -> None:
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100.00", "BRL"),
        metadata=metadata("381dfb55-80e9-41d7-bf1f-77a987e440b0", day=1),
    )
    income_category_id = UUID("e0bfb29d-9dd5-474c-ad68-56c341a4cfac")
    expense_category_id = UUID("54eaa367-472d-4201-998d-8bb18467395f")
    catalog = CategoryCatalog.empty().add_category(
        category_id=income_category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    catalog = catalog.add_category(
        category_id=expense_category_id,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    income_allocation = Allocation.create(
        total=Money("50.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(income_category_id, Money("50.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("270f7fc0-0390-42e2-94cb-51a1200fdb80", day=2),
    )
    expense_allocation = Allocation.create(
        total=Money("20.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(expense_category_id, Money("20.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("29db4577-7da3-4310-a1a8-6e1fe1662bc7", day=3),
    )
    operations = operations_with(account, catalog)
    income = operations.receive_income(
        account.account_id, income_allocation, description="Registro de teste"
    )
    expense = operations.pay_cash_expense(
        account.account_id, expense_allocation, description="Registro de teste"
    )

    statement = operations.account_statement(account.account_id)

    assert tuple(entry.difference for entry in statement.entries) == (
        Money("100.00", "BRL"),
        Money("50.00", "BRL"),
        Money("-20.00", "BRL"),
    )
    assert statement.entries[1].movement_id == income.movement_id
    assert statement.entries[2].movement_id == expense.movement_id
    assert statement.balance == Money("130.00", "BRL")


def test_archived_account_rejects_realized_movements() -> None:
    account = ResourceAccount.open(
        name="Conta antiga",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata("161ac123-9375-4919-b7ce-cfc2750ea886", day=1),
    ).archive()
    category_id = UUID("89829f34-2940-4a17-9069-834031f4fe66")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Rendimentos",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("1.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("1.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("774d639c-5c87-4da1-a33d-2f2d44691143", day=2),
    )
    operations = operations_with(account, catalog)

    with pytest.raises(ValueError, match="archived"):
        operations.receive_income(account.account_id, allocation, description="Registro de teste")

    assert operations.list_movements() == ()


def test_cash_expense_respects_the_account_negative_balance_policy() -> None:
    account = ResourceAccount.open(
        name="Carteira",
        account_type=AccountType.WALLET,
        currency="BRL",
        initial_balance=Money("1.00", "BRL"),
        metadata=metadata("dd4e6135-6ff8-42a2-872c-c1bc52be6dc8", day=1),
    )
    category_id = UUID("df262804-c993-4d15-a58b-9888080313bf")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Alimentação",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("1.01", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("1.01", "BRL")),),
        catalog=catalog,
        metadata=metadata("73aacd1f-c3d5-4266-863a-e6cad8178bf5", day=2),
    )
    operations = operations_with(account, catalog)

    with pytest.raises(ValueError, match="negative balance"):
        operations.pay_cash_expense(account.account_id, allocation, description="Registro de teste")

    assert operations.account_statement(account.account_id).balance == Money("1.00", "BRL")
    assert operations.list_movements() == ()
