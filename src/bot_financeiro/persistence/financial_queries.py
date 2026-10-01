from __future__ import annotations

from dataclasses import replace
from datetime import datetime
from types import TracebackType
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.orm import Session, sessionmaker

from bot_financeiro.application.financial_queries import (
    ExpenseRefundSnapshot,
    FinancialRecordType,
    RecognizedFactSnapshot,
)
from bot_financeiro.domain.credit_cards import (
    CardPurchase,
    CardRefund,
    InstallmentPurchase,
    InstallmentPurchaseCorrected,
    InvoiceFinancialCharge,
)
from bot_financeiro.domain.financial_changes import (
    AccountExpenseRefund,
    FinancialFactHistory,
)
from bot_financeiro.domain.financial_recognition import recognized_financial_nature
from bot_financeiro.domain.movements import CashExpense, FinancialMovement, Income
from bot_financeiro.domain.payables import FinancialCharge, PayableOpened
from bot_financeiro.persistence.categories import SqlAlchemyFinancialCategoryRepository
from bot_financeiro.persistence.credit_cards import (
    SqlAlchemyCreditCardRepository,
    credit_card_history_entries,
    credit_cards,
)
from bot_financeiro.persistence.financial_changes import (
    SqlAlchemyFinancialChangeRepository,
    current_financial_fact,
)
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialMovementRepository,
)
from bot_financeiro.persistence.payables import (
    SqlAlchemyPayableRepository,
    payable_history_entries,
    payables,
)
from bot_financeiro.persistence.planning import SqlAlchemyPlanningRepository
from bot_financeiro.persistence.resource_accounts import (
    SqlAlchemyResourceAccountRepository,
    resource_accounts,
)
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


