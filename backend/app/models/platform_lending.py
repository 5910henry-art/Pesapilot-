"""Loan products for PesaPilot's platform-owned lending business."""

from datetime import datetime
from decimal import Decimal

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Numeric,
    String,
    Text,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .core import new_uuid, utc_now


class PlatformLoanProduct(db.Model):
    """A loan product offered by PesaPilot Lending Business."""

    __tablename__ = "platform_loan_products"

    id: Mapped[object] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    name: Mapped[str] = mapped_column(
        String(120),
        nullable=False,
    )

    code: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
        unique=True,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    min_principal_minor: Mapped[int] = mapped_column(
        db.BigInteger,
        nullable=False,
    )

    max_principal_minor: Mapped[int] = mapped_column(
        db.BigInteger,
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

    min_term_count: Mapped[int] = mapped_column(
        db.Integer,
        nullable=False,
    )

    max_term_count: Mapped[int] = mapped_column(
        db.Integer,
        nullable=False,
    )

    origination_fee_minor: Mapped[int] = mapped_column(
        db.BigInteger,
        default=0,
        nullable=False,
    )

    late_payment_fee_minor: Mapped[int] = mapped_column(
        db.BigInteger,
        default=0,
        nullable=False,
    )

    allow_early_repayment: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_by: Mapped[object | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    updated_by: Mapped[object | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "min_principal_minor > 0",
            name="ck_platform_product_min_principal",
        ),
        CheckConstraint(
            "max_principal_minor >= min_principal_minor",
            name="ck_platform_product_max_principal",
        ),
        CheckConstraint(
            "interest_method IN ('none', 'flat', 'reducing')",
            name="ck_platform_product_interest_method",
        ),
        CheckConstraint(
            "interest_rate_percent >= 0",
            name="ck_platform_product_interest_rate",
        ),
        CheckConstraint(
            "interest_method != 'none' OR interest_rate_percent = 0",
            name="ck_platform_product_no_interest_rate",
        ),
        CheckConstraint(
            "repayment_frequency IN "
            "('weekly', 'biweekly', 'monthly', 'quarterly', 'one_time')",
            name="ck_platform_product_frequency",
        ),
        CheckConstraint(
            "min_term_count > 0 AND max_term_count >= min_term_count",
            name="ck_platform_product_term_range",
        ),
        CheckConstraint(
            "origination_fee_minor >= 0",
            name="ck_platform_product_origination_fee",
        ),
        CheckConstraint(
            "late_payment_fee_minor >= 0",
            name="ck_platform_product_late_fee",
        ),
        Index(
            "ix_platform_product_active_currency",
            "is_active",
            "currency_code",
        ),
    )


class PlatformLoanApplication(db.Model):
    """A customer's application for a PesaPilot lending product."""

    __tablename__ = "platform_loan_applications"

    id: Mapped[object] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    customer_id: Mapped[object] = mapped_column(
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
    )

    product_id: Mapped[object] = mapped_column(
        ForeignKey("platform_loan_products.id", ondelete="RESTRICT"),
        nullable=False,
    )

    requested_principal_minor: Mapped[int] = mapped_column(
        db.BigInteger,
        nullable=False,
    )

    approved_principal_minor: Mapped[int | None] = mapped_column(
        db.BigInteger,
        nullable=True,
    )

    term_count: Mapped[int] = mapped_column(
        db.Integer,
        nullable=False,
    )

    purpose: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
    )

    decision_notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    reviewed_by: Mapped[object | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    submitted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    reviewed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    withdrawn_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "requested_principal_minor > 0",
            name="ck_platform_application_principal",
        ),
        CheckConstraint(
            "approved_principal_minor IS NULL OR "
            "(approved_principal_minor > 0 AND "
            "approved_principal_minor <= requested_principal_minor)",
            name="ck_platform_application_approved_principal",
        ),
        CheckConstraint(
            "term_count > 0",
            name="ck_platform_application_term",
        ),
        CheckConstraint(
            "status IN ('pending', 'approved', 'rejected', 'withdrawn')",
            name="ck_platform_application_status",
        ),
        Index(
            "uq_platform_application_one_pending_per_customer",
            "customer_id",
            unique=True,
            postgresql_where=text("status = 'pending'"),
            sqlite_where=text("status = 'pending'"),
        ),
        Index(
            "ix_platform_application_customer_submitted",
            "customer_id",
            "submitted_at",
        ),
        Index(
            "ix_platform_application_status_submitted",
            "status",
            "submitted_at",
        ),
    )
