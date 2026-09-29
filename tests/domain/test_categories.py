from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest

from bot_financeiro.domain.audit import RecordMetadata, RecordOrigin
from bot_financeiro.domain.categories import (
    Allocation,
    AllocationPart,
    CategoryCatalog,
    CategoryStatus,
    FinancialCategory,
    FinancialNature,
)
from bot_financeiro.domain.money import Money


def metadata(record_id: str, *, day: int = 1) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def test_catalog_keeps_income_and_expense_trees_separate_with_at_most_two_levels() -> None:
    catalog = CategoryCatalog.empty()
    income_root_id = UUID("1615ef16-cf31-43cd-9000-ade6fffc78f2")
    expense_root_id = UUID("32e54271-6b30-4335-91fd-9fd13bd65236")
    expense_child_id = UUID("92293456-9ca6-4c1c-a3aa-11732525576c")
    catalog = catalog.add_category(
        category_id=income_root_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    catalog = catalog.add_category(
        category_id=expense_root_id,
        name="Alimentação",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=expense_child_id,
        name="Supermercado",
        nature=FinancialNature.EXPENSE,
        parent_id=expense_root_id,
    )

    assert tuple(category.name for category in catalog.categories_for(FinancialNature.INCOME)) == (
        "Salário",
    )
    assert tuple(category.name for category in catalog.categories_for(FinancialNature.EXPENSE)) == (
        "Alimentação",
        "Supermercado",
    )

    with pytest.raises(ValueError, match="same nature"):
        catalog.add_category(
            category_id=UUID("ed171f66-050c-41d5-8394-163d087c8c9a"),
            name="Natureza errada",
            nature=FinancialNature.INCOME,
            parent_id=expense_root_id,
        )

    with pytest.raises(ValueError, match="two levels"):
        catalog.add_category(
            category_id=UUID("bb4f61a3-417f-4a67-ab31-71775cf15163"),
            name="Terceiro nível",
            nature=FinancialNature.EXPENSE,
            parent_id=expense_child_id,
        )


def test_allocation_parts_must_equal_the_exact_total() -> None:
    category_id = UUID("3802e0ba-a1a4-40cb-b6bf-d3bfc9e916b0")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Alimentação",
        nature=FinancialNature.EXPENSE,
    )

    with pytest.raises(ValueError, match="exact total"):
        Allocation.create(
            total=Money("100.00", "BRL"),
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(category_id, Money("99.99", "BRL")),),
            catalog=catalog,
            metadata=metadata("f0558de8-fc00-464d-b5a6-639c03df4506"),
        )

    allocation = Allocation.create(
        total=Money("100.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("100.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("f0558de8-fc00-464d-b5a6-639c03df4506"),
    )

    assert allocation.total == Money("100.00", "BRL")
    assert allocation.parts == (AllocationPart(category_id, Money("100.00", "BRL")),)


def test_allocation_rejects_a_category_of_incompatible_nature() -> None:
    income_category_id = UUID("f93b18d6-c39e-4f14-a25c-f35a40e3bc57")
    catalog = CategoryCatalog.empty().add_category(
        category_id=income_category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )

    with pytest.raises(ValueError, match="nature"):
        Allocation.create(
            total=Money("50.00", "BRL"),
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(income_category_id, Money("50.00", "BRL")),),
            catalog=catalog,
            metadata=metadata("a05c82ea-b19c-446a-8169-13e6ead420f0"),
        )


