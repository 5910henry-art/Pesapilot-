"""Add borrower source account to platform loan repayments."""

from alembic import op
import sqlalchemy as sa


revision = "f3a9c2d7e610"
down_revision = "c9f2a1d7e6b4"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "platform_loan_payments",
        sa.Column(
            "borrower_financial_account_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey(
                "financial_accounts.id",
                ondelete="RESTRICT",
            ),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column(
        "platform_loan_payments",
        "borrower_financial_account_id",
    )
