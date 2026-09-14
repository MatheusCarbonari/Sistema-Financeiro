"""Create forecasts, recurrences, safety margins, and immutable planning history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0007"
down_revision: str | None = "0006"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "planned_occurrences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=False),
        sa.Column("margin_kind", sa.String(length=16), nullable=True),
        sa.Column("margin_value", sa.String(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint("direction IN ('inflow', 'outflow')", name="ck_forecast_direction"),
        sa.CheckConstraint("amount_minor_units > 0", name="ck_forecast_positive_amount"),
        sa.CheckConstraint(
            "(margin_kind IS NULL AND margin_value IS NULL) OR "
            "(margin_kind IN ('fixed', 'percentage') AND margin_value IS NOT NULL)",
            name="ck_forecast_margin_shape",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["resource_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "planning_recurrences",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("direction", sa.String(length=16), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("starts_on", sa.Date(), nullable=False),
        sa.Column("ends_on", sa.Date(), nullable=True),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("margin_kind", sa.String(length=16), nullable=True),
        sa.Column("margin_value", sa.String(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint("direction IN ('inflow', 'outflow')", name="ck_recurrence_direction"),
        sa.CheckConstraint("amount_minor_units > 0", name="ck_recurrence_positive_amount"),
        sa.CheckConstraint("interval > 0", name="ck_recurrence_positive_interval"),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly', 'yearly')",
            name="ck_recurrence_frequency",
        ),
        sa.CheckConstraint(
            "ends_on IS NULL OR ends_on >= starts_on", name="ck_recurrence_dates"
        ),
        sa.CheckConstraint(
            "(margin_kind IS NULL AND margin_value IS NULL) OR "
            "(margin_kind IN ('fixed', 'percentage') AND margin_value IS NOT NULL)",
            name="ck_recurrence_margin_shape",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["resource_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "planning_recurrence_revisions",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("recurrence_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("from_date", sa.Date(), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("margin_kind", sa.String(length=16), nullable=True),
        sa.Column("margin_value", sa.String(), nullable=True),
        sa.Column("frequency", sa.String(length=16), nullable=False),
        sa.Column("interval", sa.Integer(), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_recurrence_revision_sequence"),
        sa.CheckConstraint("amount_minor_units > 0", name="ck_recurrence_revision_amount"),
        sa.CheckConstraint("interval > 0", name="ck_recurrence_revision_interval"),
        sa.CheckConstraint(
            "frequency IN ('daily', 'weekly', 'monthly', 'yearly')",
            name="ck_recurrence_revision_frequency",
        ),
        sa.CheckConstraint("length(trim(reason)) > 0", name="ck_recurrence_revision_reason"),
        sa.CheckConstraint(
            "(margin_kind IS NULL AND margin_value IS NULL) OR "
            "(margin_kind IN ('fixed', 'percentage') AND margin_value IS NOT NULL)",
            name="ck_recurrence_revision_margin_shape",
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_id"], ["planning_recurrences.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("recurrence_id", "sequence"),
    )
    op.create_table(
        "planning_occurrence_events",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("occurrence_id", sa.String(length=36), nullable=False),
        sa.Column("recurrence_id", sa.String(length=36), nullable=True),
        sa.Column("source_planned_date", sa.Date(), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("expected_minor_units", sa.Integer(), nullable=False),
        sa.Column("actual_minor_units", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("planned_date", sa.Date(), nullable=True),
        sa.Column("margin_kind", sa.String(length=16), nullable=True),
        sa.Column("margin_value", sa.String(), nullable=True),
        sa.Column("movement_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("original_expected_minor_units", sa.Integer(), nullable=True),
        sa.Column("original_planned_date", sa.Date(), nullable=True),
        sa.Column("original_margin_kind", sa.String(length=16), nullable=True),
        sa.Column("original_margin_value", sa.String(), nullable=True),
        sa.Column("original_effective_date", sa.Date(), nullable=True),
        sa.Column("original_recorded_at", sa.String(), nullable=True),
        sa.Column("original_origin", sa.String(length=16), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint("sequence >= 1", name="ck_occurrence_event_sequence"),
        sa.CheckConstraint("expected_minor_units > 0", name="ck_occurrence_event_expected"),
        sa.CheckConstraint(
            "(kind = 'revised' AND actual_minor_units IS NULL "
            "AND planned_date IS NOT NULL AND movement_id IS NULL "
            "AND reason IS NOT NULL AND length(trim(reason)) > 0 "
            "AND original_expected_minor_units IS NULL "
            "AND original_planned_date IS NULL "
            "AND original_effective_date IS NULL "
            "AND original_recorded_at IS NULL AND original_origin IS NULL) OR "
            "(kind = 'confirmed' AND actual_minor_units > 0 "
            "AND planned_date IS NOT NULL AND movement_id IS NOT NULL "
            "AND reason IS NULL AND margin_kind IS NULL AND margin_value IS NULL "
            "AND original_expected_minor_units > 0 "
            "AND original_planned_date IS NOT NULL "
            "AND original_effective_date IS NOT NULL "
            "AND original_recorded_at IS NOT NULL AND original_origin IS NOT NULL)",
            name="ck_occurrence_event_shape",
        ),
        sa.CheckConstraint(
            "(margin_kind IS NULL AND margin_value IS NULL) OR "
            "(kind = 'revised' AND margin_kind IN ('fixed', 'percentage') "
            "AND margin_value IS NOT NULL)",
            name="ck_occurrence_event_margin_shape",
        ),
        sa.CheckConstraint(
            "(original_margin_kind IS NULL AND original_margin_value IS NULL) OR "
            "(kind = 'confirmed' AND original_margin_kind IN ('fixed', 'percentage') "
            "AND original_margin_value IS NOT NULL)",
            name="ck_occurrence_original_margin_shape",
        ),
        sa.ForeignKeyConstraint(
            ["movement_id"], ["financial_movements.id"], ondelete="RESTRICT"
        ),
        sa.ForeignKeyConstraint(
            ["recurrence_id"], ["planning_recurrences.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("movement_id"),
        sa.UniqueConstraint("occurrence_id", "sequence"),
    )
    for table_name in (
        "planned_occurrences",
        "planning_recurrences",
        "planning_recurrence_revisions",
        "planning_occurrence_events",
    ):
        _create_append_only_triggers(table_name)


def downgrade() -> None:
    for table_name in (
        "planning_occurrence_events",
        "planning_recurrence_revisions",
        "planning_recurrences",
        "planned_occurrences",
    ):
        _drop_append_only_triggers(table_name)
        op.drop_table(table_name)


def _create_append_only_triggers(table_name: str) -> None:
    op.execute(
        f"""CREATE TRIGGER {table_name}_no_update BEFORE UPDATE ON {table_name}
        BEGIN SELECT RAISE(ABORT, 'planning history is append-only'); END"""
    )
    op.execute(
        f"""CREATE TRIGGER {table_name}_no_delete BEFORE DELETE ON {table_name}
        BEGIN SELECT RAISE(ABORT, 'planning history is append-only'); END"""
    )


def _drop_append_only_triggers(table_name: str) -> None:
    op.execute(f"DROP TRIGGER {table_name}_no_update")
    op.execute(f"DROP TRIGGER {table_name}_no_delete")