class SqlAlchemyFinancialQuerySource:
    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        self._unit_of_work = SqlAlchemyUnitOfWork(session_factory)

    @property
    def session(self) -> Session:
        return self._unit_of_work.session

    def __enter__(self) -> SqlAlchemyFinancialQuerySource:
        self._unit_of_work.__enter__()
        return self

    def __exit__(
        self,
        exc_type: type[BaseException] | None,
        exc_value: BaseException | None,
        traceback: TracebackType | None,
    ) -> None:
        self._unit_of_work.__exit__(exc_type, exc_value, traceback)

    def list_accounts(self):
        account_ids = self.session.scalars(
            select(resource_accounts.c.id).order_by(resource_accounts.c.id)
        )
        repository = SqlAlchemyResourceAccountRepository(self.session)
        return tuple(
            account
            for account_id in account_ids
            if (account := repository.get(UUID(account_id))) is not None
        )

    def get_credit_card(self, card_id: UUID):
        return SqlAlchemyCreditCardRepository(self.session).get(card_id)

    def list_credit_cards(self):
        card_ids = self.session.scalars(select(credit_cards.c.id).order_by(credit_cards.c.id))
        repository = SqlAlchemyCreditCardRepository(self.session)
        return tuple(
            card for card_id in card_ids if (card := repository.get(UUID(card_id))) is not None
        )

    def list_payables(self):
        payable_ids = self.session.scalars(select(payables.c.id).order_by(payables.c.id))
        repository = SqlAlchemyPayableRepository(self.session)
        return tuple(
            payable
            for payable_id in payable_ids
            if (payable := repository.get(UUID(payable_id))) is not None
        )

    def list_planned_occurrences(self, through_date):
        repository = SqlAlchemyPlanningRepository(self.session)
        standalone = tuple(
            occurrence
            for occurrence in repository.list_standalone_occurrences()
            if occurrence.planned_date <= through_date
        )
        generated = tuple(
            repository.apply_history(occurrence)
            for recurrence in repository.list_recurrences()
            for occurrence in recurrence.occurrences_through(
                max((through_date, *repository.occurrence_source_dates(recurrence.recurrence_id)))
            )
            if occurrence.planned_date <= through_date
        )
        return tuple(
            sorted(
                (*standalone, *generated),
                key=lambda item: (item.planned_date, str(item.occurrence_id)),
            )
        )

    def list_movement_histories(
        self,
    ) -> tuple[FinancialFactHistory[FinancialMovement], ...]:
        movements = SqlAlchemyFinancialMovementRepository(self.session)
        changes = SqlAlchemyFinancialChangeRepository(
            self.session,
            original_non_movement_loader=self._original_non_movement,
        )
        return tuple(changes.history(item.event_id) for item in movements.list_recorded())

    def category_catalog(self):
        return SqlAlchemyFinancialCategoryRepository(self.session).catalog()

    def list_recognized_facts(self) -> tuple[RecognizedFactSnapshot, ...]:
        snapshots: list[RecognizedFactSnapshot] = []
        for history in self.list_movement_histories():
            current = history.current
            if recognized_financial_nature(current) is not None:
                snapshots.append(
                    self._recognized_snapshot(
                        history,
                        current,
                        self._recognized_record_type(current),
                    )
                )
        changes = SqlAlchemyFinancialChangeRepository(
            self.session,
            original_non_movement_loader=self._original_non_movement,
        )
        payable_ids = self.session.scalars(select(payable_history_entries.c.id))
        for event_id in payable_ids:
            history = changes.history(UUID(event_id))
            current = history.current
            if recognized_financial_nature(current) is not None:
                snapshots.append(
                    self._recognized_snapshot(
                        history,
                        current,
                        self._recognized_record_type(current),
                    )
                )
        card_repository = SqlAlchemyCreditCardRepository(self.session)
        card_event_ids = self.session.scalars(
            select(credit_card_history_entries.c.id).where(
                credit_card_history_entries.c.kind.not_in(
                    ("opened", "assignment_corrected", "installment_purchase_corrected")
                )
            )
        )
        for event_id in card_event_ids:
            history = changes.history(UUID(event_id))
            current = history.current
            if recognized_financial_nature(current) is None:
                continue
            if isinstance(current, InstallmentPurchase):
                card = card_repository.get(current.card_id)
                if card is None:
                    raise LookupError(f"credit card {current.card_id} does not exist")
                current = replace(
                    current,
                    allocation=card.expense_for_purchase(current.purchase_id),
                    description=card.description_for_purchase(current.purchase_id),
                )
                correction = next(
                    (
                        item
                        for item in reversed(card.history)
                        if isinstance(item, InstallmentPurchaseCorrected)
                        and item.purchase_id == current.purchase_id
                    ),
                    None,
                )
                snapshots.append(
                    self._recognized_snapshot(
                        history,
                        current,
                        FinancialRecordType.INSTALLMENT_PURCHASE,
                        version_id=(
                            history.version_id if correction is None else correction.event_id
                        ),
                        current_version_recorded_at=(
                            None if correction is None else correction.metadata.recorded_at
                        ),
                    )
                )
            else:
                snapshots.append(
                    self._recognized_snapshot(
                        history,
                        current,
                        self._recognized_record_type(current),
                    )
                )
        return tuple(snapshots)

    def list_expense_refunds(self) -> tuple[ExpenseRefundSnapshot, ...]:
        account_refunds = tuple(
            ExpenseRefundSnapshot(
                record_id=current.event_id,
                record_type=FinancialRecordType.ACCOUNT_EXPENSE_REFUND,
                original_expense_id=current.original_expense_id,
                amount=current.amount,
                effective_date=current.metadata.effective_date,
                recorded_at=current.metadata.recorded_at,
            )
            for history in self.list_movement_histories()
            if isinstance((current := history.current), AccountExpenseRefund)
        )
        card_repository = SqlAlchemyCreditCardRepository(self.session)
        card_refund_ids = self.session.scalars(
            select(credit_card_history_entries.c.id).where(
                credit_card_history_entries.c.kind == "refund"
            )
        )
        card_refunds = tuple(
            ExpenseRefundSnapshot(
                record_id=refund.event_id,
                record_type=FinancialRecordType.CARD_REFUND,
                original_expense_id=refund.purchase_id,
                amount=refund.amount,
                effective_date=refund.metadata.effective_date,
                recorded_at=refund.metadata.recorded_at,
            )
            for event_id in card_refund_ids
            if isinstance(
                (refund := card_repository.get_original_event(UUID(event_id))),
                CardRefund,
            )
        )
        return (*account_refunds, *card_refunds)

    def get_current_financial_fact(self, fact_id: UUID):
        movements = SqlAlchemyFinancialMovementRepository(self.session)
        original_movement = movements.get_recorded(fact_id)
        if original_movement is not None:
            return current_financial_fact(self.session, original_movement)
        original = self._original_non_movement(fact_id)
        current = current_financial_fact(self.session, original)
        if isinstance(current, InstallmentPurchase):
            card = SqlAlchemyCreditCardRepository(self.session).get(current.card_id)
            if card is None:
                raise LookupError(f"credit card {current.card_id} does not exist")
            return replace(
                current,
                allocation=card.expense_for_purchase(current.purchase_id),
                description=card.description_for_purchase(current.purchase_id),
            )
        return current

    def financial_fact_version_recorded_at(self, fact_id: UUID) -> datetime | None:
        changes = SqlAlchemyFinancialChangeRepository(
            self.session,
            original_non_movement_loader=self._original_non_movement,
        )
        try:
            history = changes.history(fact_id)
        except LookupError:
            return None
        if history.events:
            return history.events[-1].metadata.recorded_at
        return history.original.metadata.recorded_at

    def _original_non_movement(self, fact_id: UUID):
        payable_event = SqlAlchemyPayableRepository(self.session).get_original_event(fact_id)
        if payable_event is not None:
            return payable_event
        card_event = SqlAlchemyCreditCardRepository(self.session).get_original_event(fact_id)
        if card_event is not None:
            return card_event
        raise LookupError(f"financial fact {fact_id} does not exist")

    @staticmethod
    def _recognized_snapshot(
        history,
        current,
        record_type: FinancialRecordType,
        *,
        version_id: UUID | None = None,
        current_version_recorded_at: datetime | None = None,
    ) -> RecognizedFactSnapshot:
        current_recorded_at = (
            current_version_recorded_at
            if current_version_recorded_at is not None
            else (
                history.events[-1].metadata.recorded_at
                if history.events
                else history.original.metadata.recorded_at
            )
        )
        return RecognizedFactSnapshot(
            record_id=history.target_id,
            version_id=history.version_id if version_id is None else version_id,
            record_type=record_type,
            allocation=current.allocation,
            description=current.description,
            first_recorded_at=history.original.metadata.recorded_at,
            current_version_recorded_at=current_recorded_at,
        )

    @staticmethod
    def _recognized_record_type(current) -> FinancialRecordType:
        record_types = (
            (Income, FinancialRecordType.INCOME),
            (CashExpense, FinancialRecordType.CASH_EXPENSE),
            (PayableOpened, FinancialRecordType.PAYABLE_OPENED),
            (FinancialCharge, FinancialRecordType.PAYABLE_FINANCIAL_CHARGE),
            (CardPurchase, FinancialRecordType.CARD_PURCHASE),
            (InstallmentPurchase, FinancialRecordType.INSTALLMENT_PURCHASE),
            (InvoiceFinancialCharge, FinancialRecordType.INVOICE_FINANCIAL_CHARGE),
        )
        for fact_type, record_type in record_types:
            if isinstance(current, fact_type):
                return record_type
        raise TypeError(f"unsupported recognized financial fact: {type(current).__name__}")
