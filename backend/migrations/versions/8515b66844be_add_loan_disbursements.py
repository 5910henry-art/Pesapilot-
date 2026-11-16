"""add loan disbursements

Revision ID: 8515b66844be
Revises: 2bfd8dd9b618
Create Date: 2026-11-14
"""

from alembic import op
import sqlalchemy as sa


revision = "8515b66844be"
down_revision = "2bfd8dd9b618"
branch_labels = None
depends_on = None


def upgrade():
    op.create_table(
        "loan_disbursements",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("workspace_id", sa.Uuid(), nullable=False),
        sa.Column("personal_loan_id", sa.Uuid(), nullable=False),
        sa.Column("financial_account_id", sa.Uuid(), nullable=False),
        sa.Column("journal_entry_id", sa.Uuid(), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column("disbursement_date", sa.Date(), nullable=False),
        sa.Column("payment_method", sa.String(length=20), nullable=False),
        sa.Column("recorded_by", sa.Uuid(), nullable=False),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            nullable=False,
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_loan_disbursement_amount_positive",
        ),
        sa.CheckConstraint(
            "payment_method IN ('cash', 'bank', 'mobile_money', 'other')",
            name="ck_loan_disbursement_payment_method",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "personal_loan_id"],
            ["personal_loans.workspace_id", "personal_loans.id"],
            name="fk_disbursement_workspace_loan",
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "financial_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_disbursement_workspace_account",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_disbursement_workspace_journal",
        ),
        sa.ForeignKeyConstraint(
            ["workspace_id", "recorded_by"],
            ["workspace_members.workspace_id", "workspace_members.user_id"],
            name="fk_disbursement_workspace_recorder",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "workspace_id",
            "personal_loan_id",
            name="uq_loan_disbursement_per_loan",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "journal_entry_id",
            name="uq_loan_disbursement_journal",
        ),
        sa.UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_loan_disbursement_workspace_id",
        ),
    )

    op.create_index(
        "ix_loan_disbursement_workspace_date",
        "loan_disbursements",
        ["workspace_id", "disbursement_date"],
    )


def downgrade():
    op.drop_index(
        "ix_loan_disbursement_workspace_date",
        table_name="loan_disbursements",
    )
    op.drop_table("loan_disbursements")
