"""Create financial categories, allocations, and reclassification history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0002"
down_revision: str | None = "0001"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_categories",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("nature", sa.String(length=16), nullable=False),
        sa.Column("parent_id", sa.String(length=36), nullable=True),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["parent_id"], ["financial_categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("position"),
    )
    op.create_table(
        "allocations",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("total_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("nature", sa.String(length=16), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "allocation_parts",
        sa.Column("allocation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("category_id", sa.String(length=36), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.ForeignKeyConstraint(["allocation_id"], ["allocations.id"]),
        sa.ForeignKeyConstraint(["category_id"], ["financial_categories.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("allocation_id", "sequence"),
        sa.UniqueConstraint("allocation_id", "category_id"),
    )
    op.create_table(
        "allocation_reclassifications",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("allocation_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("source_category_id", sa.String(length=36), nullable=False),
        sa.Column("source_category_name", sa.String(), nullable=False),
        sa.Column("target_category_id", sa.String(length=36), nullable=False),
        sa.Column("target_category_name", sa.String(), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.ForeignKeyConstraint(["allocation_id"], ["allocations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("allocation_id", "sequence"),
    )
    op.execute(
        """
        CREATE TRIGGER allocation_reclassifications_no_update
        BEFORE UPDATE ON allocation_reclassifications
        BEGIN
            SELECT RAISE(ABORT, 'allocation reclassification history is append-only');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER allocation_reclassifications_no_delete
        BEFORE DELETE ON allocation_reclassifications
        BEGIN
            SELECT RAISE(ABORT, 'allocation reclassification history is append-only');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER allocation_reclassifications_no_delete")
    op.execute("DROP TRIGGER allocation_reclassifications_no_update")
    op.drop_table("allocation_reclassifications")
    op.drop_table("allocation_parts")
    op.drop_table("allocations")
    op.drop_table("financial_categories")
