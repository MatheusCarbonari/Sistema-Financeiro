from __future__ import annotations

from dataclasses import dataclass, replace
from decimal import Decimal
from enum import StrEnum
from uuid import UUID

from bot_financeiro.domain.audit import RecordMetadata
from bot_financeiro.domain.money import BRL_CENT, CurrencyMismatchError, Money


class FinancialNature(StrEnum):
    INCOME = "income"
    EXPENSE = "expense"


class CategoryStatus(StrEnum):
    ACTIVE = "active"
    ARCHIVED = "archived"


@dataclass(frozen=True, slots=True)
class FinancialCategory:
    category_id: UUID
    name: str
    nature: FinancialNature
    parent_id: UUID | None
    status: CategoryStatus = CategoryStatus.ACTIVE

    def __post_init__(self) -> None:
        if not isinstance(self.name, str) or not self.name.strip():
            raise ValueError("category name is required")
        if not isinstance(self.nature, FinancialNature):
            raise ValueError("category nature is invalid")
        if not isinstance(self.status, CategoryStatus):
            raise ValueError("category status is invalid")


@dataclass(frozen=True, slots=True)
class AllocationPart:
    category_id: UUID
    amount: Money


@dataclass(frozen=True, slots=True)
class AllocationReclassification:
    source_category_id: UUID
    source_category_name: str
    target_category_id: UUID
    target_category_name: str
    amount: Money
    metadata: RecordMetadata

    def __post_init__(self) -> None:
        if self.source_category_id == self.target_category_id:
            raise ValueError("reclassification requires a different target category")
        if not self.source_category_name.strip() or not self.target_category_name.strip():
            raise ValueError("reclassification must preserve category names")


