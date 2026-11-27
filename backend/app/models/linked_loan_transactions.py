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
)

from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .core import new_uuid, utc_now


class LinkedLoanTransaction(db.Model):
    """A disbursement or repayment requiring confirmation by the other party."""

    __tablename__ = "linked_loan_transactions"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    loan_workspace_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    personal_loan_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    loan_payment_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    transaction_type: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        db.BigInteger,
        nullable=False,
    )

    transaction_date: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    payment_method: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )

    reference: Mapped[str | None] = mapped_column(
        String(120),
        nullable=True,
    )

    notes: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
    )

    initiating_workspace_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    initiating_account_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
    )

    initiating_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    reversal_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    reversed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    reversed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    initiated_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    confirming_workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    confirming_account_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    confirming_journal_entry_id: Mapped[uuid.UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=True,
    )

    confirmed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    confirmed_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    disputed_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    dispute_reason: Mapped[str | None] = mapped_column(
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
            ["loan_workspace_id", "personal_loan_id"],
            ["personal_loans.workspace_id", "personal_loans.id"],
            name="fk_linked_transaction_loan",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["loan_workspace_id", "personal_loan_id", "loan_payment_id"],
            [
                "loan_payments.workspace_id",
                "loan_payments.personal_loan_id",
                "loan_payments.id",
            ],
            name="fk_linked_transaction_payment",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["initiating_workspace_id", "initiating_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_linked_transaction_initiating_account",
        ),
        ForeignKeyConstraint(
            ["initiating_workspace_id", "initiating_journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_linked_transaction_initiating_journal",
        ),
        ForeignKeyConstraint(
            ["initiating_workspace_id", "reversal_journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_linked_transaction_reversal_journal",
        ),
        ForeignKeyConstraint(
            ["confirming_workspace_id", "confirming_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_linked_transaction_confirming_account",
        ),
        ForeignKeyConstraint(
            ["confirming_workspace_id", "confirming_journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_linked_transaction_confirming_journal",
        ),
        CheckConstraint(
            "transaction_type IN ('disbursement', 'repayment')",
            name="ck_linked_transaction_type",
        ),
        CheckConstraint(
            "status IN ('pending', 'confirmed', 'disputed')",
            name="ck_linked_transaction_status",
        ),
        CheckConstraint(
            "amount_minor > 0",
            name="ck_linked_transaction_amount_positive",
        ),
        CheckConstraint(
            "payment_method IN ('cash', 'bank', 'mobile_money', 'other')",
            name="ck_linked_transaction_payment_method",
        ),
        CheckConstraint(
            "("
            "status = 'confirmed' "
            "AND confirming_workspace_id IS NOT NULL "
            "AND confirming_account_id IS NOT NULL "
            "AND confirming_journal_entry_id IS NOT NULL "
            "AND confirmed_at IS NOT NULL "
            "AND initiating_journal_entry_id IS NOT NULL "
            ") OR ("
            "status <> 'confirmed' "
            "AND confirming_workspace_id IS NULL "
            "AND confirming_account_id IS NULL "
            "AND confirming_journal_entry_id IS NULL "
            "AND confirmed_by IS NULL "
            "AND confirmed_at IS NULL"
            ")",
            name="ck_linked_transaction_confirmation_fields",
        ),
        CheckConstraint(
            "("
            "reversal_journal_entry_id IS NULL "
            "AND reversed_by IS NULL "
            "AND reversed_at IS NULL"
            ") OR ("
            "reversal_journal_entry_id IS NOT NULL "
            "AND reversed_at IS NOT NULL "
            "AND transaction_type = 'disbursement' "
            "AND status = 'disputed' "
            "AND initiating_journal_entry_id IS NOT NULL"
            ")",
            name="ck_linked_transaction_reversal_fields",
        ),
        CheckConstraint(
            "("
            "status = 'disputed' "
            "AND dispute_reason IS NOT NULL"
            ") OR ("
            "status <> 'disputed' AND disputed_by IS NULL "
            "AND dispute_reason IS NULL"
            ")",
            name="ck_linked_transaction_dispute_fields",
        ),
        Index(
            "uq_linked_transaction_payment",
            "loan_workspace_id",
            "personal_loan_id",
            "loan_payment_id",
            unique=True,
            postgresql_where=db.text(
                "transaction_type = 'repayment' "
                "AND loan_payment_id IS NOT NULL"
            ),
            sqlite_where=db.text(
                "transaction_type = 'repayment' "
                "AND loan_payment_id IS NOT NULL"
            ),
        ),
        Index(
            "ix_linked_transaction_loan_date",
            "loan_workspace_id",
            "personal_loan_id",
            "transaction_date",
        ),
        Index(
            "ix_linked_transaction_initiator",
            "initiating_workspace_id",
            "initiated_by",
        ),
        Index(
            "ix_linked_transaction_status",
            "status",
            "created_at",
        ),
        Index(
            "uq_linked_loan_active_disbursement",
            "loan_workspace_id",
            "personal_loan_id",
            unique=True,
            postgresql_where=db.text(
                "transaction_type = 'disbursement' "
                "AND (status <> 'disputed' OR reversal_journal_entry_id IS NULL)"
            ),
            sqlite_where=db.text(
                "transaction_type = 'disbursement' "
                "AND (status <> 'disputed' OR reversal_journal_entry_id IS NULL)"
            ),
        ),
    )
