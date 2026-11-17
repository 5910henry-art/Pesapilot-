"""Database-backed access control for PesaPilot platform administrators."""

from datetime import datetime, timezone
from uuid import UUID

from sqlalchemy import CheckConstraint, ForeignKey, String, Boolean, DateTime
from sqlalchemy.orm import Mapped, mapped_column

from ..extensions import db


class PlatformAdmin(db.Model):
    """Grants platform-level administrative access to a user."""

    __tablename__ = "platform_admins"

    user_id: Mapped[UUID] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="CASCADE"),
        primary_key=True,
    )

    role: Mapped[str] = mapped_column(
        String(30),
        nullable=False,
        default="owner",
    )

    is_active: Mapped[bool] = mapped_column(
        Boolean,
        nullable=False,
        default=True,
    )

    granted_by: Mapped[UUID | None] = mapped_column(
        db.Uuid(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=lambda: datetime.now(timezone.utc),
        onupdate=lambda: datetime.now(timezone.utc),
        nullable=False,
    )

    __table_args__ = (
        CheckConstraint(
            "role IN ('owner', 'admin')",
            name="ck_platform_admin_role",
        ),
    )