@dataclass(frozen=True, slots=True)
class Allocation:
    allocation_id: UUID
    total: Money
    nature: FinancialNature
    parts: tuple[AllocationPart, ...]
    metadata: RecordMetadata
    reclassifications: tuple[AllocationReclassification, ...] = ()

    def __post_init__(self) -> None:
        if self.allocation_id != self.metadata.record_id:
            raise ValueError("allocation identity must match its registration metadata")
        if not isinstance(self.parts, tuple) or not self.parts:
            raise ValueError("allocation requires at least one part")
        category_ids = [part.category_id for part in self.parts]
        if len(set(category_ids)) != len(category_ids):
            raise ValueError("allocation accepts only one part per category")

        allocated_amount = Money("0", self.total.currency)
        for part in self.parts:
            if part.amount.currency != self.total.currency:
                raise CurrencyMismatchError("allocation parts must use the total currency")
            allocated_amount += part.amount
        if allocated_amount != self.total:
            raise ValueError("allocation parts must equal the exact total")

        event_ids = [event.metadata.record_id for event in self.reclassifications]
        if len(set(event_ids)) != len(event_ids):
            raise ValueError("reclassification identities must be unique")
        if any(event.amount.currency != self.total.currency for event in self.reclassifications):
            raise CurrencyMismatchError("reclassifications must use the allocation currency")

    @classmethod
    def create(
        cls,
        *,
        total: Money,
        nature: FinancialNature,
        parts: tuple[AllocationPart, ...],
        catalog: CategoryCatalog,
        metadata: RecordMetadata,
    ) -> Allocation:
        cls._ensure_categories_compatible(parts, nature, catalog, require_active=True)
        return cls(
            allocation_id=metadata.record_id,
            total=total,
            nature=nature,
            parts=parts,
            metadata=metadata,
        )

    def ensure_categories_compatible(
        self,
        catalog: CategoryCatalog,
        *,
        require_active: bool,
    ) -> None:
        self._ensure_categories_compatible(
            self.parts,
            self.nature,
            catalog,
            require_active=require_active,
        )

    def distribute_proportionally(self, amount: Money) -> tuple[AllocationPart, ...]:
        """Distribute an amount using the current Rateios and a stable residual order."""
        if amount.currency != self.total.currency:
            raise CurrencyMismatchError(
                "proportional distribution requires the allocation currency"
            )
        if amount.amount < 0:
            raise ValueError("proportional distribution requires a non-negative amount")
        if self.total.amount <= 0:
            raise ValueError("proportional distribution requires a positive allocation")

        parts = tuple(sorted(self.parts, key=lambda part: str(part.category_id)))
        scale = (
            BRL_CENT
            if amount.currency == "BRL"
            else Decimal(1).scaleb(
                min(
                    value.amount.as_tuple().exponent
                    for value in (amount, *(part.amount for part in parts))
                )
            )
        )
        amount_units = int(amount.amount / scale)
        total_units = sum(int(part.amount.amount / scale) for part in parts)
        weighted = [
            divmod(amount_units * int(part.amount.amount / scale), total_units) for part in parts
        ]
        shares = [base for base, _ in weighted]
        residual_units = amount_units - sum(shares)
        residual_order = sorted(
            range(len(parts)),
            key=lambda index: (-weighted[index][1], str(parts[index].category_id)),
        )
        for index in residual_order[:residual_units]:
            shares[index] += 1
        return tuple(
            AllocationPart(
                category_id=part.category_id,
                amount=Money(Decimal(units) * scale, amount.currency),
            )
            for part, units in zip(parts, shares, strict=True)
        )

    @staticmethod
    def _ensure_categories_compatible(
        parts: tuple[AllocationPart, ...],
        nature: FinancialNature,
        catalog: CategoryCatalog,
        *,
        require_active: bool,
    ) -> None:
        for part in parts:
            category = catalog.require(part.category_id)
            if category.nature is not nature:
                raise ValueError("allocation and category must have the same nature")
            if require_active and category.status is CategoryStatus.ARCHIVED:
                raise ValueError("archived category rejects new allocations")

    def reclassify(
        self,
        *,
        source_category_id: UUID,
        target_category_id: UUID,
        catalog: CategoryCatalog,
        metadata: RecordMetadata,
    ) -> Allocation:
        source = catalog.require(source_category_id)
        target = catalog.require(target_category_id)
        if source.nature is not self.nature:
            raise ValueError("allocation and source category must have the same nature")
        if target.nature is not self.nature:
            raise ValueError("allocation and target category must have the same nature")
        if target.status is CategoryStatus.ARCHIVED:
            raise ValueError("archived category rejects reclassification")

        source_part = next(
            (part for part in self.parts if part.category_id == source_category_id),
            None,
        )
        if source_part is None:
            raise ValueError("allocation does not use the source category")

        event = AllocationReclassification(
            source_category_id=source_category_id,
            source_category_name=source.name,
            target_category_id=target_category_id,
            target_category_name=target.name,
            amount=source_part.amount,
            metadata=metadata,
        )
        return replace(
            self,
            parts=self._parts_after(self.parts, event),
            reclassifications=(*self.reclassifications, event),
        )

    def ensure_append_only_successor_of(self, previous: Allocation) -> None:
        if (
            self.allocation_id != previous.allocation_id
            or self.total != previous.total
            or self.nature is not previous.nature
            or self.metadata != previous.metadata
        ):
            raise ValueError("persisted allocation identity and total are immutable")

        previous_event_count = len(previous.reclassifications)
        if (
            len(self.reclassifications) < previous_event_count
            or self.reclassifications[:previous_event_count] != previous.reclassifications
        ):
            raise ValueError("persisted reclassification history is immutable")

        expected_parts = previous.parts
        for event in self.reclassifications[previous_event_count:]:
            expected_parts = self._parts_after(expected_parts, event)
        if self.parts != expected_parts:
            raise ValueError("parts do not match the reclassification history")

    def ensure_new_reclassifications_compatible(
        self,
        previous: Allocation,
        catalog: CategoryCatalog,
    ) -> None:
        for event in self.reclassifications[len(previous.reclassifications) :]:
            source = catalog.require(event.source_category_id)
            target = catalog.require(event.target_category_id)
            if source.nature is not self.nature or target.nature is not self.nature:
                raise ValueError("reclassification categories must have the same nature")
            if target.status is CategoryStatus.ARCHIVED:
                raise ValueError("archived category rejects reclassification")
            if (
                event.source_category_name != source.name
                or event.target_category_name != target.name
            ):
                raise ValueError("reclassification category names must match the catalog")

    @staticmethod
    def _parts_after(
        parts: tuple[AllocationPart, ...],
        event: AllocationReclassification,
    ) -> tuple[AllocationPart, ...]:
        source_part = next(
            (part for part in parts if part.category_id == event.source_category_id),
            None,
        )
        if source_part is None or source_part.amount != event.amount:
            raise ValueError("parts do not match the reclassification history")
        target_part = next(
            (part for part in parts if part.category_id == event.target_category_id),
            None,
        )
        if target_part is None:
            return tuple(
                AllocationPart(event.target_category_id, part.amount)
                if part.category_id == event.source_category_id
                else part
                for part in parts
            )
        return tuple(
            AllocationPart(event.target_category_id, part.amount + source_part.amount)
            if part.category_id == event.target_category_id
            else part
            for part in parts
            if part.category_id != event.source_category_id
        )


