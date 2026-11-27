"""Support pending linked repayment allocations.

Revision ID: 3d8f6a2b91c4
Revises: e4c9a7d21f60
"""

from alembic import op
import sqlalchemy as sa


revision = "3d8f6a2b91c4"
down_revision = "e4c9a7d21f60"
branch_labels = None
depends_on = None


def upgrade():
    op.add_column(
        "loan_payment_allocations",
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="confirmed",
            nullable=False,
        ),
    )

    op.create_check_constraint(
        "ck_loan_allocation_status",
        "loan_payment_allocations",
        "status IN ('pending', 'confirmed')",
    )

    op.drop_constraint(
        "ck_loan_payment_confirmed_accounting",
        "loan_payments",
        type_="check",
    )

    op.create_check_constraint(
        "ck_loan_payment_confirmed_accounting",
        "loan_payments",
        "status != 'confirmed' OR "
        "((payment_source = 'registered' "
        "AND financial_account_id IS NOT NULL "
        "AND journal_entry_id IS NOT NULL) "
        "OR (payment_source = 'external' "
        "AND financial_account_id IS NULL "
        "AND journal_entry_id IS NOT NULL) "
        "OR (payment_source = 'linked' AND ("
        "(financial_account_id IS NULL AND journal_entry_id IS NULL) "
        "OR (financial_account_id IS NOT NULL AND journal_entry_id IS NOT NULL)"
        ")))",
    )


def downgrade():
    bind = op.get_bind()
    incompatible = bind.execute(
        sa.text(
            "SELECT 1 FROM loan_payments "
            "WHERE status = 'confirmed' "
            "AND payment_source = 'linked' "
            "AND (financial_account_id IS NOT NULL "
            "OR journal_entry_id IS NOT NULL) "
            "LIMIT 1"
        )
    ).first()

    if incompatible:
        raise RuntimeError(
            "Cannot downgrade: confirmed linked repayments contain "
            "borrower-side accounting references."
        )

    op.drop_constraint(
        "ck_loan_payment_confirmed_accounting",
        "loan_payments",
        type_="check",
    )

    op.create_check_constraint(
        "ck_loan_payment_confirmed_accounting",
        "loan_payments",
        "status != 'confirmed' OR "
        "((payment_source = 'registered' "
        "AND financial_account_id IS NOT NULL "
        "AND journal_entry_id IS NOT NULL) "
        "OR (payment_source = 'external' "
        "AND financial_account_id IS NULL "
        "AND journal_entry_id IS NOT NULL) "
        "OR (payment_source = 'linked' "
        "AND financial_account_id IS NULL "
        "AND journal_entry_id IS NULL))",
    )

    op.drop_constraint(
        "ck_loan_allocation_status",
        "loan_payment_allocations",
        type_="check",
    )

    op.drop_column(
        "loan_payment_allocations",
        "status",
    )
