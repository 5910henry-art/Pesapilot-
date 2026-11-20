import uuid

from datetime import date, datetime, timezone

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    ForeignKeyConstraint,
    Index,
    UniqueConstraint,
    String,
    Text,
    Boolean,
    DateTime,
    BigInteger,
    Integer,
    Numeric,
    JSON,
    Date,
)

from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


def new_uuid():
    return uuid.uuid4()


def utc_now():
    return datetime.now(timezone.utc)


class User(db.Model):
    __tablename__ = "users"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    email: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
        index=True,
    )

    phone: Mapped[str | None] = mapped_column(
        String(30),
        unique=True,
        nullable=True,
    )

    password_hash: Mapped[str] = mapped_column(
        String(255),
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    email_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )

    phone_verified: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
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


class UserProfile(db.Model):
    __tablename__ = "user_profiles"

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    first_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    last_name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    timezone: Mapped[str] = mapped_column(
        String(60),
        default="Africa/Nairobi",
        nullable=False,
    )

    preferred_currency: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        default="KES",
        nullable=False,
    )


class Workspace(db.Model):
    __tablename__ = "workspaces"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    workspace_type: Mapped[str] = mapped_column(
        String(30),
        default="personal",
        nullable=False,
    )

    functional_currency: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        default="KES",
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "workspace_type IN ('personal', 'business')",
            name="ck_workspace_type",
        ),
    )


class WorkspaceMember(db.Model):
    __tablename__ = "workspace_members"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    workspace_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("workspaces.id", ondelete="CASCADE"),
        nullable=False,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )

    role: Mapped[str] = mapped_column(
        String(30),
        default="owner",
        nullable=False,
    )

    joined_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "user_id",
            name="uq_workspace_member",
        ),
        CheckConstraint(
            "role IN ('owner', 'admin', 'member', 'viewer')",
            name="ck_workspace_member_role",
        ),
    )


class Currency(db.Model):
    __tablename__ = "currencies"

    code: Mapped[str] = mapped_column(
        String(3),
        primary_key=True,
    )

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    minor_unit: Mapped[int] = mapped_column(
        Integer,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "minor_unit BETWEEN 0 AND 4",
            name="ck_currency_minor_unit",
        ),
    )


class ExchangeRate(db.Model):
    __tablename__ = "exchange_rates"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    base_currency: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    quote_currency: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    rate: Mapped[object] = mapped_column(
        Numeric(24, 12),
        nullable=False,
    )

    effective_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    source: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    __table_args__ = (
        CheckConstraint("rate > 0", name="ck_exchange_rate_positive"),
        CheckConstraint(
            "base_currency <> quote_currency",
            name="ck_exchange_rate_different_currencies",
        ),
        Index(
            "ix_exchange_rate_lookup",
            "base_currency",
            "quote_currency",
            "effective_at",
        ),
    )


class FinancialAccount(db.Model):
    __tablename__ = "financial_accounts"

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

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_financial_account_workspace_id",
        ),
        CheckConstraint(
            "account_type IN "
            "('cash', 'bank', 'mobile_money', 'savings', 'investment', 'credit')",
            name="ck_financial_account_type",
        ),
    )


class LedgerAccount(db.Model):
    __tablename__ = "ledger_accounts"

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

    financial_account_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("financial_accounts.id"),
        nullable=True,
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
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_ledger_account_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "financial_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_ledger_financial_account_workspace",
        ),
        UniqueConstraint(
            "workspace_id",
            "code",
            name="uq_ledger_account_code",
        ),
        UniqueConstraint(
            "financial_account_id",
            name="uq_ledger_financial_account",
        ),
        CheckConstraint(
            "account_class IN "
            "('asset', 'liability', 'equity', 'income', 'expense')",
            name="ck_ledger_account_class",
        ),
        CheckConstraint(
            "normal_balance IN ('debit', 'credit')",
            name="ck_ledger_normal_balance",
        ),
    )


class JournalEntry(db.Model):
    __tablename__ = "journal_entries"

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

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    posted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    reversal_of_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("journal_entries.id"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "reference",
            name="uq_journal_reference",
        ),
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_journal_workspace_id",
        ),
        UniqueConstraint(
            "reversal_of_id",
            name="uq_journal_reversal_of",
        ),
        CheckConstraint(
            "status IN ('draft', 'posted', 'reversed')",
            name="ck_journal_status",
        ),
    )