@dataclass(frozen=True, slots=True)
class CategoryCatalog:
    categories: tuple[FinancialCategory, ...]

    def __post_init__(self) -> None:
        if not isinstance(self.categories, tuple):
            raise ValueError("category catalog must be an immutable tuple")
        categories_by_id = {category.category_id: category for category in self.categories}
        if len(categories_by_id) != len(self.categories):
            raise ValueError("category identities must be unique")
        for category in self.categories:
            if category.parent_id is None:
                continue
            parent = categories_by_id.get(category.parent_id)
            if parent is None:
                raise ValueError("category parent does not exist in the catalog")
            if parent.nature is not category.nature:
                raise ValueError("parent and child category must have the same nature")
            if parent.parent_id is not None:
                raise ValueError("category hierarchy supports at most two levels")

    @classmethod
    def empty(cls) -> CategoryCatalog:
        return cls(categories=())

    def add_category(
        self,
        *,
        category_id: UUID,
        name: str,
        nature: FinancialNature,
        parent_id: UUID | None = None,
    ) -> CategoryCatalog:
        if self.find(category_id) is not None:
            raise ValueError("category identity already exists")
        if parent_id is not None:
            parent = self.require(parent_id)
            if parent.nature is not nature:
                raise ValueError("parent and child category must have the same nature")
            if parent.parent_id is not None:
                raise ValueError("category hierarchy supports at most two levels")
        category = FinancialCategory(
            category_id=category_id,
            name=name.strip(),
            nature=nature,
            parent_id=parent_id,
        )
        return replace(self, categories=(*self.categories, category))

    def find(self, category_id: UUID) -> FinancialCategory | None:
        return next(
            (category for category in self.categories if category.category_id == category_id),
            None,
        )

    def require(self, category_id: UUID) -> FinancialCategory:
        category = self.find(category_id)
        if category is None:
            raise LookupError(f"financial category {category_id} does not exist")
        return category

    def archive_category(self, category_id: UUID) -> CategoryCatalog:
        return self._replace_category(
            replace(self.require(category_id), status=CategoryStatus.ARCHIVED)
        )

    def rename_category(self, category_id: UUID, name: str) -> CategoryCatalog:
        return self._replace_category(replace(self.require(category_id), name=name.strip()))

    def reactivate_category(self, category_id: UUID) -> CategoryCatalog:
        return self._replace_category(
            replace(self.require(category_id), status=CategoryStatus.ACTIVE)
        )

    def delete_category(
        self,
        category_id: UUID,
        *,
        allocations: tuple[Allocation, ...],
    ) -> CategoryCatalog:
        self.require(category_id)
        if any(category.parent_id == category_id for category in self.categories):
            raise ValueError("category with children cannot be deleted")
        if any(
            part.category_id == category_id
            for allocation in allocations
            for part in allocation.parts
        ):
            raise ValueError("used category must have its allocations reclassified")
        return replace(
            self,
            categories=tuple(
                category for category in self.categories if category.category_id != category_id
            ),
        )

    def categories_for(self, nature: FinancialNature) -> tuple[FinancialCategory, ...]:
        return tuple(category for category in self.categories if category.nature is nature)

    def allocated_total(
        self,
        category_id: UUID,
        allocations: tuple[Allocation, ...],
        *,
        currency: str,
        include_descendants: bool = True,
    ) -> Money:
        self.require(category_id)
        included_ids = {category_id}
        if include_descendants:
            included_ids.update(
                category.category_id
                for category in self.categories
                if category.parent_id == category_id
            )

        total = Money("0", currency)
        for allocation in allocations:
            if allocation.total.currency != currency:
                continue
            for part in allocation.parts:
                if part.category_id in included_ids:
                    total += part.amount
        return total

    def categories_with_allocations(
        self,
        nature: FinancialNature,
        allocations: tuple[Allocation, ...],
        *,
        currency: str,
    ) -> tuple[FinancialCategory, ...]:
        return tuple(
            category
            for category in self.categories_for(nature)
            if self.allocated_total(category.category_id, allocations, currency=currency).amount
            != 0
        )

    def _replace_category(self, replacement: FinancialCategory) -> CategoryCatalog:
        return replace(
            self,
            categories=tuple(
                replacement if category.category_id == replacement.category_id else category
                for category in self.categories
            ),
        )
