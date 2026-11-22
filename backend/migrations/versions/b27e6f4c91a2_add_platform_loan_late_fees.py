"""Add installment-level platform loan late fees.

Revision ID: b27e6f4c91a2
Revises: f7a2c9d1e840
"""
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "b27e6f4c91a2"
down_revision = "f7a2c9d1e840"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

    op.add_column(
        "platform_loans",
        sa.Column(
            "late_payment_fee_minor",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
    )
    op.create_check_constraint(
        "ck_platform_loan_late_fee_snapshot",
        "platform_loans",
        "late_payment_fee_minor >= 0",
    )
    op.alter_column(
        "platform_loans",
        "late_payment_fee_minor",
        server_default=None,
    )

    op.create_table(
        "platform_loan_late_fees",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("loan_id", sa.Uuid(), nullable=False),
        sa.Column("installment_id", sa.Uuid(), nullable=False),
        sa.Column("currency_code", sa.String(length=3), nullable=False),
        sa.Column("amount_minor", sa.BigInteger(), nullable=False),
        sa.Column(
            "paid_minor",
            sa.BigInteger(),
            server_default=sa.text("0"),
            nullable=False,
        ),
        sa.Column("assessed_date", sa.Date(), nullable=False),
        sa.Column("journal_entry_id", sa.Uuid(), nullable=True),
        sa.Column(
            "status",
            sa.String(length=20),
            server_default="assessed",
            nullable=False,
        ),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            "updated_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.ForeignKeyConstraint(
            ["loan_id"],
            ["platform_loans.id"],
            name="fk_platform_late_fee_loan",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["loan_id", "installment_id"],
            [
                "platform_loan_installments.loan_id",
                "platform_loan_installments.id",
            ],
            name="fk_platform_late_fee_installment_loan",
            ondelete="RESTRICT",
        ),
        sa.ForeignKeyConstraint(
            ["currency_code"],
            ["currencies.code"],
            name="fk_platform_late_fee_currency",
        ),
        sa.ForeignKeyConstraint(
            ["journal_entry_id"],
            ["platform_journal_entries.id"],
            name="fk_platform_late_fee_journal",
            ondelete="RESTRICT",
        ),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint(
            "installment_id",
            name="uq_platform_late_fee_installment",
        ),
        sa.UniqueConstraint(
            "journal_entry_id",
            name="uq_platform_late_fee_journal",
        ),
        sa.CheckConstraint(
            "amount_minor > 0",
            name="ck_platform_late_fee_amount",
        ),
        sa.CheckConstraint(
            "paid_minor >= 0 AND paid_minor <= amount_minor",
            name="ck_platform_late_fee_paid",
        ),
        sa.CheckConstraint(
            "status IN ('assessed', 'partially_paid', 'paid')",
            name="ck_platform_late_fee_status",
        ),
    )

    op.create_index(
        "ix_platform_loan_late_fees_loan_status",
        "platform_loan_late_fees",
        ["loan_id", "status"],
    )

    currencies = sa.table(
        "currencies",
        sa.column("code", sa.String(length=3)),
        sa.column("is_active", sa.Boolean()),
    )
    ledgers = sa.table(
        "platform_ledger_accounts",
        sa.column("id", sa.Uuid()),
        sa.column("financial_account_id", sa.Uuid()),
        sa.column("code", sa.String(length=30)),
        sa.column("name", sa.String(length=150)),
        sa.column("account_class", sa.String(length=20)),
        sa.column("normal_balance", sa.String(length=10)),
        sa.column("currency_code", sa.String(length=3)),
        sa.column("is_system", sa.Boolean()),
        sa.column("is_active", sa.Boolean()),
    )

    active_codes = bind.execute(
        sa.select(currencies.c.code)
        .where(currencies.c.is_active.is_(True))
        .order_by(currencies.c.code)
    ).scalars().all()

    definitions = (
        (
            "PL-LOANS-LF-REC",
            "Platform Loan Late Fee Receivable",
            "asset",
            "debit",
        ),
        (
            "PL-LOANS-LF-INC",
            "Platform Loan Late Fee Income",
            "income",
            "credit",
        ),
    )

    for currency_code in active_codes:
        for prefix, name, account_class, normal_balance in definitions:
            code = f"{prefix}-{currency_code}"

            existing = bind.execute(
                sa.select(
                    ledgers.c.financial_account_id,
                    ledgers.c.name,
                    ledgers.c.account_class,
                    ledgers.c.normal_balance,
                    ledgers.c.currency_code,
                    ledgers.c.is_system,
                    ledgers.c.is_active,
                ).where(ledgers.c.code == code)
            ).mappings().one_or_none()

            if existing is not None:
                valid = (
                    existing["financial_account_id"] is None
                    and existing["name"] == name
                    and existing["account_class"] == account_class
                    and existing["normal_balance"] == normal_balance
                    and existing["currency_code"] == currency_code
                    and existing["is_system"] is True
                    and existing["is_active"] is True
                )
                if not valid:
                    raise RuntimeError(
                        f"Existing ledger {code} has unexpected configuration."
                    )
                continue

            bind.execute(
                sa.insert(ledgers).values(
                    id=uuid4(),
                    financial_account_id=None,
                    code=code,
                    name=name,
                    account_class=account_class,
                    normal_balance=normal_balance,
                    currency_code=currency_code,
                    is_system=True,
                    is_active=True,
                )
            )


def downgrade():
    bind = op.get_bind()
    count = bind.execute(
        sa.text("SELECT COUNT(*) FROM platform_loan_late_fees")
    ).scalar_one()

    if count:
        raise RuntimeError(
            "Cannot remove platform late-fee history while fee records exist."
        )

    op.drop_index(
        "ix_platform_loan_late_fees_loan_status",
        table_name="platform_loan_late_fees",
    )
    op.drop_table("platform_loan_late_fees")
    op.drop_constraint(
        "ck_platform_loan_late_fee_snapshot",
        "platform_loans",
        type_="check",
    )
    op.drop_column("platform_loans", "late_payment_fee_minor")
