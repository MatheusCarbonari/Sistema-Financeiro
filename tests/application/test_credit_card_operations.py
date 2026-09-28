from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.credit_cards import CreditCardOperations
from bot_financeiro.application.financial_changes import FinancialChangeOperations
from bot_financeiro.application.financial_operations import FinancialOperations
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
from bot_financeiro.persistence.credit_cards import SqlAlchemyCreditCardUnitOfWork
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.financial_change_unit_of_work import (
    SqlAlchemyFinancialChangeUnitOfWork,
)
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


def test_credit_card_purchase_is_persisted_as_expense_and_debt_without_account_exit() -> None:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    category_id = UUID("76217778-e7e0-4579-a504-a9d57a411ea2")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Alimentação",
        nature=FinancialNature.EXPENSE,
    )
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("389b5821-4a5e-47a3-b7c9-64e713935c6e", day=1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        SqlAlchemyFinancialCategoryRepository(unit_of_work.session).add(
            catalog.require(category_id)
        )
        SqlAlchemyResourceAccountRepository(unit_of_work.session).add(account)
        unit_of_work.commit()
    operations = CreditCardOperations(lambda: SqlAlchemyCreditCardUnitOfWork(sessions))
    card = operations.create_card(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("805b4598-3295-49e1-9e65-b64745eb8b61", day=1),
    )
    purchase_amount = Money("200.00", "BRL")
    allocation = Allocation.create(
        total=purchase_amount,
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, purchase_amount),),
        catalog=catalog,
        metadata=metadata("04db39d5-8b13-4591-b139-ad8030e2b2d6", day=9),
    )

    purchase = operations.record_purchase(card_id=card.card_id, allocation=allocation)

    restored = operations.get_card(card.card_id)
    assert restored is not None
    assert restored.invoice_for_purchase(purchase.purchase_id).amount_due == purchase_amount
    assert restored.available_limit == Money("800.00", "BRL")
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        persisted_account = SqlAlchemyResourceAccountRepository(unit_of_work.session).get(
            account.account_id
        )
    assert persisted_account == account


def test_installment_purchase_and_correction_round_trip_without_moving_account() -> None:
    operations, finances, account, catalog, category_id, engine = configured_scenario()
    changes = FinancialChangeOperations(
        lambda: SqlAlchemyFinancialChangeUnitOfWork(sessionmaker(bind=engine))
    )
    card = operations.create_card(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("e5ae4d3e-3a25-4035-a384-d43b4b28bac2", day=1),
    )
    original_amount = Money("120.00", "BRL")
    purchase = operations.record_installment_purchase(
        card_id=card.card_id,
        allocation=Allocation.create(
            total=original_amount,
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(category_id, original_amount),),
            catalog=catalog,
            metadata=metadata("f49152dd-96c0-430e-83d9-da870df5d85e", day=9),
        ),
        installment_count=3,
    )
    corrected_amount = Money("123.00", "BRL")

    proposal = changes.preview_installment_purchase_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        corrected_allocation=Allocation.create(
            total=corrected_amount,
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(category_id, corrected_amount),),
            catalog=catalog,
            metadata=metadata("636bc42a-48e9-436b-b9cf-b55a5c238091", day=9),
        ),
        remaining_installment_count=2,
        reason="Valor final da compra",
        effective_date=date(2026, 9, 11),
    )
    changes.confirm_installment_purchase_correction(
        proposal,
        metadata("408d66ec-c819-4870-ab5a-88cbce81fcdd", day=11),
    )

    restored = operations.get_card(card.card_id)
    assert restored is not None
    assert restored.expense_for_purchase(purchase.purchase_id).total == corrected_amount
    assert tuple(
        installment.amount
        for installment in restored.installments_for_purchase(purchase.purchase_id)
    ) == (
        Money("40.00", "BRL"),
        Money("41.50", "BRL"),
        Money("41.50", "BRL"),
    )
    assert restored.available_limit == Money("877.00", "BRL")
    cash_flow = operations.get_cash_flow(
        card_id=card.card_id,
        start_date=date(2026, 9, 1),
        end_date=date(2026, 11, 30),
    )
    assert tuple(item.amount for item in cash_flow.scheduled_installments) == (
        Money("40.00", "BRL"),
        Money("41.50", "BRL"),
        Money("41.50", "BRL"),
    )
    assert cash_flow.realized_payments == ()
    assert finances.account_statement(account.account_id).balance == Money(
        "1000.00", "BRL"
    )
    with pytest.raises(IntegrityError, match="credit card installments are append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(
                "UPDATE credit_card_installments SET amount_minor_units = 1"
            )


