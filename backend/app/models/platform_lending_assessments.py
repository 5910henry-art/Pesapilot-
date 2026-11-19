"""Private application declarations and versioned affordability assessments."""

import uuid
from datetime import datetime

from sqlalchemy import (
    BigInteger,
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    JSON,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .core import new_uuid, utc_now


class PlatformLoanApplicationDetails(db.Model):
    """Private applicant declarations; never expose encrypted identity data."""

    __tablename__ = "platform_loan_application_details"

    application_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("platform_loan_applications.id", ondelete="CASCADE"),
        primary_key=True,
    )

    customer_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    customer_financial_account_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("financial_accounts.id", ondelete="RESTRICT"),
        nullable=True,
    )
    full_name: Mapped[str] = mapped_column(String(150), nullable=False)
    national_id_encrypted: Mapped[str] = mapped_column(Text, nullable=False)
    phone: Mapped[str] = mapped_column(String(30), nullable=False)
    email: Mapped[str | None] = mapped_column(String(255), nullable=True)
    residential_address: Mapped[str] = mapped_column(String(500), nullable=False)

    employment_status: Mapped[str] = mapped_column(
        String(30), nullable=False
    )
    employer_business_name: Mapped[str | None] = mapped_column(
        String(200), nullable=True
    )

    monthly_income_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    monthly_expenses_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )
    existing_monthly_repayments_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    explanation: Mapped[str | None] = mapped_column(Text, nullable=True)

    alternative_contact_name: Mapped[str | None] = mapped_column(
        String(150), nullable=True
    )
    alternative_contact_phone: Mapped[str | None] = mapped_column(
        String(30), nullable=True
    )
    alternative_contact_relationship: Mapped[str | None] = mapped_column(
        String(80), nullable=True
    )
    additional_notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    affordability_consent: Mapped[bool] = mapped_column(
        Boolean, default=False, nullable=False
    )
    affordability_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )
    affordability_consent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "employment_status IN "
            "('employed', 'self_employed', 'unemployed', "
            "'student', 'retired', 'other')",
            name="ck_platform_application_employment_status",
        ),
        CheckConstraint(
            "monthly_income_minor >= 0 "
            "AND monthly_expenses_minor >= 0 "
            "AND existing_monthly_repayments_minor >= 0",
            name="ck_platform_application_declared_amounts",
        ),
        CheckConstraint(
            "(affordability_consent = false "
            "AND affordability_workspace_id IS NULL "
            "AND affordability_consent_at IS NULL) OR "
            "(affordability_consent = true "
            "AND affordability_workspace_id IS NOT NULL "
            "AND affordability_consent_at IS NOT NULL)",
            name="ck_platform_application_consent_scope",
        ),
    )


class PlatformLoanAffordabilityAssessment(db.Model):
    """Immutable, versioned assessment report for a loan application."""

    __tablename__ = "platform_loan_affordability_assessments"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )

    application_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("platform_loan_applications.id", ondelete="CASCADE"),
        nullable=False,
    )

    version: Mapped[int] = mapped_column(Integer, nullable=False)

    status: Mapped[str] = mapped_column(String(30), nullable=False)

    source_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
    )

    evidence_window_months: Mapped[int] = mapped_column(
        Integer, default=6, nullable=False
    )

    recommended_monthly_instalment_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    estimated_affordable_principal_minor: Mapped[int] = mapped_column(
        BigInteger, default=0, nullable=False
    )

    report: Mapped[dict] = mapped_column(
        JSON, default=dict, nullable=False
    )

    reasons: Mapped[list] = mapped_column(
        JSON, default=list, nullable=False
    )

    assessed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        UniqueConstraint(
            "application_id",
            "version",
            name="uq_platform_affordability_application_version",
        ),
        CheckConstraint(
            "version > 0",
            name="ck_platform_affordability_version",
        ),
        CheckConstraint(
            "status IN "
            "('verified', 'needs_review', 'high_risk', "
            "'insufficient_records')",
            name="ck_platform_affordability_status",
        ),
        CheckConstraint(
            "evidence_window_months > 0",
            name="ck_platform_affordability_evidence_window",
        ),
        CheckConstraint(
            "recommended_monthly_instalment_minor >= 0 "
            "AND estimated_affordable_principal_minor >= 0",
            name="ck_platform_affordability_amounts",
        ),
        Index(
            "ix_platform_affordability_application_assessed",
            "application_id",
            "assessed_at",
        ),
    )
