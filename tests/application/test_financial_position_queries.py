from dataclasses import dataclass
from datetime import UTC, date, datetime
from uuid import UUID

from bot_financeiro.application.financial_queries import (
    CommitmentKind,
    CreditCardPositionView,
    FinancialQueries,
    QueryPeriod,
)
from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    FinancialNature,
)
from bot_financeiro.domain.credit_cards import CreditCard, InvoiceStatus
from bot_financeiro.domain.financial_changes import (
    AccountExpenseRefund,
    FinancialFactCorrected,
    FinancialFactHistory,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import (
    BillPayment,
    CashExpense,
    Income,
    InvoicePayment,
    Transfer,
)
from bot_financeiro.domain.payables import Payable, PayableStatus
from bot_financeiro.domain.planning import ForecastDirection, ForecastStatus, PlannedOccurrence
from bot_financeiro.domain.resource_account import AccountType, ResourceAccount

CARD_ID = UUID("00000000-0000-0000-0000-000000000601")
CATEGORY_ID = UUID("00000000-0000-0000-0000-000000000602")
ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000603")
SECOND_ACCOUNT_ID = UUID("00000000-0000-0000-0000-000000000604")


@dataclass
class PositionSourceStub:
    card: CreditCard

    def __enter__(self) -> "PositionSourceStub":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def get_credit_card(self, card_id: UUID) -> CreditCard | None:
        return self.card if self.card.card_id == card_id else None

    def financial_fact_version_recorded_at(self, fact_id: UUID) -> datetime | None:
        del fact_id
        return None


@dataclass
class PayableSourceStub:
    payables: tuple[Payable, ...]

    def __enter__(self) -> "PayableSourceStub":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def list_payables(self) -> tuple[Payable, ...]:
        return self.payables

    def financial_fact_version_recorded_at(self, fact_id: UUID) -> datetime | None:
        del fact_id
        return None


@dataclass
class CommitmentSourceStub:
    cards: tuple[CreditCard, ...]
    payables: tuple[Payable, ...]
    occurrences: tuple[PlannedOccurrence, ...]

    def __enter__(self) -> "CommitmentSourceStub":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def list_credit_cards(self) -> tuple[CreditCard, ...]:
        return self.cards

    def list_payables(self) -> tuple[Payable, ...]:
        return self.payables

    def list_planned_occurrences(self, through_date: date) -> tuple[PlannedOccurrence, ...]:
        return tuple(
            occurrence for occurrence in self.occurrences if occurrence.planned_date <= through_date
        )

    def financial_fact_version_recorded_at(self, fact_id: UUID) -> datetime | None:
        del fact_id
        return None


@dataclass
class CashFlowSourceStub:
    histories: tuple[FinancialFactHistory, ...]

    def __enter__(self) -> "CashFlowSourceStub":
        return self

    def __exit__(self, exc_type, exc_value, traceback) -> None:
        del exc_type, exc_value, traceback

    def list_movement_histories(self) -> tuple[FinancialFactHistory, ...]:
        return self.histories

    def get_current_financial_fact(self, fact_id: UUID):
        return next(
            (history.current for history in self.histories if history.target_id == fact_id),
            None,
        )


def test_credit_card_position_reconciles_invoices_installments_and_settlements() -> None:
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Compras",
        nature=FinancialNature.EXPENSE,
    )
    card = CreditCard.open(
        name="Cartão principal",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=Money("500.00", "BRL"),
        metadata=metadata(CARD_ID, day=1),
    )
    purchase, card = card.purchase(
        allocation=expense(catalog, "100.00", 610, day=2),
        catalog=catalog,
        description="Compra simples",
    )
    installment_purchase, card = card.purchase_in_installments(
        allocation=expense(catalog, "90.00", 611, day=3),
        installment_count=3,
        catalog=catalog,
        description="Compra parcelada",
    )
    _, card = card.add_financial_charge(
        closing_date=purchase.invoice_closing_date,
        allocation=expense(catalog, "20.00", 612, day=4),
        catalog=catalog,
        description="Encargo",
    )
    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(ACCOUNT_ID, day=1),
    )
    _, card, _ = card.pay_invoice(
        closing_date=purchase.invoice_closing_date,
        account=account,
        amount=Money("50.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000613"), day=5),
        description="Pagamento parcial",
    )
    _, card = card.refund_purchase(
        purchase_id=purchase.purchase_id,
        amount=Money("20.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000614"), day=6),
    )
    _, card = card.grant_card_credit(
        amount=Money("30.00", "BRL"),
        reason="Crédito real",
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000616"), day=7),
        description="Crédito da operadora",
    )
    _, card = card.apply_card_credit(
        closing_date=purchase.invoice_closing_date,
        amount=Money("10.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000617"), day=8),
    )

    position = FinancialQueries(lambda: PositionSourceStub(card)).credit_card_position(
        card_id=CARD_ID, on_date=date(2026, 9, 18)
    )

    assert isinstance(position, CreditCardPositionView)
    assert position.principal_pending.amount == Money("110.00", "BRL")
    assert position.financial_charges_pending.amount == Money("20.00", "BRL")
    assert position.net_debt.amount == Money("130.00", "BRL")
    assert position.available_card_credit.amount == Money("20.00", "BRL")
    assert position.card_limit == Money("500.00", "BRL")
    assert position.available_limit == Money("390.00", "BRL")
    assert all(
        component.recorded_at.tzinfo is not None for component in position.net_debt.components
    )
    assert (
        sum(
            (component.contribution.amount for component in position.net_debt.components),
            start=Money("0", "BRL").amount,
        )
        == Money("130.00", "BRL").amount
    )

    first_invoice = position.invoices[0]
    assert first_invoice.status is InvoiceStatus.OVERDUE
    assert first_invoice.principal_pending == Money("50.00", "BRL")
    assert first_invoice.financial_charges_pending == Money("20.00", "BRL")
    assert len(first_invoice.installments) == 1
    assert first_invoice.installments[0].purchase_id == installment_purchase.purchase_id
    assert first_invoice.installments[0].sequence == 1
    assert first_invoice.installments[0].total_installments == 3
    assert first_invoice.installments[0].amount == Money("30.00", "BRL")