def test_installment_purchase_rolls_back_all_records_when_schedule_write_fails() -> None:
    operations, _, _, catalog, category_id, engine = configured_scenario()
    card = operations.create_card(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata("79423a68-6fec-41ca-874b-65aa0b44b198", day=1),
    )
    amount = Money("90.00", "BRL")
    allocation = Allocation.create(
        total=amount,
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, amount),),
        catalog=catalog,
        metadata=metadata("3868b04c-ee26-4b57-9e15-3b22a5b2d949", day=9),
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_installment_schedule
            BEFORE INSERT ON credit_card_installments
            BEGIN SELECT RAISE(ABORT, 'simulated schedule failure'); END
            """
        )

    with pytest.raises(IntegrityError, match="simulated schedule failure"):
        operations.record_installment_purchase(
            card_id=card.card_id,
            allocation=allocation,
            installment_count=3,
        )

    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER reject_installment_schedule")
    purchase = operations.record_purchase(card_id=card.card_id, allocation=allocation)
    restored = operations.get_card(card.card_id)
    assert restored is not None
    assert restored.history == (card.opening, purchase)


def configured_scenario() -> tuple[
    CreditCardOperations,
    FinancialOperations,
    ResourceAccount,
    CategoryCatalog,
    UUID,
    Engine,
]:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "head")
    sessions = sessionmaker(bind=engine)
    category_id = UUID("f7d5c524-b37d-482f-8b67-09d61da49aea")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Compras",
        nature=FinancialNature.EXPENSE,
    )
    account = ResourceAccount.open(
        name="Conta principal",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata("6375f35b-65fc-455b-a460-aa84789089d7", day=1),
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        SqlAlchemyFinancialCategoryRepository(unit_of_work.session).add(
            catalog.require(category_id)
        )
        SqlAlchemyResourceAccountRepository(unit_of_work.session).add(account)
        unit_of_work.commit()
    return (
        CreditCardOperations(lambda: SqlAlchemyCreditCardUnitOfWork(sessions)),
        FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions)),
        account,
        catalog,
        category_id,
        engine,
    )


def test_invoice_overpayment_atomically_persists_account_debt_movement_and_credit() -> None:
    operations, finances, account, catalog, category_id, engine = configured_scenario()
    card = operations.create_card(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("dd3e9e0f-16ec-49b9-bdd9-235e689d28e5", day=1),
    )
    amount = Money("100.00", "BRL")
    purchase = operations.record_purchase(
        card_id=card.card_id,
        allocation=Allocation.create(
            total=amount,
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(category_id, amount),),
            catalog=catalog,
            metadata=metadata("1f870dcc-0b78-48bf-bc70-72fe7330e892", day=9),
        ),
    )

    payment = operations.pay_invoice(
        card_id=card.card_id,
        closing_date=purchase.invoice_closing_date,
        account_id=account.account_id,
        amount=Money("110.00", "BRL"),
        metadata=metadata("cc040a50-91f3-4853-8b30-f104fc27f6a4", day=17),
        create_credit_from_excess=True,
    )

    restored = operations.get_card(card.card_id)
    assert restored is not None
    assert restored.invoice(purchase.invoice_closing_date).amount_due == Money("0.00", "BRL")
    assert restored.card_credit_balance == Money("10.00", "BRL")
    assert finances.account_statement(account.account_id).balance == Money("890.00", "BRL")
    assert finances.get_movement(payment.movement_id) == payment
    with engine.begin() as connection:
        connection.exec_driver_sql("DROP TRIGGER credit_card_history_entries_no_update")
        connection.exec_driver_sql(
            """
            UPDATE credit_card_history_entries
            SET applied_amount_minor_units = 9000
            WHERE id = ?
            """,
            (str(payment.movement_id),),
        )
    with pytest.raises(ValueError, match="diverges from its movement"):
        operations.get_card(card.card_id)


def test_invoice_payment_rolls_back_all_effects_when_account_save_fails() -> None:
    operations, finances, account, catalog, category_id, engine = configured_scenario()
    card = operations.create_card(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata("70791803-63fc-4f16-803e-94a7dc53cf9e", day=1),
    )
    amount = Money("100.00", "BRL")
    purchase = operations.record_purchase(
        card_id=card.card_id,
        allocation=Allocation.create(
            total=amount,
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(category_id, amount),),
            catalog=catalog,
            metadata=metadata("211306e5-986f-46f7-bcd7-b1cf645087ed", day=9),
        ),
    )
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE TRIGGER reject_card_payment_account_update
            BEFORE UPDATE ON resource_accounts
            BEGIN SELECT RAISE(ABORT, 'simulated account failure'); END
            """
        )

    with pytest.raises(IntegrityError, match="simulated account failure"):
        operations.pay_invoice(
            card_id=card.card_id,
            closing_date=purchase.invoice_closing_date,
            account_id=account.account_id,
            amount=Money("50.00", "BRL"),
            metadata=metadata("941a20bb-80b4-4ef8-8f02-4e8b6cbf7c62", day=17),
        )

    restored = operations.get_card(card.card_id)
    assert restored is not None
    assert restored.invoice(purchase.invoice_closing_date).amount_due == Money("100.00", "BRL")
    assert finances.account_statement(account.account_id).balance == Money("1000.00", "BRL")
    assert finances.get_movement(UUID("941a20bb-80b4-4ef8-8f02-4e8b6cbf7c62")) is None


