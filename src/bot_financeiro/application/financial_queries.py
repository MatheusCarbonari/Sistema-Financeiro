from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date, datetime
from enum import StrEnum
from types import TracebackType
from typing import Protocol
from uuid import UUID

from bot_financeiro.domain.categories import (
    Allocation,
    CategoryCatalog,
    CategoryStatus,
    FinancialCategory,
    FinancialNature,
)
from bot_financeiro.domain.financial_changes import FinancialFactHistory
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import (
    BillPayment,
    CashExpense,
    FinancialMovement,
    Income,
    InvoicePayment,
    Transfer,
)
from bot_financeiro.domain.resource_account import (
    AccountHistoryEntryKind,
    ResourceAccount,
)


class FinancialRecordType(StrEnum):
    INITIAL_BALANCE = "initial_balance"
    BALANCE_ADJUSTMENT = "balance_adjustment"
    INCOME = "income"
    CASH_EXPENSE = "cash_expense"
    TRANSFER = "transfer"
    BILL_PAYMENT = "bill_payment"
    INVOICE_PAYMENT = "invoice_payment"
    ACCOUNT_EXPENSE_REFUND = "account_expense_refund"
    PAYABLE_OPENED = "payable_opened"
    PAYABLE_FINANCIAL_CHARGE = "payable_financial_charge"
    CARD_PURCHASE = "card_purchase"
    INSTALLMENT_PURCHASE = "installment_purchase"
    INVOICE_FINANCIAL_CHARGE = "invoice_financial_charge"
    CARD_REFUND = "card_refund"


@dataclass(frozen=True, slots=True)
class QueryPeriod:
    """Inclusive interval over Data efetiva, never over Momento do registro."""

    start_date: date
    end_date: date

    def __post_init__(self) -> None:
        if self.start_date > self.end_date:
            raise ValueError("query period start cannot follow its end")


@dataclass(frozen=True, slots=True)
class FinancialSearchFilters:
    description: str | None = None
    amount: Money | None = None
    category_id: UUID | None = None
    account_id: UUID | None = None
    period: QueryPeriod | None = None

    def __post_init__(self) -> None:
        if self.description is None:
            return
        normalized = self.description.strip()
        if not normalized:
            raise ValueError("description filter cannot be blank")
        object.__setattr__(self, "description", normalized)


@dataclass(frozen=True, slots=True)
class QueryComponent:
    record_id: UUID
    version_id: UUID
    record_type: FinancialRecordType
    effective_date: date
    first_recorded_at: datetime
    current_version_recorded_at: datetime
    description: str | None
    contribution: Money
    category_id: UUID | None = None


@dataclass(frozen=True, slots=True)
class ExplainedAmount:
    amount: Money
    components: tuple[QueryComponent, ...]

    def __post_init__(self) -> None:
        total = Money("0", self.amount.currency)
        for component in self.components:
            total += component.contribution
        if total != self.amount:
            raise ValueError("explained amount must equal its component contributions")


@dataclass(frozen=True, slots=True)
class FinancialRecordView:
    record_id: UUID
    version_id: UUID
    record_type: FinancialRecordType
    description: str | None
    amount: Money
    effective_date: date
    first_recorded_at: datetime
    current_version_recorded_at: datetime
    account_ids: tuple[UUID, ...]
    category_ids: tuple[UUID, ...]


@dataclass(frozen=True, slots=True)
class AccountStatementLine:
    component: QueryComponent
    balance_after: Money


@dataclass(frozen=True, slots=True)
class AccountStatementView:
    account_id: UUID
    account_name: str
    currency: str
    period: QueryPeriod | None
    opening_balance: ExplainedAmount
    lines: tuple[AccountStatementLine, ...]
    period_change: ExplainedAmount
    closing_balance: ExplainedAmount


@dataclass(frozen=True, slots=True)
class RecognizedFactSnapshot:
    record_id: UUID
    version_id: UUID
    record_type: FinancialRecordType
    allocation: Allocation
    description: str | None
    first_recorded_at: datetime
    current_version_recorded_at: datetime

    @property
    def effective_date(self) -> date:
        return self.allocation.metadata.effective_date


@dataclass(frozen=True, slots=True)
class ExpenseRefundSnapshot:
    record_id: UUID
    record_type: FinancialRecordType
    original_expense_id: UUID
    amount: Money
    effective_date: date
    recorded_at: datetime


