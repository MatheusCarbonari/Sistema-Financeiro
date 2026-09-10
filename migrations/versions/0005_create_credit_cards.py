"""Create credit cards and invoice payments."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0005"
down_revision: str | None = "0004"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "credit_cards",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("closing_day", sa.Integer(), nullable=False),
        sa.Column("due_day", sa.Integer(), nullable=False),
        sa.Column("limit_minor_units", sa.Integer(), nullable=True),
        sa.CheckConstraint(
            "closing_day BETWEEN 1 AND 31", name="ck_credit_card_closing_day"
        ),
        sa.CheckConstraint("due_day BETWEEN 1 AND 31", name="ck_credit_card_due_day"),
        sa.CheckConstraint(
            "limit_minor_units IS NULL OR limit_minor_units > 0",
            name="ck_credit_card_positive_limit",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    _extend_financial_movements()
    op.create_table(
        "credit_card_history_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("card_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=32), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=True),
        sa.Column("applied_amount_minor_units", sa.Integer(), nullable=True),
        sa.Column("principal_reduction_minor_units", sa.Integer(), nullable=True),
        sa.Column("currency", sa.String(length=3), nullable=True),
        sa.Column("allocation_id", sa.String(length=36), nullable=True),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("movement_id", sa.String(length=36), nullable=True),
        sa.Column("purchase_id", sa.String(length=36), nullable=True),
        sa.Column("invoice_closing_date", sa.Date(), nullable=True),
        sa.Column("previous_closing_date", sa.Date(), nullable=True),
        sa.Column("target_closing_date", sa.Date(), nullable=True),
        sa.Column("source_event_id", sa.String(length=36), nullable=True),
        sa.Column("reason", sa.String(), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            """
            (kind = 'opened' AND amount_minor_units IS NULL AND currency IS NULL)
            OR
            (kind IN ('purchase', 'financial_charge') AND amount_minor_units > 0
                AND currency IS NOT NULL AND allocation_id IS NOT NULL
                AND invoice_closing_date IS NOT NULL)
            OR
            (kind = 'assignment_corrected' AND amount_minor_units IS NULL
                AND purchase_id IS NOT NULL AND previous_closing_date IS NOT NULL
                AND target_closing_date IS NOT NULL AND reason IS NOT NULL)
            OR
            (kind = 'invoice_payment' AND amount_minor_units > 0
                AND applied_amount_minor_units >= 0
                AND applied_amount_minor_units <= amount_minor_units
                AND currency IS NOT NULL AND account_id IS NOT NULL
                AND movement_id = id AND invoice_closing_date IS NOT NULL)
            OR
            (kind = 'refund' AND amount_minor_units > 0
                AND principal_reduction_minor_units >= 0
                AND principal_reduction_minor_units <= amount_minor_units
                AND currency IS NOT NULL AND purchase_id IS NOT NULL)
            OR
            (kind = 'credit_granted' AND amount_minor_units > 0
                AND currency IS NOT NULL AND source_event_id IS NOT NULL
                AND reason IS NOT NULL)
            OR
            (kind = 'credit_applied' AND amount_minor_units > 0
                AND currency IS NOT NULL AND invoice_closing_date IS NOT NULL)
            """,
            name="ck_credit_card_history_shape",
        ),
        sa.CheckConstraint(
            """
            (allocation_id IS NOT NULL) = (kind IN ('purchase', 'financial_charge'))
            AND (account_id IS NOT NULL) = (kind = 'invoice_payment')
            AND (movement_id IS NOT NULL) = (kind = 'invoice_payment')
            AND (purchase_id IS NOT NULL) = (kind IN ('assignment_corrected', 'refund'))
            AND (invoice_closing_date IS NOT NULL) =
                (kind IN ('purchase', 'financial_charge', 'invoice_payment', 'credit_applied'))
            AND (previous_closing_date IS NOT NULL) = (kind = 'assignment_corrected')
            AND (target_closing_date IS NOT NULL) = (kind = 'assignment_corrected')
            AND (source_event_id IS NOT NULL) = (kind = 'credit_granted')
            AND (reason IS NOT NULL) =
                (kind IN ('assignment_corrected', 'credit_granted'))
            AND (applied_amount_minor_units IS NOT NULL) = (kind = 'invoice_payment')
            AND (principal_reduction_minor_units IS NOT NULL) = (kind = 'refund')
            AND (currency IS NOT NULL) =
                (kind IN ('purchase', 'financial_charge', 'invoice_payment',
                    'refund', 'credit_granted', 'credit_applied'))
            """,
            name="ck_credit_card_history_columns",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["resource_accounts.id"]),
        sa.ForeignKeyConstraint(["allocation_id"], ["allocations.id"]),
        sa.ForeignKeyConstraint(["card_id"], ["credit_cards.id"]),
        sa.ForeignKeyConstraint(["movement_id"], ["financial_movements.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("card_id", "sequence"),
    )
    _create_card_triggers()


def downgrade() -> None:
    for table_name in ("credit_card_history_entries", "credit_cards"):
        op.execute(f"DROP TRIGGER {table_name}_no_delete")
        op.execute(f"DROP TRIGGER {table_name}_no_update")
    op.drop_table("credit_card_history_entries")
    _restore_financial_movements()
    op.drop_table("credit_cards")


def _extend_financial_movements() -> None:
    _drop_movement_triggers()
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.drop_constraint(
            "fk_account_history_entries_movement_id", type_="foreignkey"
        )
    with op.batch_alter_table("financial_movements") as batch_op:
        batch_op.drop_constraint("ck_financial_movement_shape", type_="check")
        batch_op.add_column(sa.Column("card_id", sa.String(length=36), nullable=True))
        batch_op.add_column(sa.Column("invoice_closing_date", sa.Date(), nullable=True))
        batch_op.add_column(
            sa.Column("applied_amount_minor_units", sa.Integer(), nullable=True)
        )
        batch_op.create_foreign_key(
            "fk_financial_movements_card_id",
            "credit_cards",
            ["card_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_financial_movement_shape", _movement_shape(include_invoice=True)
        )
    _restore_account_history_link()
    _create_movement_triggers()


def _restore_financial_movements() -> None:
    _drop_movement_triggers()
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.drop_constraint(
            "fk_account_history_entries_movement_id", type_="foreignkey"
        )
    with op.batch_alter_table("financial_movements") as batch_op:
        batch_op.drop_constraint("ck_financial_movement_shape", type_="check")
        batch_op.drop_constraint("fk_financial_movements_card_id", type_="foreignkey")
        batch_op.drop_column("applied_amount_minor_units")
        batch_op.drop_column("invoice_closing_date")
        batch_op.drop_column("card_id")
        batch_op.create_check_constraint(
            "ck_financial_movement_shape", _movement_shape(include_invoice=False)
        )
    _restore_account_history_link()
    _create_movement_triggers()


def _restore_account_history_link() -> None:
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.create_foreign_key(
            "fk_account_history_entries_movement_id",
            "financial_movements",
            ["movement_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def _drop_movement_triggers() -> None:
    for table_name in ("account_history_entries", "financial_movements"):
        op.execute(f"DROP TRIGGER {table_name}_no_delete")
        op.execute(f"DROP TRIGGER {table_name}_no_update")


def _create_movement_triggers() -> None:
    for table_name, message in (
        ("account_history_entries", "account history is append-only"),
        ("financial_movements", "financial movement history is append-only"),
    ):
        _create_append_only_triggers(table_name, message)


def _create_card_triggers() -> None:
    for table_name in ("credit_cards", "credit_card_history_entries"):
        _create_append_only_triggers(table_name, "credit card history is append-only")


def _create_append_only_triggers(table_name: str, message: str) -> None:
    op.execute(
        f"""CREATE TRIGGER {table_name}_no_update BEFORE UPDATE ON {table_name}
        BEGIN SELECT RAISE(ABORT, '{message}'); END"""
    )
    op.execute(
        f"""CREATE TRIGGER {table_name}_no_delete BEFORE DELETE ON {table_name}
        BEGIN SELECT RAISE(ABORT, '{message}'); END"""
    )


def _movement_shape(*, include_invoice: bool) -> str:
    new_nulls = (
        "AND card_id IS NULL AND invoice_closing_date IS NULL "
        "AND applied_amount_minor_units IS NULL"
        if include_invoice
        else ""
    )
    shape = f"""
        (kind = 'income' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            AND related_transfer_id IS NULL AND payable_id IS NULL {new_nulls})
        OR
        (kind = 'cash_expense' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            AND payable_id IS NULL {new_nulls})
        OR
        (kind = 'transfer' AND account_id IS NULL AND allocation_id IS NULL
            AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
            AND source_account_id <> destination_account_id
            AND related_transfer_id IS NULL AND payable_id IS NULL {new_nulls})
        OR
        (kind = 'bill_payment' AND account_id IS NOT NULL AND payable_id IS NOT NULL
            AND allocation_id IS NULL AND source_account_id IS NULL
            AND destination_account_id IS NULL AND related_transfer_id IS NULL {new_nulls})
    """
    if include_invoice:
        shape += """
        OR
        (kind = 'invoice_payment' AND account_id IS NOT NULL AND card_id IS NOT NULL
            AND invoice_closing_date IS NOT NULL
            AND applied_amount_minor_units IS NOT NULL
            AND applied_amount_minor_units >= 0
            AND applied_amount_minor_units <= amount_minor_units
            AND allocation_id IS NULL AND source_account_id IS NULL
            AND destination_account_id IS NULL AND related_transfer_id IS NULL
            AND payable_id IS NULL)
        """
    return shape
