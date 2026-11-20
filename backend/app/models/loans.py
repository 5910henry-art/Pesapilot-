import uuid

from datetime import date, datetime
from decimal import Decimal

from sqlalchemy import (
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
from .core import new_uuid, utc_now


class PersonalLoan(db.Model):
    """A personal loan recorded by an individual in their workspace."""

    __tablename__ = "personal_loans"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    direction: Mapped[str] = mapped_column(
        String(10),
        nullable=False,
    )

    counterparty_user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    counterparty_name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    counterparty_phone: Mapped[str | None] = mapped_column(
        String(30),
        nullable=True,
    )

    loan_type: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
    )

    principal_minor: Mapped[int] = mapped_column(
        db.BigInteger,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    interest_method: Mapped[str] = mapped_column(
        String(20),
        default="none",
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

    term_count: Mapped[int] = mapped_column(
        db.Integer,
        nullable=False,
    )

    installment_minor: Mapped[int | None] = mapped_column(
        db.BigInteger,
        nullable=True,
    )

    start_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    due_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="draft",
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
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
        UniqueConstraint(
            "workspace_id", "id",
            name="uq_personal_loan_workspace_id",
        ),
        CheckConstraint(
            "direction IN ('borrowed', 'lent')",
            name="ck_personal_loan_direction",
        ),
        CheckConstraint(
            "loan_type IN "
            "('soft', 'interest_free', 'interest_bearing', "
            "'emergency', 'business', 'personal')",
            name="ck_personal_loan_type",
        ),
        CheckConstraint(
            "principal_minor > 0",
            name="ck_personal_loan_principal_positive",
        ),
        CheckConstraint(
            "interest_method IN ('none', 'flat', 'reducing')",
            name="ck_personal_loan_interest_method",
        ),
        CheckConstraint(
            "interest_rate_percent >= 0",
            name="ck_personal_loan_interest_rate",
        ),
        CheckConstraint(
            "interest_method != 'none' OR interest_rate_percent = 0",
            name="ck_personal_loan_no_interest_rate",
        ),
        CheckConstraint(
            "loan_type != 'interest_free' OR interest_method = 'none'",
            name="ck_personal_loan_interest_free",
        ),
        CheckConstraint(
            "repayment_frequency IN "
            "('weekly', 'biweekly', 'monthly', 'quarterly', 'one_time')",
            name="ck_personal_loan_frequency",
        ),
        CheckConstraint(
            "term_count > 0",
            name="ck_personal_loan_term_positive",
        ),
        CheckConstraint(
            "installment_minor IS NULL OR installment_minor > 0",
            name="ck_personal_loan_installment_positive",
        ),
        CheckConstraint(
            "due_date >= start_date",
            name="ck_personal_loan_dates",
        ),
        CheckConstraint(
            "status IN "
            "('draft', 'active', 'paid', 'overdue', 'disputed', "
            "'restructured', 'written_off', 'cancelled')",
            name="ck_personal_loan_status",
        ),
        Index(
            "ix_personal_loan_workspace_status",
            "workspace_id",
            "status",
        ),
        Index(
            "ix_personal_loan_workspace_due_date",
            "workspace_id",
            "due_date",
        ),
    )
