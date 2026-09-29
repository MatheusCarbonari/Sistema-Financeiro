"""Add searchable financial descriptions without inventing legacy values."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0009"
down_revision: str | None = "0008"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


_DESCRIPTION_TABLES = (
    "account_history_entries",
    "financial_movements",
    "payable_history_entries",
    "credit_card_history_entries",
    "planned_occurrences",
    "planning_recurrences",
    "planning_recurrence_revisions",
    "planning_occurrence_events",
)


def upgrade() -> None:
    for table_name in _DESCRIPTION_TABLES:
        op.add_column(table_name, sa.Column("description", sa.String(), nullable=True))
        _create_non_blank_trigger(table_name)
    op.add_column(
        "financial_fact_changes",
        sa.Column("replacement_description", sa.String(), nullable=True),
    )
    _create_non_blank_trigger(
        "financial_fact_changes",
        column_name="replacement_description",
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER financial_fact_changes_description_non_blank")
    op.drop_column("financial_fact_changes", "replacement_description")
    for table_name in reversed(_DESCRIPTION_TABLES):
        op.execute(f"DROP TRIGGER {table_name}_description_non_blank")
        op.drop_column(table_name, "description")


def _create_non_blank_trigger(
    table_name: str,
    *,
    column_name: str = "description",
) -> None:
    op.execute(
        f"""
        CREATE TRIGGER {table_name}_description_non_blank
        BEFORE INSERT ON {table_name}
        WHEN NEW.{column_name} IS NOT NULL AND length(trim(NEW.{column_name})) = 0
        BEGIN
            SELECT RAISE(ABORT, 'financial description cannot be blank');
        END
        """
    )
