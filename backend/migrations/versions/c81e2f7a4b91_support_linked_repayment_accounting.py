"""support linked repayment accounting

Revision ID: c81e2f7a4b91
Revises: fa0c3a9c811a
"""

from alembic import op
import sqlalchemy as sa


revision = "c81e2f7a4b91"
down_revision = "fa0c3a9c811a"
branch_labels = None
depends_on = None


def upgrade():
    op.drop_constraint(
        "ck_loan_payment_source",
        "loan_payments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_loan_payment_source",
        "loan_payments",
        "payment_source IN ('registered', 'external', 'linked')",
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


def downgrade():
    connection = op.get_bind()
    linked_count = connection.execute(
        sa.text(
            "SELECT count(*) FROM loan_payments "
            "WHERE payment_source = 'linked'"
        )
    ).scalar_one()

    if linked_count:
        raise RuntimeError(
            "Cannot downgrade: linked loan repayment records exist."
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
        "(journal_entry_id IS NOT NULL AND "
        "((payment_source = 'registered' AND financial_account_id IS NOT NULL) "
        "OR (payment_source = 'external' AND financial_account_id IS NULL)))",
    )

    op.drop_constraint(
        "ck_loan_payment_source",
        "loan_payments",
        type_="check",
    )
    op.create_check_constraint(
        "ck_loan_payment_source",
        "loan_payments",
        "payment_source IN ('registered', 'external')",
    )
