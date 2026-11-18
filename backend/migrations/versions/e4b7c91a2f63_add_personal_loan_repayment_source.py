"""Add source tracking to personal loan repayments."""

from alembic import op
import sqlalchemy as sa


revision = "e4b7c91a2f63"
down_revision = "d8a4c71f2b90"
branch_labels = None
depends_on = None


def upgrade():
    with op.batch_alter_table("loan_payments") as batch_op:
        batch_op.add_column(
            sa.Column(
                "payment_source",
                sa.String(length=20),
                server_default="registered",
                nullable=False,
            )
        )

        batch_op.create_check_constraint(
            "ck_loan_payment_source",
            "payment_source IN ('registered', 'external')",
        )

        batch_op.drop_constraint(
            "ck_loan_payment_confirmed_accounting",
            type_="check",
        )

        batch_op.create_check_constraint(
            "ck_loan_payment_confirmed_accounting",
            "status != 'confirmed' OR "
            "(journal_entry_id IS NOT NULL AND "
            "((payment_source = 'registered' AND financial_account_id IS NOT NULL) "
            "OR (payment_source = 'external' AND financial_account_id IS NULL)))",
        )


def downgrade():
    # Do not silently discard external-payment accounting distinctions.
    connection = op.get_bind()
    external_confirmed = connection.execute(
        sa.text(
            "SELECT count(*) FROM loan_payments "
            "WHERE payment_source = 'external' AND status = 'confirmed'"
        )
    ).scalar_one()

    if external_confirmed:
        raise RuntimeError(
            "Cannot downgrade: confirmed external loan repayments exist."
        )

    with op.batch_alter_table("loan_payments") as batch_op:
        batch_op.drop_constraint(
            "ck_loan_payment_confirmed_accounting",
            type_="check",
        )

        batch_op.drop_constraint(
            "ck_loan_payment_source",
            type_="check",
        )

        batch_op.create_check_constraint(
            "ck_loan_payment_confirmed_accounting",
            "status != 'confirmed' OR "
            "(financial_account_id IS NOT NULL AND journal_entry_id IS NOT NULL)",
        )

        batch_op.drop_column("payment_source")
