"""Actual loans, repayment installments and disbursements for PesaPilot lending."""

import uuid
from datetime import date, datetime, timezone
from decimal import Decimal

from sqlalchemy import (
    BigInteger,
    CheckConstraint,
    Date,
    DateTime,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    Numeric,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .core import new_uuid


def _utc_now():
    return datetime.now(timezone.utc)


class PlatformLoan(db.Model):
    """An actual loan created from an approved platform loan application."""

    __tablename__ = "platform_loans"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_loan_applications.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    product_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_loan_products.id", ondelete="RESTRICT"),
        nullable=False,
    )

    approved_principal_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    principal_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    outstanding_principal_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    interest_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    interest_rate_percent: Mapped[Decimal] = mapped_column(
        Numeric(12, 6),
        default=Decimal("0"),
        nullable=False,
    )

    repayment_frequency: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    one_time_due_days: Mapped[int | None] = mapped_column(
        BigInteger,
        nullable=True,
    )

    term_count: Mapped[int] = mapped_column(
        db.Integer,
        nullable=False,
    )

    start_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    due_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="created",
        nullable=False,
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
        CheckConstraint(
            "approved_principal_minor > 0",
            name="ck_platform_loan_approved_principal",
        ),
        CheckConstraint(
            "principal_minor >= 0 AND "
            "principal_minor <= approved_principal_minor",
            name="ck_platform_loan_principal",
        ),
        CheckConstraint(
            "outstanding_principal_minor >= 0 AND "
            "outstanding_principal_minor <= principal_minor",
            name="ck_platform_loan_outstanding",
        ),
        CheckConstraint(
            "interest_method IN ('none', 'flat', 'reducing')",
            name="ck_platform_loan_interest_method",
        ),
        CheckConstraint(
            "interest_rate_percent >= 0",
            name="ck_platform_loan_interest_rate",
        ),
        CheckConstraint(
            "interest_method != 'none' OR interest_rate_percent = 0",
            name="ck_platform_loan_no_interest_rate",
        ),
        CheckConstraint(
            "repayment_frequency IN "
            "('weekly', 'biweekly', 'monthly', 'quarterly', 'one_time')",
            name="ck_platform_loan_frequency",
        ),
        CheckConstraint(
            "one_time_due_days IS NULL OR one_time_due_days > 0",
            name="ck_platform_loan_one_time_due_days",
        ),
        CheckConstraint(
            "term_count > 0",
            name="ck_platform_loan_term",
        ),
        CheckConstraint(
            "status IN "
            "('created', 'funding', 'active', 'paid', 'overdue', "
            "'written_off', 'cancelled')",
            name="ck_platform_loan_status",
        ),
        CheckConstraint(
            "(start_date IS NULL AND due_date IS NULL) OR "
            "(start_date IS NOT NULL AND due_date IS NOT NULL "
            "AND due_date >= start_date)",
            name="ck_platform_loan_dates",
        ),
        UniqueConstraint(
            "id",
            "currency_code",
            name="uq_platform_loan_id_currency",
        ),
        Index(
            "ix_platform_loan_customer_status",
            "customer_id",
            "status",
        ),
    )


class PlatformLoanInstallment(db.Model):
    """A scheduled repayment installment for a platform loan."""

    __tablename__ = "platform_loan_installments"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    loan_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_loans.id", ondelete="RESTRICT"),
        nullable=False,
    )

    installment_number: Mapped[int] = mapped_column(
        db.Integer,
        nullable=False,
    )

    due_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    principal_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    interest_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    paid_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="scheduled",
        nullable=False,
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
        UniqueConstraint(
            "loan_id",
            "installment_number",
            name="uq_platform_installment_loan_number",
        ),
        UniqueConstraint(
            "loan_id",
            "id",
            name="uq_platform_installment_loan_id",
        ),
        CheckConstraint(
            "installment_number > 0",
            name="ck_platform_installment_number",
        ),
        CheckConstraint(
            "amount_minor > 0 AND principal_minor > 0 "
            "AND interest_minor >= 0 "
            "AND amount_minor = principal_minor + interest_minor",
            name="ck_platform_installment_amounts",
        ),
        CheckConstraint(
            "paid_minor >= 0 AND paid_minor <= amount_minor",
            name="ck_platform_installment_paid",
        ),
        CheckConstraint(
            "status IN "
            "('scheduled', 'partially_paid', 'paid', 'overdue', 'waived')",
            name="ck_platform_installment_status",
        ),
        Index(
            "ix_platform_installment_due_status",
            "due_date",
            "status",
        ),
    )


