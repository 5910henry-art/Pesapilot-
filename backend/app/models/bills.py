import uuid
from datetime import date, datetime

from sqlalchemy import (
    BigInteger,
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


class Bill(db.Model):
    __tablename__ = "bills"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    category_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )

    payee_name: Mapped[str] = mapped_column(
        String(150), nullable=False
    )

    title: Mapped[str] = mapped_column(
        String(150), nullable=False
    )

    description: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    reference: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"), nullable=False
    )

    bill_date: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    due_date: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False, unique=True
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
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
        UniqueConstraint(
            "workspace_id", "id",
            name="uq_bill_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "category_id"],
            ["expense_categories.workspace_id", "expense_categories.id"],
            name="fk_bill_workspace_category",
            ondelete="RESTRICT",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_bill_workspace_journal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount_minor > 0",
            name="ck_bill_amount_positive",
        ),
        Index("ix_bills_workspace_due_date", "workspace_id", "due_date"),
    )


class BillPayment(db.Model):
    __tablename__ = "bill_payments"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), primary_key=True, default=new_uuid
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    bill_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )

    financial_account_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger, nullable=False
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"), nullable=False
    )

    payment_date: Mapped[date] = mapped_column(
        Date, nullable=False
    )

    reference: Mapped[str | None] = mapped_column(
        String(100), nullable=True
    )

    notes: Mapped[str | None] = mapped_column(
        Text, nullable=True
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True), nullable=False, unique=True
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utc_now, nullable=False
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "bill_id"],
            ["bills.workspace_id", "bills.id"],
            name="fk_bill_payment_workspace_bill",
            ondelete="CASCADE",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "financial_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_bill_payment_workspace_account",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "journal_entry_id"],
            ["journal_entries.workspace_id", "journal_entries.id"],
            name="fk_bill_payment_workspace_journal",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "amount_minor > 0",
            name="ck_bill_payment_amount_positive",
        ),
        Index(
            "ix_bill_payments_workspace_bill",
            "workspace_id", "bill_id",
        ),
    )