def test_payables_report_keeps_pending_and_overdue_debts_but_omits_paid_ones() -> None:
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Contas",
        nature=FinancialNature.EXPENSE,
    )
    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(ACCOUNT_ID, day=1),
    )
    overdue = Payable.open(
        allocation=expense(catalog, "100.00", 620, day=2),
        due_date=date(2026, 9, 10),
        catalog=catalog,
        description="Conta vencida",
    )
    _, overdue = overdue.add_financial_charge(
        allocation=expense(catalog, "10.00", 621, day=3),
        catalog=catalog,
        description="Multa",
    )
    _, overdue, _ = overdue.pay(
        account=account,
        amount=Money("40.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000622"), day=4),
        description="Pagamento parcial",
    )
    pending = Payable.open(
        allocation=expense(catalog, "50.00", 623, day=5),
        due_date=date(2026, 10, 10),
        catalog=catalog,
        description="Conta futura",
    )
    paid = Payable.open(
        allocation=expense(catalog, "30.00", 624, day=6),
        due_date=date(2026, 9, 15),
        catalog=catalog,
        description="Conta quitada",
    )
    _, paid, _ = paid.pay(
        account=account,
        amount=Money("30.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000625"), day=7),
        description="Quitação",
    )

    report = FinancialQueries(lambda: PayableSourceStub((pending, paid, overdue))).payables_report(
        on_date=date(2026, 9, 20), currency="BRL"
    )

    assert report.total_due.amount == Money("120.00", "BRL")
    assert [item.payable_id for item in report.payables] == [
        overdue.payable_id,
        pending.payable_id,
    ]
    overdue_view = report.payables[0]
    assert overdue_view.status is PayableStatus.OVERDUE
    assert overdue_view.original_amount == Money("100.00", "BRL")
    assert overdue_view.financial_charges == Money("10.00", "BRL")
    assert overdue_view.payments == Money("40.00", "BRL")
    assert overdue_view.amount_due.amount == Money("70.00", "BRL")
    assert (
        sum(
            (component.contribution.amount for component in overdue_view.amount_due.components),
            start=Money("0", "BRL").amount,
        )
        == Money("70.00", "BRL").amount
    )


