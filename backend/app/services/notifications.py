from uuid import UUID

from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.notifications import (
    NOTIFICATION_CATEGORIES,
    Notification,
    NotificationDelivery,
    NotificationPreference,
    PushSubscription,
)


def create_notification(
    user_id,
    category,
    title,
    body,
    target_url="/",
    dedupe_key=None,
):
    """
    Create a persistent notification and queue eligible push deliveries.

    Does not commit the database transaction. The calling operation owns
    the commit so notification creation can participate in that transaction.
    """
    try:
        user_id = UUID(str(user_id))
    except (ValueError, TypeError, AttributeError):
        raise ValueError("A valid notification recipient is required.")

    if category not in NOTIFICATION_CATEGORIES:
        raise ValueError("Unsupported notification category.")

    if not isinstance(title, str) or not title.strip() or len(title.strip()) > 160:
        raise ValueError("Notification title must contain 1–160 characters.")

    if not isinstance(body, str) or not body.strip() or len(body.strip()) > 500:
        raise ValueError("Notification body must contain 1–500 characters.")

    if (
        not isinstance(target_url, str)
        or not target_url.startswith("/")
        or target_url.startswith("//")
        or "\\" in target_url
        or len(target_url) > 500
    ):
        raise ValueError("Notification target must be a valid local path.")

    if dedupe_key is not None:
        if (
            not isinstance(dedupe_key, str)
            or not dedupe_key.strip()
            or len(dedupe_key.strip()) > 180
        ):
            raise ValueError("Notification deduplication key is invalid.")
        dedupe_key = dedupe_key.strip()

    preference = NotificationPreference.query.filter_by(
        user_id=user_id,
        category=category,
    ).first()

    in_app_enabled = preference.in_app_enabled if preference else True
    push_enabled = preference.push_enabled if preference else True

    if not in_app_enabled and not push_enabled:
        return None

    if dedupe_key is not None:
        existing = Notification.query.filter_by(
            user_id=user_id,
            dedupe_key=dedupe_key,
        ).first()

        if existing is not None:
            return existing

    notification = Notification(
        user_id=user_id,
        category=category,
        title=title.strip(),
        body=body.strip(),
        target_url=target_url,
        dedupe_key=dedupe_key,
    )

    # The savepoint protects the caller's outer transaction if a concurrent
    # request creates the same user/deduplication key.
    try:
        with db.session.begin_nested():
            db.session.add(notification)
            db.session.flush()
    except IntegrityError:
        if dedupe_key is None:
            raise

        existing = Notification.query.filter_by(
            user_id=user_id,
            dedupe_key=dedupe_key,
        ).first()

        if existing is not None:
            return existing
        raise

    if push_enabled:
        subscriptions = PushSubscription.query.filter_by(
            user_id=user_id,
            is_active=True,
        ).all()

        for subscription in subscriptions:
            db.session.add(
                NotificationDelivery(
                    notification_id=notification.id,
                    subscription_id=subscription.id,
                    status="pending",
                )
            )

    db.session.flush()
    return notification
