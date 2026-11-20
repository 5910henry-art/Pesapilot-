"""Independent accounting models for PesaPilot's platform operations."""

import uuid

from datetime import datetime, timezone

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Integer,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)

from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


def _utc_now():
    return datetime.now(timezone.utc)


class PlatformAccountingSettings(db.Model):
    """Singleton configuration for platform-level accounting."""

    __tablename__ = "platform_accounting_settings"

    id: Mapped[int] = mapped_column(
        Integer,
        primary_key=True,
        default=1,
    )

    functional_currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    updated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        onupdate=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint("id = 1", name="ck_platform_accounting_settings_singleton"),
    )


class PlatformFinancialAccount(db.Model):
    """Actual cash, bank or mobile-money accounts owned by PesaPilot."""

    __tablename__ = "platform_financial_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    account_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    institution_name: Mapped[str | None] = mapped_column(
        String(150),
        nullable=True,
    )

    display_reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "account_type IN ('cash', 'bank', 'mobile_money')",
            name="ck_platform_financial_account_type",
        ),
        UniqueConstraint(
            "id",
            "currency_code",
            name="uq_platform_financial_account_id_currency",
        ),
    )


class PlatformLedgerAccount(db.Model):
    """PesaPilot's independent chart of accounts."""

    __tablename__ = "platform_ledger_accounts"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    financial_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_financial_accounts.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )

    code: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    account_class: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    normal_balance: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    is_system: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["financial_account_id", "currency_code"],
            [
                "platform_financial_accounts.id",
                "platform_financial_accounts.currency_code",
            ],
            name="fk_platform_ledger_financial_account_currency",
        ),
        UniqueConstraint(
            "id",
            "currency_code",
            name="uq_platform_ledger_account_id_currency",
        ),
        UniqueConstraint(
            "code",
            name="uq_platform_ledger_account_code",
        ),
        CheckConstraint(
            "account_class IN "
            "('asset', 'liability', 'equity', 'income', 'expense')",
            name="ck_platform_ledger_account_class",
        ),
        CheckConstraint(
            "normal_balance IN ('debit', 'credit')",
            name="ck_platform_ledger_normal_balance",
        ),
    )


class PlatformJournalEntry(db.Model):
    """Platform-level double-entry journal transaction."""

    __tablename__ = "platform_journal_entries"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    reference: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    description: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        nullable=False,
    )

    transaction_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    functional_currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    posted_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_journal_entries.id"),
        nullable=True,
        unique=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "reference",
            name="uq_platform_journal_reference",
        ),
        CheckConstraint(
            "status IN ('draft', 'posted', 'reversed')",
            name="ck_platform_journal_status",
        ),
        Index(
            "ix_platform_journal_status_date",
            "status",
            "transaction_date",
        ),
    )


class PlatformJournalLine(db.Model):
    """Debit or credit line belonging to a platform journal entry."""

    __tablename__ = "platform_journal_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_journal_entries.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    ledger_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_ledger_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    debit_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    credit_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    functional_debit_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    functional_credit_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    exchange_rate: Mapped[object] = mapped_column(
        Numeric(24, 12),
        default=1,
        nullable=False,
    )

    memo: Mapped[str | None] = mapped_column(
        String(255),
        nullable=True,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["ledger_account_id", "currency_code"],
            [
                "platform_ledger_accounts.id",
                "platform_ledger_accounts.currency_code",
            ],
            name="fk_platform_journal_line_ledger_currency",
        ),
        CheckConstraint(
            "debit_minor >= 0 AND credit_minor >= 0",
            name="ck_platform_journal_line_nonnegative",
        ),
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR "
            "(credit_minor > 0 AND debit_minor = 0)",
            name="ck_platform_journal_line_one_side",
        ),
        CheckConstraint(
            "functional_debit_minor >= 0 AND "
            "functional_credit_minor >= 0",
            name="ck_platform_journal_line_functional_nonnegative",
        ),
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0 "
            "AND functional_credit_minor = 0) OR "
            "(credit_minor > 0 AND debit_minor = 0 "
            "AND functional_debit_minor = 0)",
            name="ck_platform_journal_line_functional_direction",
        ),
        CheckConstraint(
            "exchange_rate > 0",
            name="ck_platform_journal_line_exchange_rate",
        ),
    )
