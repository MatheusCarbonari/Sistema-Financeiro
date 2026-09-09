"""Create payables and their immutable history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0004"
down_revision: str | None = "0003"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "payables",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("due_date", sa.Date(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    _extend_financial_movements()
    op.create_table(
        "payable_history_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("payable_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("allocation_id", sa.String(length=36), nullable=True),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("movement_id", sa.String(length=36), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "amount_minor_units > 0", name="ck_payable_history_positive_amount"
        ),
        sa.CheckConstraint(
            """
            (kind IN ('opened', 'financial_charge') AND allocation_id IS NOT NULL
                AND account_id IS NULL AND movement_id IS NULL)
            OR
            (kind = 'payment' AND allocation_id IS NULL
                AND account_id IS NOT NULL AND movement_id = id)
            """,
            name="ck_payable_history_shape",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["resource_accounts.id"]),
        sa.ForeignKeyConstraint(["allocation_id"], ["allocations.id"]),
        sa.ForeignKeyConstraint(["movement_id"], ["financial_movements.id"]),
        sa.ForeignKeyConstraint(["payable_id"], ["payables.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("payable_id", "sequence"),
    )
    _create_append_only_triggers()


def downgrade() -> None:
    op.execute("DROP TRIGGER payable_history_entries_no_delete")
    op.execute("DROP TRIGGER payable_history_entries_no_update")
    op.drop_table("payable_history_entries")
    op.execute("DROP TRIGGER payables_no_delete")
    op.execute("DROP TRIGGER payables_no_update")
    _restore_financial_movements()
    op.drop_table("payables")


def _create_append_only_triggers() -> None:
    for table_name in ("payables", "payable_history_entries"):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_no_update
            BEFORE UPDATE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'payable history is append-only');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_no_delete
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, 'payable history is append-only');
            END
            """
        )


def _extend_financial_movements() -> None:
    _drop_movement_link_triggers()
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.drop_constraint(
            "fk_account_history_entries_movement_id", type_="foreignkey"
        )
    with op.batch_alter_table("financial_movements") as batch_op:
        batch_op.drop_constraint("ck_financial_movement_shape", type_="check")
        batch_op.add_column(sa.Column("payable_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_financial_movements_payable_id",
            "payables",
            ["payable_id"],
            ["id"],
            ondelete="RESTRICT",
        )
        batch_op.create_check_constraint(
            "ck_financial_movement_shape",
            _financial_movement_shape(include_bill_payment=True),
        )
    _restore_account_history_movement_link()
    _create_movement_link_triggers()


def _restore_financial_movements() -> None:
    _drop_movement_link_triggers()
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.drop_constraint(
            "fk_account_history_entries_movement_id", type_="foreignkey"
        )
    with op.batch_alter_table("financial_movements") as batch_op:
        batch_op.drop_constraint("ck_financial_movement_shape", type_="check")
        batch_op.drop_constraint("fk_financial_movements_payable_id", type_="foreignkey")
        batch_op.drop_column("payable_id")
        batch_op.create_check_constraint(
            "ck_financial_movement_shape",
            _financial_movement_shape(include_bill_payment=False),
        )
    _restore_account_history_movement_link()
    _create_movement_link_triggers()


def _restore_account_history_movement_link() -> None:
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.create_foreign_key(
            "fk_account_history_entries_movement_id",
            "financial_movements",
            ["movement_id"],
            ["id"],
            ondelete="RESTRICT",
        )


def _drop_movement_link_triggers() -> None:
    op.execute("DROP TRIGGER account_history_entries_no_delete")
    op.execute("DROP TRIGGER account_history_entries_no_update")
    op.execute("DROP TRIGGER financial_movements_no_delete")
    op.execute("DROP TRIGGER financial_movements_no_update")


def _create_movement_link_triggers() -> None:
    for table_name, message in (
        ("account_history_entries", "account history is append-only"),
        ("financial_movements", "financial movement history is append-only"),
    ):
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_no_update
            BEFORE UPDATE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, '{message}');
            END
            """
        )
        op.execute(
            f"""
            CREATE TRIGGER {table_name}_no_delete
            BEFORE DELETE ON {table_name}
            BEGIN
                SELECT RAISE(ABORT, '{message}');
            END
            """
        )


def _financial_movement_shape(*, include_bill_payment: bool) -> str:
    shape = """
        (kind = 'income' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            AND related_transfer_id IS NULL {payable_is_null})
        OR
        (kind = 'cash_expense' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
            AND source_account_id IS NULL AND destination_account_id IS NULL
            {payable_is_null})
        OR
        (kind = 'transfer' AND account_id IS NULL AND allocation_id IS NULL
            AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
            AND source_account_id <> destination_account_id AND related_transfer_id IS NULL
            {payable_is_null})
    """
    payable_is_null = "AND payable_id IS NULL" if include_bill_payment else ""
    result = shape.format(payable_is_null=payable_is_null)
    if include_bill_payment:
        result += """
        OR
        (kind = 'bill_payment' AND account_id IS NOT NULL AND payable_id IS NOT NULL
            AND allocation_id IS NULL AND source_account_id IS NULL
            AND destination_account_id IS NULL AND related_transfer_id IS NULL)
        """
    return result
