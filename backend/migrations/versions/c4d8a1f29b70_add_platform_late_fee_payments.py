"""Add separate platform loan late-fee payment records and allocations.

Revision ID: c4d8a1f29b70
Revises: b27e6f4c91a2
"""
from alembic import op
import sqlalchemy as sa

revision = "c4d8a1f29b70"
down_revision = "b27e6f4c91a2"
branch_labels = None
depends_on = None


def upgrade():
    op.create_unique_constraint(
        "uq_platform_late_fee_loan_id",
        "platform_loan_late_fees",
        ["loan_id", "id"],
    )

    op.create_table(
        "platform_loan_late_fee_payments",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("loan_id", sa.Uuid(), nullable=False),
        sa.Column("financial_account_id", sa.Uuid(), nullable=True),
        sa.Column("journal_entry_id", sa.Uuid(), nullable=True),
        sa.Column("currency_code", sa.String(3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("payment_date", sa.Date(), nullable=False),
        sa.Column("payment_method", sa.String(20), nullable=False),
        sa.Column("reference", sa.String(100), nullable=True),
        sa.Column("status", sa.String(20), server_default="pending", nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_by", sa.Uuid(), nullable=True),
        sa.Column("rejected_by", sa.Uuid(), nullable=True),
        sa.Column("confirmed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("reversed_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejected_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rejection_reason", sa.String(500), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False),
        sa.ForeignKeyConstraint(
            ["loan_id", "currency_code"],
            ["platform_loans.id", "platform_loans.currency_code"],
            name="fk_platform_late_fee_payment_loan_currency",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["financial_account_id", "currency_code"],
            ["platform_financial_accounts.id", "platform_financial_accounts.currency_code"],
            name="fk_platform_late_fee_payment_account_currency",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"], ["platform_journal_entries.id"],
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["recorded_by"], ["users.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["confirmed_by"], ["users.id"], ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["rejected_by"], ["users.id"], ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "loan_id", "id",
            name="uq_platform_late_fee_payment_loan_id",
        ),
        sa.UniqueConstraint(
            "loan_id", "reference",
            name="uq_platform_late_fee_payment_loan_reference",
        ),
        sa.UniqueConstraint(
            "journal_entry_id",
            name="uq_platform_late_fee_payment_journal",
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_platform_late_fee_payment_amount",
        ),
        sa.CheckConstraint(
            "payment_method IN ('cash', 'bank', 'mobile_money', 'other')",
            name="ck_platform_late_fee_payment_method",
        ),
        sa.CheckConstraint(
            "status IN ('pending', 'confirmed', 'reversed', 'rejected')",
            name="ck_platform_late_fee_payment_status",
        ),
        sa.CheckConstraint(
            "status != 'rejected' OR "
            "(rejected_at IS NOT NULL AND rejection_reason IS NOT NULL "
            "AND length(trim(rejection_reason)) > 0)",
            name="ck_platform_late_fee_payment_rejection",
        ),
        sa.CheckConstraint(
            "status NOT IN ('confirmed', 'reversed') OR "
            "(financial_account_id IS NOT NULL AND journal_entry_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL)",
            name="ck_platform_late_fee_payment_confirmation",
        ),
    )
    op.create_index(
        "ix_platform_late_fee_payment_loan_date",
        "platform_loan_late_fee_payments",
        ["loan_id", "payment_date"],
    )
    op.create_index(
        "ix_platform_late_fee_payment_status_date",
        "platform_loan_late_fee_payments",
        ["status", "payment_date"],
    )

    op.create_table(
        "platform_loan_late_fee_payment_allocations",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("loan_id", sa.Uuid(), nullable=False),
        sa.Column("payment_id", sa.Uuid(), nullable=False),
        sa.Column("late_fee_id", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["loan_id", "payment_id"],
            [
                "platform_loan_late_fee_payments.loan_id",
                "platform_loan_late_fee_payments.id",
            ],
            name="fk_platform_late_fee_allocation_payment_loan",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["loan_id", "late_fee_id"],
            [
                "platform_loan_late_fees.loan_id",
                "platform_loan_late_fees.id",
            ],
            name="fk_platform_late_fee_allocation_fee_loan",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "payment_id", "late_fee_id",
            name="uq_platform_late_fee_payment_allocation",
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_platform_late_fee_allocation_amount",
        ),
    )
    op.create_index(
        "ix_platform_late_fee_allocation_loan",
        "platform_loan_late_fee_payment_allocations",
        ["loan_id"],
    )


def downgrade():
    bind = op.get_bind()
    allocations = bind.execute(
        sa.text("SELECT COUNT(*) FROM platform_loan_late_fee_payment_allocations")
    ).scalar_one()
    payments = bind.execute(
        sa.text("SELECT COUNT(*) FROM platform_loan_late_fee_payments")
    ).scalar_one()

    if allocations or payments:
        raise RuntimeError(
            "Cannot remove late-fee payment history while payment or allocation records exist."
        )

    op.drop_index(
        "ix_platform_late_fee_allocation_loan",
        table_name="platform_loan_late_fee_payment_allocations",
    )
    op.drop_table("platform_loan_late_fee_payment_allocations")
    op.drop_index(
        "ix_platform_late_fee_payment_status_date",
        table_name="platform_loan_late_fee_payments",
    )
    op.drop_index(
        "ix_platform_late_fee_payment_loan_date",
        table_name="platform_loan_late_fee_payments",
    )
    op.drop_table("platform_loan_late_fee_payments")
    op.drop_constraint(
        "uq_platform_late_fee_loan_id",
        "platform_loan_late_fees",
        type_="unique",
    )
