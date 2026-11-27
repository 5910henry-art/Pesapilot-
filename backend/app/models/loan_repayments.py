import uuid

from datetime import date, datetime
from sqlalchemy import (
    CheckConstraint,
    Date,
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
from .core import new_uuid, utc_now


class LoanInstallment(db.Model):
    """One scheduled repayment installment for a personal loan."""

    __tablename__ = "loan_installments"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    personal_loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    installment_number: Mapped[int] = mapped_column(
        db.Integer, nullable=False
    )
    due_date: Mapped[date] = mapped_column(Date, nullable=False)
    amount_minor: Mapped[int] = mapped_column(
        db.BigInteger, nullable=False
    )
    principal_minor: Mapped[int] = mapped_column(
        db.BigInteger, nullable=False
    )
    interest_minor: Mapped[int] = mapped_column(
        db.BigInteger, default=0, nullable=False
    )
    paid_minor: Mapped[int] = mapped_column(
        db.BigInteger, default=0, nullable=False
    )
    status: Mapped[str] = mapped_column(
        String(20), default="scheduled", nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now,
        onupdate=utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "personal_loan_id"],
            ["personal_loans.workspace_id", "personal_loans.id"],
            name="fk_loan_installment_workspace_loan",
            ondelete="CASCADE",
        ),
        UniqueConstraint(
            "workspace_id", "personal_loan_id", "installment_number",
            name="uq_loan_installment_number",
        ),
        UniqueConstraint(
            "workspace_id", "personal_loan_id", "id",
            name="uq_loan_installment_scope",
        ),
        CheckConstraint(
            "installment_number > 0",
            name="ck_loan_installment_number_positive",
        ),
        CheckConstraint(
            "amount_minor > 0 AND principal_minor >= 0 "
            "AND interest_minor >= 0",
            name="ck_loan_installment_amounts",
        ),
        CheckConstraint(
            "amount_minor = principal_minor + interest_minor",
            name="ck_loan_installment_breakdown",
        ),
        CheckConstraint(
            "paid_minor >= 0 AND paid_minor <= amount_minor",
            name="ck_loan_installment_paid_range",
        ),
        CheckConstraint(
            "status IN ('scheduled', 'partially_paid', 'paid', "
            "'overdue', 'waived')",
            name="ck_loan_installment_status",
        ),
        Index(
            "ix_loan_installment_workspace_due",
            "workspace_id", "due_date",
        ),
    )


class LoanPayment(db.Model):
    """A payment made towards a personal loan."""

    __tablename__ = "loan_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    personal_loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    financial_account_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True), nullable=True
    )
    journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True), nullable=True
    )
    amount_minor: Mapped[int] = mapped_column(
        db.BigInteger, nullable=False
    )
    principal_minor: Mapped[int] = mapped_column(
        db.BigInteger, nullable=False
    )
    interest_minor: Mapped[int] = mapped_column(
        db.BigInteger, default=0, nullable=False
    )
    payment_date: Mapped[date] = mapped_column(Date, nullable=False)
    payment_method: Mapped[str] = mapped_column(
        String(20), default="other", nullable=False
    )
    payment_source: Mapped[str] = mapped_column(
        String(20),
        default="registered",
        server_default="registered",
        nullable=False,
    )
    reference: Mapped[str | None] = mapped_column(
        String(120), nullable=True
    )
    status: Mapped[str] = mapped_column(
        String(20), default="pending", nullable=False
    )
    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "personal_loan_id"],
            ["personal_loans.workspace_id", "personal_loans.id"],
            name="fk_loan_payment_workspace_loan",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "financial_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_loan_payment_workspace_account",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_loan_payment_workspace_journal",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id", "personal_loan_id", "id",
            name="uq_loan_payment_scope",
        ),
        UniqueConstraint(
            "workspace_id", "journal_entry_id",
            name="uq_loan_payment_journal",
        ),
        CheckConstraint(
            "amount_minor > 0 AND principal_minor >= 0 "
            "AND interest_minor >= 0",
            name="ck_loan_payment_amounts",
        ),
        CheckConstraint(
            "amount_minor = principal_minor + interest_minor",
            name="ck_loan_payment_breakdown",
        ),
        CheckConstraint(
            "payment_method IN ('cash', 'bank', 'mobile_money', 'other')",
            name="ck_loan_payment_method",
        ),
        CheckConstraint(
            "payment_source IN ('registered', 'external', 'linked')",
            name="ck_loan_payment_source",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'failed', 'reversed')",
            name="ck_loan_payment_status",
        ),
        CheckConstraint(
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
            name="ck_loan_payment_confirmed_accounting",
        ),
        Index(
            "ix_loan_payment_workspace_date",
            "workspace_id", "payment_date",
        ),
    )


class LoanPaymentAllocation(db.Model):
    """Allocates a payment across one or more scheduled installments."""

    __tablename__ = "loan_payment_allocations"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )
    workspace_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    personal_loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    payment_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    installment_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )
    amount_minor: Mapped[int] = mapped_column(
        db.BigInteger, nullable=False
    )
    principal_minor: Mapped[int] = mapped_column(
        db.BigInteger, nullable=False
    )
    interest_minor: Mapped[int] = mapped_column(
        db.BigInteger, default=0, nullable=False
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "personal_loan_id", "payment_id"],
            [
                "loan_payments.workspace_id",
                "loan_payments.personal_loan_id",
                "loan_payments.id",
            ],
            name="fk_loan_allocation_payment",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "personal_loan_id", "installment_id"],
            [
                "loan_installments.workspace_id",
                "loan_installments.personal_loan_id",
                "loan_installments.id",
            ],
            name="fk_loan_allocation_installment",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "payment_id", "installment_id",
            name="uq_loan_payment_installment",
        ),
        CheckConstraint(
            "amount_minor > 0 AND principal_minor >= 0 "
            "AND interest_minor >= 0",
            name="ck_loan_allocation_amounts",
        ),
        CheckConstraint(
            "amount_minor = principal_minor + interest_minor",
            name="ck_loan_allocation_breakdown",
        ),
    )
