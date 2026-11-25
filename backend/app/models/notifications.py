import uuid
from datetime import datetime

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    DateTime,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db
from .core import new_uuid, utc_now


NOTIFICATION_CATEGORIES = (
    "loan_repayments",
    "loan_due_reminders",
    "loan_application_updates",
    "bill_reminders",
    "budget_alerts",
    "savings_milestones",
    "account_security",
)


class Notification(db.Model):
    __tablename__ = "notifications"
    __table_args__ = (
        CheckConstraint(
            "category IN ("
            "'loan_repayments', 'loan_due_reminders', "
            "'loan_application_updates', 'bill_reminders', "
            "'budget_alerts', 'savings_milestones', "
            "'account_security'"
            ")",
            name="ck_notifications_category",
        ),
        UniqueConstraint(
            "user_id",
            "dedupe_key",
            name="uq_notifications_user_dedupe",
        ),
        Index(
            "ix_notifications_user_created",
            "user_id",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    title: Mapped[str] = mapped_column(
        String(160),
        nullable=False,
    )
    body: Mapped[str] = mapped_column(
        String(500),
        nullable=False,
    )
    target_url: Mapped[str] = mapped_column(
        String(500),
        default="/",
        nullable=False,
    )
    dedupe_key: Mapped[str | None] = mapped_column(
        String(180),
        nullable=True,
    )
    is_read: Mapped[bool] = mapped_column(
        Boolean,
        default=False,
        nullable=False,
    )
    read_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )


class NotificationPreference(db.Model):
    __tablename__ = "notification_preferences"
    __table_args__ = (
        CheckConstraint(
            "category IN ("
            "'loan_repayments', 'loan_due_reminders', "
            "'loan_application_updates', 'bill_reminders', "
            "'budget_alerts', 'savings_milestones', "
            "'account_security'"
            ")",
            name="ck_notification_preferences_category",
        ),
        UniqueConstraint(
            "user_id",
            "category",
            name="uq_notification_preferences_user_category",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    category: Mapped[str] = mapped_column(
        String(40),
        nullable=False,
    )
    in_app_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    push_enabled: Mapped[bool] = mapped_column(
        Boolean,
        default=True,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )


class PushSubscription(db.Model):
    __tablename__ = "push_subscriptions"
    __table_args__ = (
        Index("ix_push_subscriptions_user_active", "user_id", "is_active"),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )
    user_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("users.id", ondelete="CASCADE"),
        nullable=False,
    )
    endpoint_hash: Mapped[str] = mapped_column(
        String(64),
        unique=True,
        nullable=False,
    )
    endpoint_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    p256dh_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    auth_encrypted: Mapped[str] = mapped_column(
        Text,
        nullable=False,
    )
    expiration_time: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    user_agent: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
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
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        onupdate=utc_now,
        nullable=False,
    )
    last_success_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )


class NotificationDelivery(db.Model):
    __tablename__ = "notification_deliveries"
    __table_args__ = (
        CheckConstraint(
            "status IN ('pending', 'sent', 'failed', 'gone')",
            name="ck_notification_deliveries_status",
        ),
        UniqueConstraint(
            "notification_id",
            "subscription_id",
            name="uq_notification_delivery_subscription",
        ),
        Index(
            "ix_notification_deliveries_status",
            "status",
            "created_at",
        ),
    )

    id: Mapped[uuid.UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        primary_key=True,
        default=new_uuid,
    )
    notification_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("notifications.id", ondelete="CASCADE"),
        nullable=False,
    )
    subscription_id: Mapped[uuid.UUID] = mapped_column(
        ForeignKey("push_subscriptions.id", ondelete="CASCADE"),
        nullable=False,
    )
    status: Mapped[str] = mapped_column(
        String(20),
        default="pending",
        nullable=False,
    )
    attempt_count: Mapped[int] = mapped_column(
        Integer,
        default=0,
        nullable=False,
    )
    error_message: Mapped[str | None] = mapped_column(
        String(500),
        nullable=True,
    )
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=utc_now,
        nullable=False,
    )
    attempted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
    sent_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True),
        nullable=True,
    )
