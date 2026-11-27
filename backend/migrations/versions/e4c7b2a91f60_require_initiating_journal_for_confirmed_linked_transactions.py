"""require initiating journal for confirmed linked transactions

Revision ID: e4c7b2a91f60
Revises: 3d8f6a2b91c4
"""

from alembic import op


revision = "e4c7b2a91f60"
down_revision = "3d8f6a2b91c4"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "ck_linked_transaction_confirmation_fields",
        "linked_loan_transactions",
        type_="check",
    )

    # Enforce the new rule on changed/new rows while allowing historical
    # confirmed repayments to be reconciled before validating old rows.
    op.execute("""
        ALTER TABLE linked_loan_transactions
        ADD CONSTRAINT ck_linked_transaction_confirmation_fields
        CHECK (
            (
                status = 'confirmed'
                AND confirming_workspace_id IS NOT NULL
                AND confirming_account_id IS NOT NULL
                AND confirming_journal_entry_id IS NOT NULL
                AND confirmed_at IS NOT NULL
                AND initiating_journal_entry_id IS NOT NULL
            )
            OR
            (
                status <> 'confirmed'
                AND confirming_workspace_id IS NULL
                AND confirming_account_id IS NULL
                AND confirming_journal_entry_id IS NULL
                AND confirmed_by IS NULL
                AND confirmed_at IS NULL
            )
        ) NOT VALID
    """)


def downgrade():
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
