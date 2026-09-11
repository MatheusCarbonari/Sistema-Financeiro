from __future__ import annotations

from calendar import monthrange
from dataclasses import dataclass, replace
from datetime import date
from enum import StrEnum
from uuid import NAMESPACE_URL, UUID, uuid5

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.categories import Allocation, CategoryCatalog, FinancialNature
from bot_financeiro.domain.money import CurrencyMismatchError, Money
from bot_financeiro.domain.movements import InvoicePayment
from bot_financeiro.domain.resource_account import AccountHistoryEntryKind, ResourceAccount


class InvoiceStatus(StrEnum):
    OPEN = "open"
    CLOSED = "closed"
    PARTIALLY_PAID = "partially_paid"
    PAID = "paid"
    OVERDUE = "overdue"


@dataclass(frozen=True, slots=True)
class InvoiceCycle:
    closing_date: date
    due_date: date


@dataclass(frozen=True, slots=True)
class CreditCardOpened:
    name: str
    currency: str
    closing_day: int
    due_day: int
    limit: Money | None
    metadata: RecordMetadata

    @property
    def event_id(self) -> UUID:
        return self.metadata.record_id


@dataclass(frozen=True, slots=True)
class CardPurchase:
    card_id: UUID
    allocation: Allocation
    invoice_closing_date: date

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("card purchase requires an expense allocation")
        if self.allocation.total.amount <= 0:
            raise ValueError("card purchase amount must be positive")

    @property
    def purchase_id(self) -> UUID:
        return self.allocation.allocation_id

    @property
    def event_id(self) -> UUID:
        return self.purchase_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def is_expense(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class Installment:
    purchase_id: UUID
    sequence: int
    amount: Money
    cycle: InvoiceCycle

    def __post_init__(self) -> None:
        if self.sequence < 1:
            raise ValueError("installment sequence must be positive")
        if self.amount.amount <= 0:
            raise ValueError("installment amount must be positive")


@dataclass(frozen=True, slots=True)
class InstallmentPurchase:
    card_id: UUID
    allocation: Allocation
    installments: tuple[Installment, ...]

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("installment purchase requires an expense allocation")
        if self.allocation.total.amount <= 0:
            raise ValueError("installment purchase amount must be positive")
        if not self.installments:
            raise ValueError("installment purchase requires installments")
        if tuple(item.sequence for item in self.installments) != tuple(
            range(1, len(self.installments) + 1)
        ):
            raise ValueError("installment sequence must be continuous")
        if any(item.purchase_id != self.purchase_id for item in self.installments):
            raise ValueError("installment belongs to another purchase")
        total = Money("0", self.amount.currency)
        for installment in self.installments:
            total += installment.amount
        if total != self.amount:
            raise ValueError("installments must preserve the purchase total")

    @property
    def purchase_id(self) -> UUID:
        return self.allocation.allocation_id

    @property
    def event_id(self) -> UUID:
        return self.purchase_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def is_expense(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class InstallmentPurchaseCorrected:
    card_id: UUID
    purchase_id: UUID
    corrected_allocation: Allocation
    previous_installments: tuple[Installment, ...]
    frozen_installments: tuple[Installment, ...]
    replacement_installments: tuple[Installment, ...]
    reason: str
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if self.corrected_allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("installment purchase correction requires an expense allocation")
        if self.corrected_allocation.total.amount <= 0:
            raise ValueError("corrected installment purchase amount must be positive")
        if not self.previous_installments:
            raise ValueError("installment purchase correction requires open installments")
        if not self.reason.strip():
            raise ValueError("installment purchase correction requires a reason")
        if any(
            item.purchase_id != self.purchase_id
            for item in (
                *self.previous_installments,
                *self.frozen_installments,
                *self.replacement_installments,
            )
        ):
            raise ValueError("corrected installment belongs to another purchase")

    @property
    def event_id(self) -> UUID:
        return self.metadata.record_id

    @property
    def is_expense(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class InvoiceAssignmentCorrected:
    card_id: UUID
    purchase_id: UUID
    previous_closing_date: date
    target_closing_date: date
    reason: str
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if not self.reason.strip():
            raise ValueError("invoice assignment correction requires a reason")
        if self.previous_closing_date == self.target_closing_date:
            raise ValueError("invoice assignment correction must change the invoice")

    @property
    def event_id(self) -> UUID:
        return self.metadata.record_id


@dataclass(frozen=True, slots=True)
class InvoiceFinancialCharge:
    card_id: UUID
    invoice_closing_date: date
    allocation: Allocation

    def __post_init__(self) -> None:
        if self.allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("invoice financial charge requires an expense allocation")
        if self.allocation.total.amount <= 0:
            raise ValueError("invoice financial charge must be positive")

    @property
    def event_id(self) -> UUID:
        return self.allocation.allocation_id

    @property
    def amount(self) -> Money:
        return self.allocation.total

    @property
    def is_expense(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class CardRefund:
    card_id: UUID
    purchase_id: UUID
    amount: Money
    principal_reduction: Money
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if self.amount.amount <= 0:
            raise ValueError("card refund amount must be positive")
        if self.principal_reduction.currency != self.amount.currency:
            raise CurrencyMismatchError("card refund values must use the same currency")
        if self.principal_reduction.amount < 0 or self.principal_reduction > self.amount:
            raise ValueError("card refund principal reduction is invalid")

    @property
    def event_id(self) -> UUID:
        return self.metadata.record_id

    @property
    def is_income(self) -> bool:
        return False

    @property
    def reduces_expense(self) -> bool:
        return True


@dataclass(frozen=True, slots=True)
class CardCreditGranted:
    card_id: UUID
    amount: Money
    source_event_id: UUID
    reason: str
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if self.amount.amount <= 0:
            raise ValueError("card credit amount must be positive")
        if not self.reason.strip():
            raise ValueError("card credit requires a reason")

    @property
    def event_id(self) -> UUID:
        return self.metadata.record_id

    @property
    def is_income(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class CardCreditApplied:
    card_id: UUID
    invoice_closing_date: date
    amount: Money
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if self.amount.amount <= 0:
            raise ValueError("card credit application must be positive")

    @property
    def event_id(self) -> UUID:
        return self.metadata.record_id

    @property
    def is_income(self) -> bool:
        return False


@dataclass(frozen=True, slots=True)
class Invoice:
    card_id: UUID
    cycle: InvoiceCycle
    principal_due: Money
    financial_charges_due: Money
    has_settlement: bool = False
    installments: tuple[Installment, ...] = ()

    @property
    def amount_due(self) -> Money:
        return self.principal_due + self.financial_charges_due

    def status_on(self, on_date: date) -> InvoiceStatus:
        if self.amount_due.amount == 0:
            return InvoiceStatus.PAID
        if on_date > self.cycle.due_date:
            return InvoiceStatus.OVERDUE
        if self.has_settlement:
            return InvoiceStatus.PARTIALLY_PAID
        if on_date > self.cycle.closing_date:
            return InvoiceStatus.CLOSED
        return InvoiceStatus.OPEN


@dataclass(frozen=True, slots=True)
class CardCashFlow:
    scheduled_installments: tuple[Installment, ...]
    realized_payments: tuple[InvoicePayment, ...]


CreditCardEvent = (
    CreditCardOpened
    | CardPurchase
    | InstallmentPurchase
    | InstallmentPurchaseCorrected
    | InvoiceAssignmentCorrected
    | InvoiceFinancialCharge
    | InvoicePayment
    | CardRefund
    | CardCreditGranted
    | CardCreditApplied
)


@dataclass(frozen=True, slots=True)
class CreditCard:
    history: tuple[CreditCardEvent, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.history, tuple) or not self.history:
            raise ValueError("credit card history must be a non-empty immutable tuple")
        if not isinstance(self.history[0], CreditCardOpened):
            raise ValueError("credit card history must begin with its opening")
        opening = self.opening
        if not opening.name.strip():
            raise ValueError("credit card name is required")
        if not 1 <= opening.closing_day <= 31 or not 1 <= opening.due_day <= 31:
            raise ValueError("billing days must be between 1 and 31")
        if opening.limit is not None:
            self._require_currency(opening.limit)
            if opening.limit.amount <= 0:
                raise ValueError("credit card limit must be positive")
        seen_ids: set[UUID] = set()
        for event_index, event in enumerate(self.history):
            if event.event_id in seen_ids:
                raise ValueError("credit card event identities must be unique")
            seen_ids.add(event.event_id)
            if isinstance(event, CardPurchase):
                if event.card_id != self.card_id:
                    raise ValueError("credit card event belongs to another card")
                self._require_currency(event.amount)
                expected_cycle = self.cycle_for(event.allocation.metadata.effective_date)
                if event.invoice_closing_date != expected_cycle.closing_date:
                    raise ValueError("card purchase must start in its calculated invoice")
            elif isinstance(event, InstallmentPurchase):
                if event.card_id != self.card_id:
                    raise ValueError("credit card event belongs to another card")
                self._require_currency(event.amount)
                expected_cycle = self.cycle_for(event.allocation.metadata.effective_date)
                if event.installments[0].cycle != expected_cycle:
                    raise ValueError("installment purchase must start in its calculated invoice")
                expected_cycles = self._installment_cycles(
                    expected_cycle, len(event.installments)
                )
                if tuple(item.cycle for item in event.installments) != expected_cycles:
                    raise ValueError("installments must use consecutive invoice cycles")
            elif isinstance(event, InstallmentPurchaseCorrected):
                if event.card_id != self.card_id:
                    raise ValueError("credit card event belongs to another card")
                self._require_currency(event.corrected_allocation.total)
                before = type(self)(history=self.history[:event_index])
                before._validate_installment_correction(event)
            elif isinstance(event, InvoiceAssignmentCorrected):
                if event.card_id != self.card_id:
                    raise ValueError("credit card event belongs to another card")
                self._purchase(event.purchase_id)
                expected_closing = _day_in_month(
                    event.target_closing_date.year,
                    event.target_closing_date.month,
                    self.opening.closing_day,
                )
                if event.target_closing_date != expected_closing:
                    raise ValueError("invoice correction target must be a closing date")
            elif isinstance(event, InvoicePayment):
                if event.card_id != self.card_id:
                    raise ValueError("credit card event belongs to another card")
                self._require_currency(event.amount)
                self._require_currency(event.applied_amount)
            elif isinstance(
                event,
                (InvoiceFinancialCharge, CardRefund, CardCreditGranted, CardCreditApplied),
            ):
                if event.card_id != self.card_id:
                    raise ValueError("credit card event belongs to another card")
                self._require_currency(event.amount)
        for invoice in self.invoices():
            _ = invoice.amount_due
        self._validate_limit_history()

    @classmethod
    def open(
        cls,
        *,
        name: str,
        currency: str,
        closing_day: int,
        due_day: int,
        limit: Money | None,
        metadata: RecordMetadata,
    ) -> CreditCard:
        return cls(
            history=(
                CreditCardOpened(
                    name=name.strip(),
                    currency=currency,
                    closing_day=closing_day,
                    due_day=due_day,
                    limit=limit,
                    metadata=metadata,
                ),
            )
        )

    @property
    def opening(self) -> CreditCardOpened:
        opening = self.history[0]
        if not isinstance(opening, CreditCardOpened):
            raise ValueError("credit card history must begin with its opening")
        return opening

    @property
    def card_id(self) -> UUID:
        return self.opening.event_id

    @property
    def currency(self) -> str:
        return self.opening.currency

    @property
    def available_limit(self) -> Money | None:
        if self.opening.limit is None:
            return None
        pending = Money("0", self.currency)
        for invoice in self.invoices():
            pending += invoice.principal_due
        return Money(self.opening.limit.amount - pending.amount, self.currency)

    @property
    def card_credit_balance(self) -> Money:
        balance = Money("0", self.currency)
        for event in self.history:
            if isinstance(event, CardCreditGranted):
                balance += event.amount
            elif isinstance(event, CardCreditApplied):
                balance = Money(balance.amount - event.amount.amount, balance.currency)
                if balance.amount < 0:
                    raise ValueError("card credit application exceeds available credit")
        return balance

    def purchase(
        self,
        *,
        allocation: Allocation,
        catalog: CategoryCatalog,
    ) -> tuple[CardPurchase, CreditCard]:
        self._validate_purchase_allocation(
            allocation=allocation,
            catalog=catalog,
            operation_name="card purchase",
        )
        cycle = self.cycle_for(allocation.metadata.effective_date)
        purchase = CardPurchase(
            card_id=self.card_id,
            allocation=allocation,
            invoice_closing_date=cycle.closing_date,
        )
        return purchase, replace(self, history=(*self.history, purchase))

    def purchase_in_installments(
        self,
        *,
        allocation: Allocation,
        installment_count: int,
        catalog: CategoryCatalog,
    ) -> tuple[InstallmentPurchase, CreditCard]:
        self._validate_purchase_allocation(
            allocation=allocation,
            catalog=catalog,
            operation_name="installment purchase",
        )
        amounts = allocation.total.split_equally(installment_count)
        if any(amount.amount <= 0 for amount in amounts):
            raise ValueError("each installment must be positive")
        cycles = self._installment_cycles(
            self.cycle_for(allocation.metadata.effective_date), installment_count
        )
        purchase = InstallmentPurchase(
            card_id=self.card_id,
            allocation=allocation,
            installments=tuple(
                Installment(
                    purchase_id=allocation.allocation_id,
                    sequence=index,
                    amount=amount,
                    cycle=cycle,
                )
                for index, (amount, cycle) in enumerate(
                    zip(amounts, cycles, strict=True), start=1
                )
            ),
        )
        return purchase, replace(self, history=(*self.history, purchase))

    def correct_installment_purchase(
        self,
        *,
        purchase_id: UUID,
        corrected_allocation: Allocation,
        remaining_installment_count: int,
        reason: str,
        metadata: RecordMetadata,
        catalog: CategoryCatalog,
    ) -> tuple[InstallmentPurchaseCorrected, CreditCard]:
        self._installment_purchase(purchase_id)
        corrected_allocation.ensure_categories_compatible(catalog, require_active=True)
        if corrected_allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("installment purchase correction requires an expense allocation")
        self._require_currency(corrected_allocation.total)
        current, previous_open, frozen = self._installment_partition_for_correction(
            purchase_id=purchase_id,
            on_date=metadata.effective_date,
        )
        frozen_total = self._installment_total(frozen)
        if corrected_allocation.total < frozen_total:
            raise ValueError("corrected total cannot be lower than frozen installments")
        remaining = Money(
            corrected_allocation.total.amount - frozen_total.amount,
            self.currency,
        )
        if remaining.amount == 0:
            if remaining_installment_count != 0:
                raise ValueError(
                    "zero remaining amount requires zero replacement installments"
                )
            amounts: tuple[Money, ...] = ()
        else:
            amounts = remaining.split_equally(remaining_installment_count)
        if any(amount.amount <= 0 for amount in amounts):
            raise ValueError("each replacement installment must be positive")
        cycles = self._replacement_cycles(
            current=current,
            frozen=frozen,
            count=remaining_installment_count,
            on_date=metadata.effective_date,
        )
        next_sequence = max(item.sequence for item in current) + 1
        replacement = tuple(
            Installment(
                purchase_id=purchase_id,
                sequence=next_sequence + index,
                amount=amount,
                cycle=cycle,
            )
            for index, (amount, cycle) in enumerate(zip(amounts, cycles, strict=True))
        )
        correction = InstallmentPurchaseCorrected(
            card_id=self.card_id,
            purchase_id=purchase_id,
            corrected_allocation=corrected_allocation,
            previous_installments=previous_open,
            frozen_installments=frozen,
            replacement_installments=replacement,
            reason=reason.strip(),
            metadata=metadata,
        )
        updated = replace(self, history=(*self.history, correction))
        return correction, updated

    def correct_invoice_assignment(
        self,
        *,
        purchase_id: UUID,
        target_closing_date: date,
        reason: str,
        metadata: RecordMetadata,
    ) -> tuple[InvoiceAssignmentCorrected, CreditCard]:
        purchase = self._purchase(purchase_id)
        current_closing_date = self._purchase_closing_date(purchase)
        expected_closing_date = _day_in_month(
            target_closing_date.year,
            target_closing_date.month,
            self.opening.closing_day,
        )
        if target_closing_date != expected_closing_date:
            raise ValueError("target date is not a valid invoice closing date")
        correction = InvoiceAssignmentCorrected(
            card_id=self.card_id,
            purchase_id=purchase_id,
            previous_closing_date=current_closing_date,
            target_closing_date=target_closing_date,
            reason=reason.strip(),
            metadata=metadata,
        )
        return correction, replace(self, history=(*self.history, correction))

    def pay_invoice(
        self,
        *,
        closing_date: date,
        account: ResourceAccount,
        amount: Money,
        metadata: RecordMetadata,
        create_credit_from_excess: bool = False,
    ) -> tuple[InvoicePayment, CreditCard, ResourceAccount]:
        self._require_currency(amount)
        invoice = self.invoice(closing_date)
        if amount > invoice.amount_due and not create_credit_from_excess:
            raise ValueError("payment above invoice debt requires explicit card credit")
        applied_amount = (
            invoice.amount_due if amount > invoice.amount_due else amount
        )
        payment = InvoicePayment(
            card_id=self.card_id,
            invoice_closing_date=closing_date,
            account_id=account.account_id,
            amount=amount,
            applied_amount=applied_amount,
            metadata=metadata,
        )
        updated_account = account.record_movement(
            kind=AccountHistoryEntryKind.INVOICE_PAYMENT,
            difference=Money(-amount.amount, amount.currency),
            movement_metadata=metadata,
        )
        new_events: tuple[CreditCardEvent, ...] = (payment,)
        if amount > invoice.amount_due:
            credit_amount = Money(
                amount.amount - invoice.amount_due.amount,
                self.currency,
            )
            new_events = (
                payment,
                self._credit_granted(
                    amount=credit_amount,
                    source_event_id=payment.event_id,
                    reason="invoice payment excess",
                    metadata=metadata,
                ),
            )
        return (
            payment,
            replace(self, history=(*self.history, *new_events)),
            updated_account,
        )

    def add_financial_charge(
        self,
        *,
        closing_date: date,
        allocation: Allocation,
        catalog: CategoryCatalog,
    ) -> tuple[InvoiceFinancialCharge, CreditCard]:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        if allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError("invoice financial charge requires an expense allocation")
        self._require_currency(allocation.total)
        if allocation.total.amount <= 0:
            raise ValueError("invoice financial charge must be positive")
        charge = InvoiceFinancialCharge(
            card_id=self.card_id,
            invoice_closing_date=closing_date,
            allocation=allocation,
        )
        return charge, replace(self, history=(*self.history, charge))

    def refund_purchase(
        self,
        *,
        purchase_id: UUID,
        amount: Money,
        metadata: RecordMetadata,
    ) -> tuple[CardRefund, CreditCard]:
        purchase = self._purchase(purchase_id)
        self._require_currency(amount)
        if amount.amount <= 0:
            raise ValueError("card refund amount must be positive")
        already_refunded = Money("0", self.currency)
        for event in self.history:
            if isinstance(event, CardRefund) and event.purchase_id == purchase_id:
                already_refunded += event.amount
        if already_refunded + amount > purchase.amount:
            raise ValueError("card refund cannot exceed purchase amount")
        closing_date = self._purchase_closing_date(purchase)
        principal_due = self.invoice(closing_date).principal_due
        principal_reduction = Money(
            min(principal_due.amount, amount.amount),
            self.currency,
        )
        refund = CardRefund(
            card_id=self.card_id,
            purchase_id=purchase_id,
            amount=amount,
            principal_reduction=principal_reduction,
            metadata=metadata,
        )
        new_events: tuple[CreditCardEvent, ...] = (refund,)
        if amount > principal_reduction:
            credit_amount = Money(
                amount.amount - principal_reduction.amount,
                self.currency,
            )
            new_events = (
                refund,
                self._credit_granted(
                    amount=credit_amount,
                    source_event_id=refund.event_id,
                    reason="card refund excess",
                    metadata=metadata,
                ),
            )
        return refund, replace(self, history=(*self.history, *new_events))

    def grant_card_credit(
        self,
        *,
        amount: Money,
        reason: str,
        metadata: RecordMetadata,
    ) -> tuple[CardCreditGranted, CreditCard]:
        self._require_currency(amount)
        if amount.amount <= 0:
            raise ValueError("card credit amount must be positive")
        if not reason.strip():
            raise ValueError("card credit requires a reason")
        credit = CardCreditGranted(
            card_id=self.card_id,
            amount=amount,
            source_event_id=metadata.record_id,
            reason=reason.strip(),
            metadata=metadata,
        )
        return credit, replace(self, history=(*self.history, credit))

    def apply_card_credit(
        self,
        *,
        closing_date: date,
        amount: Money,
        metadata: RecordMetadata,
    ) -> tuple[CardCreditApplied, CreditCard]:
        self._require_currency(amount)
        if amount.amount <= 0:
            raise ValueError("card credit application must be positive")
        if amount > self.card_credit_balance:
            raise ValueError("card credit application exceeds available credit")
        if amount > self.invoice(closing_date).amount_due:
            raise ValueError("card credit application exceeds invoice debt")
        applied = CardCreditApplied(
            card_id=self.card_id,
            invoice_closing_date=closing_date,
            amount=amount,
            metadata=metadata,
        )
        return applied, replace(self, history=(*self.history, applied))

    def ensure_append_only_successor_of(self, previous: CreditCard) -> None:
        size = len(previous.history)
        if (
            self.card_id != previous.card_id
            or len(self.history) < size
            or self.history[:size] != previous.history
        ):
            raise ValueError("persisted credit card history is immutable")

    def cycle_for(self, purchase_date: date) -> InvoiceCycle:
        closing_date = _day_in_month(
            purchase_date.year,
            purchase_date.month,
            self.opening.closing_day,
        )
        if purchase_date > closing_date:
            year, month = _next_month(purchase_date.year, purchase_date.month)
            closing_date = _day_in_month(year, month, self.opening.closing_day)
        due_date = _day_in_month(
            closing_date.year,
            closing_date.month,
            self.opening.due_day,
        )
        if due_date <= closing_date:
            year, month = _next_month(closing_date.year, closing_date.month)
            due_date = _day_in_month(year, month, self.opening.due_day)
        return InvoiceCycle(closing_date=closing_date, due_date=due_date)

    def invoice_for_purchase(self, purchase_id: UUID) -> Invoice:
        purchase = self._purchase(purchase_id)
        return self.invoice(self._purchase_closing_date(purchase))

    def invoice(
        self,
        closing_date: date,
        *,
        through_effective_date: date | None = None,
    ) -> Invoice:
        principal = Money("0", self.currency)
        charges = Money("0", self.currency)
        has_settlement = False
        installments: list[Installment] = []
        for purchase in (
            event for event in self.history if isinstance(event, InstallmentPurchase)
        ):
            for installment in self.installments_for_purchase(
                purchase.purchase_id,
                through_effective_date=through_effective_date,
            ):
                if installment.cycle.closing_date == closing_date:
                    principal += installment.amount
                    installments.append(installment)
        for event in self.history:
            if (
                through_effective_date is not None
                and self._event_effective_date(event) > through_effective_date
            ):
                continue
            if (
                isinstance(event, CardPurchase)
                and self._purchase_closing_date(event) == closing_date
            ):
                principal += event.amount
            elif (
                isinstance(event, CardRefund)
                and self._purchase_closing_date(self._purchase(event.purchase_id))
                == closing_date
            ):
                if event.principal_reduction > principal:
                    raise ValueError("card refund exceeds principal due")
                principal = Money(
                    principal.amount - event.principal_reduction.amount,
                    principal.currency,
                )
            elif (
                isinstance(event, InvoiceFinancialCharge)
                and event.invoice_closing_date == closing_date
            ):
                charges += event.amount
            elif (
                isinstance(event, (InvoicePayment, CardCreditApplied))
                and event.invoice_closing_date == closing_date
            ):
                amount = (
                    event.applied_amount
                    if isinstance(event, InvoicePayment)
                    else event.amount
                )
                principal, charges = _apply_settlement(
                    principal,
                    charges,
                    amount,
                )
                has_settlement = True
        cycle = self.cycle_for(closing_date)
        if cycle.closing_date != closing_date:
            cycle = InvoiceCycle(
                closing_date=closing_date,
                due_date=self._due_date_after(closing_date),
            )
        return Invoice(
            card_id=self.card_id,
            cycle=cycle,
            principal_due=principal,
            financial_charges_due=charges,
            has_settlement=has_settlement,
            installments=tuple(installments),
        )

    def invoices(self) -> tuple[Invoice, ...]:
        closing_dates = sorted(
            {
                self._purchase_closing_date(event)
                for event in self.history
                if isinstance(event, CardPurchase)
            }
            | {
                installment.cycle.closing_date
                for event in self.history
                if isinstance(event, InstallmentPurchase)
                for installment in self.installments_for_purchase(event.purchase_id)
            }
            | {
                event.invoice_closing_date
                for event in self.history
                if isinstance(event, InvoicePayment)
            }
            | {
                event.invoice_closing_date
                for event in self.history
                if isinstance(event, (InvoiceFinancialCharge, CardCreditApplied))
            }
        )
        return tuple(self.invoice(closing_date) for closing_date in closing_dates)

    def cash_flow(self, *, start_date: date, end_date: date) -> CardCashFlow:
        if end_date < start_date:
            raise ValueError("cash flow end date cannot precede its start date")
        scheduled = tuple(
            sorted(
                (
                    installment
                    for event in self.history
                    if isinstance(event, InstallmentPurchase)
                    for installment in self.installments_for_purchase(event.purchase_id)
                    if start_date <= installment.cycle.due_date <= end_date
                ),
                key=lambda item: (
                    item.cycle.due_date,
                    str(item.purchase_id),
                    item.sequence,
                ),
            )
        )
        realized = tuple(
            sorted(
                (
                    event
                    for event in self.history
                    if isinstance(event, InvoicePayment)
                    and start_date <= event.metadata.effective_date <= end_date
                ),
                key=lambda event: (
                    event.metadata.effective_date,
                    event.metadata.recorded_at,
                    str(event.event_id),
                ),
            )
        )
        return CardCashFlow(
            scheduled_installments=scheduled,
            realized_payments=realized,
        )

    def _purchase(self, purchase_id: UUID) -> CardPurchase:
        purchase = next(
            (
                event
                for event in self.history
                if isinstance(event, CardPurchase) and event.purchase_id == purchase_id
            ),
            None,
        )
        if purchase is None:
            raise LookupError(f"card purchase {purchase_id} does not exist")
        return purchase

    def _installment_purchase(self, purchase_id: UUID) -> InstallmentPurchase:
        purchase = next(
            (
                event
                for event in self.history
                if isinstance(event, InstallmentPurchase)
                and event.purchase_id == purchase_id
            ),
            None,
        )
        if purchase is None:
            raise LookupError(f"installment purchase {purchase_id} does not exist")
        return purchase

    def installments_for_purchase(
        self,
        purchase_id: UUID,
        *,
        through_effective_date: date | None = None,
    ) -> tuple[Installment, ...]:
        current = list(self._installment_purchase(purchase_id).installments)
        for event in self.history:
            if (
                isinstance(event, InstallmentPurchaseCorrected)
                and event.purchase_id == purchase_id
                and (
                    through_effective_date is None
                    or event.metadata.effective_date <= through_effective_date
                )
            ):
                current = [*event.frozen_installments, *event.replacement_installments]
        return tuple(sorted(current, key=lambda item: item.cycle.closing_date))

    def expense_for_purchase(self, purchase_id: UUID) -> Allocation:
        allocation = self._installment_purchase(purchase_id).allocation
        for event in self.history:
            if (
                isinstance(event, InstallmentPurchaseCorrected)
                and event.purchase_id == purchase_id
            ):
                allocation = event.corrected_allocation
        return allocation

    def _purchase_closing_date(self, purchase: CardPurchase) -> date:
        closing_date = purchase.invoice_closing_date
        for event in self.history:
            if (
                isinstance(event, InvoiceAssignmentCorrected)
                and event.purchase_id == purchase.purchase_id
            ):
                if event.previous_closing_date != closing_date:
                    raise ValueError("invoice assignment correction history is inconsistent")
                closing_date = event.target_closing_date
        return closing_date

    def _due_date_after(self, closing_date: date) -> date:
        due_date = _day_in_month(
            closing_date.year,
            closing_date.month,
            self.opening.due_day,
        )
        if due_date <= closing_date:
            year, month = _next_month(closing_date.year, closing_date.month)
            due_date = _day_in_month(year, month, self.opening.due_day)
        return due_date

    def _installment_cycles(
        self, first_cycle: InvoiceCycle, count: int
    ) -> tuple[InvoiceCycle, ...]:
        cycles = [first_cycle]
        while len(cycles) < count:
            year, month = _next_month(
                cycles[-1].closing_date.year, cycles[-1].closing_date.month
            )
            closing_date = _day_in_month(year, month, self.opening.closing_day)
            cycles.append(
                InvoiceCycle(
                    closing_date=closing_date,
                    due_date=self._due_date_after(closing_date),
                )
            )
        return tuple(cycles)

    def _replacement_cycles(
        self,
        *,
        current: tuple[Installment, ...],
        frozen: tuple[Installment, ...],
        count: int,
        on_date: date,
    ) -> tuple[InvoiceCycle, ...]:
        if count < 0:
            raise ValueError("remaining_installment_count cannot be negative")
        open_installments = tuple(item for item in current if item not in frozen)
        if not open_installments:
            raise ValueError("installment purchase has no open installments to correct")
        if count == 0:
            return ()
        frozen_dates = {item.cycle.closing_date for item in frozen}
        candidate = open_installments[0].cycle
        cycles: list[InvoiceCycle] = []
        while len(cycles) < count:
            invoice = self.invoice(
                candidate.closing_date,
                through_effective_date=on_date,
            )
            if (
                candidate.closing_date not in frozen_dates
                and invoice.status_on(on_date) is InvoiceStatus.OPEN
            ):
                cycles.append(candidate)
            year, month = _next_month(
                candidate.closing_date.year, candidate.closing_date.month
            )
            closing_date = _day_in_month(year, month, self.opening.closing_day)
            candidate = InvoiceCycle(
                closing_date=closing_date,
                due_date=self._due_date_after(closing_date),
            )
        return tuple(cycles)

    def _validate_installment_correction(
        self, correction: InstallmentPurchaseCorrected
    ) -> None:
        current, expected_open, frozen = self._installment_partition_for_correction(
            purchase_id=correction.purchase_id,
            on_date=correction.metadata.effective_date,
        )
        if correction.previous_installments != expected_open:
            raise ValueError(
                "installment correction must replace exactly the open installments"
            )
        if correction.frozen_installments != frozen:
            raise ValueError("installment correction must preserve frozen installments")
        expected_cycles = self._replacement_cycles(
            current=current,
            frozen=frozen,
            count=len(correction.replacement_installments),
            on_date=correction.metadata.effective_date,
        )
        if tuple(item.cycle for item in correction.replacement_installments) != expected_cycles:
            raise ValueError("replacement installments use invalid invoice cycles")
        frozen_total = self._installment_total(frozen)
        replacement_total = self._installment_total(correction.replacement_installments)
        if frozen_total + replacement_total != correction.corrected_allocation.total:
            raise ValueError("corrected installments must preserve the corrected total")

    def _installment_total(self, installments: tuple[Installment, ...]) -> Money:
        total = Money("0", self.currency)
        for installment in installments:
            total += installment.amount
        return total

    def _installment_partition_for_correction(
        self,
        *,
        purchase_id: UUID,
        on_date: date,
    ) -> tuple[
        tuple[Installment, ...],
        tuple[Installment, ...],
        tuple[Installment, ...],
    ]:
        current = self.installments_for_purchase(
            purchase_id,
            through_effective_date=on_date,
        )
        open_installments = tuple(
            installment
            for installment in current
            if self.invoice(
                installment.cycle.closing_date,
                through_effective_date=on_date,
            ).status_on(on_date)
            is InvoiceStatus.OPEN
        )
        frozen_installments = tuple(
            item for item in current if item not in open_installments
        )
        return current, open_installments, frozen_installments

    def _validate_purchase_allocation(
        self,
        *,
        allocation: Allocation,
        catalog: CategoryCatalog,
        operation_name: str,
    ) -> None:
        allocation.ensure_categories_compatible(catalog, require_active=True)
        if allocation.nature is not FinancialNature.EXPENSE:
            raise ValueError(f"{operation_name} requires an expense allocation")
        self._require_currency(allocation.total)
        if allocation.total.amount <= 0:
            raise ValueError(f"{operation_name} amount must be positive")
        available = self.available_limit
        if available is not None and allocation.total > available:
            raise ValueError(f"{operation_name} exceeds available limit")

    @staticmethod
    def _event_effective_date(event: CreditCardEvent) -> date:
        if isinstance(
            event,
            (CardPurchase, InstallmentPurchase, InvoiceFinancialCharge),
        ):
            return event.allocation.metadata.effective_date
        return event.metadata.effective_date

    def _require_currency(self, amount: Money) -> None:
        if amount.currency != self.currency:
            raise CurrencyMismatchError(
                f"credit card uses {self.currency}, but value uses {amount.currency}"
            )

    def _credit_granted(
        self,
        *,
        amount: Money,
        source_event_id: UUID,
        reason: str,
        metadata: RecordMetadata,
    ) -> CardCreditGranted:
        credit_metadata = replace(
            metadata,
            record_id=uuid5(NAMESPACE_URL, f"card-credit:{source_event_id}"),
        )
        return CardCreditGranted(
            card_id=self.card_id,
            amount=amount,
            source_event_id=source_event_id,
            reason=reason,
            metadata=credit_metadata,
        )

    def _validate_limit_history(self) -> None:
        if self.opening.limit is None:
            return
        principal_by_invoice: dict[date, Money] = {}
        purchase_remaining: dict[UUID, Money] = {}
        assignment: dict[UUID, date] = {}
        installment_schedules: dict[UUID, tuple[Installment, ...]] = {}
        for event in self.history[1:]:
            if isinstance(event, CardPurchase):
                pending = self._pending_principal(principal_by_invoice)
                if pending + event.amount > self.opening.limit:
                    raise ValueError("credit card history exceeds its limit")
                assignment[event.purchase_id] = event.invoice_closing_date
                purchase_remaining[event.purchase_id] = event.amount
                principal_by_invoice[event.invoice_closing_date] = (
                    principal_by_invoice.get(
                        event.invoice_closing_date,
                        Money("0", self.currency),
                    )
                    + event.amount
                )
            elif isinstance(event, InstallmentPurchase):
                pending = self._pending_principal(principal_by_invoice)
                if pending + event.amount > self.opening.limit:
                    raise ValueError("credit card history exceeds its limit")
                for installment in event.installments:
                    closing_date = installment.cycle.closing_date
                    principal_by_invoice[closing_date] = (
                        principal_by_invoice.get(
                            closing_date,
                            Money("0", self.currency),
                        )
                        + installment.amount
                    )
                installment_schedules[event.purchase_id] = event.installments
            elif isinstance(event, InstallmentPurchaseCorrected):
                for installment in installment_schedules[event.purchase_id]:
                    closing_date = installment.cycle.closing_date
                    current = principal_by_invoice.get(
                        closing_date, Money("0", self.currency)
                    )
                    principal_by_invoice[closing_date] = Money(
                        current.amount - installment.amount.amount,
                        self.currency,
                    )
                for installment in (
                    *event.frozen_installments,
                    *event.replacement_installments,
                ):
                    closing_date = installment.cycle.closing_date
                    principal_by_invoice[closing_date] = (
                        principal_by_invoice.get(
                            closing_date, Money("0", self.currency)
                        )
                        + installment.amount
                    )
                installment_schedules[event.purchase_id] = tuple(
                    sorted(
                        (*event.frozen_installments, *event.replacement_installments),
                        key=lambda item: item.cycle.closing_date,
                    )
                )
                pending = self._pending_principal(principal_by_invoice)
                if pending > self.opening.limit:
                    raise ValueError("credit card history exceeds its limit")
            elif isinstance(event, InvoiceAssignmentCorrected):
                remaining = purchase_remaining[event.purchase_id]
                previous = assignment[event.purchase_id]
                principal_by_invoice[previous] = Money(
                    principal_by_invoice[previous].amount - remaining.amount,
                    self.currency,
                )
                principal_by_invoice[event.target_closing_date] = (
                    principal_by_invoice.get(
                        event.target_closing_date,
                        Money("0", self.currency),
                    )
                    + remaining
                )
                assignment[event.purchase_id] = event.target_closing_date
            elif isinstance(event, CardRefund):
                closing_date = assignment[event.purchase_id]
                principal_by_invoice[closing_date] = Money(
                    principal_by_invoice[closing_date].amount
                    - event.principal_reduction.amount,
                    self.currency,
                )
                purchase_remaining[event.purchase_id] = Money(
                    purchase_remaining[event.purchase_id].amount
                    - event.principal_reduction.amount,
                    self.currency,
                )
            elif isinstance(event, (InvoicePayment, CardCreditApplied)):
                settlement = (
                    event.applied_amount
                    if isinstance(event, InvoicePayment)
                    else event.amount
                )
                principal = principal_by_invoice.get(
                    event.invoice_closing_date,
                    Money("0", self.currency),
                )
                reduction = _principal_reduction(principal, settlement)
                principal_by_invoice[event.invoice_closing_date] = Money(
                    principal.amount - reduction.amount,
                    self.currency,
                )

    def _pending_principal(self, principal_by_invoice: dict[date, Money]) -> Money:
        pending = Money("0", self.currency)
        for invoice_principal in principal_by_invoice.values():
            pending += invoice_principal
        return pending


def _day_in_month(year: int, month: int, day: int) -> date:
    return date(year, month, min(day, monthrange(year, month)[1]))


def _next_month(year: int, month: int) -> tuple[int, int]:
    return (year + 1, 1) if month == 12 else (year, month + 1)


def _apply_settlement(
    principal: Money,
    charges: Money,
    amount: Money,
) -> tuple[Money, Money]:
    if amount.amount <= 0:
        raise ValueError("invoice settlement must be positive")
    total = principal + charges
    if amount > total:
        raise ValueError("invoice settlement cannot exceed amount due")
    principal_reduction = _principal_reduction(principal, amount)
    remaining = Money(amount.amount - principal_reduction.amount, amount.currency)
    return (
        Money(principal.amount - principal_reduction.amount, principal.currency),
        Money(charges.amount - remaining.amount, charges.currency),
    )


def _principal_reduction(principal: Money, settlement: Money) -> Money:
    if principal.currency != settlement.currency:
        raise CurrencyMismatchError("invoice settlement values must use the same currency")
    return Money(min(principal.amount, settlement.amount), principal.currency)