def test_commitments_keep_overdue_and_bound_future_without_duplicating_installments() -> None:
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Compromissos",
        nature=FinancialNature.EXPENSE,
    )
    card = CreditCard.open(
        name="Cartão",
        currency="BRL",
        closing_day=10,
        due_day=17,
        limit=None,
        metadata=metadata(CARD_ID, day=1),
    )
    installment_purchase, card = card.purchase_in_installments(
        allocation=expense(catalog, "90.00", 630, day=2),
        installment_count=3,
        catalog=catalog,
        description="Compra em três vezes",
    )
    first_closing_date = installment_purchase.installments[0].cycle.closing_date
    _, card = card.add_financial_charge(
        closing_date=first_closing_date,
        allocation=expense(catalog, "5.00", 639, day=3),
        catalog=catalog,
        description="Encargo da primeira fatura",
    )
    account = ResourceAccount.open(
        name="Conta para pagamento",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(ACCOUNT_ID, day=1),
    )
    _, card, _ = card.pay_invoice(
        closing_date=first_closing_date,
        account=account,
        amount=Money("10.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000647"), day=4),
        description="Pagamento parcial da primeira fatura",
    )
    _, card = card.refund_purchase(
        purchase_id=installment_purchase.purchase_id,
        amount=Money("3.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000648"), day=5),
    )
    overdue_payable = Payable.open(
        allocation=expense(catalog, "100.00", 631, day=3),
        due_date=date(2026, 9, 10),
        catalog=catalog,
        description="Conta atrasada",
    )
    future_payable = Payable.open(
        allocation=expense(catalog, "50.00", 632, day=4),
        due_date=date(2026, 10, 20),
        catalog=catalog,
        description="Conta futura",
    )
    overdue_forecast = forecast(633, planned_date=date(2026, 9, 15), amount="25.00")
    future_forecast = forecast(634, planned_date=date(2026, 10, 5), amount="40.00")
    beyond_horizon = forecast(635, planned_date=date(2026, 11, 5), amount="60.00")
    confirmed = forecast(636, planned_date=date(2026, 9, 16), amount="70.00").confirm(
        movement_id=UUID("00000000-0000-0000-0000-000000000637"),
        actual_amount=Money("70.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000638"), day=16),
    )

    report = FinancialQueries(
        lambda: CommitmentSourceStub(
            cards=(card,),
            payables=(future_payable, overdue_payable),
            occurrences=(
                beyond_horizon,
                future_forecast,
                confirmed,
                overdue_forecast,
            ),
        )
    ).commitments_report(
        as_of=date(2026, 9, 20),
        through_date=date(2026, 10, 31),
        currency="BRL",
    )

    assert report.recognized_debt_total == Money("202.00", "BRL")
    assert report.forecast_outflow_total == Money("65.00", "BRL")
    assert len(report.recognized_debts) == 4
    assert [item.kind for item in report.recognized_debts].count(CommitmentKind.INVOICE) == 2
    assert [item.kind for item in report.recognized_debts].count(CommitmentKind.PAYABLE) == 2
    assert all(item.due_date <= date(2026, 10, 31) for item in report.recognized_debts)
    assert any(item.due_date < date(2026, 9, 20) for item in report.recognized_debts)
    invoice_commitments = tuple(
        item for item in report.recognized_debts if item.kind is CommitmentKind.INVOICE
    )
    assert sum(len(item.installments) for item in invoice_commitments) == 2
    assert [item.amount for item in invoice_commitments] == [
        Money("22.00", "BRL"),
        Money("30.00", "BRL"),
    ]
    assert all(
        sum(
            (component.contribution.amount for component in item.components),
            start=Money("0", "BRL").amount,
        )
        == item.amount.amount
        for item in report.recognized_debts
    )
    assert {component.kind for component in invoice_commitments[0].components} >= {
        "installment",
        "financial_charge",
        "payment",
        "credit_applied",
    }
    assert {item.status for item in report.forecast_occurrences} == {
        ForecastStatus.OVERDUE,
        ForecastStatus.PENDING,
    }
    assert {item.occurrence_id for item in report.forecast_occurrences} == {
        overdue_forecast.occurrence_id,
        future_forecast.occurrence_id,
    }