@dataclass(frozen=True, slots=True)
class IncomeExpenseReport:
    period: QueryPeriod
    currency: str
    income: ExplainedAmount
    gross_expense: ExplainedAmount
    refunds: ExplainedAmount
    net_expense: ExplainedAmount
    income_categories: tuple[IncomeCategoryBreakdown, ...] = ()
    expense_categories: tuple[ExpenseCategoryBreakdown, ...] = ()


@dataclass(frozen=True, slots=True)
class CategoryAmounts:
    direct: ExplainedAmount
    descendants: ExplainedAmount
    inclusive: ExplainedAmount

    def __post_init__(self) -> None:
        if self.direct.amount + self.descendants.amount != self.inclusive.amount:
            raise ValueError("inclusive category amount must equal direct and descendants")


@dataclass(frozen=True, slots=True)
class IncomeCategoryBreakdown:
    category_id: UUID
    name: str
    status: CategoryStatus
    amounts: CategoryAmounts
    subcategories: tuple[IncomeCategoryBreakdown, ...] = ()


@dataclass(frozen=True, slots=True)
class ExpenseCategoryBreakdown:
    category_id: UUID
    name: str
    status: CategoryStatus
    gross: CategoryAmounts
    refunds: CategoryAmounts
    net: CategoryAmounts
    subcategories: tuple[ExpenseCategoryBreakdown, ...] = ()


class FinancialQuerySource(Protocol):
    def __enter__(self) -> FinancialQuerySource: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def list_accounts(self) -> tuple[ResourceAccount, ...]: ...

    def list_movement_histories(
        self,
    ) -> tuple[FinancialFactHistory[FinancialMovement], ...]: ...

    def category_catalog(self) -> CategoryCatalog: ...

    def get_current_financial_fact(self, fact_id: UUID): ...

    def list_recognized_facts(self) -> tuple[RecognizedFactSnapshot, ...]: ...

    def list_expense_refunds(self) -> tuple[ExpenseRefundSnapshot, ...]: ...


