from datetime import UTC, date, datetime
from decimal import Decimal
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.financial_operations import FinancialOperations
from bot_financeiro.application.planning import PlanningOperations
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.planning import (
    ForecastDirection,
    ForecastStatus,
    RecurrenceFrequency,
    SafetyMargin,
)
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount
from bot_financeiro.persistence.categories import SqlAlchemyFinancialCategoryRepository
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialOperationUnitOfWork,
)
from bot_financeiro.persistence.planning import SqlAlchemyPlanningUnitOfWork
from bot_financeiro.persistence.resource_accounts import SqlAlchemyResourceAccountRepository
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork

ACCOUNT_ID = UUID("312bf83f-6d71-4926-83d3-4f28a17f35a8")
INCOME_CATEGORY_ID = UUID("598fddf4-5232-4cc4-8082-4ce6923e737b")
EXPENSE_CATEGORY_ID = UUID("a42172a8-d6fd-4139-80d9-c13594918c37")


def record_metadata(record_id: str, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def test_forecast_and_margin_change_only_projected_balances() -> None:
    planning, finances, account, _ = configured_scenario()

    occurrence = planning.create_forecast(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        planned_date=date(2026, 9, 20),
        margin=SafetyMargin.percentage(Decimal("10")),
        metadata=record_metadata("7e46b67e-83d7-4e4e-bd55-6dd99855b4f9", 2),
    )
    projection = planning.project_account_balance(
        account_id=account.account_id,
        through_date=date(2026, 9, 30),
        as_of=date(2026, 9, 10),
    )

    assert finances.account_statement(account.account_id).balance == Money("1000.00", "BRL")
    assert finances.list_movements() == ()
    assert projection.current_balance == Money("1000.00", "BRL")
    assert projection.projected_balance == Money("900.00", "BRL")
    assert projection.conservative_projected_balance == Money("890.00", "BRL")
    assert len(projection.lines) == 1
    line = projection.lines[0]
    assert line.occurrence_id == occurrence.occurrence_id
    assert line.expected_amount == Money("100.00", "BRL")
    assert line.margin_amount == Money("10.00", "BRL")
    assert line.normal_impact == Money("-100.00", "BRL")
    assert line.conservative_impact == Money("-110.00", "BRL")


def test_recurrence_generates_forecasts_and_date_only_changes_computed_status() -> None:
    planning, finances, account, _ = configured_scenario()
    recurrence = planning.create_recurrence(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.INFLOW,
        expected_amount=Money("200.00", "BRL"),
        starts_on=date(2026, 9, 10),
        ends_on=date(2026, 11, 10),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=SafetyMargin.fixed(Money("250.00", "BRL")),
        metadata=record_metadata("a494308a-a1d4-4e1d-b6ac-37012527ab93", 1),
    )

    occurrences = planning.list_occurrences(
        account_id=account.account_id,
        through_date=date(2026, 11, 30),
    )

    assert recurrence.recurrence_id == UUID("a494308a-a1d4-4e1d-b6ac-37012527ab93")
    assert tuple(item.planned_date for item in occurrences) == (
        date(2026, 9, 10),
        date(2026, 10, 10),
        date(2026, 11, 10),
    )
    assert occurrences[0].status_on(date(2026, 9, 10)) is ForecastStatus.PENDING
    assert occurrences[0].status_on(date(2026, 9, 11)) is ForecastStatus.OVERDUE
    projection = planning.project_account_balance(
        account_id=account.account_id,
        through_date=date(2026, 9, 30),
        as_of=date(2026, 9, 11),
    )
    assert projection.projected_balance == Money("1200.00", "BRL")
    assert projection.conservative_projected_balance == Money("1000.00", "BRL")
    assert finances.list_movements() == ()


def test_confirmation_links_existing_movement_without_counting_it_twice() -> None:
    planning, finances, account, catalog = configured_scenario()
    occurrence = planning.create_forecast(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.INFLOW,
        expected_amount=Money("100.00", "BRL"),
        planned_date=date(2026, 9, 10),
        margin=None,
        metadata=record_metadata("7e46b67e-83d7-4e4e-bd55-6dd99855b4f9", 2),
    )
    actual = Money("120.00", "BRL")
    movement = finances.receive_income(
        account.account_id,
        allocation(
            amount=actual,
            nature=FinancialNature.INCOME,
            category_id=INCOME_CATEGORY_ID,
            catalog=catalog,
            metadata=record_metadata("4e77ff95-8646-48cc-b916-21eb5e5ee268", 10),
        ),
        description="Registro de teste",
    )

    confirmed = planning.confirm_occurrence(
        occurrence=occurrence,
        movement_id=movement.movement_id,
        metadata=record_metadata("a23ff265-b9f5-4197-b24c-69fc3aa4f577", 10),
    )
    projection = planning.project_account_balance(
        account_id=account.account_id,
        through_date=date(2026, 9, 30),
        as_of=date(2026, 9, 10),
    )

    assert confirmed.confirmed_movement_id == movement.movement_id
    assert confirmed.expected_amount == Money("100.00", "BRL")
    assert confirmed.actual_amount == actual
    assert confirmed.divergence == Money("20.00", "BRL")
    assert projection.lines == ()
    assert projection.current_balance == Money("1120.00", "BRL")
    assert projection.projected_balance == Money("1120.00", "BRL")
    assert (
        planning.list_occurrences(account_id=account.account_id, through_date=date(2026, 9, 30))[0]
        == confirmed
    )

    second = planning.create_forecast(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.INFLOW,
        expected_amount=Money("120.00", "BRL"),
        planned_date=date(2026, 9, 25),
        margin=None,
        metadata=record_metadata("951c7313-a415-4cf8-9b46-eb7369800eb2", 3),
    )
    with pytest.raises(ValueError, match="already confirms another occurrence"):
        planning.confirm_occurrence(
            occurrence=second,
            movement_id=movement.movement_id,
            metadata=record_metadata("45220a0a-ad75-460f-a185-486c1466d53d", 10),
        )


def test_confirmation_validates_account_direction_and_currency() -> None:
    planning, finances, account, catalog = configured_scenario()
    outflow = planning.create_forecast(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("50.00", "BRL"),
        planned_date=date(2026, 9, 10),
        margin=None,
        metadata=record_metadata("7e46b67e-83d7-4e4e-bd55-6dd99855b4f9", 2),
    )
    income = finances.receive_income(
        account.account_id,
        allocation(
            amount=Money("50.00", "BRL"),
            nature=FinancialNature.INCOME,
            category_id=INCOME_CATEGORY_ID,
            catalog=catalog,
            metadata=record_metadata("4e77ff95-8646-48cc-b916-21eb5e5ee268", 10),
        ),
        description="Registro de teste",
    )

    with pytest.raises(ValueError, match="direction"):
        planning.confirm_occurrence(
            occurrence=outflow,
            movement_id=income.movement_id,
            metadata=record_metadata("a23ff265-b9f5-4197-b24c-69fc3aa4f577", 10),
        )


def test_confirmed_divergence_updates_only_later_pending_occurrences_explicitly() -> None:
    planning, finances, account, catalog = configured_scenario()
    planning.create_recurrence(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 9, 15),
        ends_on=date(2026, 12, 15),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=record_metadata("a494308a-a1d4-4e1d-b6ac-37012527ab93", 1),
    )
    occurrences = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )
    planning.revise_occurrence(
        occurrence=occurrences[2],
        expected_amount=Money("90.00", "BRL"),
        planned_date=occurrences[2].planned_date,
        margin=None,
        reason="Excecao anterior a confirmacao",
        metadata=record_metadata("b05724a9-f9bd-48bb-af16-e85f5518796d", 10),
    )
    movement = finances.pay_cash_expense(
        account.account_id,
        allocation(
            amount=Money("125.00", "BRL"),
            nature=FinancialNature.EXPENSE,
            category_id=EXPENSE_CATEGORY_ID,
            catalog=catalog,
            metadata=record_metadata("4e77ff95-8646-48cc-b916-21eb5e5ee268", 15),
        ),
        description="Registro de teste",
    )
    confirmed = planning.confirm_occurrence(
        occurrence=occurrences[0],
        movement_id=movement.movement_id,
        metadata=record_metadata("a23ff265-b9f5-4197-b24c-69fc3aa4f577", 15),
    )

    unchanged = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )
    assert tuple(item.expected_amount for item in unchanged) == (
        Money("100.00", "BRL"),
        Money("100.00", "BRL"),
        Money("90.00", "BRL"),
        Money("100.00", "BRL"),
    )

    planning.confirm_future_amount_update(
        confirmed_occurrence=confirmed,
        reason="Usar valor realizado nas previsoes futuras",
        metadata=record_metadata("45220a0a-ad75-460f-a185-486c1466d53d", 16),
    )
    updated = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )

    assert updated[0] == confirmed
    assert tuple(item.expected_amount for item in updated[1:]) == (
        Money("125.00", "BRL"),
        Money("125.00", "BRL"),
        Money("125.00", "BRL"),
    )
    assert len(updated[2].history) == 2
    assert len(planning.get_recurrence(confirmed.recurrence_id).history) == 2


