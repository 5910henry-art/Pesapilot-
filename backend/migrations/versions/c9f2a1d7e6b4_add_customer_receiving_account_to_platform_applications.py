"""Add customer receiving account to platform loan applications."""

from alembic import op
import sqlalchemy as sa

revision = "c9f2a1d7e6b4"
down_revision = "948ebd8a833e"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "platform_loan_application_details",
        sa.Column(
            "customer_financial_account_id",
            sa.Uuid(as_uuid=True),
            sa.ForeignKey("financial_accounts.id", ondelete="RESTRICT"),
            nullable=True,
        ),
    )


def downgrade():
    op.drop_column(
        "platform_loan_application_details",
        "customer_financial_account_id",
    )