class JournalLine(db.Model):
    __tablename__ = "journal_lines"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="RESTRICT"),
        nullable=False,
        index=True,
    )

    ledger_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
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
        CheckConstraint(
            "debit_minor >= 0 AND credit_minor >= 0",
            name="ck_journal_line_nonnegative",
        ),
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0) OR "
            "(credit_minor > 0 AND debit_minor = 0)",
            name="ck_journal_line_one_side",
        ),
        CheckConstraint(
            "functional_debit_minor >= 0 AND "
            "functional_credit_minor >= 0",
            name="ck_journal_line_functional_nonnegative",
        ),
        CheckConstraint(
            "(debit_minor > 0 AND credit_minor = 0 "
            "AND functional_credit_minor = 0) OR "
            "(credit_minor > 0 AND debit_minor = 0 "
            "AND functional_debit_minor = 0)",
            name="ck_journal_line_functional_direction",
        ),
        CheckConstraint(
            "exchange_rate > 0",
            name="ck_journal_line_exchange_rate",
        ),
    )


class AuditLog(db.Model):
    __tablename__ = "audit_logs"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    workspace_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("workspaces.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )

    user_id: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    action: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    resource_type: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    resource_id: Mapped[str | None] = mapped_column(
        String(100),
        nullable=True,
    )

    details: Mapped[dict | None] = mapped_column(
        JSON,
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class RefreshToken(db.Model):
    __tablename__ = "refresh_tokens"

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )

    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )

    token_hash: Mapped[str] = mapped_column(
        String(255),
        unique=True,
        nullable=False,
    )

    session_id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        nullable=False,
        index=True,
    )

    user_agent: Mapped[str | None] = mapped_column(
        String(512),
        nullable=True,
    )

    ip_address: Mapped[str | None] = mapped_column(
        String(45),
        nullable=True,
    )

    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        nullable=False,
    )

    revoked_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class ExpenseCategory(db.Model):
    __tablename__ = "expense_categories"

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

    name: Mapped[str] = mapped_column(
        String(100),
        nullable=False,
    )

    ledger_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("ledger_accounts.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        UniqueConstraint(
            "workspace_id",
            "id",
            name="uq_expense_category_workspace_id",
        ),
        ForeignKeyConstraint(
            ["workspace_id", "ledger_account_id"],
            ["ledger_accounts.workspace_id", "ledger_accounts.id"],
            name="fk_expense_category_ledger_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_expense_category_workspace_name",
        ),
    )


class Budget(db.Model):
    __tablename__ = "budgets"

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

    category_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("expense_categories.id", ondelete="RESTRICT"),
        nullable=False,
    )

    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    period_start: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    period_end: Mapped[date] = mapped_column(
        Date,
        nullable=False,
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "category_id"],
            ["expense_categories.workspace_id", "expense_categories.id"],
            name="fk_budget_category_workspace",
            ondelete="RESTRICT",
        ),
        UniqueConstraint(
            "category_id",
            "period_start",
            "period_end",
            name="uq_budget_category_period",
        ),
        CheckConstraint(
            "amount_minor > 0",
            name="ck_budget_amount_positive",
        ),
        CheckConstraint(
            "period_end >= period_start",
            name="ck_budget_period_valid",
        ),
    )


class SavingsGoal(db.Model):
    __tablename__ = "savings_goals"

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

    name: Mapped[str] = mapped_column(
        String(150),
        nullable=False,
    )

    description: Mapped[str | None] = mapped_column(
        Text,
        nullable=True,
    )

    target_amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )

    currency_code: Mapped[str] = mapped_column(
        ForeignKey("currencies.code"),
        nullable=False,
    )

    target_date: Mapped[date | None] = mapped_column(
        Date,
        nullable=True,
    )

    financial_account_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("financial_accounts.id", ondelete="RESTRICT"),
        nullable=False,
    )

    status: Mapped[str] = mapped_column(
        String(20),
        default="active",
        nullable=False,
    )

    created_by: Mapped[uuid.UUID | None] = mapped_column(
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        ForeignKeyConstraint(
            ["workspace_id", "financial_account_id"],
            ["financial_accounts.workspace_id", "financial_accounts.id"],
            name="fk_savings_goal_account_workspace",
            ondelete="RESTRICT",
        ),
        CheckConstraint(
            "target_amount_minor > 0",
            name="ck_savings_goal_target_positive",
        ),
        CheckConstraint(
            "status IN ('active', 'paused', 'archived')",
            name="ck_savings_goal_status",
        ),
        UniqueConstraint(
            "workspace_id",
            "name",
            name="uq_savings_goal_workspace_name",
        ),
    )


class SavingsGoalMovement(db.Model):
    """Tracks the amount allocated to an individual savings goal."""

    __tablename__ = "savings_goal_movements"

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
    goal_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("savings_goals.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    journal_entry_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("journal_entries.id", ondelete="RESTRICT"),
        nullable=False,
        unique=True,
    )
    amount_minor: Mapped[int] = mapped_column(
        BigInteger,
        nullable=False,
    )
    operation: Mapped[str] = mapped_column(
        String(20),
        nullable=False,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "amount_minor > 0",
            name="ck_savings_goal_movement_amount_positive",
        ),
        CheckConstraint(
            "operation IN ('contribution', 'withdrawal')",
            name="ck_savings_goal_movement_operation",
        ),
    )