def test_realized_cash_flow_excludes_internal_transfers_from_global_totals_but_details_them() -> (
    None
):
    catalog = (
        CategoryCatalog.empty()
        .add_category(
            category_id=CATEGORY_ID,
            name="Entradas",
            nature=FinancialNature.INCOME,
        )
        .add_category(
            category_id=UUID("00000000-0000-0000-0000-000000000605"),
            name="Saídas",
            nature=FinancialNature.EXPENSE,
        )
    )
    expense_category_id = UUID("00000000-0000-0000-0000-000000000605")
    first = ResourceAccount.open(
        name="Conta A",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("1000.00", "BRL"),
        metadata=metadata(ACCOUNT_ID, day=1),
    )
    second = ResourceAccount.open(
        name="Conta B",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("0.00", "BRL"),
        metadata=metadata(SECOND_ACCOUNT_ID, day=1),
    )
    income_allocation = allocation_for(
        catalog,
        category_id=CATEGORY_ID,
        nature=FinancialNature.INCOME,
        amount="100.00",
        identity_suffix=640,
        day=2,
    )
    income, first = Income.receive(
        account=first,
        allocation=income_allocation,
        catalog=catalog,
        description="Recebimento",
    )
    expense_allocation = allocation_for(
        catalog,
        category_id=expense_category_id,
        nature=FinancialNature.EXPENSE,
        amount="30.00",
        identity_suffix=641,
        day=3,
    )
    cash_expense, first = CashExpense.pay(
        account=first,
        allocation=expense_allocation,
        catalog=catalog,
        description="Compra",
    )
    transfer, first, second = Transfer.execute(
        source=first,
        destination=second,
        amount=Money("20.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000642"), day=4),
        description="Transferência interna",
    )
    bill_payment = BillPayment(
        payable_id=UUID("00000000-0000-0000-0000-000000000643"),
        account_id=ACCOUNT_ID,
        amount=Money("10.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000644"), day=5),
        description="Pagamento de conta",
    )
    invoice_payment = InvoicePayment(
        card_id=CARD_ID,
        invoice_closing_date=date(2026, 9, 10),
        account_id=ACCOUNT_ID,
        amount=Money("15.00", "BRL"),
        applied_amount=Money("15.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000645"), day=6),
        description="Pagamento de fatura",
    )
    refund, _ = AccountExpenseRefund.receive(
        original_expense_id=cash_expense.movement_id,
        account=first,
        amount=Money("5.00", "BRL"),
        metadata=metadata(UUID("00000000-0000-0000-0000-000000000646"), day=7),
    )
    histories = tuple(
        FinancialFactHistory(original=movement)
        for movement in (
            income,
            cash_expense,
            transfer,
            bill_payment,
            invoice_payment,
            refund,
        )
    )
    queries = FinancialQueries(lambda: CashFlowSourceStub(histories))
    period = QueryPeriod(date(2026, 9, 1), date(2026, 9, 30))

    global_flow = queries.realized_cash_flow(period=period, currency="BRL")
    account_flow = queries.realized_cash_flow(
        period=period,
        currency="BRL",
        account_id=ACCOUNT_ID,
    )

    assert global_flow.inflows == Money("105.00", "BRL")
    assert global_flow.outflows == Money("55.00", "BRL")
    assert global_flow.net_change == Money("50.00", "BRL")
    transfer_details = tuple(
        item for item in global_flow.entries if item.record_id == transfer.movement_id
    )
    assert len(transfer_details) == 2
    assert all(not item.included_in_totals for item in transfer_details)
    assert all(item.recorded_at.tzinfo is not None for item in global_flow.entries)
    assert account_flow.inflows == Money("105.00", "BRL")
    assert account_flow.outflows == Money("75.00", "BRL")
    assert account_flow.net_change == Money("30.00", "BRL")
    assert any(
        item.record_id == transfer.movement_id and item.included_in_totals
        for item in account_flow.entries
    )


