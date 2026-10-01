from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import date, datetime
from enum import StrEnum
from uuid import UUID

from bot_financeiro.domain.credit_cards import (
    CardCreditApplied,
    CardCreditGranted,
    CreditCard,
    Invoice,
    InvoiceComponentKind,
    InvoiceStatus,
)
from bot_financeiro.domain.financial_changes import AccountExpenseRefund, FinancialFactHistory
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import (
    BillPayment,
    CashExpense,
    FinancialMovement,
    Income,
    InvoicePayment,
    Transfer,
)
from bot_financeiro.domain.payables import (
    FinancialCharge,
    Payable,
    PayableOpened,
    PayableStatus,
)
from bot_financeiro.domain.planning import ForecastDirection, ForecastStatus, PlannedOccurrence

VersionRecordedAt = Callable[[UUID, datetime], datetime]


@dataclass(frozen=True, slots=True)
class DebtComponentView:
    record_id: UUID
    kind: str
    effective_date: date
    recorded_at: datetime
    description: str | None
    contribution: Money


@dataclass(frozen=True, slots=True)
class ExplainedDebtAmount:
    amount: Money
    components: tuple[DebtComponentView, ...]

    def __post_init__(self) -> None:
        total = Money("0", self.amount.currency)
        for component in self.components:
            total += component.contribution
        if total != self.amount:
            raise ValueError("explained debt amount must equal its components")


def _explained_debt(
    amount: Money,
    components: tuple[DebtComponentView, ...],
) -> ExplainedDebtAmount:
    return ExplainedDebtAmount(amount=amount, components=components)


@dataclass(frozen=True, slots=True)
class InstallmentView:
    purchase_id: UUID
    sequence: int
    total_installments: int
    amount: Money
    description: str | None


@dataclass(frozen=True, slots=True)
class InvoiceComponentView:
    record_id: UUID
    kind: InvoiceComponentKind
    effective_date: date
    recorded_at: datetime
    description: str | None
    principal_effect: Money
    financial_charge_effect: Money
    purchase_id: UUID | None
    installment_sequence: int | None

    @property
    def total_effect(self) -> Money:
        return self.principal_effect + self.financial_charge_effect


@dataclass(frozen=True, slots=True)
class InvoiceView:
    closing_date: date
    due_date: date
    status: InvoiceStatus
    principal_pending: Money
    financial_charges_pending: Money
    amount_due: Money
    components: tuple[InvoiceComponentView, ...]
    installments: tuple[InstallmentView, ...]

    def __post_init__(self) -> None:
        principal = Money("0", self.principal_pending.currency)
        charges = Money("0", self.financial_charges_pending.currency)
        for component in self.components:
            principal += component.principal_effect
            charges += component.financial_charge_effect
        if principal != self.principal_pending or charges != self.financial_charges_pending:
            raise ValueError("invoice view components must reconcile pending balances")
        if self.principal_pending + self.financial_charges_pending != self.amount_due:
            raise ValueError("invoice amount due must reconcile principal and charges")


def _invoice_view(
    card: CreditCard,
    invoice: Invoice,
    on_date: date,
    version_recorded_at: VersionRecordedAt,
) -> InvoiceView:
    components = tuple(
        InvoiceComponentView(
            record_id=component.event_id,
            kind=component.kind,
            effective_date=component.effective_date,
            recorded_at=version_recorded_at(component.event_id, component.recorded_at),
            description=component.description,
            principal_effect=component.principal_effect,
            financial_charge_effect=component.financial_charge_effect,
            purchase_id=component.purchase_id,
            installment_sequence=component.installment_sequence,
        )
        for component in invoice.components
    )
    installments = tuple(
        InstallmentView(
            purchase_id=installment.purchase_id,
            sequence=installment.sequence,
            total_installments=len(card.installments_for_purchase(installment.purchase_id)),
            amount=installment.amount,
            description=card.description_for_purchase(installment.purchase_id),
        )
        for installment in invoice.installments
    )
    return InvoiceView(
        closing_date=invoice.cycle.closing_date,
        due_date=invoice.cycle.due_date,
        status=invoice.status_on(on_date),
        principal_pending=invoice.principal_due,
        financial_charges_pending=invoice.financial_charges_due,
        amount_due=invoice.amount_due,
        components=components,
        installments=installments,
    )