class PlatformLoanDisbursement(db.Model):
    """A funding tranche paid out against a platform loan."""

    __tablename__ = "platform_loan_disbursements"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    financial_account_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("platform_journal_entries.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )

    # Customer-side accounting references for this disbursement tranche.
    # Nullable to preserve compatibility with historical disbursements.
    customer_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    customer_financial_account_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    customer_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    funding_source: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    disbursement_date: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    recorded_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "id", "loan_id", "currency_code", "funding_source",
            name="uq_platform_disbursement_funding_identity",
        ),
        UniqueConstraint(
            "customer_journal_entry_id",
            name="uq_platform_disbursement_customer_journal",
        ),
        ForeignKeyConstraint(
            ["customer_workspace_id", "customer_financial_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_platform_disbursement_customer_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["customer_workspace_id", "customer_journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_platform_disbursement_customer_journal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "("
            "customer_workspace_id IS NULL "
            "AND customer_financial_account_id IS NULL "
            "AND customer_journal_entry_id IS NULL"
            ") OR ("
            "customer_workspace_id IS NOT NULL "
            "AND customer_financial_account_id IS NOT NULL "
            "AND customer_journal_entry_id IS NOT NULL"
            ")",
            name="ck_platform_disbursement_customer_refs_complete",
        ),
        ForeignKeyConstraint(
            ["loan_id", "currency_code"],
            ["platform_loans.id", "platform_loans.currency_code"],
            name="fk_platform_disbursement_loan_currency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["financial_account_id", "currency_code"],
            [
                "platform_financial_accounts.id",
                "platform_financial_accounts.currency_code",
            ],
            name="fk_platform_disbursement_account_currency",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount_minor > 0",
            name="ck_platform_disbursement_amount",
        ),
        CheckConstraint(
            "funding_source IN ('pesapilot_capital', 'external_investor')",
            name="ck_platform_disbursement_funding_source",
        ),
        Index(
            "ix_platform_disbursement_loan_date",
            "loan_id",
            "disbursement_date",
        ),
    )



class PlatformLoanPayment(db.Model):
    """A repayment received or recorded against a platform loan."""

    __tablename__ = "platform_loan_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    financial_account_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("platform_journal_entries.id", ondelete="RESTRICT"),
        nullable=True,
        unique=True,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    principal_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    interest_minor: Mapped[int] = mapped_column(
        BigInteger,
        default=0,
        nullable=False,
    )

    payment_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    payment_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    reference: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
    )

    recorded_by: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    reversed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejected_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=True,
    )

    rejected_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    rejection_reason: Mapped[str | None] = mapped_column(
        String(500),
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
        ForeignKeyConstraint(
            ["loan_id", "currency_code"],
            ["platform_loans.id", "platform_loans.currency_code"],
            name="fk_platform_payment_loan_currency",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["financial_account_id", "currency_code"],
            [
                "platform_financial_accounts.id",
                "platform_financial_accounts.currency_code",
            ],
            name="fk_platform_payment_account_currency",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "loan_id",
            "id",
            name="uq_platform_payment_loan_id",
        ),
        UniqueConstraint(
            "loan_id",
            "reference",
            name="uq_platform_payment_loan_reference",
        ),
        CheckConstraint(
            "amount_minor > 0",
            name="ck_platform_payment_amount",
        ),
        CheckConstraint(
            "principal_minor >= 0 AND interest_minor >= 0 "
            "AND principal_minor + interest_minor <= amount_minor",
            name="ck_platform_payment_allocated_amounts",
        ),
        CheckConstraint(
            "payment_method IN ('cash', 'bank', 'mobile_money', 'other')",
            name="ck_platform_payment_method",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'failed', 'reversed', 'rejected')",
            name="ck_platform_payment_status",
        ),
        CheckConstraint(
            "status != 'rejected' OR "
            "(rejected_by IS NOT NULL AND rejected_at IS NOT NULL "
            "AND rejection_reason IS NOT NULL "
            "AND length(trim(rejection_reason)) > 0)",
            name="ck_platform_payment_rejection",
        ),
        CheckConstraint(
            "status NOT IN ('confirmed', 'reversed') OR "
            "(financial_account_id IS NOT NULL "
            "AND journal_entry_id IS NOT NULL "
            "AND confirmed_by IS NOT NULL "
            "AND confirmed_at IS NOT NULL "
            "AND principal_minor + interest_minor = amount_minor)",
            name="ck_platform_payment_confirmation",
        ),
        Index(
            "ix_platform_payment_loan_date",
            "loan_id",
            "payment_date",
        ),
        Index(
            "ix_platform_payment_status_date",
            "status",
            "payment_date",
        ),
    )


class PlatformLoanPaymentAllocation(db.Model):
    """The principal and interest portions applied to one installment."""

    __tablename__ = "platform_loan_payment_allocations"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    payment_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    installment_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    principal_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    interest_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utc_now,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["loan_id", "payment_id"],
            ["platform_loan_payments.loan_id", "platform_loan_payments.id"],
            name="fk_platform_allocation_payment_loan",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["loan_id", "installment_id"],
            ["platform_loan_installments.loan_id", "platform_loan_installments.id"],
            name="fk_platform_allocation_installment_loan",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "payment_id",
            "installment_id",
            name="uq_platform_allocation_payment_installment",
        ),
        CheckConstraint(
            "amount_minor > 0 AND principal_minor >= 0 "
            "AND interest_minor >= 0 "
            "AND amount_minor = principal_minor + interest_minor",
            name="ck_platform_allocation_amounts",
        ),
        Index(
            "ix_platform_allocation_loan",
            "loan_id",
        ),
    )