def test_card_history_round_trips_correction_charge_refund_and_credit_application() -> None:
    operations, _, _, catalog, category_id, engine = configured_scenario()
    changes = FinancialChangeOperations(
        lambda: SqlAlchemyFinancialChangeUnitOfWork(sessionmaker(bind=engine))
    )
    card = operations.create_card(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("1000.00", "BRL"),
        metadata=metadata("0668ae02-5809-4329-aa5b-85a6f9db35c1", day=1),
    )
    principal = Money("200.00", "BRL")
    purchase = operations.record_purchase(
        card_id=card.card_id,
        allocation=Allocation.create(
            total=principal,
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(category_id, principal),),
            catalog=catalog,
            metadata=metadata("16a69845-3d37-46f1-a0c7-330bb0b14c77", day=9),
        ),
    )
    assignment = changes.preview_invoice_assignment_correction(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        target_closing_date=date(2026, 10, 10),
        reason="Fatura real da operadora",
        effective_date=date(2026, 9, 11),
    )
    changes.confirm_invoice_assignment_correction(
        assignment,
        metadata("8b9ddcca-340c-4fb3-a2ec-b07c3d265e4d", day=11),
    )
    charge_amount = Money("20.00", "BRL")
    operations.add_financial_charge(
        card_id=card.card_id,
        closing_date=date(2026, 10, 10),
        allocation=Allocation.create(
            total=charge_amount,
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(category_id, charge_amount),),
            catalog=catalog,
            metadata=metadata("57314fcb-5de6-4462-a4d3-a1424cb0887b", day=12),
        ),
    )
    refund = changes.preview_card_refund(
        card_id=card.card_id,
        purchase_id=purchase.purchase_id,
        amount=Money("30.00", "BRL"),
    )
    changes.confirm_card_refund(
        refund,
        metadata=metadata("619502c6-536a-4432-996e-bf5ce21e753f", day=13),
    )
    operations.grant_card_credit(
        card_id=card.card_id,
        amount=Money("40.00", "BRL"),
        reason="Crédito confirmado na operadora",
        metadata=metadata("d59e97d3-432c-481b-ab36-48e90a18dc62", day=14),
    )
    operations.apply_card_credit(
        card_id=card.card_id,
        closing_date=date(2026, 10, 10),
        amount=Money("25.00", "BRL"),
        metadata=metadata("15d334ae-c17e-4f9d-9343-16c2e56e2ddb", day=15),
    )

    restored = operations.get_card(card.card_id)
    assert restored is not None
    invoice = restored.invoice(date(2026, 10, 10))
    assert invoice.principal_due == Money("145.00", "BRL")
    assert invoice.financial_charges_due == Money("20.00", "BRL")
    assert restored.card_credit_balance == Money("15.00", "BRL")
    assert restored.available_limit == Money("855.00", "BRL")
    assert len(restored.history) == 7