@dataclass(frozen=True, slots=True)
class CreditCardPositionView:
    card_id: UUID
    card_name: str
    currency: str
    on_date: date
    principal_pending: ExplainedDebtAmount
    financial_charges_pending: ExplainedDebtAmount
    net_debt: ExplainedDebtAmount
    available_card_credit: ExplainedDebtAmount
    card_limit: Money | None
    available_limit: Money | None
    invoices: tuple[InvoiceView, ...]


class CreditCardPositionProjector:
    def project(
        self,
        card: CreditCard,
        *,
        on_date: date,
        version_recorded_at: VersionRecordedAt,
    ) -> CreditCardPositionView:
        domain_invoices = card.invoices()
        invoices = tuple(
            _invoice_view(card, invoice, on_date, version_recorded_at)
            for invoice in domain_invoices
        )
        principal_components = tuple(
            DebtComponentView(
                record_id=component.record_id,
                kind=component.kind.value,
                effective_date=component.effective_date,
                recorded_at=component.recorded_at,
                description=component.description,
                contribution=component.principal_effect,
            )
            for invoice in invoices
            for component in invoice.components
            if component.principal_effect.amount != 0
        )
        charge_components = tuple(
            DebtComponentView(
                record_id=component.record_id,
                kind=component.kind.value,
                effective_date=component.effective_date,
                recorded_at=component.recorded_at,
                description=component.description,
                contribution=component.financial_charge_effect,
            )
            for invoice in invoices
            for component in invoice.components
            if component.financial_charge_effect.amount != 0
        )
        net_components = tuple(
            DebtComponentView(
                record_id=component.record_id,
                kind=component.kind.value,
                effective_date=component.effective_date,
                recorded_at=component.recorded_at,
                description=component.description,
                contribution=component.total_effect,
            )
            for invoice in invoices
            for component in invoice.components
            if component.total_effect.amount != 0
        )
        credit_components = tuple(
            DebtComponentView(
                record_id=event.event_id,
                kind=(
                    "card_credit_granted"
                    if isinstance(event, CardCreditGranted)
                    else "card_credit_applied"
                ),
                effective_date=event.metadata.effective_date,
                recorded_at=version_recorded_at(event.event_id, event.metadata.recorded_at),
                description=getattr(event, "description", None),
                contribution=event.credit_effect,
            )
            for event in card.history
            if isinstance(event, (CardCreditGranted, CardCreditApplied))
        )
        principal_amount = Money("0", card.currency)
        charges_amount = Money("0", card.currency)
        for invoice in domain_invoices:
            principal_amount += invoice.principal_due
            charges_amount += invoice.financial_charges_due
        principal = _explained_debt(principal_amount, principal_components)
        charges = _explained_debt(charges_amount, charge_components)
        net_debt = _explained_debt(principal_amount + charges_amount, net_components)
        available_credit = _explained_debt(card.card_credit_balance, credit_components)
        if net_debt.amount != principal.amount + charges.amount:
            raise ValueError("card debt components do not reconcile principal and charges")
        return CreditCardPositionView(
            card_id=card.card_id,
            card_name=card.opening.name,
            currency=card.currency,
            on_date=on_date,
            principal_pending=principal,
            financial_charges_pending=charges,
            net_debt=net_debt,
            available_card_credit=available_credit,
            card_limit=card.opening.limit,
            available_limit=card.available_limit,
            invoices=invoices,
        )


