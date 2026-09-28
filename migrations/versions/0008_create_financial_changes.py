"""Create append-only financial changes and account expense refunds."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0008"
down_revision: str | None = "0007"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _drop_movement_triggers()
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.drop_constraint("fk_account_history_entries_movement_id", type_="foreignkey")
    with op.batch_alter_table("financial_movements") as batch_op:
        batch_op.drop_constraint("ck_financial_movement_shape", type_="check")
        batch_op.add_column(sa.Column("original_expense_id", sa.String(length=36), nullable=True))
        batch_op.create_check_constraint("ck_financial_movement_shape", _movement_shape(True))
    _restore_account_history_link()
    _create_movement_triggers()

    op.create_table(
        "financial_fact_changes",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("target_id", sa.String(length=36), nullable=False),
        sa.Column("target_kind", sa.String(length=32), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=16), nullable=False),
        sa.Column("previous_version_id", sa.String(length=36), nullable=False),
        sa.Column("reason", sa.String(), nullable=False),
        sa.Column("recorded_effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("replacement_kind", sa.String(length=24), nullable=True),
        sa.Column("replacement_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_account_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_source_account_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_destination_account_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_allocation_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_related_transfer_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_payable_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_card_id", sa.String(length=36), nullable=True),
        sa.Column("replacement_invoice_closing_date", sa.Date(), nullable=True),
        sa.Column("replacement_due_date", sa.Date(), nullable=True),
        sa.Column("replacement_applied_amount_minor_units", sa.Integer(), nullable=True),
        sa.Column("replacement_amount_minor_units", sa.Integer(), nullable=True),
        sa.Column("replacement_currency", sa.String(length=3), nullable=True),
        sa.Column("replacement_effective_date", sa.Date(), nullable=True),
        sa.Column("replacement_recorded_at", sa.String(), nullable=True),
        sa.Column("replacement_origin", sa.String(length=16), nullable=True),
        sa.CheckConstraint("kind IN ('correction', 'cancellation')", name="ck_fact_change_kind"),
        sa.CheckConstraint(
            "(kind = 'cancellation' AND replacement_kind IS NULL "
            "AND replacement_id IS NULL AND replacement_amount_minor_units IS NULL) OR "
            "(kind = 'correction' AND replacement_kind IS NOT NULL "
            "AND replacement_id IS NOT NULL AND replacement_amount_minor_units > 0 "
            "AND replacement_currency IS NOT NULL)",
            name="ck_fact_change_shape",
        ),
        sa.ForeignKeyConstraint(["replacement_allocation_id"], ["allocations.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("target_id", "sequence"),
    )
    _append_only("financial_fact_changes", "financial change history is append-only")


def downgrade() -> None:
    op.execute("DROP TRIGGER financial_fact_changes_no_delete")
    op.execute("DROP TRIGGER financial_fact_changes_no_update")
    op.drop_table("financial_fact_changes")
    _drop_movement_triggers()
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.drop_constraint("fk_account_history_entries_movement_id", type_="foreignkey")
    with op.batch_alter_table("financial_movements") as batch_op:
        batch_op.drop_constraint("ck_financial_movement_shape", type_="check")
        batch_op.drop_column("original_expense_id")
        batch_op.create_check_constraint("ck_financial_movement_shape", _movement_shape(False))
    _restore_account_history_link()
    _create_movement_triggers()


def _movement_shape(include_refund: bool) -> str:
    original_is_null = " AND original_expense_id IS NULL" if include_refund else ""
    refund = ""
    if include_refund:
        refund = """
        OR
        (kind = 'account_expense_refund' AND account_id IS NOT NULL
            AND original_expense_id IS NOT NULL AND allocation_id IS NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            AND related_transfer_id IS NULL AND payable_id IS NULL
            AND card_id IS NULL AND invoice_closing_date IS NULL
            AND applied_amount_minor_units IS NULL)
        """
    return f"""
        (kind = 'income' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            AND related_transfer_id IS NULL AND payable_id IS NULL
            AND card_id IS NULL AND invoice_closing_date IS NULL
            AND applied_amount_minor_units IS NULL{original_is_null})
        OR
        (kind = 'cash_expense' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            AND payable_id IS NULL AND card_id IS NULL
            AND invoice_closing_date IS NULL AND applied_amount_minor_units IS NULL
            {original_is_null})
        OR
        (kind = 'transfer' AND account_id IS NULL AND allocation_id IS NULL
            AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
            AND source_account_id <> destination_account_id AND related_transfer_id IS NULL
            AND payable_id IS NULL AND card_id IS NULL
            AND invoice_closing_date IS NULL AND applied_amount_minor_units IS NULL
            {original_is_null})
        OR
        (kind = 'bill_payment' AND account_id IS NOT NULL AND payable_id IS NOT NULL
            AND allocation_id IS NULL AND source_account_id IS NULL
            AND destination_account_id IS NULL AND related_transfer_id IS NULL
            AND card_id IS NULL AND invoice_closing_date IS NULL
            AND applied_amount_minor_units IS NULL{original_is_null})
        OR
        (kind = 'invoice_payment' AND account_id IS NOT NULL AND card_id IS NOT NULL
            AND invoice_closing_date IS NOT NULL AND applied_amount_minor_units IS NOT NULL
            AND applied_amount_minor_units >= 0 AND applied_amount_minor_units <= amount_minor_units
            AND allocation_id IS NULL AND source_account_id IS NULL
            AND destination_account_id IS NULL AND related_transfer_id IS NULL
            AND payable_id IS NULL{original_is_null})
        {refund}
    """


def _drop_movement_triggers() -> None:
    for table_name in ("account_history_entries", "financial_movements"):
        op.execute(f"DROP TRIGGER {table_name}_no_delete")
        op.execute(f"DROP TRIGGER {table_name}_no_update")


def _restore_account_history_link() -> None:
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.create_foreign_key(
            "fk_account_history_entries_movement_id",
            "financial_movements",
            ["movement_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    _append_only("account_history_entries", "account history is append-only")


def _create_movement_triggers() -> None:
    _append_only("financial_movements", "financial movement history is append-only")


def _append_only(table_name: str, message: str) -> None:
    op.execute(
        f"CREATE TRIGGER {table_name}_no_update BEFORE UPDATE ON {table_name} "
        f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
    )
    op.execute(
        f"CREATE TRIGGER {table_name}_no_delete BEFORE DELETE ON {table_name} "
        f"BEGIN SELECT RAISE(ABORT, '{message}'); END"
    )
