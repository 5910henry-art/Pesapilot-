"""Investor capital and funding allocations for PesaPilot Lending Business."""

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
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .core import new_uuid


def _utc_now():
    return datetime.now(timezone.utc)


class PlatformInvestor(db.Model):
    """An external capital provider for PesaPilot Lending Business."""

    __tablename__ = "platform_investors"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    name: Mapped[str] = mapped_column(String(160), nullable=False)
    email: Mapped[str | None] = mapped_column(String(254), nullable=True)
    phone: Mapped[str | None] = mapped_column(String(30), nullable=True)
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    is_active: Mapped[bool] = mapped_column(
        Boolean, default=True, nullable=False
    )
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now,
        onupdate=_utc_now, nullable=False
    )

    __table_args__ = (
        CheckConstraint("length(trim(name)) > 0", name="ck_platform_investor_name"),
        Index("ix_platform_investor_active_name", "is_active", "name"),
    )


class PlatformInvestorContribution(db.Model):
    """Investor capital received and posted into platform accounting."""

    __tablename__ = "platform_investor_contributions"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    investor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_investors.id", ondelete="RESTRICT"),
        nullable=False
    )
    financial_account_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_journal_entries.id", ondelete="RESTRICT"),
        nullable=False, unique=True
    )
    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    contribution_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="posted", nullable=False
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["financial_account_id", "currency_code"],
            [
                "platform_financial_accounts.id",
                "platform_financial_accounts.currency_code",
            ],
            name="fk_platform_investor_contribution_account_currency",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor > 0", name="ck_platform_investor_contribution_amount"),
        CheckConstraint(
            "status IN ('posted', 'reversed')",
            name="ck_platform_investor_contribution_status",
        ),
        Index(
            "ix_platform_investor_contribution_investor_date",
            "investor_id", "contribution_date",
        ),
    )


class PlatformInvestorWithdrawal(db.Model):
    """Return of unallocated investor capital."""

    __tablename__ = "platform_investor_withdrawals"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    investor_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_investors.id", ondelete="RESTRICT"),
        nullable=False
    )
    financial_account_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_journal_entries.id", ondelete="RESTRICT"),
        nullable=False, unique=True
    )
    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    withdrawal_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False
    )
    reference: Mapped[str | None] = mapped_column(String(100), nullable=True)
    status: Mapped[str] = mapped_column(
        String(20), default="posted", nullable=False
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["financial_account_id", "currency_code"],
            [
                "platform_financial_accounts.id",
                "platform_financial_accounts.currency_code",
            ],
            name="fk_platform_investor_withdrawal_account_currency",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor > 0", name="ck_platform_investor_withdrawal_amount"),
        CheckConstraint(
            "status IN ('posted', 'reversed')",
            name="ck_platform_investor_withdrawal_status",
        ),
        Index(
            "ix_platform_investor_withdrawal_investor_date",
            "investor_id", "withdrawal_date",
        ),
    )


class PlatformLoanFundingAllocation(db.Model):
    """Capital assigned to a specific platform loan disbursement."""

    __tablename__ = "platform_loan_funding_allocations"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    disbursement_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False
    )
    funding_source: Mapped[str] = mapped_column(String(30), nullable=False)
    investor_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_investors.id", ondelete="RESTRICT"),
        nullable=True
    )
    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    outstanding_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["disbursement_id", "loan_id", "currency_code", "funding_source"],
            [
                "platform_loan_disbursements.id",
                "platform_loan_disbursements.loan_id",
                "platform_loan_disbursements.currency_code",
                "platform_loan_disbursements.funding_source",
            ],
            name="fk_platform_funding_allocation_disbursement_identity",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "loan_id", "id", name="uq_platform_funding_allocation_loan_id"
        ),
        CheckConstraint("amount_minor > 0", name="ck_platform_funding_allocation_amount"),
        CheckConstraint(
            "outstanding_minor >= 0 AND outstanding_minor <= amount_minor",
            name="ck_platform_funding_allocation_outstanding",
        ),
        CheckConstraint(
            "funding_source IN ('pesapilot_capital', 'external_investor')",
            name="ck_platform_funding_allocation_source",
        ),
        CheckConstraint(
            "(funding_source = 'pesapilot_capital' AND investor_id IS NULL) OR "
            "(funding_source = 'external_investor' AND investor_id IS NOT NULL)",
            name="ck_platform_funding_allocation_investor",
        ),
        Index(
            "ix_platform_funding_allocation_loan",
            "loan_id", "funding_source",
        ),
        Index(
            "ix_platform_funding_allocation_investor",
            "investor_id", "currency_code",
        ),
    )


class PlatformLoanFundingRelease(db.Model):
    """Principal released from funding allocations by a repayment."""

    __tablename__ = "platform_loan_funding_releases"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    funding_allocation_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(BigInteger, nullable=False)
    is_reversed: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=_utc_now, nullable=False
    )
    reversed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["loan_id", "payment_id"],
            ["platform_loan_payments.loan_id", "platform_loan_payments.id"],
            name="fk_platform_funding_release_payment",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["loan_id", "funding_allocation_id"],
            [
                "platform_loan_funding_allocations.loan_id",
                "platform_loan_funding_allocations.id",
            ],
            name="fk_platform_funding_release_allocation",
            ondelete="RESTRICT",
        ),
        CheckConstraint("amount_minor > 0", name="ck_platform_funding_release_amount"),
        UniqueConstraint(
            "payment_id",
            "funding_allocation_id",
            name="uq_platform_funding_release_payment_allocation",
        ),
        CheckConstraint(
            "(is_reversed = false AND reversed_at IS NULL) OR "
            "(is_reversed = true AND reversed_at IS NOT NULL)",
            name="ck_platform_funding_release_reversal",
        ),
        Index(
            "ix_platform_funding_release_payment",
            "payment_id", "is_reversed",
        ),
    )