def test_parent_accepts_direct_allocation_and_aggregates_its_child_values() -> None:
    root_id = UUID("401d2e39-2687-48f8-884c-1dbb673678fc")
    child_id = UUID("04b02f96-a79a-4a78-a77b-9468eea92e22")
    catalog = CategoryCatalog.empty().add_category(
        category_id=root_id,
        name="Alimentação",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=child_id,
        name="Supermercado",
        nature=FinancialNature.EXPENSE,
        parent_id=root_id,
    )
    allocation = Allocation.create(
        total=Money("100.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(
            AllocationPart(root_id, Money("40.00", "BRL")),
            AllocationPart(child_id, Money("60.00", "BRL")),
        ),
        catalog=catalog,
        metadata=metadata("f5905af4-b82c-4973-ad2c-80e7060b0f91"),
    )

    assert catalog.allocated_total(
        root_id, (allocation,), currency="BRL", include_descendants=False
    ) == Money("40.00", "BRL")
    assert catalog.allocated_total(root_id, (allocation,), currency="BRL") == Money("100.00", "BRL")
    assert catalog.allocated_total(child_id, (allocation,), currency="BRL") == Money("60.00", "BRL")


def test_archived_category_keeps_history_rejects_new_allocations_and_can_reactivate() -> None:
    used_id = UUID("3412df6d-a70a-4560-84f5-3d50b91dca78")
    unused_id = UUID("b3ca98ec-52a7-4e22-95d7-19b8f7cf9c3c")
    catalog = CategoryCatalog.empty().add_category(
        category_id=used_id,
        name="Lazer",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=unused_id,
        name="Cursos",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("200.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(used_id, Money("200.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("1ae8b0c7-c21f-4165-8614-a524a1f001f6"),
    )

    archived = catalog.archive_category(used_id)

    assert archived.require(used_id).status is CategoryStatus.ARCHIVED
    assert archived.allocated_total(used_id, (allocation,), currency="BRL") == Money(
        "200.00", "BRL"
    )
    assert archived.categories_with_allocations(
        FinancialNature.EXPENSE, (allocation,), currency="BRL"
    ) == (archived.require(used_id),)
    assert archived.require(unused_id).status is CategoryStatus.ACTIVE
    with pytest.raises(ValueError, match="archived"):
        Allocation.create(
            total=Money("10.00", "BRL"),
            nature=FinancialNature.EXPENSE,
            parts=(AllocationPart(used_id, Money("10.00", "BRL")),),
            catalog=archived,
            metadata=metadata("d3fb3a6a-c23a-4054-a2a0-d7b26cbe14cd", day=2),
        )

    reactivated = archived.reactivate_category(used_id)
    assert reactivated.require(used_id).status is CategoryStatus.ACTIVE


def test_used_category_must_be_reclassified_before_safe_deletion() -> None:
    source_id = UUID("ad947ece-0a56-41aa-a799-8b3bb6b9d97f")
    replacement_id = UUID("84cbda3d-4b34-4710-a0d9-b9802c3a01e9")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=replacement_id,
        name="Supermercado",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("75.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(source_id, Money("75.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("a98a6f5a-dddf-424d-909c-c96573d91c29"),
    )

    with pytest.raises(ValueError, match="reclassified"):
        catalog.delete_category(source_id, allocations=(allocation,))

    reclassification = metadata("3c74e6fd-16d0-4cdc-a1f2-03ae46d10cab", day=2)
    reclassified = allocation.reclassify(
        source_category_id=source_id,
        target_category_id=replacement_id,
        catalog=catalog,
        metadata=reclassification,
    )

    assert allocation.parts == (AllocationPart(source_id, Money("75.00", "BRL")),)
    assert reclassified.parts == (AllocationPart(replacement_id, Money("75.00", "BRL")),)
    assert len(reclassified.reclassifications) == 1
    event = reclassified.reclassifications[0]
    assert event.source_category_id == source_id
    assert event.target_category_id == replacement_id
    assert event.source_category_name == "Mercado"
    assert event.target_category_name == "Supermercado"
    assert event.amount == Money("75.00", "BRL")
    assert event.metadata == reclassification

    without_source = catalog.delete_category(source_id, allocations=(reclassified,))
    assert without_source.find(source_id) is None
    assert without_source.require(replacement_id).name == "Supermercado"


@pytest.mark.parametrize("target_is_archived", [False, True])
def test_reclassification_rejects_an_incompatible_or_archived_target(
    target_is_archived: bool,
) -> None:
    source_id = UUID("074af74c-c4a2-4386-840d-52bd35ee85e9")
    target_id = UUID("6fc86ed9-0f31-46fe-a367-ab0644e38806")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Origem",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=target_id,
        name="Destino",
        nature=(FinancialNature.EXPENSE if target_is_archived else FinancialNature.INCOME),
    )
    if target_is_archived:
        catalog = catalog.archive_category(target_id)
    allocation = Allocation.create(
        total=Money("10.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(source_id, Money("10.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("879e80ef-6436-4f4d-acfb-707f5e44ea53"),
    )

    expected_error = "archived" if target_is_archived else "nature"
    with pytest.raises(ValueError, match=expected_error):
        allocation.reclassify(
            source_category_id=source_id,
            target_category_id=target_id,
            catalog=catalog,
            metadata=metadata("c610eaf9-1448-429e-a5d3-a44df24572ab", day=2),
        )


def test_category_with_children_cannot_be_deleted() -> None:
    parent_id = UUID("5c154170-679c-46a1-87ba-ced0f142e544")
    child_id = UUID("ccbb2a8e-485a-42fa-8045-93cedbc1e0b5")
    catalog = CategoryCatalog.empty().add_category(
        category_id=parent_id,
        name="Moradia",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=child_id,
        name="Energia",
        nature=FinancialNature.EXPENSE,
        parent_id=parent_id,
    )

    with pytest.raises(ValueError, match="children"):
        catalog.delete_category(parent_id, allocations=())


def test_category_can_be_renamed_without_changing_its_classification() -> None:
    category_id = UUID("52e81487-92a3-40e1-88ce-adcfbf707e01")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )

    renamed = catalog.rename_category(category_id, "Supermercado")

    category = renamed.require(category_id)
    assert category.name == "Supermercado"
    assert category.nature is FinancialNature.EXPENSE
    assert category.parent_id is None
    assert category.status is CategoryStatus.ACTIVE


