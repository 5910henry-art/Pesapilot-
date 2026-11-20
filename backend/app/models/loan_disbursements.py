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


class LoanDisbursement(db.Model):
    """Records the actual release of principal for a personal loan."""

    __tablename__ = "loan_disbursements"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    personal_loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    financial_account_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        db.BigInteger,
        nullable=False,
    )

    disbursement_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    payment_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    recorded_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "personal_loan_id"],
            ["personal_loans.workspace_id", "personal_loans.id"],
            name="fk_disbursement_workspace_loan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "financial_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_disbursement_workspace_account",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_disbursement_workspace_journal",
        ),
        UniqueConstraint(
            "workspace_id",
            "personal_loan_id",
            name="uq_loan_disbursement_per_loan",
        ),
        UniqueConstraint(
            "workspace_id",
            "journal_entry_id",
            name="uq_loan_disbursement_journal",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_loan_disbursement_workspace_id",
        ),
        CheckConstraint(
            "amount_minor > 0",
            name="ck_loan_disbursement_amount_positive",
        ),
        CheckConstraint(
            "payment_method IN ('cash', 'bank', 'mobile_money', 'other')",
            name="ck_loan_disbursement_payment_method",
        ),
        Index(
            "ix_loan_disbursement_workspace_date",
            "workspace_id",
            "disbursement_date",
        ),
    )
