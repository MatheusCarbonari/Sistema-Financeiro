from dataclasses import replace
from datetime import UTC, date, datetime
from uuid import UUID

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy.engine import Engine
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import sessionmaker

from bot_financeiro.application.categories import (
    AllocationRepository,
    FinancialCategoryRepository,
)
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
from bot_financeiro.domain.money import Money
from bot_financeiro.persistence.categories import (
    SqlAlchemyAllocationRepository,
    SqlAlchemyFinancialCategoryRepository,
)
from bot_financeiro.persistence.database import create_sqlite_engine
from bot_financeiro.persistence.unit_of_work import SqlAlchemyUnitOfWork


def metadata(record_id: str, *, day: int = 1) -> RecordMetadata:
    return RecordMetadata(
        effective_date=date(2026, 9, day),
        recorded_at=datetime(2026, 9, day, 12, tzinfo=UTC),
        origin=RecordOrigin.MANUAL,
        record_id=UUID(record_id),
    )


def database_upgraded_from_0001() -> Engine:
    engine = create_sqlite_engine("sqlite+pysqlite:///:memory:")
    config = Config("alembic.ini")
    with engine.begin() as connection:
        config.attributes["connection"] = connection
        command.upgrade(config, "0001")
        command.upgrade(config, "head")
    return engine


def test_repositories_preserve_category_lifecycle_allocation_and_reclassification() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    source_id = UUID("484f718a-bf2b-46e9-9827-6a9be302a81e")
    target_id = UUID("3e5d13e9-859b-45b8-8019-e76cf64fd5b8")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Mercado",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=target_id,
        name="Supermercado",
        nature=FinancialNature.EXPENSE,
    ).archive_category(target_id)
    allocation = Allocation.create(
        total=Money("35.90", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(source_id, Money("35.90", "BRL")),),
        catalog=catalog,
        metadata=metadata("3450c515-b1c2-4c07-9c79-50c9151021f0"),
    )

    with unit_of_work:
        categories: FinancialCategoryRepository = SqlAlchemyFinancialCategoryRepository(
            unit_of_work.session
        )
        allocations: AllocationRepository = SqlAlchemyAllocationRepository(unit_of_work.session)
        for category in catalog.categories:
            categories.add(category)
        allocations.add(allocation)
        unit_of_work.commit()

    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        restored_catalog = categories.catalog()
        restored_allocation = allocations.get(allocation.allocation_id)

    assert restored_catalog == catalog
    assert restored_allocation == allocation
    assert restored_catalog.require(target_id).status is CategoryStatus.ARCHIVED

    active_catalog = restored_catalog.reactivate_category(target_id)
    assert restored_allocation is not None
    reclassified = restored_allocation.reclassify(
        source_category_id=source_id,
        target_category_id=target_id,
        catalog=active_catalog,
        metadata=metadata("f28447a4-b527-4a5a-87d1-3d96aa935c64", day=2),
    )
    without_source = active_catalog.delete_category(source_id, allocations=(reclassified,))

    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        categories.save(without_source.require(target_id))
        allocations.save(reclassified)
        categories.remove(source_id)
        unit_of_work.commit()

    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        final_catalog = categories.catalog()
        final_allocation = allocations.get(allocation.allocation_id)

    assert final_catalog == without_source
    assert final_allocation == reclassified
    assert final_allocation is not None
    assert final_allocation.reclassifications[0].source_category_id == source_id


def test_repository_cannot_remove_a_category_still_used_by_an_allocation() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    category_id = UUID("b8978036-7520-4650-a8bb-3f03c50f8f78")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Lazer",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("25.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("25.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("78d8146d-da20-4cd4-ac1a-24e048c2a040"),
    )
    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        categories.add(catalog.require(category_id))
        allocations.add(allocation)
        unit_of_work.commit()

    with pytest.raises(IntegrityError):
        with unit_of_work:
            categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
            categories.remove(category_id)
            unit_of_work.commit()

    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        restored = categories.get(category_id)

    assert restored == catalog.require(category_id)