@dataclass(frozen=True, slots=True)
class PayablePositionView:
    payable_id: UUID
    description: str | None
    due_date: date
    status: PayableStatus
    original_amount: Money
    financial_charges: Money
    payments: Money
    amount_due: ExplainedDebtAmount


@dataclass(frozen=True, slots=True)
class PayablesReport:
    on_date: date
    currency: str
    total_due: ExplainedDebtAmount
    payables: tuple[PayablePositionView, ...]


def _payable_view(
    payable: Payable,
    on_date: date,
    version_recorded_at: VersionRecordedAt,
) -> PayablePositionView:
    components: list[DebtComponentView] = []
    charges = Money("0", payable.amount_due.currency)
    payments = Money("0", payable.amount_due.currency)
    for event in payable.history:
        if isinstance(event, PayableOpened):
            kind = "payable_opened"
        elif isinstance(event, FinancialCharge):
            charges += event.amount
            kind = "payable_financial_charge"
        elif isinstance(event, BillPayment):
            payments += event.amount
            kind = "bill_payment"
        else:  # pragma: no cover - closed domain union
            raise TypeError(f"unsupported payable event: {type(event).__name__}")
        components.append(
            DebtComponentView(
                record_id=event.event_id,
                kind=kind,
                effective_date=event.metadata.effective_date,
                recorded_at=version_recorded_at(event.event_id, event.metadata.recorded_at),
                description=getattr(event, "description", None),
                contribution=event.debt_effects[0].amount,
            )
        )
    amount_due = _explained_debt(payable.amount_due, tuple(components))
    return PayablePositionView(
        payable_id=payable.payable_id,
        description=payable.opening.description,
        due_date=payable.opening.due_date,
        status=payable.status_on(on_date),
        original_amount=payable.original_amount,
        financial_charges=charges,
        payments=payments,
        amount_due=amount_due,
    )


class PayablesProjector:
    def project(
        self,
        payables: tuple[Payable, ...],
        *,
        on_date: date,
        currency: str,
        version_recorded_at: VersionRecordedAt,
    ) -> PayablesReport:
        views = tuple(
            sorted(
                (
                    _payable_view(payable, on_date, version_recorded_at)
                    for payable in payables
                    if payable.amount_due.currency == currency and payable.amount_due.amount != 0
                ),
                key=lambda item: (item.due_date, str(item.payable_id)),
            )
        )
        components = tuple(
            component for payable in views for component in payable.amount_due.components
        )
        total_due = Money("0", currency)
        for view in views:
            total_due += view.amount_due.amount
        return PayablesReport(
            on_date=on_date,
            currency=currency,
            total_due=_explained_debt(total_due, components),
            payables=views,
        )


class CommitmentKind(StrEnum):
    PAYABLE = "payable"
    INVOICE = "invoice"


@dataclass(frozen=True, slots=True)
class DebtCommitmentView:
    kind: CommitmentKind
    owner_id: UUID
    description: str | None
    due_date: date
    status: PayableStatus | InvoiceStatus
    amount: Money
    components: tuple[DebtComponentView, ...]
    installments: tuple[InstallmentView, ...] = ()

    def __post_init__(self) -> None:
        _explained_debt(self.amount, self.components)


@dataclass(frozen=True, slots=True)
class ForecastCommitmentView:
    occurrence_id: UUID
    account_id: UUID
    description: str | None
    planned_date: date
    recorded_at: datetime
    status: ForecastStatus
    direction: ForecastDirection
    expected_amount: Money


@dataclass(frozen=True, slots=True)
class CommitmentsReport:
    as_of: date
    through_date: date
    currency: str
    recognized_debt_total: Money
    forecast_outflow_total: Money
    recognized_debts: tuple[DebtCommitmentView, ...]
    forecast_occurrences: tuple[ForecastCommitmentView, ...]

    def __post_init__(self) -> None:
        recognized = Money("0", self.currency)
        for item in self.recognized_debts:
            recognized += item.amount
        forecasts = Money("0", self.currency)
        for item in self.forecast_occurrences:
            if item.direction is ForecastDirection.OUTFLOW:
                forecasts += item.expected_amount
        if recognized != self.recognized_debt_total:
            raise ValueError("recognized commitments must reconcile their total")
        if forecasts != self.forecast_outflow_total:
            raise ValueError("forecast commitments must reconcile their outflow total")


