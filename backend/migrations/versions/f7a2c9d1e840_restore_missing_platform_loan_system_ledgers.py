"""restore missing currency-specific platform loan system ledgers

Revision ID: f7a2c9d1e840
Revises: c81d6f2a9b40
"""
from uuid import uuid4

from alembic import op
import sqlalchemy as sa


revision = "f7a2c9d1e840"
down_revision = "c81d6f2a9b40"
branch_labels = None
depends_on = None


def upgrade():
    bind = op.get_bind()

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
        sa.select(currencies.c.code).where(
            currencies.c.is_active.is_(True)
        ).order_by(currencies.c.code)
    ).scalars().all()

    definitions = (
        (
            "PL-LOANS-RECEIVABLE",
            "Platform Loans Receivable",
            "asset",
            "debit",
        ),
        (
            "PL-LOANS-INTEREST-INCOME",
            "Platform Loans Interest Income",
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
                        f"Existing ledger {code} has an unexpected configuration."
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
    # Preserve system ledgers because journal history may depend on them.
    pass
