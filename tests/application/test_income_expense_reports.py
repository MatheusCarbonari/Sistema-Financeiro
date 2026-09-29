from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from bot_financeiro.application.financial_queries import (
    ExpenseRefundSnapshot,
    FinancialQueries,
    FinancialRecordType,
    IncomeExpenseReport,
    QueryPeriod,
    RecognizedFactSnapshot,
)
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.money import Money

INCOME_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000101")
EXPENSE_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000102")
SECOND_EXPENSE_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000103")
EMPTY_EXPENSE_CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000104")
INCOME_SUBCATEGORY_ID = UUID("00000000-0000-0000-0000-000000000105")


@dataclass
class ReportSourceStub:
    catalog: CategoryCatalog
    facts: tuple[RecognizedFactSnapshot, ...]
    refunds: tuple[ExpenseRefundSnapshot, ...] = ()

    def __enter__(self) -> "ReportSourceStub":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def category_catalog(self) -> CategoryCatalog:
        return self.catalog

    def list_recognized_facts(self) -> tuple[RecognizedFactSnapshot, ...]:
        return self.facts

    def list_expense_refunds(self) -> tuple[ExpenseRefundSnapshot, ...]:
        return self.refunds


def test_report_separates_income_and_recognized_expenses_with_exact_components() -> None:
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=INCOME_CATEGORY_ID,
            name="Salário",
            nature=FinancialNature.INCOME,
        )
        .add_category(
            category_id=EXPENSE_CATEGORY_ID,
            name="Moradia",
            nature=FinancialNature.EXPENSE,
        )
    )
    facts = (
        recognized_fact(
            "00000000-0000-0000-0000-000000000201",
            FinancialRecordType.INCOME,
            "1000.00",
            INCOME_CATEGORY_ID,
            FinancialNature.INCOME,
            day=5,
        ),
        recognized_fact(
            "00000000-0000-0000-0000-000000000202",
            FinancialRecordType.CASH_EXPENSE,
            "100.00",
            EXPENSE_CATEGORY_ID,
            FinancialNature.EXPENSE,
            day=6,
        ),
        recognized_fact(
            "00000000-0000-0000-0000-000000000203",
            FinancialRecordType.PAYABLE_OPENED,
            "300.00",
            EXPENSE_CATEGORY_ID,
            FinancialNature.EXPENSE,
            day=7,
        ),
        recognized_fact(
            "00000000-0000-0000-0000-000000000204",
            FinancialRecordType.INSTALLMENT_PURCHASE,
            "600.00",
            EXPENSE_CATEGORY_ID,
            FinancialNature.EXPENSE,
            day=8,
        ),
    )
    queries = FinancialQueries(lambda: ReportSourceStub(catalog=catalog, facts=facts))

    report = queries.income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert isinstance(report, IncomeExpenseReport)
    assert report.income.amount == Money("1000.00", "BRL")
    assert report.gross_expense.amount == Money("1000.00", "BRL")
    assert report.refunds.amount == Money("0.00", "BRL")
    assert report.net_expense.amount == Money("1000.00", "BRL")
    assert {component.record_id for component in report.income.components} == {
        UUID("00000000-0000-0000-0000-000000000201")
    }
    assert {component.record_id for component in report.gross_expense.components} == {
        UUID("00000000-0000-0000-0000-000000000202"),
        UUID("00000000-0000-0000-0000-000000000203"),
        UUID("00000000-0000-0000-0000-000000000204"),
    }