class CommitmentsProjector:
    def project(
        self,
        *,
        cards: tuple[CreditCard, ...],
        payables: tuple[Payable, ...],
        occurrences: tuple[PlannedOccurrence, ...],
        as_of: date,
        through_date: date,
        currency: str,
        version_recorded_at: VersionRecordedAt,
    ) -> CommitmentsReport:
        if through_date < as_of:
            raise ValueError("commitment horizon cannot precede its reference date")
        debts = [
            DebtCommitmentView(
                kind=CommitmentKind.PAYABLE,
                owner_id=payable.payable_id,
                description=payable.opening.description,
                due_date=payable.opening.due_date,
                status=payable.status_on(as_of),
                amount=payable.amount_due,
                components=_payable_view(
                    payable,
                    as_of,
                    version_recorded_at,
                ).amount_due.components,
            )
            for payable in payables
            if payable.amount_due.currency == currency
            and payable.amount_due.amount != 0
            and payable.opening.due_date <= through_date
        ]
        for card in cards:
            if card.currency != currency:
                continue
            for invoice in card.invoices():
                if invoice.amount_due.amount == 0 or invoice.cycle.due_date > through_date:
                    continue
                invoice_view = _invoice_view(
                    card,
                    invoice,
                    as_of,
                    version_recorded_at,
                )
                debts.append(
                    DebtCommitmentView(
                        kind=CommitmentKind.INVOICE,
                        owner_id=card.card_id,
                        description=card.opening.name,
                        due_date=invoice.cycle.due_date,
                        status=invoice.status_on(as_of),
                        amount=invoice.amount_due,
                        components=tuple(
                            DebtComponentView(
                                record_id=component.record_id,
                                kind=component.kind.value,
                                effective_date=component.effective_date,
                                recorded_at=component.recorded_at,
                                description=component.description,
                                contribution=component.total_effect,
                            )
                            for component in invoice_view.components
                            if component.total_effect.amount != 0
                        ),
                        installments=invoice_view.installments,
                    )
                )
        debt_views = tuple(
            sorted(debts, key=lambda item: (item.due_date, item.kind.value, str(item.owner_id)))
        )
        forecast_views = tuple(
            sorted(
                (
                    ForecastCommitmentView(
                        occurrence_id=occurrence.occurrence_id,
                        account_id=occurrence.account_id,
                        description=occurrence.description,
                        planned_date=occurrence.planned_date,
                        recorded_at=occurrence.history[-1].metadata.recorded_at,
                        status=occurrence.status_on(as_of),
                        direction=occurrence.direction,
                        expected_amount=occurrence.expected_amount,
                    )
                    for occurrence in occurrences
                    if occurrence.expected_amount.currency == currency
                    and occurrence.confirmation is None
                    and occurrence.planned_date <= through_date
                ),
                key=lambda item: (item.planned_date, str(item.occurrence_id)),
            )
        )
        debt_total = Money("0", currency)
        for item in debt_views:
            debt_total += item.amount
        forecast_total = Money("0", currency)
        for item in forecast_views:
            if item.direction is ForecastDirection.OUTFLOW:
                forecast_total += item.expected_amount
        return CommitmentsReport(
            as_of=as_of,
            through_date=through_date,
            currency=currency,
            recognized_debt_total=debt_total,
            forecast_outflow_total=forecast_total,
            recognized_debts=debt_views,
            forecast_occurrences=forecast_views,
        )


class CashFlowKind(StrEnum):
    INCOME = "income"
    CASH_EXPENSE = "cash_expense"
    TRANSFER = "transfer"
    BILL_PAYMENT = "bill_payment"
    INVOICE_PAYMENT = "invoice_payment"
    ACCOUNT_EXPENSE_REFUND = "account_expense_refund"


