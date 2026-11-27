"""fix linked transaction confirmation fields

Revision ID: 7b6e2d9a4c31
Revises: c81e2f7a4b91
"""

from alembic import op
import sqlalchemy as sa


revision = "7b6e2d9a4c31"
down_revision = "c81e2f7a4b91"
branch_labels = None
depends_on = None


def upgrade():
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
        "AND confirming_workspace_id IS NOT NULL "
        "AND confirming_account_id IS NOT NULL "
        "AND confirming_journal_entry_id IS NOT NULL "
        "AND confirmed_at IS NOT NULL "
        "AND ("
        "(transaction_type = 'disbursement' "
        "AND initiating_journal_entry_id IS NOT NULL) "
        "OR (transaction_type = 'repayment' "
        "AND initiating_journal_entry_id IS NULL)"
        ")"
        ") OR ("
        "status <> 'confirmed' "
        "AND confirming_workspace_id IS NULL "
        "AND confirming_account_id IS NULL "
        "AND confirming_journal_entry_id IS NULL "
        "AND confirmed_by IS NULL "
        "AND confirmed_at IS NULL"
        ")",
    )


def downgrade():
    connection = op.get_bind()

    incompatible_count = connection.execute(
        sa.text(
            "SELECT COUNT(*) FROM linked_loan_transactions "
            "WHERE ("
            "status = 'confirmed' "
            "AND transaction_type = 'repayment' "
            "AND initiating_journal_entry_id IS NULL"
            ") OR ("
            "status <> 'confirmed' "
            "AND initiating_journal_entry_id IS NOT NULL"
            ")"
        )
    ).scalar_one()

    if incompatible_count:
        raise RuntimeError(
            "Unsafe downgrade: linked transactions exist that are "
            "incompatible with the previous confirmation constraint. "
            "Resolve these records before rolling back this migration."
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
        "AND confirming_workspace_id IS NULL "
        "AND confirming_account_id IS NULL "
        "AND confirming_journal_entry_id IS NULL "
        "AND confirmed_by IS NULL "
        "AND confirmed_at IS NULL"
        ")",
    )