def test_single_and_following_edits_remain_separate_after_persistence() -> None:
    planning, _, account, _ = configured_scenario()
    recurrence = planning.create_recurrence(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 10, 10),
        ends_on=date(2026, 12, 10),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=record_metadata("a494308a-a1d4-4e1d-b6ac-37012527ab93", 1),
    )
    occurrences = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )

    planning.revise_occurrence(
        occurrence=occurrences[1],
        expected_amount=Money("110.00", "BRL"),
        planned_date=date(2026, 11, 10),
        margin=None,
        reason="Ajuste somente de novembro",
        metadata=record_metadata("a23ff265-b9f5-4197-b24c-69fc3aa4f577", 5),
    )
    planning.revise_recurrence_from(
        recurrence_id=recurrence.recurrence_id,
        from_date=date(2026, 12, 10),
        expected_amount=Money("120.00", "BRL"),
        margin=SafetyMargin.fixed(Money("5.00", "BRL")),
        reason="Ajuste de dezembro em diante",
        metadata=record_metadata("45220a0a-ad75-460f-a185-486c1466d53d", 6),
    )

    restored = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )
    assert tuple(item.expected_amount for item in restored) == (
        Money("100.00", "BRL"),
        Money("110.00", "BRL"),
        Money("120.00", "BRL"),
    )
    assert tuple(len(item.history) for item in restored) == (1, 2, 1)


