from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from bot_financeiro.application.financial_queries import (
    FinancialQueries,
    FinancialRecordType,
    FinancialSearchFilters,
    QueryPeriod,
)
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.financial_changes import (
    AccountExpenseRefund,
    FinancialFactCanceled,
    FinancialFactCorrected,
    FinancialFactHistory,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import CashExpense, FinancialMovement, Income
from bot_financeiro.domain.resource_account import (
    AccountHistoryEntryKind,
    AccountType,
    ResourceAccount,
)

ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000001")
CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000101")
INCOME_ID = UUID("00000000-0000-0000-0000-000000000201")


def metadata(record_id: UUID, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=record_id,
    )


@dataclass
class QuerySourceStub:
    accounts: tuple[ResourceAccount, ...]
    histories: tuple[FinancialFactHistory[FinancialMovement], ...]
    catalog: CategoryCatalog

    def __enter__(self) -> "QuerySourceStub":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def list_accounts(self) -> tuple[ResourceAccount, ...]:
        return self.accounts

    def list_movement_histories(
        self,
    ) -> tuple[FinancialFactHistory[FinancialMovement], ...]:
        return self.histories

    def category_catalog(self) -> CategoryCatalog:
        return self.catalog

    def get_current_financial_fact(self, fact_id: UUID):
        for history in self.histories:
            if history.target_id == fact_id:
                return history.current
        return None


def test_statement_uses_current_fact_effects_once_and_reconciles_with_account() -> None:
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    account = ResourceAccount.open(
        name="Conta corrente",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100", "BRL"),
        metadata=metadata(ACCOUNT_ID, 1),
    )
    allocation = Allocation.create(
        total=Money("50", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(CATEGORY_ID, Money("50", "BRL")),),
        catalog=catalog,
        metadata=metadata(INCOME_ID, 2),
    )
    income, account = Income.receive(
        account=account,
        allocation=allocation,
        catalog=catalog,
        description="Salário de setembro",
    )
    queries = FinancialQueries(
        lambda: QuerySourceStub(
            accounts=(account,),
            histories=(FinancialFactHistory(original=income, events=()),),
            catalog=catalog,
        )
    )

    statement = queries.account_statement(account_id=ACCOUNT_ID)

    assert statement.opening_balance.amount == Money("0", "BRL")
    assert [line.component.contribution for line in statement.lines] == [
        Money("100", "BRL"),
        Money("50", "BRL"),
    ]
    assert statement.period_change.amount == Money("150", "BRL")
    assert statement.closing_balance.amount == Money("150", "BRL")
    assert statement.closing_balance.amount == account.balance
    assert len(statement.closing_balance.components) == 2

    september_second = queries.account_statement(
        account_id=ACCOUNT_ID,
        period=QueryPeriod(date(2026, 9, 2), date(2026, 9, 2)),
    )
    assert september_second.opening_balance.amount == Money("100", "BRL")
    assert [line.component.record_id for line in september_second.lines] == [INCOME_ID]
    assert september_second.period_change.amount == Money("50", "BRL")
    assert september_second.closing_balance.amount == Money("150", "BRL")


def test_search_combines_description_amount_parent_category_account_and_period() -> None:
    child_id = UUID("00000000-0000-0000-0000-000000000102")
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=CATEGORY_ID,
            name="Receitas",
            nature=FinancialNature.INCOME,
        )
        .add_category(
            category_id=child_id,
            name="Salário",
            nature=FinancialNature.INCOME,
            parent_id=CATEGORY_ID,
        )
    )
    account = ResourceAccount.open(
        name="Conta corrente",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0", "BRL"),
        metadata=metadata(ACCOUNT_ID, 1),
    )
    allocation = Allocation.create(
        total=Money("2500", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(child_id, Money("2500", "BRL")),),
        catalog=catalog,
        metadata=metadata(INCOME_ID, 10),
    )
    income, account = Income.receive(
        account=account,
        allocation=allocation,
        catalog=catalog,
        description="Salário mensal",
    )
    queries = FinancialQueries(
        lambda: QuerySourceStub(
            accounts=(account,),
            histories=(FinancialFactHistory(original=income, events=()),),
            catalog=catalog,
        )
    )

    results = queries.search_movements(
        FinancialSearchFilters(
            description="SALÁRIO",
            amount=Money("2500", "BRL"),
            category_id=CATEGORY_ID,
            account_id=ACCOUNT_ID,
            period=QueryPeriod(date(2026, 9, 10), date(2026, 9, 10)),
        )
    )

    assert len(results) == 1
    assert results[0].record_id == INCOME_ID
    assert results[0].record_type is FinancialRecordType.INCOME
    assert results[0].description == "Salário mensal"
    assert results[0].category_ids == (child_id,)