def test_refund_uses_its_own_date_and_has_stable_exact_proportional_allocation() -> None:
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=EXPENSE_CATEGORY_ID,
            name="Mercado",
            nature=FinancialNature.EXPENSE,
        )
        .add_category(
            category_id=SECOND_EXPENSE_CATEGORY_ID,
            name="Limpeza",
            nature=FinancialNature.EXPENSE,
        )
    )
    expense_id = UUID("00000000-0000-0000-0000-000000000211")
    refund_id = UUID("00000000-0000-0000-0000-000000000212")
    expense = recognized_fact_with_parts(
        expense_id,
        FinancialRecordType.CASH_EXPENSE,
        (
            AllocationPart(SECOND_EXPENSE_CATEGORY_ID, Money("10.00", "BRL")),
            AllocationPart(EXPENSE_CATEGORY_ID, Money("20.00", "BRL")),
        ),
        FinancialNature.EXPENSE,
        effective_date=date(2026, 8, 15),
    )
    refund = ExpenseRefundSnapshot(
        record_id=refund_id,
        record_type=FinancialRecordType.ACCOUNT_EXPENSE_REFUND,
        original_expense_id=expense_id,
        amount=Money("10.01", "BRL"),
        effective_date=date(2026, 9, 5),
        recorded_at=datetime(2026, 9, 5, 12, tzinfo=UTC),
    )
    queries = FinancialQueries(
        lambda: ReportSourceStub(catalog=catalog, facts=(expense,), refunds=(refund,))
    )

    august = queries.income_and_expense_report(
        period=QueryPeriod(date(2026, 8, 1), date(2026, 8, 31)),
        currency="BRL",
    )
    september = queries.income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert august.gross_expense.amount == Money("30.00", "BRL")
    assert august.refunds.amount == Money("0.00", "BRL")
    assert august.net_expense.amount == Money("30.00", "BRL")
    assert september.gross_expense.amount == Money("0.00", "BRL")
    assert september.refunds.amount == Money("10.01", "BRL")
    assert september.net_expense.amount == Money("-10.01", "BRL")
    assert {
        component.category_id: component.contribution for component in september.refunds.components
    } == {
        EXPENSE_CATEGORY_ID: Money("6.67", "BRL"),
        SECOND_EXPENSE_CATEGORY_ID: Money("3.34", "BRL"),
    }
    assert (
        sum(
            (component.contribution.amount for component in september.refunds.components),
            start=Money("0", "BRL").amount,
        )
        == Money("10.01", "BRL").amount
    )


def test_category_tree_separates_direct_descendant_and_inclusive_amounts() -> None:
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=EXPENSE_CATEGORY_ID,
            name="Alimentação",
            nature=FinancialNature.EXPENSE,
        )
        .add_category(
            category_id=SECOND_EXPENSE_CATEGORY_ID,
            name="Supermercado",
            nature=FinancialNature.EXPENSE,
            parent_id=EXPENSE_CATEGORY_ID,
        )
        .add_category(
            category_id=EMPTY_EXPENSE_CATEGORY_ID,
            name="Restaurantes",
            nature=FinancialNature.EXPENSE,
            parent_id=EXPENSE_CATEGORY_ID,
        )
        .archive_category(SECOND_EXPENSE_CATEGORY_ID)
    )
    expense_id = UUID("00000000-0000-0000-0000-000000000221")
    expense = recognized_fact_with_parts(
        expense_id,
        FinancialRecordType.CASH_EXPENSE,
        (
            AllocationPart(EXPENSE_CATEGORY_ID, Money("20.00", "BRL")),
            AllocationPart(SECOND_EXPENSE_CATEGORY_ID, Money("30.00", "BRL")),
        ),
        FinancialNature.EXPENSE,
        effective_date=date(2026, 9, 10),
    )
    refund = ExpenseRefundSnapshot(
        record_id=UUID("00000000-0000-0000-0000-000000000222"),
        record_type=FinancialRecordType.ACCOUNT_EXPENSE_REFUND,
        original_expense_id=expense_id,
        amount=Money("10.00", "BRL"),
        effective_date=date(2026, 9, 12),
        recorded_at=datetime(2026, 9, 12, 12, tzinfo=UTC),
    )
    report = FinancialQueries(
        lambda: ReportSourceStub(catalog=catalog, facts=(expense,), refunds=(refund,))
    ).income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert len(report.expense_categories) == 1
    parent = report.expense_categories[0]
    assert parent.category_id == EXPENSE_CATEGORY_ID
    assert parent.gross.direct.amount == Money("20.00", "BRL")
    assert parent.gross.descendants.amount == Money("30.00", "BRL")
    assert parent.gross.inclusive.amount == Money("50.00", "BRL")
    assert parent.refunds.direct.amount == Money("4.00", "BRL")
    assert parent.refunds.descendants.amount == Money("6.00", "BRL")
    assert parent.refunds.inclusive.amount == Money("10.00", "BRL")
    assert parent.net.direct.amount == Money("16.00", "BRL")
    assert parent.net.descendants.amount == Money("24.00", "BRL")
    assert parent.net.inclusive.amount == Money("40.00", "BRL")
    assert len(parent.subcategories) == 1
    assert parent.subcategories[0].category_id == SECOND_EXPENSE_CATEGORY_ID
    assert parent.subcategories[0].status.value == "archived"
    assert parent.subcategories[0].gross.inclusive.amount == Money("30.00", "BRL")
    assert all(item.category_id != EMPTY_EXPENSE_CATEGORY_ID for item in parent.subcategories)
    assert parent.gross.inclusive.amount == report.gross_expense.amount
    assert parent.refunds.inclusive.amount == report.refunds.amount
    assert parent.net.inclusive.amount == report.net_expense.amount


