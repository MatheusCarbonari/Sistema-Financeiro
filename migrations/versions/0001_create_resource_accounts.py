"""Create resource accounts and their append-only history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0001"
down_revision: str | None = None
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "resource_accounts",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("name", sa.String(), nullable=False),
        sa.Column("account_type", sa.String(length=16), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("balance_minor_units", sa.Integer(), nullable=False),
        sa.Column("status", sa.String(length=16), nullable=False),
        sa.Column("allows_negative_balance", sa.Boolean(), nullable=False),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_table(
        "account_history_entries",
        sa.Column("id", sa.String(length=36), nullable=False),
        sa.Column("account_id", sa.String(length=36), nullable=False),
        sa.Column("sequence", sa.Integer(), nullable=False),
        sa.Column("kind", sa.String(length=24), nullable=False),
        sa.Column("difference_minor_units", sa.Integer(), nullable=False),
        sa.Column("balance_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("effective_date", sa.Date(), nullable=False),
        sa.Column("recorded_at", sa.String(), nullable=False),
        sa.Column("origin", sa.String(length=16), nullable=False),
        sa.Column("reason", sa.String(), nullable=True),
        sa.ForeignKeyConstraint(["account_id"], ["resource_accounts.id"], ondelete="RESTRICT"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("account_id", "sequence"),
    )
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


def downgrade() -> None:
    op.execute("DROP TRIGGER account_history_entries_no_delete")
    op.execute("DROP TRIGGER account_history_entries_no_update")
    op.drop_table("account_history_entries")
    op.drop_table("resource_accounts")