def test_cadence_edit_cannot_erase_occurrence_with_preserved_history() -> None:
    planning, _, account, _ = configured_scenario()
    recurrence = planning.create_recurrence(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 10, 10),
        ends_on=date(2027, 1, 10),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=record_metadata("a494308a-a1d4-4e1d-b6ac-37012527ab93", 1),
    )
    occurrences = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2027, 1, 31)
    )
    planning.revise_occurrence(
        occurrence=occurrences[2],
        expected_amount=Money("105.00", "BRL"),
        planned_date=date(2026, 12, 10),
        margin=None,
        reason="Excecao preservada",
        metadata=record_metadata("a23ff265-b9f5-4197-b24c-69fc3aa4f577", 5),
    )

    with pytest.raises(ValueError, match="preserved history"):
        planning.revise_recurrence_from(
            recurrence_id=recurrence.recurrence_id,
            from_date=date(2026, 11, 10),
            expected_amount=Money("120.00", "BRL"),
            margin=None,
            frequency=RecurrenceFrequency.WEEKLY,
            interval=1,
            reason="Nao pode apagar dezembro",
            metadata=record_metadata("45220a0a-ad75-460f-a185-486c1466d53d", 6),
        )


def test_confirmed_future_occurrence_keeps_original_forecast_after_series_edit() -> None:
    planning, finances, account, catalog = configured_scenario()
    recurrence = planning.create_recurrence(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 10, 10),
        ends_on=date(2026, 12, 10),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=SafetyMargin.fixed(Money("10.00", "BRL")),
        metadata=record_metadata("a494308a-a1d4-4e1d-b6ac-37012527ab93", 1),
    )
    occurrences = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )
    movement = finances.pay_cash_expense(
        account.account_id,
        allocation(
            amount=Money("105.00", "BRL"),
            nature=FinancialNature.EXPENSE,
            category_id=EXPENSE_CATEGORY_ID,
            catalog=catalog,
            metadata=record_metadata("4e77ff95-8646-48cc-b916-21eb5e5ee268", 5),
        ),
        description="Registro de teste",
    )
    confirmed = planning.confirm_occurrence(
        occurrence=occurrences[1],
        movement_id=movement.movement_id,
        metadata=record_metadata("a23ff265-b9f5-4197-b24c-69fc3aa4f577", 5),
    )

    planning.revise_recurrence_from(
        recurrence_id=recurrence.recurrence_id,
        from_date=date(2026, 10, 10),
        expected_amount=Money("130.00", "BRL"),
        margin=SafetyMargin.percentage("20"),
        reason="Nova previsao da serie",
        metadata=record_metadata("45220a0a-ad75-460f-a185-486c1466d53d", 6),
    )
    restored = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )

    assert restored[1] == confirmed
    assert restored[1].expected_amount == Money("100.00", "BRL")
    assert restored[1].margin == SafetyMargin.fixed(Money("10.00", "BRL"))
    assert restored[1].history[0] == confirmed.history[0]
    assert restored[0].expected_amount == Money("130.00", "BRL")
    assert restored[2].expected_amount == Money("130.00", "BRL")