def test_repository_rejects_rewriting_reclassification_history() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    source_id = UUID("068d9b0a-cba2-42ca-9dd8-69f70bace034")
    target_id = UUID("e36fad94-8dd9-433d-9329-f0135f61bf1e")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Origem",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=target_id,
        name="Destino",
        nature=FinancialNature.EXPENSE,
    )
    original = Allocation.create(
        total=Money("15.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(source_id, Money("15.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("21552bb7-d75c-49a4-a4f0-eb3b45dd3731"),
    )
    allocation = original.reclassify(
        source_category_id=source_id,
        target_category_id=target_id,
        catalog=catalog,
        metadata=metadata("5c7e526b-50cd-4ad6-b186-0774575f8ccd", day=2),
    )
    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        for category in catalog.categories:
            categories.add(category)
        allocations.add(original)
        allocations.save(allocation)
        unit_of_work.commit()

    rewritten_event = replace(allocation.reclassifications[0], source_category_name="Adulterada")
    rewritten = replace(allocation, reclassifications=(rewritten_event,))
    with pytest.raises(ValueError, match="history is immutable"):
        with unit_of_work:
            allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
            allocations.save(rewritten)


def test_repository_rejects_reclassification_without_a_history_event() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    source_id = UUID("83671b01-2d03-4c2c-8692-9e3345415744")
    target_id = UUID("b467f849-99b8-4f50-b47c-cb42636b4acb")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Origem",
        nature=FinancialNature.INCOME,
    )
    catalog = catalog.add_category(
        category_id=target_id,
        name="Destino",
        nature=FinancialNature.INCOME,
    )
    allocation = Allocation.create(
        total=Money("100.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(source_id, Money("100.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("f7e58e67-d490-41a2-98d1-0dd2d434ba94"),
    )
    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        for category in catalog.categories:
            categories.add(category)
        allocations.add(allocation)
        unit_of_work.commit()

    silently_changed = replace(
        allocation,
        parts=(AllocationPart(target_id, Money("100.00", "BRL")),),
    )
    with pytest.raises(ValueError, match="reclassification history"):
        with unit_of_work:
            allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
            allocations.save(silently_changed)


def test_category_repository_rejects_an_invalid_tree_and_structural_edits() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    parent_id = UUID("374df856-195b-4775-950c-7744b987985b")
    catalog = CategoryCatalog.empty().add_category(
        category_id=parent_id,
        name="Receitas",
        nature=FinancialNature.INCOME,
    )
    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        categories.add(catalog.require(parent_id))
        unit_of_work.commit()

    incompatible_child = FinancialCategory(
        category_id=UUID("dfd7bddc-15f3-4c7e-8a0d-6ff72a1663f5"),
        name="Incompatível",
        nature=FinancialNature.EXPENSE,
        parent_id=parent_id,
    )
    with pytest.raises(ValueError, match="same nature"):
        with unit_of_work:
            categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
            categories.add(incompatible_child)

    changed_nature = replace(catalog.require(parent_id), nature=FinancialNature.EXPENSE)
    with pytest.raises(ValueError, match="nature.*immutable"):
        with unit_of_work:
            categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
            categories.save(changed_nature)


def test_allocation_repository_rejects_a_category_of_incompatible_nature() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    category_id = UUID("f721cc38-5557-40bc-961e-c3a196f61258")
    catalog = CategoryCatalog.empty().add_category(
        category_id=category_id,
        name="Salário",
        nature=FinancialNature.INCOME,
    )
    invalid_allocation = Allocation(
        allocation_id=UUID("4e670b64-9c53-485f-bc8b-67118cb4f4dc"),
        total=Money("10.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(category_id, Money("10.00", "BRL")),),
        metadata=metadata("4e670b64-9c53-485f-bc8b-67118cb4f4dc"),
    )

    with pytest.raises(ValueError, match="same nature"):
        with unit_of_work:
            categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
            allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
            categories.add(catalog.require(category_id))
            allocations.add(invalid_allocation)


@pytest.mark.parametrize(
    ("archive_target", "source_name", "target_name", "expected_error"),
    [
        (True, "Origem", "Destino", "archived"),
        (False, "Nome falso", "Destino", "category names"),
        (False, "Origem", "Nome falso", "category names"),
    ],
)
def test_repository_validates_new_reclassification_against_the_current_catalog(
    archive_target: bool,
    source_name: str,
    target_name: str,
    expected_error: str,
) -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    source_id = UUID("2fe2ea4e-9a6a-48db-956e-cb829c01cd60")
    target_id = UUID("8aef9a86-5f5e-4550-b870-038c5a6eab5e")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Origem",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=target_id,
        name="Destino",
        nature=FinancialNature.EXPENSE,
    )
    allocation = Allocation.create(
        total=Money("20.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(source_id, Money("20.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("75776f2d-dbcd-461e-8555-a37542fdef84"),
    )
    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        for category in catalog.categories:
            categories.add(category)
        allocations.add(allocation)
        unit_of_work.commit()

    if archive_target:
        catalog = catalog.archive_category(target_id)
        with unit_of_work:
            categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
            categories.save(catalog.require(target_id))
            unit_of_work.commit()

    event = AllocationReclassification(
        source_category_id=source_id,
        source_category_name=source_name,
        target_category_id=target_id,
        target_category_name=target_name,
        amount=Money("20.00", "BRL"),
        metadata=metadata("03edb177-491f-4626-85c8-690b5868b22f", day=2),
    )
    forged = replace(
        allocation,
        parts=(AllocationPart(target_id, Money("20.00", "BRL")),),
        reclassifications=(event,),
    )

    with pytest.raises(ValueError, match=expected_error):
        with unit_of_work:
            allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
            allocations.save(forged)


@pytest.mark.parametrize(
    "statement",
    [
        "UPDATE allocation_reclassifications SET source_category_name = 'adulterado'",
        "DELETE FROM allocation_reclassifications",
    ],
)
def test_database_rejects_changes_to_reclassification_history(statement: str) -> None:
    engine = database_upgraded_from_0001()
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=engine))
    source_id = UUID("46c2b98a-68fc-41b2-868a-01123b615508")
    target_id = UUID("97f835ba-acec-4bef-ab02-d42ce0b088d9")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Origem",
        nature=FinancialNature.INCOME,
    )
    catalog = catalog.add_category(
        category_id=target_id,
        name="Destino",
        nature=FinancialNature.INCOME,
    )
    original = Allocation.create(
        total=Money("30.00", "BRL"),
        nature=FinancialNature.INCOME,
        parts=(AllocationPart(source_id, Money("30.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("9f71f3f2-3247-4a39-bd49-7f4ac0fd7941"),
    )
    allocation = original.reclassify(
        source_category_id=source_id,
        target_category_id=target_id,
        catalog=catalog,
        metadata=metadata("eb2e6f75-8d3c-4d8a-b5f5-b79450513c8b", day=2),
    )
    with unit_of_work:
        categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
        allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
        for category in catalog.categories:
            categories.add(category)
        allocations.add(original)
        allocations.save(allocation)
        unit_of_work.commit()

    with pytest.raises(IntegrityError, match="reclassification history is append-only"):
        with engine.begin() as connection:
            connection.exec_driver_sql(statement)


def test_repository_requires_a_new_allocation_to_start_without_reclassification_history() -> None:
    unit_of_work = SqlAlchemyUnitOfWork(sessionmaker(bind=database_upgraded_from_0001()))
    source_id = UUID("00a898f8-f092-4195-833d-cf719f8c76f2")
    target_id = UUID("60003a6f-07f9-44a3-8a0e-bf92ed50f40f")
    catalog = CategoryCatalog.empty().add_category(
        category_id=source_id,
        name="Origem",
        nature=FinancialNature.EXPENSE,
    )
    catalog = catalog.add_category(
        category_id=target_id,
        name="Destino",
        nature=FinancialNature.EXPENSE,
    )
    preclassified = Allocation.create(
        total=Money("40.00", "BRL"),
        nature=FinancialNature.EXPENSE,
        parts=(AllocationPart(source_id, Money("40.00", "BRL")),),
        catalog=catalog,
        metadata=metadata("a67f36a2-ae33-40a3-8e10-bc0e890c654c"),
    ).reclassify(
        source_category_id=source_id,
        target_category_id=target_id,
        catalog=catalog,
        metadata=metadata("97a4935e-70d0-4545-b0e9-eb1fd954ae66", day=2),
    )

    with pytest.raises(ValueError, match="without reclassification history"):
        with unit_of_work:
            categories = SqlAlchemyFinancialCategoryRepository(unit_of_work.session)
            allocations = SqlAlchemyAllocationRepository(unit_of_work.session)
            for category in catalog.categories:
                categories.add(category)
            allocations.add(preclassified)