def test_allocation_cannot_be_reconstructed_with_a_total_different_from_its_parts() -> None:
    category_id = UUID("cbd1020e-d8b5-484d-973f-0eab47b536ec")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("1000.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(category_id, Money("1000.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("9279221c-c788-441a-a63d-72bf0344e169"),
    )

    with pytest.raises(ValueError, match="exact total"):
        replace(allocation, total=Money("999.99", "BRL"))


def test_catalog_cannot_be_reconstructed_with_an_invalid_tree() -> None:
    orphan = FinancialCategory(
        category_id=UUID("2f495f47-c721-4eaa-a762-a0c9522fcba5"),
        name="Órfã",
        nature=FinancialNature.EXPENSE,
        parent_id=UUID("5036587e-f059-4b7e-b1e3-c821a4d7a89d"),
    )

    with pytest.raises(ValueError, match="parent"):
        CategoryCatalog(categories=(orphan,))


def test_allocation_distributes_an_amount_proportionally_in_stable_exact_order() -> None:
    first_id = UUID("00000000-0000-0000-0000-000000000001")
    second_id = UUID("00000000-0000-0000-0000-000000000002")
    allocation = Allocation(
        allocation_id=UUID("00000000-0000-0000-0000-000000000003"),
        total=Money("30.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(
            AllocationPart(second_id, Money("10.00", "BRL")),
            AllocationPart(first_id, Money("20.00", "BRL")),
        ),
        metadata=metadata("00000000-0000-0000-0000-000000000003"),
    )

    distributed = allocation.distribute_proportionally(Money("10.01", "BRL"))

    assert distributed == (
        AllocationPart(first_id, Money("6.67", "BRL")),
        AllocationPart(second_id, Money("3.34", "BRL")),
    )
    assert (
        sum(
            (part.amount.amount for part in distributed),
            start=Money("0", "BRL").amount,
        )
        == Money("10.01", "BRL").amount
    )
