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
    insert,
    select,
)
from sqlalchemy.orm import Session, sessionmaker

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.movements import CashExpense, FinancialMovement, Income, Transfer
from bot_financeiro.persistence.categories import (
    SqlAlchemyAllocationRepository,
    SqlAlchemyFinancialCategoryRepository,
    allocations,
)
from bot_financeiro.persistence.money import money_from_minor_units, money_to_minor_units
from bot_financeiro.persistence.resource_accounts import (
    SqlAlchemyResourceAccountRepository,
    resource_accounts,
)
from bot_financeiro.persistence.schema import metadata
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork

financial_movements = Table(
    "financial_movements",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("kind", String(24), nullable=False),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("account_id", String(36), ForeignKey(resource_accounts.c.id), nullable=True),
    Column("source_account_id", String(36), ForeignKey(resource_accounts.c.id), nullable=True),
    Column(
        "destination_account_id", String(36), ForeignKey(resource_accounts.c.id), nullable=True
    ),
    Column("allocation_id", String(36), ForeignKey(allocations.c.id), nullable=True, unique=True),
    Column("related_transfer_id", String(36), ForeignKey("financial_movements.id"), nullable=True),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    CheckConstraint("amount_minor_units > 0", name="ck_financial_movement_positive_amount"),
    CheckConstraint(
        """
        (kind = 'income' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            AND related_transfer_id IS NULL)
        OR
        (kind = 'cash_expense' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL)
        OR
        (kind = 'transfer' AND account_id IS NULL AND allocation_id IS NULL
            AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
            AND source_account_id <> destination_account_id AND related_transfer_id IS NULL)
        """,
        name="ck_financial_movement_shape",
    ),
)


class SqlAlchemyFinancialMovementRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, movement: FinancialMovement) -> None:
        if isinstance(movement, CashExpense) and movement.related_transfer_id is not None:
            self._require_transfer_reference(movement.related_transfer_id)
        values: dict[str, object] = {
            "id": str(movement.movement_id),
            "kind": self._kind(movement),
            "amount_minor_units": money_to_minor_units(movement.amount),
            "currency": movement.amount.currency,
            "account_id": None,
            "source_account_id": None,
            "destination_account_id": None,
            "allocation_id": None,
            "related_transfer_id": None,
            "effective_date": movement.metadata.effective_date,
            "recorded_at": movement.metadata.recorded_at.isoformat(),
            "origin": movement.metadata.origin.value,
        }
        if isinstance(movement, Transfer):
            values["source_account_id"] = str(movement.source_account_id)
            values["destination_account_id"] = str(movement.destination_account_id)
        else:
            values["account_id"] = str(movement.account_id)
            values["allocation_id"] = str(movement.allocation.allocation_id)
            if isinstance(movement, CashExpense) and movement.related_transfer_id is not None:
                values["related_transfer_id"] = str(movement.related_transfer_id)
        self._session.execute(insert(financial_movements).values(values))

    def get(self, movement_id: UUID) -> FinancialMovement | None:
        row = (
            self._session.execute(
                select(financial_movements).where(financial_movements.c.id == str(movement_id))
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._movement(row)

    def list_all(self) -> tuple[FinancialMovement, ...]:
        rows = self._session.execute(
            select(financial_movements).order_by(
                financial_movements.c.effective_date,
                financial_movements.c.recorded_at,
                financial_movements.c.id,
            )
        ).mappings()
        return tuple(self._movement(row) for row in rows)

    def _movement(self, row: object) -> FinancialMovement:
        movement_metadata = RecordMetadata(
            effective_date=row["effective_date"],
            recorded_at=datetime.fromisoformat(row["recorded_at"]),
            origin=RecordOrigin(row["origin"]),
            record_id=UUID(row["id"]),
        )
        amount = money_from_minor_units(row["amount_minor_units"], row["currency"])
        if row["kind"] == "transfer":
            return Transfer(
                source_account_id=UUID(row["source_account_id"]),
                destination_account_id=UUID(row["destination_account_id"]),
                amount=amount,
                metadata=movement_metadata,
            )
        allocation = SqlAlchemyAllocationRepository(self._session).get(UUID(row["allocation_id"]))
        if allocation is None:
            raise ValueError("financial movement allocation does not exist")
        if allocation.total != amount or allocation.metadata != movement_metadata:
            raise ValueError("financial movement diverges from its allocation")
        if row["kind"] == "income":
            return Income(account_id=UUID(row["account_id"]), allocation=allocation)
        if row["kind"] == "cash_expense":
            related_transfer_id = (
                None
                if row["related_transfer_id"] is None
                else UUID(row["related_transfer_id"])
            )
            if related_transfer_id is not None:
                self._require_transfer_reference(related_transfer_id)
            return CashExpense(
                account_id=UUID(row["account_id"]),
                allocation=allocation,
                related_transfer_id=related_transfer_id,
            )
        raise ValueError(f"unknown financial movement kind: {row['kind']}")

    def _require_transfer_reference(self, movement_id: UUID) -> None:
        kind = self._session.scalar(
            select(financial_movements.c.kind).where(
                financial_movements.c.id == str(movement_id)
            )
        )
        if kind != "transfer":
            raise ValueError("transfer fee must reference a transfer")

    @staticmethod
    def _kind(movement: FinancialMovement) -> str:
        if isinstance(movement, Income):
            return "income"
        if isinstance(movement, CashExpense):
            return "cash_expense"
        return "transfer"


class SqlAlchemyFinancialOperationUnitOfWork(SqlAlchemyUnitOfWork):
    accounts: SqlAlchemyResourceAccountRepository
    categories: SqlAlchemyFinancialCategoryRepository
    allocations: SqlAlchemyAllocationRepository
    movements: SqlAlchemyFinancialMovementRepository

    def __init__(self, session_factory: sessionmaker[Session]) -> None:
        super().__init__(session_factory)

    def __enter__(self) -> SqlAlchemyFinancialOperationUnitOfWork:
        super().__enter__()
        self.accounts = SqlAlchemyResourceAccountRepository(self.session)
        self.categories = SqlAlchemyFinancialCategoryRepository(self.session)
        self.allocations = SqlAlchemyAllocationRepository(self.session)
        self.movements = SqlAlchemyFinancialMovementRepository(self.session)
        return self
