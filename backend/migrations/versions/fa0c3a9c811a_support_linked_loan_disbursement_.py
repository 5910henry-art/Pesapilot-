"""support linked loan disbursement confirmation and reversal

Revision ID: fa0c3a9c811a
Revises: d043fdeae4bb
"""

from alembic import op
import sqlalchemy as sa


revision = "fa0c3a9c811a"
down_revision = "d043fdeae4bb"
branch_labels = None
depends_on = None


def upgrade():
    # Reversal metadata for disputed lender-side disbursements.
    op.add_column(
        "linked_loan_transactions",
        sa.Column("reversal_journal_entry_id", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "linked_loan_transactions",
        sa.Column("reversed_by", sa.Uuid(), nullable=True),
    )
    op.add_column(
        "linked_loan_transactions",
        sa.Column(
            "reversed_at",
            sa.DateTime(timezone=True),
            nullable=True,
        ),
    )

    op.create_foreign_key(
        "fk_linked_transaction_reversal_journal",
        "linked_loan_transactions",
        "journal_entries",
        ["initiating_workspace_id", "reversal_journal_entry_id"],
        ["workspace_id", "id"],
    )
    op.create_foreign_key(
        "fk_linked_transaction_reversed_by",
        "linked_loan_transactions",
        "users",
        ["reversed_by"],
        ["id"],
        ondelete="SET NULL",
    )

    # Reversal is only valid for a disputed disbursement that has
    # an initiating journal. A deleted user may leave reversed_by NULL.
    op.create_check_constraint(
        "ck_linked_transaction_reversal_fields",
        "linked_loan_transactions",
        "("
        "reversal_journal_entry_id IS NULL "
        "AND reversed_at IS NULL "
        "AND reversed_by IS NULL"
        ") OR ("
        "reversal_journal_entry_id IS NOT NULL "
        "AND reversed_at IS NOT NULL "
        "AND transaction_type = 'disbursement' "
        "AND status = 'disputed' "
        "AND initiating_journal_entry_id IS NOT NULL"
        ")",
    )

    # A pending lender disbursement may already have its initiating
    # journal posted. Only the confirming-side fields must remain empty.
    op.drop_constraint(
        "ck_linked_transaction_confirmation_fields",
        "linked_loan_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_linked_transaction_confirmation_fields",
        "linked_loan_transactions",
        "("
        "status = 'confirmed' "
        "AND initiating_journal_entry_id IS NOT NULL "
        "AND confirming_workspace_id IS NOT NULL "
        "AND confirming_account_id IS NOT NULL "
        "AND confirming_journal_entry_id IS NOT NULL "
        "AND confirmed_at IS NOT NULL"
        ") OR ("
        "status <> 'confirmed' "
        "AND confirming_workspace_id IS NULL "
        "AND confirming_account_id IS NULL "
        "AND confirming_journal_entry_id IS NULL "
        "AND confirmed_by IS NULL "
        "AND confirmed_at IS NULL"
        ")",
    )

    # Keep a disputed disbursement unique until its reversal is recorded.
    op.drop_index(
        "uq_linked_loan_active_disbursement",
        table_name="linked_loan_transactions",
    )
    op.create_index(
        "uq_linked_loan_active_disbursement",
        "linked_loan_transactions",
        ["loan_workspace_id", "personal_loan_id"],
        unique=True,
        postgresql_where=sa.text(
            "transaction_type = 'disbursement' "
            "AND (status <> 'disputed' OR reversal_journal_entry_id IS NULL)"
        ),
        sqlite_where=sa.text(
            "transaction_type = 'disbursement' "
            "AND (status <> 'disputed' OR reversal_journal_entry_id IS NULL)"
        ),
    )

    # Permit loans awaiting the borrower's confirmation of receipt.
    op.drop_constraint(
        "ck_personal_loan_status",
        "personal_loans",
        type_="check",
    )
    op.create_check_constraint(
        "ck_personal_loan_status",
        "personal_loans",
        "status IN "
        "('draft', 'pending_approval', 'awaiting_confirmation', "
        "'active', 'paid', 'overdue', 'disputed', 'restructured', "
        "'written_off', 'cancelled', 'rejected')",
    )


def downgrade():
    # Refuse rollback if new linked-loan records cannot satisfy
    # the original constraints or would lose reversal metadata.
    connection = op.get_bind()

    awaiting_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM personal_loans "
            "WHERE status = 'awaiting_confirmation'"
        )
    ).scalar_one()

    pending_journal_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM linked_loan_transactions "
            "WHERE status <> 'confirmed' "
            "AND initiating_journal_entry_id IS NOT NULL"
        )
    ).scalar_one()

    reversal_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM linked_loan_transactions "
            "WHERE reversal_journal_entry_id IS NOT NULL "
            "OR reversed_by IS NOT NULL "
            "OR reversed_at IS NOT NULL"
        )
    ).scalar_one()

    if awaiting_count or pending_journal_count or reversal_count:
        raise RuntimeError(
            "Unsafe downgrade: linked-loan confirmation or reversal "
            "records exist. Preserve the data and resolve them before "
            "rolling back this migration."
        )

    # Restore the original loan status constraint.
    op.drop_constraint(
        "ck_personal_loan_status",
        "personal_loans",
        type_="check",
    )
    op.create_check_constraint(
        "ck_personal_loan_status",
        "personal_loans",
        "status IN "
        "('draft', 'pending_approval', 'active', 'paid', 'overdue', "
        "'disputed', 'restructured', 'written_off', 'cancelled', 'rejected')",
    )

    # Restore the original disbursement uniqueness rule.
    op.drop_index(
        "uq_linked_loan_active_disbursement",
        table_name="linked_loan_transactions",
    )
    op.create_index(
        "uq_linked_loan_active_disbursement",
        "linked_loan_transactions",
        ["loan_workspace_id", "personal_loan_id"],
        unique=True,
        postgresql_where=sa.text(
            "transaction_type = 'disbursement' AND status <> 'disputed'"
        ),
        sqlite_where=sa.text(
            "transaction_type = 'disbursement' AND status <> 'disputed'"
        ),
    )

    op.drop_constraint(
        "ck_linked_transaction_confirmation_fields",
        "linked_loan_transactions",
        type_="check",
    )
    op.create_check_constraint(
        "ck_linked_transaction_confirmation_fields",
        "linked_loan_transactions",
        "("
        "status = 'confirmed' "
        "AND initiating_journal_entry_id IS NOT NULL "
        "AND confirming_workspace_id IS NOT NULL "
        "AND confirming_account_id IS NOT NULL "
        "AND confirming_journal_entry_id IS NOT NULL "
        "AND confirmed_at IS NOT NULL"
        ") OR ("
        "status <> 'confirmed' "
        "AND initiating_journal_entry_id IS NULL "
        "AND confirming_workspace_id IS NULL "
        "AND confirming_account_id IS NULL "
        "AND confirming_journal_entry_id IS NULL "
        "AND confirmed_by IS NULL "
        "AND confirmed_at IS NULL"
        ")",
    )

    op.drop_constraint(
        "ck_linked_transaction_reversal_fields",
        "linked_loan_transactions",
        type_="check",
    )
    op.drop_constraint(
        "fk_linked_transaction_reversed_by",
        "linked_loan_transactions",
        type_="foreignkey",
    )
    op.drop_constraint(
        "fk_linked_transaction_reversal_journal",
        "linked_loan_transactions",
        type_="foreignkey",
    )

    op.drop_column("linked_loan_transactions", "reversed_at")
    op.drop_column("linked_loan_transactions", "reversed_by")
    op.drop_column("linked_loan_transactions", "reversal_journal_entry_id")