def test_income_categories_are_hierarchical_and_totals_never_mix_currencies() -> None:
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=INCOME_CATEGORY_ID,
            name="Trabalho",
            nature=FinancialNature.INCOME,
        )
        .add_category(
            category_id=INCOME_SUBCATEGORY_ID,
            name="Freelance",
            nature=FinancialNature.INCOME,
            parent_id=INCOME_CATEGORY_ID,
        )
        .add_category(
            category_id=EXPENSE_CATEGORY_ID,
            name="Moradia",
            nature=FinancialNature.EXPENSE,
        )
    )
    facts = (
        recognized_fact(
            "00000000-0000-0000-0000-000000000231",
            FinancialRecordType.INCOME,
            "100.00",
            INCOME_CATEGORY_ID,
            FinancialNature.INCOME,
            day=2,
        ),
        recognized_fact(
            "00000000-0000-0000-0000-000000000232",
            FinancialRecordType.INCOME,
            "50.00",
            INCOME_SUBCATEGORY_ID,
            FinancialNature.INCOME,
            day=3,
        ),
        recognized_fact(
            "00000000-0000-0000-0000-000000000233",
            FinancialRecordType.INCOME,
            "25.00",
            INCOME_CATEGORY_ID,
            FinancialNature.INCOME,
            day=4,
            currency="USD",
        ),
    )
    queries = FinancialQueries(lambda: ReportSourceStub(catalog=catalog, facts=facts))

    report = queries.income_and_expense_report(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert report.income.amount == Money("150.00", "BRL")
    assert len(report.income_categories) == 1
    parent = report.income_categories[0]
    assert parent.amounts.direct.amount == Money("100.00", "BRL")
    assert parent.amounts.descendants.amount == Money("50.00", "BRL")
    assert parent.amounts.inclusive.amount == Money("150.00", "BRL")
    assert len(parent.subcategories) == 1
    assert parent.subcategories[0].category_id == INCOME_SUBCATEGORY_ID


def recognized_fact(
    record_id: str,
    record_type: FinancialRecordType,
    amount: str,
    category_id: UUID,
    nature: FinancialNature,
    *,
    day: int,
    currency: str = "BRL",
) -> RecognizedFactSnapshot:
    identity = UUID(record_id)
    value = Money(amount, currency)
    allocation = Allocation(
        allocation_id=identity,
        total=value,
        nature=nature,
        parts=(AllocationPart(category_id, value),),
        metadata=metadata(identity, day),
    )
    return RecognizedFactSnapshot(
        record_id=identity,
        version_id=identity,
        record_type=record_type,
        allocation=allocation,
        description="Registro de teste",
        first_recorded_at=allocation.metadata.recorded_at,
        current_version_recorded_at=allocation.metadata.recorded_at,
    )


def recognized_fact_with_parts(
    record_id: UUID,
    record_type: FinancialRecordType,
    parts: tuple[AllocationPart, ...],
    nature: FinancialNature,
    *,
    effective_date: date,
) -> RecognizedFactSnapshot:
    total = Money(
        str(sum((part.amount.amount for part in parts), start=Money("0", "BRL").amount)),
        "BRL",
    )
    recorded_at = datetime.combine(effective_date, datetime.min.time(), tzinfo=UTC)
    allocation = Allocation(
        allocation_id=record_id,
        total=total,
        nature=nature,
        parts=parts,
        metadata=RecordMetadata(
            effective_date=effective_date,
            recorded_at=recorded_at,
            origin=RecordOrigin.MANUAL,
            record_id=record_id,
        ),
    )
    return RecognizedFactSnapshot(
        record_id=record_id,
        version_id=record_id,
        record_type=record_type,
        allocation=allocation,
        description="Compra com Rateios",
        first_recorded_at=recorded_at,
        current_version_recorded_at=recorded_at,
    )


def metadata(record_id: UUID, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=record_id,
    )
