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
from bot_financeiro.domain.money import Money
from bot_financeiro.domain.movements import BillPayment
from bot_financeiro.domain.payables import FinancialCharge, Payable, PayableOpened
from bot_financeiro.persistence.categories import SqlAlchemyAllocationRepository, allocations
from bot_financeiro.persistence.financial_operations import (
    SqlAlchemyFinancialMovementRepository,
    SqlAlchemyFinancialOperationUnitOfWork,
    financial_movements,
)
from bot_financeiro.persistence.money import money_from_minor_units, money_to_minor_units
from bot_financeiro.persistence.resource_accounts import resource_accounts
from bot_financeiro.persistence.schema import metadata

payables = Table(
    "payables",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("due_date", Date, nullable=False),
)

payable_history_entries = Table(
    "payable_history_entries",
    metadata,
    Column("id", String(36), primary_key=True),
    Column(
        "payable_id",
        String(36),
        ForeignKey(payables.c.id, ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("sequence", Integer, nullable=False),
    Column("kind", String(24), nullable=False),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("allocation_id", String(36), ForeignKey(allocations.c.id), nullable=True),
    Column("account_id", String(36), ForeignKey(resource_accounts.c.id), nullable=True),
    Column("movement_id", String(36), ForeignKey(financial_movements.c.id), nullable=True),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    CheckConstraint("amount_minor_units > 0", name="ck_payable_history_positive_amount"),
    CheckConstraint(
        """
        (kind IN ('opened', 'financial_charge') AND allocation_id IS NOT NULL
            AND account_id IS NULL AND movement_id IS NULL)
        OR
        (kind = 'payment' AND allocation_id IS NULL
            AND account_id IS NOT NULL AND movement_id = id)
        """,
        name="ck_payable_history_shape",
    ),
    UniqueConstraint("payable_id", "sequence"),
)


class SqlAlchemyPayableRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, payable: Payable) -> None:
        if len(payable.history) != 1:
            raise ValueError("new payable must be persisted with only its opening")
        opening = payable.opening
        self._session.execute(
            insert(payables).values(id=str(payable.payable_id), due_date=opening.due_date)
        )
        self._session.execute(
            insert(payable_history_entries).values(
                self._opening_values(payable.payable_id, opening)
            )
        )

    def get(self, payable_id: UUID) -> Payable | None:
        payable_row = (
            self._session.execute(select(payables).where(payables.c.id == str(payable_id)))
            .mappings()
            .one_or_none()
        )
        if payable_row is None:
            return None
        history_rows = tuple(
            self._session.execute(
                select(payable_history_entries)
                .where(payable_history_entries.c.payable_id == str(payable_id))
                .order_by(payable_history_entries.c.sequence)
            ).mappings()
        )
        if not history_rows or history_rows[0]["kind"] != "opened":
            raise ValueError("persisted payable history is invalid")
        allocation = SqlAlchemyAllocationRepository(self._session).get(
            UUID(history_rows[0]["allocation_id"])
        )
        if allocation is None:
            raise ValueError("payable opening allocation does not exist")
        opening = PayableOpened(allocation=allocation, due_date=payable_row["due_date"])
        self._validate_event_row(
            history_rows[0],
            opening.event_id,
            opening.allocation.total,
            opening.allocation.metadata,
        )
        events: list[PayableOpened | BillPayment | FinancialCharge] = [opening]
        for row in history_rows[1:]:
            events.append(self._event(row))
        return Payable(history=tuple(events))

    def save(self, payable: Payable) -> None:
        persisted = self.get(payable.payable_id)
        if persisted is None:
            raise LookupError(f"payable {payable.payable_id} does not exist")
        payable.ensure_append_only_successor_of(persisted)
        persisted_size = len(persisted.history)
        new_events = payable.history[persisted_size:]
        for sequence, event in enumerate(new_events, persisted_size):
            self._session.execute(
                insert(payable_history_entries).values(
                    self._event_values(payable.payable_id, sequence, event)
                )
            )

    @staticmethod
    def _opening_values(payable_id: UUID, opening: PayableOpened) -> dict[str, object]:
        return {
            "id": str(opening.event_id),
            "payable_id": str(payable_id),
            "sequence": 0,
            "kind": "opened",
            "amount_minor_units": money_to_minor_units(opening.allocation.total),
            "currency": opening.allocation.total.currency,
            "allocation_id": str(opening.allocation.allocation_id),
            "account_id": None,
            "movement_id": None,
            "effective_date": opening.allocation.metadata.effective_date,
            "recorded_at": opening.allocation.metadata.recorded_at.isoformat(),
            "origin": opening.allocation.metadata.origin.value,
        }

    @staticmethod
    def _event_values(
        payable_id: UUID,
        sequence: int,
        event: BillPayment | FinancialCharge,
    ) -> dict[str, object]:
        if isinstance(event, BillPayment):
            allocation_id = None
            account_id = str(event.account_id)
            movement_id = str(event.movement_id)
            kind = "payment"
            event_metadata = event.metadata
        else:
            allocation_id = str(event.allocation.allocation_id)
            account_id = None
            movement_id = None
            kind = "financial_charge"
            event_metadata = event.allocation.metadata
        return {
            "id": str(event.event_id),
            "payable_id": str(payable_id),
            "sequence": sequence,
            "kind": kind,
            "amount_minor_units": money_to_minor_units(event.amount),
            "currency": event.amount.currency,
            "allocation_id": allocation_id,
            "account_id": account_id,
            "movement_id": movement_id,
            "effective_date": event_metadata.effective_date,
            "recorded_at": event_metadata.recorded_at.isoformat(),
            "origin": event_metadata.origin.value,
        }

    def _event(self, row: object) -> BillPayment | FinancialCharge:
        event_metadata = RecordMetadata(
            effective_date=row["effective_date"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            origin=RecordOrigin(row["origin"]),
            record_id=UUID(row["id"]),
        )
        if row["kind"] == "payment":
            payment = BillPayment(
                payable_id=UUID(row["payable_id"]),
                account_id=UUID(row["account_id"]),
                amount=money_from_minor_units(row["amount_minor_units"], row["currency"]),
                metadata=event_metadata,
            )
            movement = SqlAlchemyFinancialMovementRepository(self._session).get(
                payment.movement_id
            )
            if movement != payment:
                raise ValueError(
                    "payable payment history diverges from its financial movement"
                )
            return payment
        if row["kind"] == "financial_charge":
            allocation = SqlAlchemyAllocationRepository(self._session).get(
                UUID(row["allocation_id"])
            )
            if allocation is None:
                raise ValueError("financial charge allocation does not exist")
            self._validate_event_row(
                row,
                allocation.allocation_id,
                allocation.total,
                allocation.metadata,
            )
            return FinancialCharge(
                payable_id=UUID(row["payable_id"]),
                allocation=allocation,
            )
        raise ValueError(f"unknown payable history kind: {row['kind']}")

    @staticmethod
    def _validate_event_row(
        row: object,
        event_id: UUID,
        event_amount: Money,
        event_metadata: RecordMetadata,
    ) -> None:
        persisted_metadata = RecordMetadata(
            effective_date=row["effective_date"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            origin=RecordOrigin(row["origin"]),
            record_id=UUID(row["id"]),
        )
        persisted_amount = money_from_minor_units(row["amount_minor_units"], row["currency"])
        if (
            UUID(row["id"]) != event_id
            or persisted_amount != event_amount
            or persisted_metadata != event_metadata
        ):
            raise ValueError("payable history diverges from its domain event")


class SqlAlchemyPayableUnitOfWork(SqlAlchemyFinancialOperationUnitOfWork):
    payables: SqlAlchemyPayableRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)

    def __enter__(self) -> SqlAlchemyPayableUnitOfWork:
        super().__enter__()
        self.payables = SqlAlchemyPayableRepository(self.session)
        return self
