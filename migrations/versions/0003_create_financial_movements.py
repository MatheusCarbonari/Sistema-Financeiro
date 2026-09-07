"""Create realized financial movements and link account history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0003"
down_revision: str | None = "0002"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "financial_movements",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=True),
        sa.Column("source_account_id", sa.String(length=36), nullable=True),
        sa.Column("destination_account_id", sa.String(length=36), nullable=True),
        sa.Column("allocation_id", sa.String(length=36), nullable=True),
        sa.Column("related_transfer_id", sa.String(length=36), nullable=True),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.CheckConstraint(
            "amount_minor_units > 0", name="ck_financial_movement_positive_amount"
        ),
        sa.CheckConstraint(
            """
            (kind = 'income' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
                AND source_account_id IS NULL AND destination_account_id IS NULL
                AND related_transfer_id IS NULL)
            OR
            (kind = 'cash_expense' AND account_id IS NOT NULL AND allocation_id IS NOT NULL
                AND source_account_id IS NULL AND destination_account_id IS NULL)
            OR
            (kind = 'transfer' AND account_id IS NULL AND allocation_id IS NULL
                AND source_account_id IS NOT NULL AND destination_account_id IS NOT NULL
                AND source_account_id <> destination_account_id AND related_transfer_id IS NULL)
            """,
            name="ck_financial_movement_shape",
        ),
        sa.ForeignKeyConstraint(["account_id"], ["resource_accounts.id"]),
        sa.ForeignKeyConstraint(["allocation_id"], ["allocations.id"]),
        sa.ForeignKeyConstraint(["destination_account_id"], ["resource_accounts.id"]),
        sa.ForeignKeyConstraint(["related_transfer_id"], ["financial_movements.id"]),
        sa.ForeignKeyConstraint(["source_account_id"], ["resource_accounts.id"]),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("allocation_id"),
    )
    op.execute("DROP TRIGGER account_history_entries_no_delete")
    op.execute("DROP TRIGGER account_history_entries_no_update")
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.add_column(sa.Column("movement_id", sa.String(length=36), nullable=True))
        batch_op.create_foreign_key(
            "fk_account_history_entries_movement_id",
            "financial_movements",
            ["movement_id"],
            ["id"],
            ondelete="RESTRICT",
        )
    _create_account_history_triggers()
    op.execute(
        """
        CREATE TRIGGER financial_movements_no_update
        BEFORE UPDATE ON financial_movements
        BEGIN
            SELECT RAISE(ABORT, 'financial movement history is append-only');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER financial_movements_no_delete
        BEFORE DELETE ON financial_movements
        BEGIN
            SELECT RAISE(ABORT, 'financial movement history is append-only');
        END
        """
    )


def downgrade() -> None:
    op.execute("DROP TRIGGER financial_movements_no_delete")
    op.execute("DROP TRIGGER financial_movements_no_update")
    op.execute("DROP TRIGGER account_history_entries_no_delete")
    op.execute("DROP TRIGGER account_history_entries_no_update")
    with op.batch_alter_table("account_history_entries") as batch_op:
        batch_op.drop_constraint(
            "fk_account_history_entries_movement_id", type_="foreignkey"
        )
        batch_op.drop_column("movement_id")
    op.drop_table("financial_movements")
    _create_account_history_triggers()


def _create_account_history_triggers() -> None:
    op.execute(
        """
        CREATE TRIGGER account_history_entries_no_update
        BEFORE UPDATE ON account_history_entries
        BEGIN
            SELECT RAISE(ABORT, 'account history is append-only');
        END
        """
    )
    op.execute(
        """
        CREATE TRIGGER account_history_entries_no_delete
        BEFORE DELETE ON account_history_entries
        BEGIN
            SELECT RAISE(ABORT, 'account history is append-only');
        END
        """
    )