def test_occurrence_date_revision_respects_query_horizon_and_rejects_collision() -> None:
    planning, _, account, _ = configured_scenario()
    planning.create_recurrence(
        description="Registro de teste",
        account_id=account.account_id,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money("100.00", "BRL"),
        starts_on=date(2026, 10, 10),
        ends_on=date(2026, 12, 10),
        frequency=RecurrenceFrequency.MONTHLY,
        interval=1,
        margin=None,
        metadata=record_metadata("a494308a-a1d4-4e1d-b6ac-37012527ab93", 1),
    )
    occurrences = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 12, 31)
    )

    moved_earlier = planning.revise_occurrence(
        occurrence=occurrences[1],
        expected_amount=occurrences[1].expected_amount,
        planned_date=date(2026, 10, 15),
        margin=None,
        reason="Antecipada",
        metadata=record_metadata("a23ff265-b9f5-4197-b24c-69fc3aa4f577", 5),
    )
    through_october = planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 10, 20)
    )
    assert tuple(item.planned_date for item in through_october) == (
        date(2026, 10, 10),
        date(2026, 10, 15),
    )

    moved_later = planning.revise_occurrence(
        occurrence=occurrences[0],
        expected_amount=occurrences[0].expected_amount,
        planned_date=date(2026, 10, 25),
        margin=None,
        reason="Adiada",
        metadata=record_metadata("45220a0a-ad75-460f-a185-486c1466d53d", 6),
    )
    assert planning.list_occurrences(
        account_id=account.account_id, through_date=date(2026, 10, 20)
    ) == (moved_earlier,)
    with pytest.raises(ValueError, match="conflicts"):
        planning.revise_occurrence(
            occurrence=moved_later,
            expected_amount=moved_later.expected_amount,
            planned_date=moved_earlier.planned_date,
            margin=None,
            reason="Data duplicada",
            metadata=record_metadata("902d0f5a-71ca-46f7-b4f9-e110989fc44b", 7),
        )


def allocation(
    *,
    amount: Money,
    nature: FinancialNature,
    category_id: UUID,
    catalog: CategoryCatalog,
    metadata: RecordMetadata,
) -> Allocation:
    return Allocation.create(
        total=amount,
        nature=nature,
        parts=(AllocationPart(category_id=category_id, amount=amount),),
        catalog=catalog,
        metadata=metadata,
    )


def configured_scenario() -> tuple[
    PlanningOperations,
    FinancialOperations,
    ResourceAccount,
    CategoryCatalog,
]:
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
        metadata=RecordMetadata(
            effective_date=date(2026, 9, 1),
            recorded_at=datetime(2026, 9, 1, 8, tzinfo=UTC),
            origin=RecordOrigin.MANUAL,
            record_id=ACCOUNT_ID,
        ),
    )
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=INCOME_CATEGORY_ID,
            name="Salario",
            nature=FinancialNature.INCOME,
        )
        .add_category(
            category_id=EXPENSE_CATEGORY_ID,
            name="Moradia",
            nature=FinancialNature.EXPENSE,
        )
    )
    with SqlAlchemyUnitOfWork(sessions) as unit_of_work:
        SqlAlchemyResourceAccountRepository(unit_of_work.session).add(account)
        category_repository = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        category_repository.add(catalog.require(INCOME_CATEGORY_ID))
        category_repository.add(catalog.require(EXPENSE_CATEGORY_ID))
        unit_of_work.commit()
    return (
        PlanningOperations(lambda: SqlAlchemyPlanningUnitOfWork(sessions)),
        FinancialOperations(lambda: SqlAlchemyFinancialOperationUnitOfWork(sessions)),
        account,
        catalog,
    )