@dataclass(frozen=True, slots=True)
class CashFlowEntryView:
    record_id: UUID
    account_id: UUID
    kind: CashFlowKind
    effective_date: date
    recorded_at: datetime
    description: str | None
    amount: Money
    included_in_totals: bool


@dataclass(frozen=True, slots=True)
class RealizedCashFlowReport:
    start_date: date
    end_date: date
    currency: str
    account_id: UUID | None
    inflows: Money
    outflows: Money
    net_change: Money
    entries: tuple[CashFlowEntryView, ...]

    def __post_init__(self) -> None:
        inflows = Money("0", self.currency)
        outflows = Money("0", self.currency)
        for entry in self.entries:
            if not entry.included_in_totals:
                continue
            if entry.amount.amount >= 0:
                inflows += entry.amount
            else:
                outflows += Money(-entry.amount.amount, entry.amount.currency)
        if inflows != self.inflows or outflows != self.outflows:
            raise ValueError("cash flow entries must reconcile inflows and outflows")
        if inflows + Money(-outflows.amount, outflows.currency) != self.net_change:
            raise ValueError("cash flow net change must reconcile its totals")


class RealizedCashFlowProjector:
    def project(
        self,
        histories: tuple[FinancialFactHistory[FinancialMovement], ...],
        *,
        start_date: date,
        end_date: date,
        currency: str,
        account_id: UUID | None,
        description_for: Callable[[FinancialMovement], str | None],
    ) -> RealizedCashFlowReport:
        entries: list[CashFlowEntryView] = []
        for history in histories:
            current = history.current
            if current is None or not (start_date <= current.metadata.effective_date <= end_date):
                continue
            for effect in current.account_effects:
                if effect.amount.currency != currency:
                    continue
                if account_id is not None and effect.account_id != account_id:
                    continue
                entries.append(
                    CashFlowEntryView(
                        record_id=history.target_id,
                        account_id=effect.account_id,
                        kind=self._kind(current),
                        effective_date=current.metadata.effective_date,
                        recorded_at=(
                            history.events[-1].metadata.recorded_at
                            if history.events
                            else history.original.metadata.recorded_at
                        ),
                        description=description_for(current),
                        amount=effect.amount,
                        included_in_totals=(
                            account_id is not None or not isinstance(current, Transfer)
                        ),
                    )
                )
        ordered = tuple(
            sorted(
                entries,
                key=lambda item: (
                    item.effective_date,
                    str(item.record_id),
                    str(item.account_id),
                ),
            )
        )
        inflows = Money("0", currency)
        outflows = Money("0", currency)
        for entry in ordered:
            if not entry.included_in_totals:
                continue
            if entry.amount.amount >= 0:
                inflows += entry.amount
            else:
                outflows += Money(-entry.amount.amount, currency)
        return RealizedCashFlowReport(
            start_date=start_date,
            end_date=end_date,
            currency=currency,
            account_id=account_id,
            inflows=inflows,
            outflows=outflows,
            net_change=Money(inflows.amount - outflows.amount, currency),
            entries=ordered,
        )

    @staticmethod
    def _kind(movement: FinancialMovement) -> CashFlowKind:
        kinds = (
            (Income, CashFlowKind.INCOME),
            (CashExpense, CashFlowKind.CASH_EXPENSE),
            (Transfer, CashFlowKind.TRANSFER),
            (BillPayment, CashFlowKind.BILL_PAYMENT),
            (InvoicePayment, CashFlowKind.INVOICE_PAYMENT),
            (AccountExpenseRefund, CashFlowKind.ACCOUNT_EXPENSE_REFUND),
        )
        for movement_type, kind in kinds:
            if isinstance(movement, movement_type):
                return kind
        raise TypeError(f"unsupported realized movement: {type(movement).__name__}")
