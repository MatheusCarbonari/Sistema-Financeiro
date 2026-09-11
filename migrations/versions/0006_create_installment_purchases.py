"""Create installment purchase schedules and correction history."""

from collections.abc import Sequence

import sqlalchemy as sa
from alembic import op

revision: str = "0006"
down_revision: str | None = "0005"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    _drop_history_triggers()
    with op.batch_alter_table("credit_card_history_entries") as batch_op:
        batch_op.drop_constraint("ck_credit_card_history_shape", type_="check")
        batch_op.drop_constraint("ck_credit_card_history_columns", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_card_history_shape", _history_shape(include_installments=True)
        )
        batch_op.create_check_constraint(
            "ck_credit_card_history_columns",
            _history_columns(include_installments=True),
        )
    op.create_table(
        "credit_card_installments",
        sa.Column("event_id", sa.String(length=36), nullable=False),
        sa.Column("role", sa.String(length=16), nullable=False),
        sa.Column("position", sa.Integer(), nullable=False),
        sa.Column("purchase_id", sa.String(length=36), nullable=False),
        sa.Column("installment_sequence", sa.Integer(), nullable=False),
        sa.Column("amount_minor_units", sa.Integer(), nullable=False),
        sa.Column("currency", sa.String(length=3), nullable=False),
        sa.Column("invoice_closing_date", sa.Date(), nullable=False),
        sa.Column("invoice_due_date", sa.Date(), nullable=False),
        sa.CheckConstraint(
            "role IN ('original', 'previous', 'frozen', 'replacement')",
            name="ck_credit_card_installment_role",
        ),
        sa.CheckConstraint(
            "position >= 1 AND installment_sequence >= 1 "
            "AND amount_minor_units > 0",
            name="ck_credit_card_installment_positive_values",
        ),
        sa.ForeignKeyConstraint(
            ["event_id"], ["credit_card_history_entries.id"], ondelete="RESTRICT"
        ),
        sa.PrimaryKeyConstraint("event_id", "role", "position"),
    )
    _create_append_only_triggers(
        "credit_card_history_entries", "credit card history is append-only"
    )
    _create_append_only_triggers(
        "credit_card_installments", "credit card installments are append-only"
    )
    _create_installment_validation_trigger()


def downgrade() -> None:
    _drop_installment_triggers()
    op.drop_table("credit_card_installments")
    _drop_history_triggers()
    with op.batch_alter_table("credit_card_history_entries") as batch_op:
        batch_op.drop_constraint("ck_credit_card_history_shape", type_="check")
        batch_op.drop_constraint("ck_credit_card_history_columns", type_="check")
        batch_op.create_check_constraint(
            "ck_credit_card_history_shape", _history_shape(include_installments=False)
        )
        batch_op.create_check_constraint(
            "ck_credit_card_history_columns",
            _history_columns(include_installments=False),
        )
    _create_append_only_triggers(
        "credit_card_history_entries", "credit card history is append-only"
    )


def _drop_history_triggers() -> None:
    op.execute("DROP TRIGGER credit_card_history_entries_no_delete")
    op.execute("DROP TRIGGER credit_card_history_entries_no_update")


def _drop_installment_triggers() -> None:
    op.execute("DROP TRIGGER credit_card_installments_validate")
    op.execute("DROP TRIGGER credit_card_installments_no_delete")
    op.execute("DROP TRIGGER credit_card_installments_no_update")


def _create_append_only_triggers(table_name: str, message: str) -> None:
    op.execute(
        f"""CREATE TRIGGER {table_name}_no_update BEFORE UPDATE ON {table_name}
        BEGIN SELECT RAISE(ABORT, '{message}'); END"""
    )
    op.execute(
        f"""CREATE TRIGGER {table_name}_no_delete BEFORE DELETE ON {table_name}
        BEGIN SELECT RAISE(ABORT, '{message}'); END"""
    )


def _create_installment_validation_trigger() -> None:
    op.execute(
        """
        CREATE TRIGGER credit_card_installments_validate
        BEFORE INSERT ON credit_card_installments
        WHEN NOT EXISTS (
            SELECT 1
            FROM credit_card_history_entries AS history
            WHERE history.id = NEW.event_id
              AND history.currency = NEW.currency
              AND (
                (NEW.role = 'original'
                    AND history.kind = 'installment_purchase'
                    AND history.id = NEW.purchase_id)
                OR
                (NEW.role IN ('previous', 'frozen', 'replacement')
                    AND history.kind = 'installment_purchase_corrected'
                    AND history.purchase_id = NEW.purchase_id)
              )
        )
        BEGIN
            SELECT RAISE(
                ABORT,
                'installment schedule does not match its history event'
            );
        END
        """
    )


def _history_shape(*, include_installments: bool) -> str:
    installment_shapes = ""
    purchase_kinds = "'purchase', 'financial_charge'"
    if include_installments:
        purchase_kinds += ", 'installment_purchase'"
        installment_shapes = """
        OR
        (kind = 'installment_purchase_corrected' AND amount_minor_units > 0
            AND currency IS NOT NULL AND allocation_id IS NOT NULL
            AND purchase_id IS NOT NULL AND reason IS NOT NULL)
        """
    return f"""
        (kind = 'opened' AND amount_minor_units IS NULL AND currency IS NULL)
        OR
        (kind IN ({purchase_kinds}) AND amount_minor_units > 0
            AND currency IS NOT NULL AND allocation_id IS NOT NULL
            AND (invoice_closing_date IS NOT NULL) =
                (kind IN ('purchase', 'financial_charge')))
        {installment_shapes}
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
    """


def _history_columns(*, include_installments: bool) -> str:
    allocation_kinds = "'purchase', 'financial_charge'"
    purchase_id_kinds = "'assignment_corrected', 'refund'"
    reason_kinds = "'assignment_corrected', 'credit_granted'"
    currency_kinds = (
        "'purchase', 'financial_charge', 'invoice_payment', "
        "'refund', 'credit_granted', 'credit_applied'"
    )
    if include_installments:
        installment_kinds = "'installment_purchase', 'installment_purchase_corrected'"
        allocation_kinds += f", {installment_kinds}"
        purchase_id_kinds += ", 'installment_purchase_corrected'"
        reason_kinds += ", 'installment_purchase_corrected'"
        currency_kinds += f", {installment_kinds}"
    return f"""
        (allocation_id IS NOT NULL) = (kind IN ({allocation_kinds}))
        AND (account_id IS NOT NULL) = (kind = 'invoice_payment')
        AND (movement_id IS NOT NULL) = (kind = 'invoice_payment')
        AND (purchase_id IS NOT NULL) = (kind IN ({purchase_id_kinds}))
        AND (invoice_closing_date IS NOT NULL) =
            (kind IN ('purchase', 'financial_charge', 'invoice_payment', 'credit_applied'))
        AND (previous_closing_date IS NOT NULL) = (kind = 'assignment_corrected')
        AND (target_closing_date IS NOT NULL) = (kind = 'assignment_corrected')
        AND (source_event_id IS NOT NULL) = (kind = 'credit_granted')
        AND (reason IS NOT NULL) = (kind IN ({reason_kinds}))
        AND (applied_amount_minor_units IS NOT NULL) = (kind = 'invoice_payment')
        AND (principal_reduction_minor_units IS NOT NULL) = (kind = 'refund')
        AND (currency IS NOT NULL) = (kind IN ({currency_kinds}))
    """