def test_statement_uses_corrected_effect_and_not_its_historical_compensations() -> None:
    category_id = UUID("00000000-0000-0000-0000-000000000103")
    expense_id = UUID("00000000-0000-0000-0000-000000000203")
    replacement_id = UUID("00000000-0000-0000-0000-000000000204")
    change_id = UUID("00000000-0000-0000-0000-000000000205")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("500", "BRL"),
        metadata=metadata(ACCOUNT_ID, 1),
    )
    original_allocation = Allocation.create(
        total=Money("100", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("100", "BRL")),),
        catalog=catalog,
        metadata=metadata(expense_id, 2),
    )
    original, account = CashExpense.pay(
        account=account,
        allocation=original_allocation,
        catalog=catalog,
        description="Mercado antigo",
    )
    replacement_metadata = RecordMetadata(
        effective_date=date(2026, 9, 2),
        recorded_at=datetime(2026, 9, 3, 10, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=replacement_id,
    )
    replacement_allocation = Allocation.create(
        total=Money("80", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("80", "BRL")),),
        catalog=catalog,
        metadata=replacement_metadata,
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=replacement_allocation,
        description="Mercado corrigido",
        original_event_id=expense_id,
    )
    change_metadata = RecordMetadata(
        effective_date=date(2026, 9, 2),
        recorded_at=datetime(2026, 9, 3, 11, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=change_id,
    )
    correction = FinancialFactCorrected(
        target_id=expense_id,
        previous_version_id=expense_id,
        replacement=replacement,
        reason="Valor e descrição incorretos",
        metadata=change_metadata,
    )
    account = account.record_fact_change(
        kind=AccountHistoryEntryKind.FACT_CORRECTION,
        difference=Money("20", "BRL"),
        target_movement_id=expense_id,
        reason=correction.reason,
        metadata=change_metadata,
    )
    history = FinancialFactHistory(original=original, events=(correction,))
    queries = FinancialQueries(
        lambda: QuerySourceStub(
            accounts=(account,),
            histories=(history,),
            catalog=catalog,
        )
    )

    statement = queries.account_statement(account_id=ACCOUNT_ID)
    results = queries.search_movements(FinancialSearchFilters(description="corrigido"))

    assert [line.component.contribution for line in statement.lines] == [
        Money("500", "BRL"),
        Money("-80", "BRL"),
    ]
    assert statement.closing_balance.amount == Money("420", "BRL")
    assert [result.description for result in results] == ["Mercado corrigido"]
    assert results[0].effective_date == date(2026, 9, 2)
    assert results[0].first_recorded_at == datetime(2026, 9, 2, 12, tzinfo=UTC)
    assert results[0].current_version_recorded_at == datetime(
        2026,
        9,
        3,
        11,
        tzinfo=UTC,
    )
    assert history.original.description == "Mercado antigo"
    assert history.current is replacement


def test_canceled_movements_are_absent_and_refunds_inherit_current_description() -> None:
    category_id = UUID("00000000-0000-0000-0000-000000000104")
    expense_id = UUID("00000000-0000-0000-0000-000000000206")
    canceled_id = UUID("00000000-0000-0000-0000-000000000207")
    refund_id = UUID("00000000-0000-0000-0000-000000000208")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Compras",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("100", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("100", "BRL")),),
        catalog=catalog,
        metadata=metadata(expense_id, 2),
    )
    expense = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation,
        description="Compra devolvida",
    )
    refund = AccountExpenseRefund(
        original_expense_id=expense_id,
        account_id=ACCOUNT_ID,
        amount=Money("30", "BRL"),
        metadata=metadata(refund_id, 3),
    )
    cancellation = FinancialFactCanceled(
        target_id=expense_id,
        previous_version_id=expense_id,
        reason="Registro duplicado",
        metadata=metadata(canceled_id, 4),
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=allocation,
        description="Compra corrigida",
        original_event_id=expense_id,
    )
    correction = FinancialFactCorrected(
        target_id=expense_id,
        previous_version_id=expense_id,
        replacement=replacement,
        reason="Descrição corrigida",
        metadata=RecordMetadata(
            effective_date=date(2026, 9, 2),
            recorded_at=datetime(2026, 9, 3, 13, tzinfo=UTC),
            origin=RecordOrigin.MANUAL,
            record_id=UUID("00000000-0000-0000-0000-000000000209"),
        ),
    )
    active_source = QuerySourceStub(
        accounts=(),
        histories=(
            FinancialFactHistory(original=expense, events=(correction,)),
            FinancialFactHistory(original=refund, events=()),
        ),
        catalog=catalog,
    )
    canceled_source = QuerySourceStub(
        accounts=(),
        histories=(FinancialFactHistory(original=expense, events=(cancellation,)),),
        catalog=catalog,
    )

    refund_results = FinancialQueries(lambda: active_source).search_movements(
        FinancialSearchFilters(description="corrigida")
    )
    canceled_results = FinancialQueries(lambda: canceled_source).search_movements(
        FinancialSearchFilters()
    )

    assert [result.record_id for result in refund_results] == [expense_id, refund_id]
    assert [result.description for result in refund_results] == [
        "Compra corrigida",
        "Compra corrigida",
    ]
    assert canceled_results == ()


def test_exact_amount_filter_never_combines_currencies() -> None:
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("100", "USD"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(CATEGORY_ID, Money("100", "USD")),),
        catalog=catalog,
        metadata=metadata(INCOME_ID, 2),
    )
    income = Income(
        account_id=ACCOUNT_ID,
        allocation=allocation,
        description="Salary",
    )
    queries = FinancialQueries(
        lambda: QuerySourceStub(
            accounts=(),
            histories=(FinancialFactHistory(original=income, events=()),),
            catalog=catalog,
        )
    )

    results = queries.search_movements(FinancialSearchFilters(amount=Money("100", "BRL")))

    assert results == ()


def test_query_rejects_an_inverted_period_and_blank_description_filter() -> None:
    with pytest.raises(ValueError, match="start"):
        QueryPeriod(date(2026, 9, 2), date(2026, 9, 1))
    with pytest.raises(ValueError, match="description filter"):
        FinancialSearchFilters(description="   ")
