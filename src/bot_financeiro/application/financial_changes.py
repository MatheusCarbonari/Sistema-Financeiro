from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import date
from types import TracebackType
from typing import Protocol
from uuid import UUID

from bot_financeiro.application.categories import AllocationRepository, FinancialCategoryRepository
from bot_financeiro.application.credit_cards import CreditCardRepository
from bot_financeiro.application.financial_change_policies import (
    financial_fact_change_blockers,
    normalize_financial_fact_replacement,
)
from bot_financeiro.application.financial_operations import FinancialMovementRepository
from bot_financeiro.application.payables import PayableRepository
from bot_financeiro.application.resource_accounts import ResourceAccountRepository
from bot_financeiro.domain.audit import RecordMetadata, ensure_registration_order
from bot_financeiro.domain.categories import Allocation
from bot_financeiro.domain.credit_cards import (
    CardPurchase,
    CardRefund,
    Installment,
    InstallmentPurchase,
    InstallmentPurchaseCorrected,
    InvoiceAssignmentCorrected,
    InvoiceFinancialCharge,
)
from bot_financeiro.domain.financial_changes import (
    AccountExpenseRefund,
    ExpenseRefundPosition,
    FactChange,
    FinancialFactCanceled,
    FinancialFactCorrected,
    FinancialFactHistory,
    ensure_financial_fact_correction,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import (
    FinancialMovement,
)
from bot_financeiro.domain.payables import FinancialCharge, PayableOpened
from bot_financeiro.domain.resource_account import AccountHistoryEntryKind

ChangeableFinancialFact = (
    FinancialMovement
    | PayableOpened
    | FinancialCharge
    | CardPurchase
    | InstallmentPurchase
    | InvoiceFinancialCharge
)


class FinancialChangeRepository(Protocol):
    def add(self, change: FactChange) -> None: ...

    def history(self, movement_id: UUID) -> FinancialFactHistory[ChangeableFinancialFact]: ...


class FinancialChangeUnitOfWork(Protocol):
    accounts: ResourceAccountRepository
    categories: FinancialCategoryRepository
    allocations: AllocationRepository
    movements: FinancialMovementRepository
    changes: FinancialChangeRepository
    credit_cards: CreditCardRepository
    payables: PayableRepository

    def __enter__(self) -> FinancialChangeUnitOfWork: ...

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None: ...

    def commit(self) -> None: ...


@dataclass(frozen=True, slots=True)
class AccountBalanceImpact:
    account_id: UUID
    current_effect: Money
    proposed_effect: Money
    net_change: Money


@dataclass(frozen=True, slots=True)
class ExpenseImpact:
    gross_before: Money
    gross_after: Money
    refunded_before: Money
    refunded_after: Money


@dataclass(frozen=True, slots=True)
class EffectiveDateImpact:
    before: date
    after: date | None


@dataclass(frozen=True, slots=True)
class DebtImpact:
    owner_id: UUID
    current_effect: Money
    proposed_effect: Money
    net_change: Money


@dataclass(frozen=True, slots=True)
class FinancialImpactPreview:
    description: str
    balance_impacts: tuple[AccountBalanceImpact, ...] = ()
    expense: ExpenseImpact | None = None
    debt_impacts: tuple[DebtImpact, ...] = ()
    effective_date: EffectiveDateImpact | None = None
    blockers: tuple[str, ...] = ()

    @property
    def is_blocked(self) -> bool:
        return bool(self.blockers)


@dataclass(frozen=True, slots=True)
class CorrectionProposal:
    target_id: UUID
    expected_version_id: UUID
    replacement: ChangeableFinancialFact
    reason: str
    impact: FinancialImpactPreview


@dataclass(frozen=True, slots=True)
class CancellationProposal:
    target_id: UUID
    expected_version_id: UUID
    reason: str
    impact: FinancialImpactPreview


@dataclass(frozen=True, slots=True)
class RefundProposal:
    target_id: UUID
    expected_version_id: UUID
    expected_related_version_id: UUID | None
    account_id: UUID
    amount: Money
    expected_refunded_amount: Money
    impact: FinancialImpactPreview


@dataclass(frozen=True, slots=True)
class CardRefundProposal:
    card_id: UUID
    purchase_id: UUID
    expected_card_version_id: UUID
    amount: Money
    expected_refunded_amount: Money
    impact: FinancialImpactPreview


@dataclass(frozen=True, slots=True)
class InstallmentImpact:
    previous: tuple[Installment, ...]
    frozen: tuple[Installment, ...]
    replacement: tuple[Installment, ...]


@dataclass(frozen=True, slots=True)
class InstallmentCorrectionProposal:
    card_id: UUID
    purchase_id: UUID
    expected_card_version_id: UUID
    corrected_allocation: Allocation
    remaining_installment_count: int
    reason: str
    effective_date: date
    expected_refunded_amount: Money
    installment_impact: InstallmentImpact
    impact: FinancialImpactPreview


@dataclass(frozen=True, slots=True)
class InvoiceAssignmentImpact:
    previous_closing_date: date
    target_closing_date: date


@dataclass(frozen=True, slots=True)
class InvoiceAssignmentCorrectionProposal:
    card_id: UUID
    purchase_id: UUID
    expected_card_version_id: UUID
    target_closing_date: date
    reason: str
    effective_date: date
    assignment_impact: InvoiceAssignmentImpact
    impact: FinancialImpactPreview


@dataclass(frozen=True, slots=True)
class ExpenseBreakdown:
    gross_expense: Money
    refunded_amount: Money
    net_expense: Money


class _FinancialFactChangeOperations:
    def __init__(self, unit_of_work_factory: Callable[[], FinancialChangeUnitOfWork]) -> None:
        self._unit_of_work_factory = unit_of_work_factory

    def preview_correction(
        self,
        target_id: UUID,
        replacement: ChangeableFinancialFact,
        *,
        reason: str,
    ) -> CorrectionProposal:
        normalized_reason = self._reason(reason, "correction")
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.changes.history(target_id)
            current = self._active(history)
            self._ensure_supported_change(current, operation="corrected")
            replacement = normalize_financial_fact_replacement(target_id, replacement)
            self._validate_replacement(current, replacement, unit_of_work)
            blockers = self._dependency_blockers(target_id, replacement.amount, unit_of_work)
            blockers += financial_fact_change_blockers(current, replacement, unit_of_work)
            impact = self._proposal_impact(
                target_id, current, replacement, blockers, unit_of_work
            )
            return CorrectionProposal(
                target_id=target_id,
                expected_version_id=history.version_id,
                replacement=replacement,
                reason=normalized_reason,
                impact=impact,
            )

    def confirm_correction(
        self, proposal: CorrectionProposal, metadata: RecordMetadata
    ) -> FinancialFactCorrected[ChangeableFinancialFact]:
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.changes.history(proposal.target_id)
            self._require_current_proposal(history, proposal.expected_version_id)
            current = self._active(history)
            self._ensure_supported_change(current, operation="corrected")
            self._validate_replacement(current, proposal.replacement, unit_of_work)
            blockers = self._dependency_blockers(
                proposal.target_id, proposal.replacement.amount, unit_of_work
            )
            blockers += financial_fact_change_blockers(
                current, proposal.replacement, unit_of_work
            )
            expected_impact = self._proposal_impact(
                proposal.target_id,
                current,
                proposal.replacement,
                blockers,
                unit_of_work,
            )
            self._require_unblocked(expected_impact.blockers)
            if expected_impact != proposal.impact:
                raise ValueError("correction proposal is obsolete")
            correction = FinancialFactCorrected(
                target_id=proposal.target_id,
                previous_version_id=proposal.expected_version_id,
                replacement=proposal.replacement,
                reason=proposal.reason,
                metadata=metadata,
            )
            history.append_correction(correction)
            self._save_replacement_allocation(proposal.replacement, unit_of_work)
            self._apply_balance_impacts(
                expected_impact.balance_impacts,
                target_id=proposal.target_id,
                reason=proposal.reason,
                metadata=metadata,
                kind=AccountHistoryEntryKind.FACT_CORRECTION,
                unit_of_work=unit_of_work,
            )
            unit_of_work.changes.add(correction)
            unit_of_work.commit()
            return correction

    def preview_cancellation(self, target_id: UUID, *, reason: str) -> CancellationProposal:
        normalized_reason = self._reason(reason, "cancellation")
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.changes.history(target_id)
            current = self._active(history)
            self._ensure_supported_change(current, operation="canceled")
            blockers = self._dependency_blockers(target_id, None, unit_of_work)
            blockers += financial_fact_change_blockers(current, None, unit_of_work)
            impact = self._proposal_impact(target_id, current, None, blockers, unit_of_work)
            return CancellationProposal(
                target_id=target_id,
                expected_version_id=history.version_id,
                reason=normalized_reason,
                impact=impact,
            )

    def confirm_cancellation(
        self, proposal: CancellationProposal, metadata: RecordMetadata
    ) -> FinancialFactCanceled:
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.changes.history(proposal.target_id)
            self._require_current_proposal(history, proposal.expected_version_id)
            current = self._active(history)
            self._ensure_supported_change(current, operation="canceled")
            blockers = self._dependency_blockers(proposal.target_id, None, unit_of_work)
            blockers += financial_fact_change_blockers(current, None, unit_of_work)
            expected_impact = self._proposal_impact(
                proposal.target_id, current, None, blockers, unit_of_work
            )
            self._require_unblocked(expected_impact.blockers)
            if expected_impact != proposal.impact:
                raise ValueError("cancellation proposal is obsolete")
            cancellation = FinancialFactCanceled(
                target_id=proposal.target_id,
                previous_version_id=proposal.expected_version_id,
                reason=proposal.reason,
                metadata=metadata,
            )
            history.append_cancellation(cancellation)
            self._apply_balance_impacts(
                expected_impact.balance_impacts,
                target_id=proposal.target_id,
                reason=proposal.reason,
                metadata=metadata,
                kind=AccountHistoryEntryKind.FACT_CANCELLATION,
                unit_of_work=unit_of_work,
            )
            unit_of_work.changes.add(cancellation)
            unit_of_work.commit()
            return cancellation

class _AccountExpenseRefundOperations:
    def preview_account_refund(
        self, target_id: UUID, *, account_id: UUID, amount: Money
    ) -> RefundProposal:
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.changes.history(target_id)
            current = self._active(history)
            if not self._is_expense_fact(current):
                raise ValueError("account refund requires an expense")
            current_expense = self._current_expense_amount(
                current, target_id, unit_of_work
            )
            account = unit_of_work.accounts.get(account_id)
            if account is None:
                raise LookupError(f"resource account {account_id} does not exist")
            already_refunded = self._all_refund_total(current, target_id, unit_of_work)
            blockers = self._refund_blockers(
                current_expense,
                already_refunded,
                amount,
                receiving_currency=account.currency,
            )
            blockers = (*blockers, *self._refund_account_blockers(account, amount))
            impact = self._account_refund_impact(
                account_id=account_id,
                amount=amount,
                gross=current_expense,
                refunded=already_refunded,
                blockers=blockers,
            )
            return RefundProposal(
                target_id=target_id,
                expected_version_id=history.version_id,
                expected_related_version_id=self._related_version_id(
                    current, unit_of_work
                ),
                account_id=account_id,
                amount=amount,
                expected_refunded_amount=already_refunded,
                impact=impact,
            )

    def confirm_account_refund(
        self, proposal: RefundProposal, metadata: RecordMetadata
    ) -> AccountExpenseRefund:
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.changes.history(proposal.target_id)
            self._require_current_proposal(history, proposal.expected_version_id)
            current = self._active(history)
            if not self._is_expense_fact(current):
                raise ValueError("account refund requires an expense")
            if (
                self._related_version_id(current, unit_of_work)
                != proposal.expected_related_version_id
            ):
                raise ValueError("account refund proposal is obsolete")
            current_expense = self._current_expense_amount(
                current, proposal.target_id, unit_of_work
            )
            account = unit_of_work.accounts.get(proposal.account_id)
            if account is None:
                raise LookupError(f"resource account {proposal.account_id} does not exist")
            previous_metadata = [
                history.events[-1].metadata if history.events else history.original.metadata,
                account.history[-1].metadata,
            ]
            previous_metadata.extend(
                self._refund_registration_metadata(
                    current,
                    proposal.target_id,
                    unit_of_work,
                )
            )
            ensure_registration_order(metadata, *previous_metadata)
            already_refunded = self._all_refund_total(
                current, proposal.target_id, unit_of_work
            )
            if already_refunded != proposal.expected_refunded_amount:
                raise ValueError("account refund proposal is obsolete")
            blockers = self._refund_blockers(
                current_expense,
                already_refunded,
                proposal.amount,
                receiving_currency=account.currency,
            )
            blockers = (
                *blockers,
                *self._refund_account_blockers(account, proposal.amount),
            )
            expected_impact = self._account_refund_impact(
                account_id=proposal.account_id,
                amount=proposal.amount,
                gross=current_expense,
                refunded=already_refunded,
                blockers=blockers,
            )
            if expected_impact != proposal.impact:
                raise ValueError("account refund proposal is obsolete")
            self._require_unblocked(expected_impact.blockers)
            refund, updated_account = AccountExpenseRefund.receive(
                original_expense_id=proposal.target_id,
                account=account,
                amount=proposal.amount,
                metadata=metadata,
            )
            unit_of_work.movements.add(refund)
            unit_of_work.accounts.save(updated_account)
            unit_of_work.commit()
            return refund

class _CreditCardFinancialChangeOperations:
    def preview_card_refund(
        self, *, card_id: UUID, purchase_id: UUID, amount: Money
    ) -> CardRefundProposal:
        with self._unit_of_work_factory() as unit_of_work:
            card = unit_of_work.credit_cards.get(card_id)
            if card is None:
                raise LookupError(f"credit card {card_id} does not exist")
            purchase = self._card_purchase(card, purchase_id)
            current_expense = self._current_card_expense(card, purchase)
            refunded = self._refund_total(purchase_id, unit_of_work) + self._card_refund_total(
                card, purchase_id
            )
            blockers = self._refund_blockers(
                current_expense,
                refunded,
                amount,
                receiving_currency=card.currency,
            )
            principal_due = (
                self._card_principal(card)
                if isinstance(purchase, InstallmentPurchase)
                else card.invoice_for_purchase(purchase_id).principal_due
            )
            principal_reduction = Money(
                "0" if blockers else min(principal_due.amount, amount.amount),
                purchase.amount.currency,
            )
            return CardRefundProposal(
                card_id=card_id,
                purchase_id=purchase_id,
                expected_card_version_id=card.history[-1].event_id,
                amount=amount,
                expected_refunded_amount=refunded,
                impact=FinancialImpactPreview(
                    description="Refund the card expense through its original card channel.",
                    expense=ExpenseImpact(
                        gross_before=current_expense,
                        gross_after=current_expense,
                        refunded_before=refunded,
                        refunded_after=refunded if blockers else refunded + amount,
                    ),
                    debt_impacts=(
                        DebtImpact(
                            owner_id=card_id,
                            current_effect=Money("0", amount.currency),
                            proposed_effect=Money(
                                -principal_reduction.amount, amount.currency
                            ),
                            net_change=Money(
                                -principal_reduction.amount, amount.currency
                            ),
                        ),
                    ),
                    blockers=blockers,
                ),
            )

    def preview_installment_purchase_correction(
        self,
        *,
        card_id: UUID,
        purchase_id: UUID,
        corrected_allocation: Allocation,
        remaining_installment_count: int,
        reason: str,
        effective_date: date,
    ) -> InstallmentCorrectionProposal:
        normalized_reason = self._reason(reason, "installment correction")
        with self._unit_of_work_factory() as unit_of_work:
            card = unit_of_work.credit_cards.get(card_id)
            if card is None:
                raise LookupError(f"credit card {card_id} does not exist")
            assessment = card.assess_installment_purchase_correction(
                purchase_id=purchase_id,
                corrected_allocation=corrected_allocation,
                remaining_installment_count=remaining_installment_count,
                reason=normalized_reason,
                effective_date=effective_date,
                catalog=unit_of_work.categories.catalog(),
            )
            before = card.expense_for_purchase(purchase_id).total
            after = corrected_allocation.total
            refunded = self._all_refund_total(
                self._card_purchase(card, purchase_id), purchase_id, unit_of_work
            )
            blockers = self._expense_change_blockers(before, refunded, after)
            debt_before = self._card_principal(card)
            debt_after = debt_before
            if not assessment.blockers:
                debt_after = Money(
                    debt_before.amount + after.amount - before.amount,
                    debt_before.currency,
                )
            blockers = (*assessment.blockers, *blockers)
            return InstallmentCorrectionProposal(
                card_id=card_id,
                purchase_id=purchase_id,
                expected_card_version_id=card.history[-1].event_id,
                corrected_allocation=corrected_allocation,
                remaining_installment_count=remaining_installment_count,
                reason=normalized_reason,
                effective_date=effective_date,
                expected_refunded_amount=refunded,
                installment_impact=InstallmentImpact(
                    assessment.previous_installments,
                    assessment.frozen_installments,
                    assessment.replacement_installments,
                ),
                impact=FinancialImpactPreview(
                    description=(
                        "Correct only open installments; affected cycles remain frozen."
                    ),
                    expense=ExpenseImpact(
                        gross_before=before,
                        gross_after=after,
                        refunded_before=refunded,
                        refunded_after=refunded,
                    ),
                    debt_impacts=(
                        DebtImpact(
                            owner_id=card_id,
                            current_effect=debt_before,
                            proposed_effect=debt_after,
                            net_change=Money(
                                debt_after.amount - debt_before.amount,
                                debt_before.currency,
                            ),
                        ),
                    ),
                    effective_date=EffectiveDateImpact(
                        before=card.expense_for_purchase(purchase_id).metadata.effective_date,
                        after=corrected_allocation.metadata.effective_date,
                    ),
                    blockers=blockers,
                ),
            )

    def confirm_installment_purchase_correction(
        self,
        proposal: InstallmentCorrectionProposal,
        metadata: RecordMetadata,
    ) -> InstallmentPurchaseCorrected:
        if metadata.effective_date != proposal.effective_date:
            raise ValueError("correction effective date differs from preview")
        with self._unit_of_work_factory() as unit_of_work:
            card = unit_of_work.credit_cards.get(proposal.card_id)
            if card is None:
                raise LookupError(f"credit card {proposal.card_id} does not exist")
            if card.history[-1].event_id != proposal.expected_card_version_id:
                raise ValueError("installment correction proposal is obsolete")
            ensure_registration_order(metadata, card.history[-1].metadata)
            current_expense = card.expense_for_purchase(proposal.purchase_id).total
            refunded = self._all_refund_total(
                self._card_purchase(card, proposal.purchase_id),
                proposal.purchase_id,
                unit_of_work,
            )
            if refunded != proposal.expected_refunded_amount:
                raise ValueError("installment correction proposal is obsolete")
            blockers = self._expense_change_blockers(
                current_expense,
                refunded,
                proposal.corrected_allocation.total,
            )
            self._require_unblocked(blockers)
            assessment = card.assess_installment_purchase_correction(
                purchase_id=proposal.purchase_id,
                corrected_allocation=proposal.corrected_allocation,
                remaining_installment_count=proposal.remaining_installment_count,
                reason=proposal.reason,
                effective_date=metadata.effective_date,
                catalog=unit_of_work.categories.catalog(),
            )
            self._require_unblocked((*assessment.blockers, *blockers))
            actual_impact = InstallmentImpact(
                assessment.previous_installments,
                assessment.frozen_installments,
                assessment.replacement_installments,
            )
            if actual_impact != proposal.installment_impact:
                raise ValueError("installment correction proposal is obsolete")
            correction, updated = card.correct_installment_purchase(
                purchase_id=proposal.purchase_id,
                corrected_allocation=proposal.corrected_allocation,
                remaining_installment_count=proposal.remaining_installment_count,
                reason=proposal.reason,
                metadata=metadata,
                catalog=unit_of_work.categories.catalog(),
            )
            unit_of_work.allocations.add(proposal.corrected_allocation)
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
            return correction

    def preview_invoice_assignment_correction(
        self,
        *,
        card_id: UUID,
        purchase_id: UUID,
        target_closing_date: date,
        reason: str,
        effective_date: date,
    ) -> InvoiceAssignmentCorrectionProposal:
        with self._unit_of_work_factory() as unit_of_work:
            card = unit_of_work.credit_cards.get(card_id)
            if card is None:
                raise LookupError(f"credit card {card_id} does not exist")
            purchase = self._card_purchase(card, purchase_id)
            if not isinstance(purchase, CardPurchase):
                raise ValueError(
                    "invoice assignment correction requires a non-installment purchase"
                )
            assessment = card.assess_invoice_assignment_correction(
                purchase_id=purchase_id,
                target_closing_date=target_closing_date,
            )
            blockers = self._invoice_assignment_blockers(
                card,
                purchase_id,
                assessment.previous_closing_date,
                target_closing_date,
            )
            assignment_impact = InvoiceAssignmentImpact(
                assessment.previous_closing_date,
                assessment.target_closing_date,
            )
            return InvoiceAssignmentCorrectionProposal(
                card_id=card_id,
                purchase_id=purchase_id,
                expected_card_version_id=card.history[-1].event_id,
                target_closing_date=target_closing_date,
                reason=self._reason(reason, "invoice assignment correction"),
                effective_date=effective_date,
                assignment_impact=assignment_impact,
                impact=FinancialImpactPreview(
                    description=(
                        "Move the purchase from invoice "
                        f"{assessment.previous_closing_date} to {target_closing_date}."
                    ),
                    blockers=blockers,
                ),
            )

    def confirm_invoice_assignment_correction(
        self,
        proposal: InvoiceAssignmentCorrectionProposal,
        metadata: RecordMetadata,
    ) -> InvoiceAssignmentCorrected:
        if metadata.effective_date != proposal.effective_date:
            raise ValueError("correction effective date differs from preview")
        with self._unit_of_work_factory() as unit_of_work:
            card = unit_of_work.credit_cards.get(proposal.card_id)
            if card is None:
                raise LookupError(f"credit card {proposal.card_id} does not exist")
            if card.history[-1].event_id != proposal.expected_card_version_id:
                raise ValueError("invoice assignment correction proposal is obsolete")
            ensure_registration_order(metadata, card.history[-1].metadata)
            assessment = card.assess_invoice_assignment_correction(
                purchase_id=proposal.purchase_id,
                target_closing_date=proposal.target_closing_date,
            )
            blockers = self._invoice_assignment_blockers(
                card,
                proposal.purchase_id,
                assessment.previous_closing_date,
                proposal.target_closing_date,
            )
            self._require_unblocked(blockers)
            actual_assignment = InvoiceAssignmentImpact(
                assessment.previous_closing_date,
                assessment.target_closing_date,
            )
            if actual_assignment != proposal.assignment_impact:
                raise ValueError("invoice assignment correction proposal is obsolete")
            correction, updated = card.correct_invoice_assignment(
                purchase_id=proposal.purchase_id,
                target_closing_date=proposal.target_closing_date,
                reason=proposal.reason,
                metadata=metadata,
            )
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
            return correction

    def confirm_card_refund(
        self, proposal: CardRefundProposal, metadata: RecordMetadata
    ) -> CardRefund:
        with self._unit_of_work_factory() as unit_of_work:
            card = unit_of_work.credit_cards.get(proposal.card_id)
            if card is None:
                raise LookupError(f"credit card {proposal.card_id} does not exist")
            if card.history[-1].event_id != proposal.expected_card_version_id:
                raise ValueError("card refund proposal is obsolete")
            purchase = self._card_purchase(card, proposal.purchase_id)
            ensure_registration_order(
                metadata,
                card.history[-1].metadata,
                *self._refund_registration_metadata(
                    purchase,
                    proposal.purchase_id,
                    unit_of_work,
                ),
            )
            current_expense = self._current_card_expense(card, purchase)
            refunded = self._refund_total(
                proposal.purchase_id, unit_of_work
            ) + self._card_refund_total(card, proposal.purchase_id)
            if refunded != proposal.expected_refunded_amount:
                raise ValueError("card refund proposal is obsolete")
            blockers = self._refund_blockers(
                current_expense,
                refunded,
                proposal.amount,
                receiving_currency=card.currency,
            )
            self._require_unblocked(blockers)
            refund, updated = card.refund_purchase(
                purchase_id=proposal.purchase_id,
                amount=proposal.amount,
                metadata=metadata,
            )
            unit_of_work.credit_cards.save(updated)
            unit_of_work.commit()
            return refund

class _FinancialChangeQueries:
    def get_history(self, target_id: UUID) -> FinancialFactHistory[ChangeableFinancialFact]:
        with self._unit_of_work_factory() as unit_of_work:
            return unit_of_work.changes.history(target_id)

    def expense_breakdown(self, target_id: UUID) -> ExpenseBreakdown:
        with self._unit_of_work_factory() as unit_of_work:
            history = unit_of_work.changes.history(target_id)
            current = history.current
            original = history.original
            if not self._is_expense_fact(original):
                raise ValueError("expense breakdown requires an expense")
            gross = (
                Money("0", original.amount.currency)
                if current is None
                else self._current_expense_amount(current, target_id, unit_of_work)
            )
            refunded = self._all_refund_total(original, target_id, unit_of_work)
            return ExpenseBreakdown(
                gross,
                refunded,
                Money(gross.amount - refunded.amount, gross.currency),
            )

    def card_expense_breakdown(self, card_id: UUID, purchase_id: UUID) -> ExpenseBreakdown:
        with self._unit_of_work_factory() as unit_of_work:
            card = unit_of_work.credit_cards.get(card_id)
            if card is None:
                raise LookupError(f"credit card {card_id} does not exist")
            history = unit_of_work.changes.history(purchase_id)
            purchase = history.current
            if purchase is None:
                gross = Money("0", history.original.amount.currency)
            elif isinstance(history.original, InstallmentPurchase):
                gross = card.expense_for_purchase(purchase_id).total
            else:
                gross = purchase.amount
            refunded = self._refund_total(purchase_id, unit_of_work) + self._card_refund_total(
                card, purchase_id
            )
            return ExpenseBreakdown(
                gross,
                refunded,
                Money(gross.amount - refunded.amount, gross.currency),
            )

class _FinancialChangeSupport:
    @staticmethod
    def _active(history: FinancialFactHistory[ChangeableFinancialFact]):
        if history.is_canceled:
            raise ValueError("cancellation is terminal")
        current = history.current
        if current is None:
            raise ValueError("financial fact is canceled")
        return current

    @staticmethod
    def _reason(value: str, operation: str) -> str:
        normalized = value.strip()
        if not normalized:
            raise ValueError(f"{operation} reason is required")
        return normalized

    @staticmethod
    def _ensure_supported_change(current, *, operation: str) -> None:
        if isinstance(current, AccountExpenseRefund):
            raise ValueError(f"refund movements cannot be {operation}")

    @staticmethod
    def _require_current_proposal(
        history: FinancialFactHistory[FinancialMovement], expected_version_id: UUID
    ) -> None:
        if history.version_id != expected_version_id:
            raise ValueError("financial change proposal is obsolete")
        if history.is_canceled:
            raise ValueError("cancellation is terminal")

    @staticmethod
    def _require_unblocked(blockers: tuple[str, ...]) -> None:
        if blockers:
            raise ValueError("financial change is blocked: " + "; ".join(blockers))

    @staticmethod
    def _validate_replacement(current, replacement, unit_of_work) -> None:
        ensure_financial_fact_correction(
            current,
            replacement,
            unit_of_work.categories.catalog(),
        )

    @staticmethod
    def _effects(movement: FinancialMovement | None) -> dict[UUID, Money]:
        if movement is None:
            return {}
        return {effect.account_id: effect.amount for effect in movement.account_effects}

    def _impact(
        self,
        current: FinancialMovement,
        proposed: FinancialMovement | None,
        *,
        refunded: Money,
        blockers: tuple[str, ...],
    ) -> FinancialImpactPreview:
        before = self._effects(current)
        after = self._effects(proposed)
        currency = current.amount.currency
        zero = Money("0", currency)
        balance_impacts = tuple(
            AccountBalanceImpact(
                account_id=account_id,
                current_effect=before.get(account_id, zero),
                proposed_effect=after.get(account_id, zero),
                net_change=Money(
                    after.get(account_id, zero).amount - before.get(account_id, zero).amount,
                    currency,
                ),
            )
            for account_id in sorted(before.keys() | after.keys(), key=str)
            if after.get(account_id, zero) != before.get(account_id, zero)
        )
        expense = None
        if current.is_expense:
            expense = ExpenseImpact(
                gross_before=current.amount,
                gross_after=zero if proposed is None else proposed.amount,
                refunded_before=refunded,
                refunded_after=refunded,
            )
        debt_impacts = self._debt_impacts(current, proposed)
        before_date = current.metadata.effective_date
        after_date = None if proposed is None else proposed.metadata.effective_date
        temporal = EffectiveDateImpact(before=before_date, after=after_date)
        action = "Cancel" if proposed is None else "Correct"
        temporal_description = (
            f" Effective date changes from {before_date} to {after_date}."
            if before_date != after_date
            else ""
        )
        return FinancialImpactPreview(
            description=(
                f"{action} the fact and recalculate every affected account atomically."
                f"{temporal_description}"
            ),
            balance_impacts=balance_impacts,
            expense=expense,
            debt_impacts=debt_impacts,
            effective_date=temporal,
            blockers=blockers,
        )

    def _proposal_impact(
        self,
        target_id: UUID,
        current,
        proposed,
        blockers: tuple[str, ...],
        unit_of_work,
    ) -> FinancialImpactPreview:
        refunded = (
            self._all_refund_total(current, target_id, unit_of_work)
            if self._is_expense_fact(current)
            else Money("0", current.amount.currency)
        )
        impact = self._impact(
            current,
            proposed,
            refunded=refunded,
            blockers=blockers,
        )
        account_blockers = self._account_blockers(impact.balance_impacts, unit_of_work)
        if not account_blockers:
            return impact
        return replace(impact, blockers=(*impact.blockers, *account_blockers))

    @staticmethod
    def _account_blockers(impacts, unit_of_work) -> tuple[str, ...]:
        blockers: list[str] = []
        for impact in impacts:
            account = unit_of_work.accounts.get(impact.account_id)
            if account is None:
                blockers.append(f"resource account {impact.account_id} does not exist")
                continue
            try:
                account.balance_after_fact_change(impact.net_change)
            except ValueError as error:
                blockers.append(
                    f"resource account would reject the financial change: {error}"
                )
        return tuple(blockers)

    @staticmethod
    def _refund_account_blockers(account, amount: Money) -> tuple[str, ...]:
        try:
            account.balance_after_movement(amount)
        except ValueError as error:
            return (f"resource account would reject the refund: {error}",)
        return ()

    @staticmethod
    def _account_refund_impact(
        *,
        account_id: UUID,
        amount: Money,
        gross: Money,
        refunded: Money,
        blockers: tuple[str, ...],
    ) -> FinancialImpactPreview:
        return FinancialImpactPreview(
            description="Receive an expense refund into the selected resource account.",
            balance_impacts=(
                AccountBalanceImpact(
                    account_id=account_id,
                    current_effect=Money("0", amount.currency),
                    proposed_effect=amount,
                    net_change=amount,
                ),
            ),
            expense=ExpenseImpact(
                gross_before=gross,
                gross_after=gross,
                refunded_before=refunded,
                refunded_after=refunded if blockers else refunded + amount,
            ),
            blockers=blockers,
        )

    @staticmethod
    def _apply_balance_impacts(
        impacts: tuple[AccountBalanceImpact, ...],
        *,
        target_id: UUID,
        reason: str,
        metadata: RecordMetadata,
        kind: AccountHistoryEntryKind,
        unit_of_work,
    ) -> None:
        for impact in impacts:
            account = unit_of_work.accounts.get(impact.account_id)
            if account is None:
                raise LookupError(f"resource account {impact.account_id} does not exist")
            updated = account.record_fact_change(
                kind=kind,
                difference=impact.net_change,
                target_movement_id=target_id,
                reason=reason,
                metadata=metadata,
            )
            unit_of_work.accounts.save(updated)

    @staticmethod
    def _save_replacement_allocation(replacement, unit_of_work) -> None:
        allocation = getattr(replacement, "allocation", None)
        if allocation is not None:
            unit_of_work.allocations.add(allocation)

    @staticmethod
    def _debt_impacts(current, proposed) -> tuple[DebtImpact, ...]:
        currency = current.amount.currency
        zero = Money("0", currency)
        before = {effect.owner_id: effect.amount for effect in current.debt_effects}
        after = (
            {}
            if proposed is None
            else {effect.owner_id: effect.amount for effect in proposed.debt_effects}
        )
        return tuple(
            DebtImpact(
                owner_id=owner_id,
                current_effect=before.get(owner_id, zero),
                proposed_effect=after.get(owner_id, zero),
                net_change=Money(
                    after.get(owner_id, zero).amount - before.get(owner_id, zero).amount,
                    currency,
                ),
            )
            for owner_id in sorted(before.keys() | after.keys(), key=str)
            if before.get(owner_id, zero) != after.get(owner_id, zero)
        )

    @staticmethod
    def _refund_total(target_id: UUID, unit_of_work) -> Money:
        original = unit_of_work.movements.get(target_id)
        if original is None:
            original = unit_of_work.changes.history(target_id).original
        total = Money("0", original.amount.currency)
        for movement in unit_of_work.movements.list_all():
            if (
                isinstance(movement, AccountExpenseRefund)
                and movement.original_expense_id == target_id
            ):
                total += movement.amount
        return total

    @staticmethod
    def _refund_registration_metadata(
        original,
        target_id: UUID,
        unit_of_work,
    ) -> tuple[RecordMetadata, ...]:
        metadata = [
            movement.metadata
            for movement in unit_of_work.movements.list_all()
            if isinstance(movement, AccountExpenseRefund)
            and movement.original_expense_id == target_id
        ]
        if isinstance(original, (CardPurchase, InstallmentPurchase)):
            card = unit_of_work.credit_cards.get(original.card_id)
            if card is None:
                raise LookupError(f"credit card {original.card_id} does not exist")
            metadata.extend(
                event.metadata
                for event in card.history
                if isinstance(event, CardRefund) and event.purchase_id == target_id
            )
        return tuple(metadata)

    def _all_refund_total(self, original, target_id: UUID, unit_of_work) -> Money:
        total = self._refund_total(target_id, unit_of_work)
        if isinstance(original, (CardPurchase, InstallmentPurchase)):
            card = unit_of_work.credit_cards.get(original.card_id)
            if card is None:
                raise LookupError(f"credit card {original.card_id} does not exist")
            total += self._card_refund_total(card, target_id)
        return total

    def _dependency_blockers(self, target_id, proposed_amount, unit_of_work) -> tuple[str, ...]:
        try:
            history = unit_of_work.changes.history(target_id)
        except LookupError:
            return ()
        original = history.original
        if not self._is_expense_fact(original):
            return ()
        refunded = self._all_refund_total(original, target_id, unit_of_work)
        if refunded.amount == 0:
            return ()
        current = history.current or original
        gross = self._current_expense_amount(current, target_id, unit_of_work)
        position = ExpenseRefundPosition(gross, refunded)
        try:
            if proposed_amount is None:
                position.ensure_expense_can_be_canceled()
            else:
                position.ensure_expense_can_change_to(proposed_amount)
        except ValueError as error:
            return (str(error),)
        return ()

    @staticmethod
    def _is_expense_fact(fact) -> bool:
        return bool(fact.is_expense)

    @staticmethod
    def _card_purchase(card, purchase_id: UUID) -> CardPurchase | InstallmentPurchase:
        purchase = next(
            (
                event
                for event in card.history
                if isinstance(event, (CardPurchase, InstallmentPurchase))
                and event.purchase_id == purchase_id
            ),
            None,
        )
        if purchase is None:
            raise LookupError(f"card purchase {purchase_id} does not exist")
        return purchase

    @staticmethod
    def _current_card_expense(
        card, purchase: CardPurchase | InstallmentPurchase
    ) -> Money:
        if isinstance(purchase, InstallmentPurchase):
            return card.expense_for_purchase(purchase.purchase_id).total
        return purchase.amount

    @staticmethod
    def _current_expense_amount(fact, target_id: UUID, unit_of_work) -> Money:
        if not isinstance(fact, InstallmentPurchase):
            return fact.amount
        card = unit_of_work.credit_cards.get(fact.card_id)
        if card is None:
            raise LookupError(f"credit card {fact.card_id} does not exist")
        return card.expense_for_purchase(target_id).total

    @staticmethod
    def _related_version_id(fact, unit_of_work) -> UUID | None:
        if not isinstance(fact, (CardPurchase, InstallmentPurchase)):
            return None
        card = unit_of_work.credit_cards.get(fact.card_id)
        if card is None:
            raise LookupError(f"credit card {fact.card_id} does not exist")
        return card.history[-1].event_id

    @staticmethod
    def _card_refund_total(card, purchase_id: UUID) -> Money:
        total = Money("0", card.currency)
        for event in card.history:
            if isinstance(event, CardRefund) and event.purchase_id == purchase_id:
                total += event.amount
        return total

    @staticmethod
    def _card_principal(card) -> Money:
        total = Money("0", card.currency)
        for invoice in card.invoices():
            total += invoice.principal_due
        return total

    @staticmethod
    def _invoice_assignment_blockers(
        card,
        purchase_id: UUID,
        previous_closing_date: date,
        target_closing_date: date,
    ) -> tuple[str, ...]:
        try:
            card.ensure_invoice_assignment_change_allowed(
                purchase_id=purchase_id,
                previous_closing_date=previous_closing_date,
                target_closing_date=target_closing_date,
            )
        except ValueError as error:
            return (str(error),)
        return ()

    @staticmethod
    def _refund_blockers(
        gross: Money,
        already_refunded: Money,
        requested: Money,
        *,
        receiving_currency: str,
    ) -> tuple[str, ...]:
        try:
            ExpenseRefundPosition(gross, already_refunded).ensure_can_refund(
                requested,
                receiving_currency=receiving_currency,
            )
        except ValueError as error:
            return (str(error),)
        return ()

    @staticmethod
    def _expense_change_blockers(
        gross: Money,
        refunded: Money,
        proposed: Money,
    ) -> tuple[str, ...]:
        try:
            ExpenseRefundPosition(gross, refunded).ensure_expense_can_change_to(proposed)
        except ValueError as error:
            return (str(error),)
        return ()


class FinancialChangeOperations(
    _FinancialFactChangeOperations,
    _AccountExpenseRefundOperations,
    _CreditCardFinancialChangeOperations,
    _FinancialChangeQueries,
    _FinancialChangeSupport,
):
    """Public facade for the explicit Ticket 11 application seams."""