def test_realized_cash_flow_exposes_the_registration_moment_of_the_current_correction() -> None:
    catalog = CategoryCatalog.empty().add_category(
        category_id=CATEGORY_ID,
        name="Despesas",
        nature=FinancialNature.EXPENSE,
    )
    account = ResourceAccount.open(
        name="Conta",
        account_type=AccountType.CHECKING,
        currency="BRL",
        initial_balance=Money("100.00", "BRL"),
        metadata=metadata(ACCOUNT_ID, day=1),
    )
    original, _ = CashExpense.pay(
        account=account,
        allocation=expense(catalog, "20.00", 650, day=2),
        catalog=catalog,
        description="Despesa original",
    )
    replacement = CashExpense(
        account_id=ACCOUNT_ID,
        allocation=expense(catalog, "25.00", 651, day=2),
        description="Despesa corrigida",
        original_event_id=original.movement_id,
    )
    correction_recorded_at = datetime(2026, 9, 10, 18, tzinfo=UTC)
    correction = FinancialFactCorrected(
        target_id=original.movement_id,
        previous_version_id=original.movement_id,
        replacement=replacement,
        reason="Valor correto",
        metadata=RecordMetadata(
            effective_date=date(2026, 9, 2),
            recorded_at=correction_recorded_at,
            origin=RecordOrigin.MANUAL,
            record_id=UUID("00000000-0000-0000-0000-000000000652"),
        ),
    )
    history = FinancialFactHistory(original=original, events=(correction,))

    report = FinancialQueries(lambda: CashFlowSourceStub((history,))).realized_cash_flow(
        period=QueryPeriod(date(2026, 9, 1), date(2026, 9, 30)),
        currency="BRL",
    )

    assert report.outflows == Money("25.00", "BRL")
    assert report.entries[0].effective_date == date(2026, 9, 2)
    assert report.entries[0].recorded_at == correction_recorded_at


def expense(
    catalog: CategoryCatalog,
    amount: str,
    identity_suffix: int,
    *,
    day: int,
) -> Allocation:
    identity = UUID(f"00000000-0000-0000-0000-{identity_suffix:012d}")
    value = Money(amount, "BRL")
    return Allocation.create(
        total=value,
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(CATEGORY_ID, value),),
        catalog=catalog,
        metadata=metadata(identity, day=day),
    )


def allocation_for(
    catalog: CategoryCatalog,
    *,
    category_id: UUID,
    nature: FinancialNature,
    amount: str,
    identity_suffix: int,
    day: int,
) -> Allocation:
    identity = UUID(f"00000000-0000-0000-0000-{identity_suffix:012d}")
    value = Money(amount, "BRL")
    return Allocation.create(
        total=value,
        nature=nature,
        parts=(AllocationPart(category_id, value),),
        catalog=catalog,
        metadata=metadata(identity, day=day),
    )


def metadata(identity: UUID, *, day: int) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=identity,
    )


def forecast(identity_suffix: int, *, planned_date: date, amount: str) -> PlannedOccurrence:
    identity = UUID(f"00000000-0000-0000-0000-{identity_suffix:012d}")
    return PlannedOccurrence.plan(
        account_id=ACCOUNT_ID,
        direction=ForecastDirection.OUTFLOW,
        expected_amount=Money(amount, "BRL"),
        planned_date=planned_date,
        margin=None,
        description="Previsão",
        metadata=metadata(identity, day=1),
    )