class FinancialQueries:
    """Current-state queries with effective dates and registration moments exposed separately.

    Corrections restate the current financial view at the replacement's effective date.
    ``current_version_recorded_at`` identifies when that current version was registered;
    these Part 1 queries are not historical "known as of" queries.
    """

    def __init__(
        self,
        source_factory: Callable[[], FinancialQuerySource],
    ) -> None:
        self._source_factory = source_factory

    def search_movements(
        self,
        filters: FinancialSearchFilters,
    ) -> tuple[FinancialRecordView, ...]:
        with self._source_factory() as source:
            catalog = source.category_catalog()
            included_category_ids: set[UUID] | None = None
            if filters.category_id is not None:
                catalog.require(filters.category_id)
                included_category_ids = {
                    filters.category_id,
                    *(
                        category.category_id
                        for category in catalog.categories
                        if category.parent_id == filters.category_id
                    ),
                }
            results: list[FinancialRecordView] = []
            for history in source.list_movement_histories():
                current = history.current
                if current is None:
                    continue
                description = self._description(current, source)
                account_ids = tuple(effect.account_id for effect in current.account_effects)
                classified_fact = self._classification_fact(current, source)
                allocation = getattr(classified_fact, "allocation", None)
                category_ids = (
                    ()
                    if allocation is None
                    else tuple(part.category_id for part in allocation.parts)
                )
                if filters.description is not None and (
                    description is None
                    or filters.description.casefold() not in description.casefold()
                ):
                    continue
                if filters.amount is not None:
                    if current.amount.currency != filters.amount.currency:
                        continue
                    if current.amount != filters.amount:
                        continue
                if included_category_ids is not None and included_category_ids.isdisjoint(
                    category_ids
                ):
                    continue
                if filters.account_id is not None and filters.account_id not in account_ids:
                    continue
                if filters.period is not None and not (
                    filters.period.start_date
                    <= current.metadata.effective_date
                    <= filters.period.end_date
                ):
                    continue
                results.append(
                    FinancialRecordView(
                        record_id=history.target_id,
                        version_id=history.version_id,
                        record_type=self._record_type(current),
                        description=description,
                        amount=current.amount,
                        effective_date=current.metadata.effective_date,
                        first_recorded_at=history.original.metadata.recorded_at,
                        current_version_recorded_at=self._version_recorded_at(history),
                        account_ids=account_ids,
                        category_ids=category_ids,
                    )
                )
        return tuple(
            sorted(
                results,
                key=lambda item: (
                    item.effective_date,
                    item.current_version_recorded_at,
                    item.record_id,
                ),
            )
        )

    def account_statement(
        self,
        *,
        account_id: UUID,
        period: QueryPeriod | None = None,
    ) -> AccountStatementView:
        with self._source_factory() as source:
            account = next(
                (item for item in source.list_accounts() if item.account_id == account_id),
                None,
            )
            if account is None:
                raise LookupError(f"resource account {account_id} does not exist")
            components = self._account_components(account, source)

        reconstructed = self._sum_components(components, account.currency)
        if reconstructed.amount != account.balance:
            raise ValueError("reconstructed account balance does not match persisted balance")

        if period is None:
            opening_components: tuple[QueryComponent, ...] = ()
            period_components = components
            closing_components = components
        else:
            opening_components = tuple(
                item for item in components if item.effective_date < period.start_date
            )
            period_components = tuple(
                item
                for item in components
                if period.start_date <= item.effective_date <= period.end_date
            )
            closing_components = tuple(
                item for item in components if item.effective_date <= period.end_date
            )

        opening = self._sum_components(opening_components, account.currency)
        period_change = self._sum_components(period_components, account.currency)
        balance = opening.amount
        lines: list[AccountStatementLine] = []
        for component in period_components:
            balance += component.contribution
            lines.append(AccountStatementLine(component=component, balance_after=balance))
        closing = self._sum_components(closing_components, account.currency)
        return AccountStatementView(
            account_id=account.account_id,
            account_name=account.name,
            currency=account.currency,
            period=period,
            opening_balance=opening,
            lines=tuple(lines),
            period_change=period_change,
            closing_balance=closing,
        )

    def income_and_expense_report(
        self,
        *,
        period: QueryPeriod,
        currency: str,
    ) -> IncomeExpenseReport:
        with self._source_factory() as source:
            catalog = source.category_catalog()
            all_facts = source.list_recognized_facts()
            expense_facts_by_id = {
                fact.record_id: fact
                for fact in all_facts
                if fact.allocation.nature is FinancialNature.EXPENSE
            }
            facts = tuple(
                fact
                for fact in all_facts
                if fact.allocation.total.currency == currency
                and period.start_date <= fact.effective_date <= period.end_date
            )
            refund_records = tuple(
                refund
                for refund in source.list_expense_refunds()
                if refund.amount.currency == currency
                and period.start_date <= refund.effective_date <= period.end_date
            )

        income_components: list[QueryComponent] = []
        expense_components: list[QueryComponent] = []
        for fact in facts:
            destination = (
                income_components
                if fact.allocation.nature is FinancialNature.INCOME
                else expense_components
            )
            for part in sorted(
                fact.allocation.parts,
                key=lambda item: str(item.category_id),
            ):
                destination.append(
                    QueryComponent(
                        record_id=fact.record_id,
                        version_id=fact.version_id,
                        record_type=fact.record_type,
                        effective_date=fact.effective_date,
                        first_recorded_at=fact.first_recorded_at,
                        current_version_recorded_at=fact.current_version_recorded_at,
                        description=fact.description,
                        contribution=part.amount,
                        category_id=part.category_id,
                    )
                )

        refund_components: list[QueryComponent] = []
        for refund in refund_records:
            original = expense_facts_by_id.get(refund.original_expense_id)
            if original is None:
                raise ValueError("expense refund does not reference a current expense")
            refund_components.extend(self._refund_components(refund, original))

        income = self._sum_components(self._sorted_components(income_components), currency)
        gross_expense = self._sum_components(
            self._sorted_components(expense_components),
            currency,
        )
        refunds = self._sum_components(
            self._sorted_components(refund_components),
            currency,
        )
        net_components = [
            *expense_components,
            *(
                replace(
                    component,
                    contribution=Money(
                        -component.contribution.amount,
                        component.contribution.currency,
                    ),
                )
                for component in refund_components
            ),
        ]
        net_expense = self._sum_components(
            self._sorted_components(net_components),
            currency,
        )
        income_categories = self._income_category_breakdowns(
            catalog,
            income.components,
            currency,
        )
        expense_categories = self._expense_category_breakdowns(
            catalog,
            gross_expense.components,
            refunds.components,
            net_expense.components,
            currency,
        )
        return IncomeExpenseReport(
            period=period,
            currency=currency,
            income=income,
            gross_expense=gross_expense,
            refunds=refunds,
            net_expense=net_expense,
            income_categories=income_categories,
            expense_categories=expense_categories,
        )

    @classmethod
    def _income_category_breakdowns(
        cls,
        catalog: CategoryCatalog,
        components: tuple[QueryComponent, ...],
        currency: str,
    ) -> tuple[IncomeCategoryBreakdown, ...]:
        relevant_ids = {item.category_id for item in components}
        roots = cls._relevant_roots(catalog, FinancialNature.INCOME, relevant_ids)
        return tuple(
            IncomeCategoryBreakdown(
                category_id=root.category_id,
                name=root.name,
                status=root.status,
                amounts=cls._category_amounts(
                    root.category_id,
                    cls._child_ids(catalog, root.category_id),
                    components,
                    currency,
                ),
                subcategories=tuple(
                    IncomeCategoryBreakdown(
                        category_id=child.category_id,
                        name=child.name,
                        status=child.status,
                        amounts=cls._category_amounts(
                            child.category_id,
                            (),
                            components,
                            currency,
                        ),
                    )
                    for child in cls._relevant_children(catalog, root, relevant_ids)
                ),
            )
            for root in roots
        )

    @classmethod
    def _expense_category_breakdowns(
        cls,
        catalog: CategoryCatalog,
        gross_components: tuple[QueryComponent, ...],
        refund_components: tuple[QueryComponent, ...],
        net_components: tuple[QueryComponent, ...],
        currency: str,
    ) -> tuple[ExpenseCategoryBreakdown, ...]:
        relevant_ids = {item.category_id for item in (*gross_components, *refund_components)}
        roots = cls._relevant_roots(catalog, FinancialNature.EXPENSE, relevant_ids)
        return tuple(
            cls._expense_category(
                root,
                cls._child_ids(catalog, root.category_id),
                tuple(cls._relevant_children(catalog, root, relevant_ids)),
                gross_components,
                refund_components,
                net_components,
                currency,
            )
            for root in roots
        )

    @classmethod
    def _expense_category(
        cls,
        category: FinancialCategory,
        descendant_ids: tuple[UUID, ...],
        children: tuple[FinancialCategory, ...],
        gross_components: tuple[QueryComponent, ...],
        refund_components: tuple[QueryComponent, ...],
        net_components: tuple[QueryComponent, ...],
        currency: str,
    ) -> ExpenseCategoryBreakdown:
        return ExpenseCategoryBreakdown(
            category_id=category.category_id,
            name=category.name,
            status=category.status,
            gross=cls._category_amounts(
                category.category_id,
                descendant_ids,
                gross_components,
                currency,
            ),
            refunds=cls._category_amounts(
                category.category_id,
                descendant_ids,
                refund_components,
                currency,
            ),
            net=cls._category_amounts(
                category.category_id,
                descendant_ids,
                net_components,
                currency,
            ),
            subcategories=tuple(
                cls._expense_category(
                    child,
                    (),
                    (),
                    gross_components,
                    refund_components,
                    net_components,
                    currency,
                )
                for child in children
            ),
        )

    @classmethod
    def _category_amounts(
        cls,
        category_id: UUID,
        descendant_ids: tuple[UUID, ...],
        components: tuple[QueryComponent, ...],
        currency: str,
    ) -> CategoryAmounts:
        direct_components = tuple(item for item in components if item.category_id == category_id)
        descendant_components = tuple(
            item for item in components if item.category_id in descendant_ids
        )
        return CategoryAmounts(
            direct=cls._sum_components(direct_components, currency),
            descendants=cls._sum_components(descendant_components, currency),
            inclusive=cls._sum_components(
                (*direct_components, *descendant_components),
                currency,
            ),
        )

    @staticmethod
    def _relevant_roots(
        catalog: CategoryCatalog,
        nature: FinancialNature,
        relevant_ids: set[UUID | None],
    ) -> tuple[FinancialCategory, ...]:
        return tuple(
            sorted(
                (
                    category
                    for category in catalog.categories_for(nature)
                    if category.parent_id is None
                    and (
                        category.category_id in relevant_ids
                        or any(
                            child.parent_id == category.category_id
                            and child.category_id in relevant_ids
                            for child in catalog.categories
                        )
                    )
                ),
                key=lambda item: str(item.category_id),
            )
        )

    @staticmethod
    def _relevant_children(
        catalog: CategoryCatalog,
        parent: FinancialCategory,
        relevant_ids: set[UUID | None],
    ) -> tuple[FinancialCategory, ...]:
        return tuple(
            sorted(
                (
                    category
                    for category in catalog.categories
                    if category.parent_id == parent.category_id
                    and category.category_id in relevant_ids
                ),
                key=lambda item: str(item.category_id),
            )
        )

    @staticmethod
    def _child_ids(catalog: CategoryCatalog, parent_id: UUID) -> tuple[UUID, ...]:
        return tuple(
            category.category_id
            for category in catalog.categories
            if category.parent_id == parent_id
        )

    @staticmethod
    def _refund_components(
        refund: ExpenseRefundSnapshot,
        original: RecognizedFactSnapshot,
    ) -> tuple[QueryComponent, ...]:
        if refund.amount.currency != original.allocation.total.currency:
            raise ValueError("refund and original expense must use the same currency")
        distributed = original.allocation.distribute_proportionally(refund.amount)
        return tuple(
            QueryComponent(
                record_id=refund.record_id,
                version_id=refund.record_id,
                record_type=refund.record_type,
                effective_date=refund.effective_date,
                first_recorded_at=refund.recorded_at,
                current_version_recorded_at=refund.recorded_at,
                description=original.description,
                contribution=part.amount,
                category_id=part.category_id,
            )
            for part in distributed
        )

    @staticmethod
    def _sorted_components(
        components: list[QueryComponent],
    ) -> tuple[QueryComponent, ...]:
        return tuple(
            sorted(
                components,
                key=lambda item: (
                    item.effective_date,
                    item.current_version_recorded_at,
                    item.record_id,
                    "" if item.category_id is None else str(item.category_id),
                ),
            )
        )

    def _account_components(
        self,
        account: ResourceAccount,
        source: FinancialQuerySource,
    ) -> tuple[QueryComponent, ...]:
        components = [
            QueryComponent(
                record_id=entry.metadata.record_id,
                version_id=entry.metadata.record_id,
                record_type=(
                    FinancialRecordType.INITIAL_BALANCE
                    if entry.kind is AccountHistoryEntryKind.INITIAL_BALANCE
                    else FinancialRecordType.BALANCE_ADJUSTMENT
                ),
                effective_date=entry.metadata.effective_date,
                first_recorded_at=entry.metadata.recorded_at,
                current_version_recorded_at=entry.metadata.recorded_at,
                description=getattr(entry, "description", None),
                contribution=entry.difference,
            )
            for entry in account.history
            if entry.kind
            in {
                AccountHistoryEntryKind.INITIAL_BALANCE,
                AccountHistoryEntryKind.BALANCE_ADJUSTMENT,
            }
        ]
        for history in source.list_movement_histories():
            current = history.current
            if current is None:
                continue
            for effect in current.account_effects:
                if effect.account_id != account.account_id:
                    continue
                components.append(
                    QueryComponent(
                        record_id=history.target_id,
                        version_id=history.version_id,
                        record_type=self._record_type(current),
                        effective_date=current.metadata.effective_date,
                        first_recorded_at=history.original.metadata.recorded_at,
                        current_version_recorded_at=self._version_recorded_at(history),
                        description=self._description(current, source),
                        contribution=effect.amount,
                    )
                )
        return tuple(
            sorted(
                components,
                key=lambda item: (
                    item.effective_date,
                    item.current_version_recorded_at,
                    item.record_id,
                ),
            )
        )

    @staticmethod
    def _sum_components(
        components: tuple[QueryComponent, ...] | list[QueryComponent],
        currency: str,
    ) -> ExplainedAmount:
        total = Money("0", currency)
        for component in components:
            total += component.contribution
        return ExplainedAmount(amount=total, components=tuple(components))

    @staticmethod
    def _description(movement: FinancialMovement, source: FinancialQuerySource) -> str | None:
        original_expense_id = getattr(movement, "original_expense_id", None)
        if original_expense_id is not None:
            original = source.get_current_financial_fact(original_expense_id)
            return None if original is None else getattr(original, "description", None)
        return getattr(movement, "description", None)

    @staticmethod
    def _classification_fact(movement: FinancialMovement, source: FinancialQuerySource):
        original_expense_id = getattr(movement, "original_expense_id", None)
        if original_expense_id is None:
            return movement
        return source.get_current_financial_fact(original_expense_id)

    @staticmethod
    def _record_type(movement: FinancialMovement) -> FinancialRecordType:
        if isinstance(movement, Income):
            return FinancialRecordType.INCOME
        if isinstance(movement, CashExpense):
            return FinancialRecordType.CASH_EXPENSE
        if isinstance(movement, Transfer):
            return FinancialRecordType.TRANSFER
        if isinstance(movement, BillPayment):
            return FinancialRecordType.BILL_PAYMENT
        if isinstance(movement, InvoicePayment):
            return FinancialRecordType.INVOICE_PAYMENT
        return FinancialRecordType.ACCOUNT_EXPENSE_REFUND

    @staticmethod
    def _version_recorded_at(
        history: FinancialFactHistory[FinancialMovement],
    ) -> datetime:
        if history.events:
            return history.events[-1].metadata.recorded_at
        return history.original.metadata.recorded_at
