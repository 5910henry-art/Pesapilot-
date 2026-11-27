"""validate linked transaction confirmation constraint

Revision ID: f1a6c9d82b40
Revises: e4c7b2a91f60
"""

from alembic import op


revision = "f1a6c9d82b40"
down_revision = "e4c7b2a91f60"
branch_labels = None
depends_on = None


def upgrade():
    op.execute("""
        ALTER TABLE pesapilot.linked_loan_transactions
        VALIDATE CONSTRAINT ck_linked_transaction_confirmation_fields
    """)


def downgrade():
    # PostgreSQL does not support reverting a validated constraint
    # to NOT VALID. The preceding migration handles constraint rollback.
    pass
