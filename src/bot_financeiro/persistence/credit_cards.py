from __future__ import annotations

from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    CheckConstraint,
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    insert,
    select,
)
from sqlalchemy.orm import Session, sessionmaker

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.credit_cards import (
    CardCreditApplied,
    CardCreditGranted,
    CardPurchase,
    CardRefund,
    CreditCard,
    CreditCardEvent,
    CreditCardOpened,
    Installment,
    InstallmentPurchase,
    InstallmentPurchaseCorrected,
    InvoiceAssignmentCorrected,
    InvoiceCycle,
    InvoiceFinancialCharge,
)
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import InvoicePayment
from bot_financeiro.persistence.categories import SqlAlchemyAllocationRepository, allocations
from bot_financeiro.persistence.financial_changes import current_financial_fact
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialMovementRepository,
    SqlAlchemyFinancialOperationUnitOfWork,
    financial_movements,
)
from bot_financeiro.persistence.money import money_from_minor_units, money_to_minor_units
from bot_financeiro.persistence.resource_accounts import resource_accounts
from bot_financeiro.persistence.schema import metadata

credit_cards = Table(
    "credit_cards",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("name", String, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("closing_day", Integer, nullable=False),
    Column("due_day", Integer, nullable=False),
    Column("limit_minor_units", Integer, nullable=True),
    CheckConstraint("closing_day BETWEEN 1 AND 31", name="ck_credit_card_closing_day"),
    CheckConstraint("due_day BETWEEN 1 AND 31", name="ck_credit_card_due_day"),
    CheckConstraint(
        "limit_minor_units IS NULL OR limit_minor_units > 0",
        name="ck_credit_card_positive_limit",
    ),
)

credit_card_history_entries = Table(
    "credit_card_history_entries",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("card_id", String(36), ForeignKey(credit_cards.c.id), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("kind", String(32), nullable=False),
    Column("amount_minor_units", Integer, nullable=True),
    Column("applied_amount_minor_units", Integer, nullable=True),
    Column("principal_reduction_minor_units", Integer, nullable=True),
    Column("currency", String(3), nullable=True),
    Column("allocation_id", String(36), ForeignKey(allocations.c.id), nullable=True),
    Column("account_id", String(36), ForeignKey(resource_accounts.c.id), nullable=True),
    Column("movement_id", String(36), ForeignKey(financial_movements.c.id), nullable=True),
    Column("purchase_id", String(36), nullable=True),
    Column("invoice_closing_date", Date, nullable=True),
    Column("previous_closing_date", Date, nullable=True),
    Column("target_closing_date", Date, nullable=True),
    Column("source_event_id", String(36), nullable=True),
    Column("reason", String, nullable=True),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    Column("description", String, nullable=True),
    CheckConstraint(
        """
        (kind = 'opened' AND amount_minor_units IS NULL AND currency IS NULL)
        OR
        (kind IN ('purchase', 'financial_charge', 'installment_purchase')
            AND amount_minor_units > 0
            AND currency IS NOT NULL AND allocation_id IS NOT NULL
            AND (invoice_closing_date IS NOT NULL) =
                (kind IN ('purchase', 'financial_charge')))
        OR
        (kind = 'installment_purchase_corrected' AND amount_minor_units > 0
            AND currency IS NOT NULL AND allocation_id IS NOT NULL
            AND purchase_id IS NOT NULL AND reason IS NOT NULL)
        OR
        (kind = 'assignment_corrected' AND amount_minor_units IS NULL
            AND purchase_id IS NOT NULL AND previous_closing_date IS NOT NULL
            AND target_closing_date IS NOT NULL AND reason IS NOT NULL)
        OR
        (kind = 'invoice_payment' AND amount_minor_units > 0
            AND applied_amount_minor_units >= 0
            AND applied_amount_minor_units <= amount_minor_units
            AND currency IS NOT NULL AND account_id IS NOT NULL
            AND movement_id = id AND invoice_closing_date IS NOT NULL)
        OR
        (kind = 'refund' AND amount_minor_units > 0
            AND principal_reduction_minor_units >= 0
            AND principal_reduction_minor_units <= amount_minor_units
            AND currency IS NOT NULL AND purchase_id IS NOT NULL)
        OR
        (kind = 'credit_granted' AND amount_minor_units > 0
            AND currency IS NOT NULL AND source_event_id IS NOT NULL
            AND reason IS NOT NULL)
        OR
        (kind = 'credit_applied' AND amount_minor_units > 0
            AND currency IS NOT NULL AND invoice_closing_date IS NOT NULL)
        """,
        name="ck_credit_card_history_shape",
    ),
    CheckConstraint(
        """
        (allocation_id IS NOT NULL) =
            (kind IN ('purchase', 'financial_charge', 'installment_purchase',
                'installment_purchase_corrected'))
        AND (account_id IS NOT NULL) = (kind = 'invoice_payment')
        AND (movement_id IS NOT NULL) = (kind = 'invoice_payment')
        AND (purchase_id IS NOT NULL) =
            (kind IN ('assignment_corrected', 'refund',
                'installment_purchase_corrected'))
        AND (invoice_closing_date IS NOT NULL) =
            (kind IN ('purchase', 'financial_charge', 'invoice_payment', 'credit_applied'))
        AND (previous_closing_date IS NOT NULL) = (kind = 'assignment_corrected')
        AND (target_closing_date IS NOT NULL) = (kind = 'assignment_corrected')
        AND (source_event_id IS NOT NULL) = (kind = 'credit_granted')
        AND (reason IS NOT NULL) =
            (kind IN ('assignment_corrected', 'credit_granted',
                'installment_purchase_corrected'))
        AND (applied_amount_minor_units IS NOT NULL) = (kind = 'invoice_payment')
        AND (principal_reduction_minor_units IS NOT NULL) = (kind = 'refund')
        AND (currency IS NOT NULL) =
            (kind IN ('purchase', 'financial_charge', 'invoice_payment',
                'refund', 'credit_granted', 'credit_applied',
                'installment_purchase', 'installment_purchase_corrected'))
        """,
        name="ck_credit_card_history_columns",
    ),
    UniqueConstraint("card_id", "sequence"),
)

credit_card_installments = Table(
    "credit_card_installments",
    metadata,
    Column(
        "event_id",
        String(36),
        ForeignKey(credit_card_history_entries.c.id),
        primary_key=True,
    ),
    Column("role", String(16), primary_key=True),
    Column("position", Integer, primary_key=True),
    Column("purchase_id", String(36), nullable=False),
    Column("installment_sequence", Integer, nullable=False),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("invoice_closing_date", Date, nullable=False),
    Column("invoice_due_date", Date, nullable=False),
    CheckConstraint(
        "role IN ('original', 'previous', 'frozen', 'replacement')",
        name="ck_credit_card_installment_role",
    ),
    CheckConstraint(
        "position >= 1 AND installment_sequence >= 1 AND amount_minor_units > 0",
        name="ck_credit_card_installment_positive_values",
    ),
)


class SqlAlchemyCreditCardRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, card: CreditCard) -> None:
        if len(card.history) != 1:
            raise ValueError("new credit card must contain only its opening")
        opening = card.opening
        self._session.execute(
            insert(credit_cards).values(
                id=str(card.card_id),
                name=opening.name,
                currency=opening.currency,
                closing_day=opening.closing_day,
                due_day=opening.due_day,
                limit_minor_units=(
                    None if opening.limit is None else money_to_minor_units(opening.limit)
                ),
            )
        )
        self._session.execute(
            insert(credit_card_history_entries).values(self._event_values(card.card_id, 0, opening))
        )

    def get(self, card_id: UUID) -> CreditCard | None:
        card_row = (
            self._session.execute(select(credit_cards).where(credit_cards.c.id == str(card_id)))
            .mappings()
            .one_or_none()
        )
        if card_row is None:
            return None
        rows = tuple(
            self._session.execute(
                select(credit_card_history_entries)
                .where(credit_card_history_entries.c.card_id == str(card_id))
                .order_by(credit_card_history_entries.c.sequence)
            ).mappings()
        )
        if not rows or rows[0]["kind"] != "opened":
            raise ValueError("persisted credit card history is invalid")
        opening_metadata = self._metadata(rows[0])
        limit = (
            None
            if card_row["limit_minor_units"] is None
            else money_from_minor_units(card_row["limit_minor_units"], card_row["currency"])
        )
        opening = CreditCardOpened(
            name=card_row["name"],
            currency=card_row["currency"],
            closing_day=card_row["closing_day"],
            due_day=card_row["due_day"],
            limit=limit,
            metadata=opening_metadata,
        )
        if opening.event_id != UUID(rows[0]["id"]):
            raise ValueError("credit card opening history diverges")
        events: list[CreditCardEvent] = [opening]
        for row in rows[1:]:
            event = self._event(row, card_row["currency"])
            if event is not None and isinstance(
                event, (CardPurchase, InstallmentPurchase, InvoiceFinancialCharge)
            ):
                event = current_financial_fact(self._session, event)
            if event is not None:
                events.append(event)
        return CreditCard(history=tuple(events))

    def get_original_event(self, event_id: UUID) -> CreditCardEvent | None:
        row = (
            self._session.execute(
                select(credit_card_history_entries).where(
                    credit_card_history_entries.c.id == str(event_id)
                )
            )
            .mappings()
            .one_or_none()
        )
        if row is None or row["kind"] == "opened":
            return None
        currency = row["currency"]
        if currency is None:
            currency = self._session.scalar(
                select(credit_cards.c.currency).where(credit_cards.c.id == row["card_id"])
            )
        return self._event(row, currency)

    def save(self, card: CreditCard) -> None:
        persisted = self.get(card.card_id)
        if persisted is None:
            raise LookupError(f"credit card {card.card_id} does not exist")
        card.ensure_append_only_successor_of(persisted)
        start = len(persisted.history)
        last_sequence = self._session.scalar(
            select(credit_card_history_entries.c.sequence)
            .where(credit_card_history_entries.c.card_id == str(card.card_id))
            .order_by(credit_card_history_entries.c.sequence.desc())
            .limit(1)
        )
        for sequence, event in enumerate(card.history[start:], (last_sequence or 0) + 1):
            self._session.execute(
                insert(credit_card_history_entries).values(
                    self._event_values(card.card_id, sequence, event)
                )
            )
            self._add_installments(event)

    def _event(self, row: object, currency: str) -> CreditCardEvent | None:
        kind = row["kind"]
        event_metadata = self._metadata(row)
        amount = (
            None
            if row["amount_minor_units"] is None
            else money_from_minor_units(row["amount_minor_units"], row["currency"])
        )
        if kind in {"purchase", "financial_charge", "installment_purchase"}:
            allocation = SqlAlchemyAllocationRepository(self._session).get(
                UUID(row["allocation_id"])
            )
            if (
                allocation is None
                or allocation.allocation_id != UUID(row["id"])
                or allocation.total != amount
                or allocation.metadata != event_metadata
            ):
                raise ValueError("credit card event diverges from its allocation")
            if kind == "purchase":
                return CardPurchase(
                    card_id=UUID(row["card_id"]),
                    allocation=allocation,
                    invoice_closing_date=row["invoice_closing_date"],
                    description=row["description"],
                )
            if kind == "installment_purchase":
                return InstallmentPurchase(
                    card_id=UUID(row["card_id"]),
                    allocation=allocation,
                    installments=self._installments(UUID(row["id"]), "original"),
                    description=row["description"],
                )
            return InvoiceFinancialCharge(
                card_id=UUID(row["card_id"]),
                invoice_closing_date=row["invoice_closing_date"],
                allocation=allocation,
                description=row["description"],
            )
        if kind == "assignment_corrected":
            return InvoiceAssignmentCorrected(
                card_id=UUID(row["card_id"]),
                purchase_id=UUID(row["purchase_id"]),
                previous_closing_date=row["previous_closing_date"],
                target_closing_date=row["target_closing_date"],
                reason=row["reason"],
                metadata=event_metadata,
            )
        if kind == "installment_purchase_corrected":
            allocation = SqlAlchemyAllocationRepository(self._session).get(
                UUID(row["allocation_id"])
            )
            if allocation is None or allocation.total != amount:
                raise ValueError("installment correction diverges from its allocation")
            return InstallmentPurchaseCorrected(
                card_id=UUID(row["card_id"]),
                purchase_id=UUID(row["purchase_id"]),
                corrected_allocation=allocation,
                previous_installments=self._installments(UUID(row["id"]), "previous"),
                frozen_installments=self._installments(UUID(row["id"]), "frozen"),
                replacement_installments=self._installments(UUID(row["id"]), "replacement"),
                description=row["description"],
                reason=row["reason"],
                metadata=event_metadata,
            )
        if kind == "invoice_payment":
            movement = SqlAlchemyFinancialMovementRepository(self._session).get_recorded(
                UUID(row["movement_id"])
            )
            if not isinstance(movement, InvoicePayment):
                raise ValueError("invoice payment history has no financial movement")
            persisted_applied = money_from_minor_units(
                row["applied_amount_minor_units"], row["currency"]
            )
            if (
                movement.event_id != UUID(row["id"])
                or movement.card_id != UUID(row["card_id"])
                or movement.account_id != UUID(row["account_id"])
                or movement.invoice_closing_date != row["invoice_closing_date"]
                or movement.amount != amount
                or movement.applied_amount != persisted_applied
                or movement.metadata != event_metadata
            ):
                raise ValueError("invoice payment history diverges from its movement")
            current = current_financial_fact(self._session, movement)
            if current is not None and not isinstance(current, InvoicePayment):
                raise ValueError("invoice payment correction has an invalid type")
            return current
        if kind == "refund":
            return CardRefund(
                card_id=UUID(row["card_id"]),
                purchase_id=UUID(row["purchase_id"]),
                amount=self._required_amount(amount),
                principal_reduction=money_from_minor_units(
                    row["principal_reduction_minor_units"], row["currency"]
                ),
                metadata=event_metadata,
            )
        if kind == "credit_granted":
            return CardCreditGranted(
                card_id=UUID(row["card_id"]),
                amount=self._required_amount(amount),
                source_event_id=UUID(row["source_event_id"]),
                reason=row["reason"],
                metadata=event_metadata,
                description=row["description"],
            )
        if kind == "credit_applied":
            return CardCreditApplied(
                card_id=UUID(row["card_id"]),
                invoice_closing_date=row["invoice_closing_date"],
                amount=self._required_amount(amount),
                metadata=event_metadata,
            )
        raise ValueError(f"unknown credit card history kind: {kind}")

    @staticmethod
    def _event_values(
        card_id: UUID,
        sequence: int,
        event: CreditCardEvent,
    ) -> dict[str, object]:
        values: dict[str, object] = {
            "id": str(event.event_id),
            "card_id": str(card_id),
            "sequence": sequence,
            "kind": "opened",
            "amount_minor_units": None,
            "applied_amount_minor_units": None,
            "principal_reduction_minor_units": None,
            "currency": None,
            "allocation_id": None,
            "account_id": None,
            "movement_id": None,
            "purchase_id": None,
            "invoice_closing_date": None,
            "previous_closing_date": None,
            "target_closing_date": None,
            "source_event_id": None,
            "reason": None,
            "description": getattr(event, "description", None),
        }
        event_metadata = event.metadata if hasattr(event, "metadata") else event.allocation.metadata
        if isinstance(event, CardPurchase):
            values.update(
                kind="purchase",
                amount_minor_units=money_to_minor_units(event.amount),
                currency=event.amount.currency,
                allocation_id=str(event.allocation.allocation_id),
                invoice_closing_date=event.invoice_closing_date,
            )
        elif isinstance(event, InstallmentPurchase):
            values.update(
                kind="installment_purchase",
                amount_minor_units=money_to_minor_units(event.amount),
                currency=event.amount.currency,
                allocation_id=str(event.allocation.allocation_id),
            )
        elif isinstance(event, InstallmentPurchaseCorrected):
            values.update(
                kind="installment_purchase_corrected",
                amount_minor_units=money_to_minor_units(event.corrected_allocation.total),
                currency=event.corrected_allocation.total.currency,
                allocation_id=str(event.corrected_allocation.allocation_id),
                purchase_id=str(event.purchase_id),
                reason=event.reason,
            )
        elif isinstance(event, InvoiceAssignmentCorrected):
            values.update(
                kind="assignment_corrected",
                purchase_id=str(event.purchase_id),
                previous_closing_date=event.previous_closing_date,
                target_closing_date=event.target_closing_date,
                reason=event.reason,
            )
        elif isinstance(event, InvoicePayment):
            values.update(
                kind="invoice_payment",
                amount_minor_units=money_to_minor_units(event.amount),
                applied_amount_minor_units=money_to_minor_units(event.applied_amount),
                currency=event.amount.currency,
                account_id=str(event.account_id),
                movement_id=str(event.movement_id),
                invoice_closing_date=event.invoice_closing_date,
            )
        elif isinstance(event, InvoiceFinancialCharge):
            values.update(
                kind="financial_charge",
                amount_minor_units=money_to_minor_units(event.amount),
                currency=event.amount.currency,
                allocation_id=str(event.allocation.allocation_id),
                invoice_closing_date=event.invoice_closing_date,
            )
        elif isinstance(event, CardRefund):
            values.update(
                kind="refund",
                amount_minor_units=money_to_minor_units(event.amount),
                principal_reduction_minor_units=money_to_minor_units(event.principal_reduction),
                currency=event.amount.currency,
                purchase_id=str(event.purchase_id),
            )
        elif isinstance(event, CardCreditGranted):
            values.update(
                kind="credit_granted",
                amount_minor_units=money_to_minor_units(event.amount),
                currency=event.amount.currency,
                source_event_id=str(event.source_event_id),
                reason=event.reason,
            )
        elif isinstance(event, CardCreditApplied):
            values.update(
                kind="credit_applied",
                amount_minor_units=money_to_minor_units(event.amount),
                currency=event.amount.currency,
                invoice_closing_date=event.invoice_closing_date,
            )
        values.update(
            effective_date=event_metadata.effective_date,
            recorded_at=event_metadata.recorded_at.isoformat(),
            origin=event_metadata.origin.value,
        )
        return values

    def _add_installments(self, event: CreditCardEvent) -> None:
        groups: tuple[tuple[str, tuple[Installment, ...]], ...] = ()
        if isinstance(event, InstallmentPurchase):
            groups = (("original", event.installments),)
        elif isinstance(event, InstallmentPurchaseCorrected):
            groups = (
                ("previous", event.previous_installments),
                ("frozen", event.frozen_installments),
                ("replacement", event.replacement_installments),
            )
        for role, installments in groups:
            for position, installment in enumerate(installments, start=1):
                self._session.execute(
                    insert(credit_card_installments).values(
                        event_id=str(event.event_id),
                        role=role,
                        position=position,
                        purchase_id=str(installment.purchase_id),
                        installment_sequence=installment.sequence,
                        amount_minor_units=money_to_minor_units(installment.amount),
                        currency=installment.amount.currency,
                        invoice_closing_date=installment.cycle.closing_date,
                        invoice_due_date=installment.cycle.due_date,
                    )
                )

    def _installments(self, event_id: UUID, role: str) -> tuple[Installment, ...]:
        rows = tuple(
            self._session.execute(
                select(credit_card_installments)
                .where(credit_card_installments.c.event_id == str(event_id))
                .where(credit_card_installments.c.role == role)
                .order_by(credit_card_installments.c.position)
            ).mappings()
        )
        return tuple(
            Installment(
                purchase_id=UUID(row["purchase_id"]),
                sequence=row["installment_sequence"],
                amount=money_from_minor_units(row["amount_minor_units"], row["currency"]),
                cycle=InvoiceCycle(
                    closing_date=row["invoice_closing_date"],
                    due_date=row["invoice_due_date"],
                ),
            )
            for row in rows
        )

    @staticmethod
    def _metadata(row: object) -> RecordMetadata:
        return RecordMetadata(
            effective_date=row["effective_date"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            origin=RecordOrigin(row["origin"]),
            record_id=UUID(row["id"]),
        )

    @staticmethod
    def _required_amount(amount: Money | None) -> Money:
        if amount is None:
            raise ValueError("credit card history amount is required")
        return amount


class SqlAlchemyCreditCardUnitOfWork(SqlAlchemyFinancialOperationUnitOfWork):
    credit_cards: SqlAlchemyCreditCardRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)

    def __enter__(self) -> SqlAlchemyCreditCardUnitOfWork:
        super().__enter__()
        self.credit_cards = SqlAlchemyCreditCardRepository(self.session)
        return self
