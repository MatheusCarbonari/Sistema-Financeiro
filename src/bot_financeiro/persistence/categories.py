from datetime import datetime
from uuid import UUID

from sqlalchemy import (
    Column,
    Date,
    ForeignKey,
    Integer,
    String,
    Table,
    UniqueConstraint,
    delete,
    func,
    insert,
    select,
    update,
)
from sqlalchemy.orm import Session

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    AllocationReclassification,
    CategoryCatalog,
    CategoryStatus,
    FinancialCategory,
    FinancialNature,
)
from bot_financeiro.persistence.money import (
    money_from_minor_units,
    money_to_minor_units,
)
from bot_financeiro.persistence.schema import metadata

financial_categories = Table(
    "financial_categories",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("position", Integer, nullable=False, unique=True),
    Column("name", String, nullable=False),
    Column("nature", String(16), nullable=False),
    Column(
        "parent_id",
        String(36),
        ForeignKey("financial_categories.id", ondelete="RESTRICT"),
        nullable=True,
    ),
    Column("status", String(16), nullable=False),
)

allocations = Table(
    "allocations",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("total_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("nature", String(16), nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
)

allocation_parts = Table(
    "allocation_parts",
    metadata,
    Column("allocation_id", String(36), ForeignKey("allocations.id"), primary_key=True),
    Column("sequence", Integer, primary_key=True),
    Column(
        "category_id",
        String(36),
        ForeignKey("financial_categories.id", ondelete="RESTRICT"),
        nullable=False,
    ),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    UniqueConstraint("allocation_id", "category_id"),
)

allocation_reclassifications = Table(
    "allocation_reclassifications",
    metadata,
    Column("id", String(36), primary_key=True),
    Column("allocation_id", String(36), ForeignKey("allocations.id"), nullable=False),
    Column("sequence", Integer, nullable=False),
    Column("source_category_id", String(36), nullable=False),
    Column("source_category_name", String, nullable=False),
    Column("target_category_id", String(36), nullable=False),
    Column("target_category_name", String, nullable=False),
    Column("amount_minor_units", Integer, nullable=False),
    Column("currency", String(3), nullable=False),
    Column("effective_date", Date, nullable=False),
    Column("recorded_at", String, nullable=False),
    Column("origin", String(16), nullable=False),
    UniqueConstraint("allocation_id", "sequence"),
)


class SqlAlchemyFinancialCategoryRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, category: FinancialCategory) -> None:
        CategoryCatalog((*self.catalog().categories, category))
        last_position = self._session.scalar(select(func.max(financial_categories.c.position)))
        self._session.execute(
            insert(financial_categories).values(
                **self._values(category),
                position=0 if last_position is None else last_position + 1,
            )
        )

    def get(self, category_id: UUID) -> FinancialCategory | None:
        row = (
            self._session.execute(
                select(financial_categories).where(financial_categories.c.id == str(category_id))
            )
            .mappings()
            .one_or_none()
        )
        return None if row is None else self._category(row)

    def save(self, category: FinancialCategory) -> None:
        persisted = self.get(category.category_id)
        if persisted is None:
            raise LookupError(f"financial category {category.category_id} does not exist")
        if category.nature is not persisted.nature:
            raise ValueError("category nature is immutable")
        if category.parent_id != persisted.parent_id:
            raise ValueError("category parent is immutable")
        result = self._session.execute(
            update(financial_categories)
            .where(financial_categories.c.id == str(category.category_id))
            .values(self._values(category))
        )
        if result.rowcount != 1:
            raise RuntimeError("category disappeared during update")

    def remove(self, category_id: UUID) -> None:
        result = self._session.execute(
            delete(financial_categories).where(financial_categories.c.id == str(category_id))
        )
        if result.rowcount != 1:
            raise LookupError(f"financial category {category_id} does not exist")

    def catalog(self) -> CategoryCatalog:
        rows = self._session.execute(
            select(financial_categories).order_by(financial_categories.c.position)
        ).mappings()
        return CategoryCatalog(tuple(self._category(row) for row in rows))

    @staticmethod
    def _values(category: FinancialCategory) -> dict[str, object]:
        return {
            "id": str(category.category_id),
            "name": category.name,
            "nature": category.nature.value,
            "parent_id": None if category.parent_id is None else str(category.parent_id),
            "status": category.status.value,
        }

    @staticmethod
    def _category(row: object) -> FinancialCategory:
        return FinancialCategory(
            category_id=UUID(row["id"]),
            name=row["name"],
            nature=FinancialNature(row["nature"]),
            parent_id=None if row["parent_id"] is None else UUID(row["parent_id"]),
            status=CategoryStatus(row["status"]),
        )


class SqlAlchemyAllocationRepository:
    def __init__(self, session: Session) -> None:
        self._session = session

    def add(self, allocation: Allocation) -> None:
        if allocation.reclassifications:
            raise ValueError("new allocation must be persisted without reclassification history")
        allocation.ensure_categories_compatible(
            SqlAlchemyFinancialCategoryRepository(self._session).catalog(),
            require_active=True,
        )
        self._session.execute(insert(allocations).values(self._allocation_values(allocation)))
        self._replace_parts(allocation)
        self._append_reclassifications(allocation, start_at=0)

    def get(self, allocation_id: UUID) -> Allocation | None:
        row = (
            self._session.execute(select(allocations).where(allocations.c.id == str(allocation_id)))
            .mappings()
            .one_or_none()
        )
        if row is None:
            return None
        return self._validated_allocation(row)

    def save(self, allocation: Allocation) -> None:
        persisted = self.get(allocation.allocation_id)
        if persisted is None:
            raise LookupError(f"allocation {allocation.allocation_id} does not exist")
        allocation.ensure_append_only_successor_of(persisted)
        catalog = SqlAlchemyFinancialCategoryRepository(self._session).catalog()
        allocation.ensure_new_reclassifications_compatible(persisted, catalog)
        allocation.ensure_categories_compatible(
            catalog,
            require_active=False,
        )
        persisted_events = len(persisted.reclassifications)

        self._replace_parts(allocation)
        self._append_reclassifications(allocation, start_at=persisted_events)

    def list_all(self) -> tuple[Allocation, ...]:
        rows = self._session.execute(select(allocations).order_by(allocations.c.id)).mappings()
        return tuple(self._validated_allocation(row) for row in rows)

    def _validated_allocation(self, row: object) -> Allocation:
        allocation = self._allocation(row)
        allocation.ensure_categories_compatible(
            SqlAlchemyFinancialCategoryRepository(self._session).catalog(),
            require_active=False,
        )
        return allocation

    @staticmethod
    def _allocation_values(allocation: Allocation) -> dict[str, object]:
        return {
            "id": str(allocation.allocation_id),
            "total_minor_units": money_to_minor_units(allocation.total),
            "currency": allocation.total.currency,
            "nature": allocation.nature.value,
            "effective_date": allocation.metadata.effective_date,
            "recorded_at": allocation.metadata.recorded_at.isoformat(),
            "origin": allocation.metadata.origin.value,
        }

    def _replace_parts(self, allocation: Allocation) -> None:
        self._session.execute(
            delete(allocation_parts).where(
                allocation_parts.c.allocation_id == str(allocation.allocation_id)
            )
        )
        self._session.execute(
            insert(allocation_parts),
            [
                {
                    "allocation_id": str(allocation.allocation_id),
                    "sequence": sequence,
                    "category_id": str(part.category_id),
                    "amount_minor_units": money_to_minor_units(part.amount),
                    "currency": part.amount.currency,
                }
                for sequence, part in enumerate(allocation.parts)
            ],
        )

    def _append_reclassifications(self, allocation: Allocation, *, start_at: int) -> None:
        values = [
            {
                "id": str(event.metadata.record_id),
                "allocation_id": str(allocation.allocation_id),
                "sequence": sequence,
                "source_category_id": str(event.source_category_id),
                "source_category_name": event.source_category_name,
                "target_category_id": str(event.target_category_id),
                "target_category_name": event.target_category_name,
                "amount_minor_units": money_to_minor_units(event.amount),
                "currency": event.amount.currency,
                "effective_date": event.metadata.effective_date,
                "recorded_at": event.metadata.recorded_at.isoformat(),
                "origin": event.metadata.origin.value,
            }
            for sequence, event in enumerate(allocation.reclassifications)
            if sequence >= start_at
        ]
        if values:
            self._session.execute(insert(allocation_reclassifications), values)

    def _allocation(self, row: object) -> Allocation:
        allocation_id = row["id"]
        part_rows = self._session.execute(
            select(allocation_parts)
            .where(allocation_parts.c.allocation_id == allocation_id)
            .order_by(allocation_parts.c.sequence)
        ).mappings()
        event_rows = self._session.execute(
            select(allocation_reclassifications)
            .where(allocation_reclassifications.c.allocation_id == allocation_id)
            .order_by(allocation_reclassifications.c.sequence)
        ).mappings()
        return Allocation(
            allocation_id=UUID(allocation_id),
            total=money_from_minor_units(row["total_minor_units"], row["currency"]),
            nature=FinancialNature(row["nature"]),
            parts=tuple(
                AllocationPart(
                    category_id=UUID(part["category_id"]),
                    amount=money_from_minor_units(part["amount_minor_units"], part["currency"]),
                )
                for part in part_rows
            ),
            metadata=RecordMetadata(
                effective_date=row["effective_date"],
                recorded_at=datetime.fromisoformat(row["recorded_at"]),
                origin=RecordOrigin(row["origin"]),
                record_id=UUID(allocation_id),
            ),
            reclassifications=tuple(
                AllocationReclassification(
                    source_category_id=UUID(event["source_category_id"]),
                    source_category_name=event["source_category_name"],
                    target_category_id=UUID(event["target_category_id"]),
                    target_category_name=event["target_category_name"],
                    amount=money_from_minor_units(event["amount_minor_units"], event["currency"]),
                    metadata=RecordMetadata(
                        effective_date=event["effective_date"],
                        recorded_at=datetime.fromisoformat(event["recorded_at"]),
                        origin=RecordOrigin(event["origin"]),
                        record_id=UUID(event["id"]),
                    ),
                )
                for event in event_rows
            ),
        )
