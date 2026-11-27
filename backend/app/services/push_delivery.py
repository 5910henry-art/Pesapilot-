import json
import os
from datetime import datetime, timedelta, timezone
from urllib.parse import urlsplit
from uuid import UUID

import requests
from flask import current_app
from sqlalchemy import or_
from pywebpush import WebPushException, webpush
from py_vapid import Vapid

from app.extensions import db
from app.models.notifications import (
    Notification,
    NotificationDelivery,
    PushSubscription,
)
from app.services.push_security import decrypt_subscription_secret


ALLOWED_PUSH_HOSTS = {
    "fcm.googleapis.com",
    "fcmregistrations.googleapis.com",
    "updates.push.services.mozilla.com",
}
MAX_ATTEMPTS = 5
RETRY_DELAY = timedelta(minutes=5)
DEFAULT_BATCH_SIZE = 50


class PushEndpointValidationError(ValueError):
    """Raised when a push endpoint violates the outbound URL restrictions."""


class _SafePushSession(requests.Session):
    """Restrict outbound push requests and prohibit redirects."""

    def post(self, url, **kwargs):
        parsed = urlsplit(url)

        if (
            parsed.scheme != "https"
            or parsed.hostname not in ALLOWED_PUSH_HOSTS
            or parsed.port not in (None, 443)
            or parsed.username is not None
            or parsed.password is not None
        ):
            raise PushEndpointValidationError(
                "Push endpoint is not an approved provider."
            )

        kwargs["allow_redirects"] = False
        return super().post(url, **kwargs)


def _delivery_error(status_code=None):
    if status_code is not None:
        return f"Push provider returned HTTP {status_code}."
    return "Push delivery failed due to a network or connection error."


def _retryable_status(status_code):
    return status_code in (408, 425, 429) or status_code >= 500


def deliver_pending_push_notifications(
    batch_size=DEFAULT_BATCH_SIZE,
    subscription_id=None,
):
    """
    Deliver queued notifications to active subscriptions.

    Each delivery is committed independently. Temporary failures remain
    pending for retry after RETRY_DELAY, up to MAX_ATTEMPTS.
    """
    if (
        isinstance(batch_size, bool)
        or not isinstance(batch_size, int)
        or not 1 <= batch_size <= 500
    ):
        raise ValueError("Batch size must be between 1 and 500.")

    if subscription_id is not None:
        try:
            subscription_id = (
                subscription_id
                if isinstance(subscription_id, UUID)
                else UUID(str(subscription_id))
            )
        except (ValueError, TypeError, AttributeError):
            raise ValueError("Invalid push subscription ID.") from None

    private_key = current_app.config.get("VAPID_PRIVATE_KEY")
    subject = current_app.config.get("VAPID_CLAIMS_SUBJECT")

    if not private_key or not subject:
        raise RuntimeError("Web Push VAPID configuration is incomplete.")

    try:
        if isinstance(private_key, Vapid):
            vapid_key = private_key
        elif "-----BEGIN" in private_key:
            vapid_key = Vapid.from_pem(private_key.encode("utf-8"))
        elif os.path.isfile(private_key):
            vapid_key = Vapid.from_file(private_key_file=private_key)
        else:
            vapid_key = Vapid.from_string(private_key)
    except Exception:
        raise RuntimeError(
            "Web Push VAPID private key is invalid or unreadable."
        ) from None

    delivered = 0
    failed = 0
    deferred = 0
    processed = 0
    now = datetime.now(timezone.utc)

    with _SafePushSession() as http_session:
        while processed < batch_size:
            cutoff = now - RETRY_DELAY

            query = (
                db.session.query(
                    NotificationDelivery,
                    Notification,
                    PushSubscription,
                )
                .join(
                    Notification,
                    Notification.id == NotificationDelivery.notification_id,
                )
                .join(
                    PushSubscription,
                    PushSubscription.id == NotificationDelivery.subscription_id,
                )
                .filter(
                    NotificationDelivery.status == "pending",
                    NotificationDelivery.attempt_count < MAX_ATTEMPTS,
                    PushSubscription.is_active.is_(True),
                    or_(
                        NotificationDelivery.attempted_at.is_(None),
                        NotificationDelivery.attempted_at <= cutoff,
                    ),
                )
                .order_by(
                    NotificationDelivery.created_at.asc(),
                    NotificationDelivery.id.asc(),
                )
                .with_for_update(skip_locked=True)
            )

            if subscription_id is not None:
                query = query.filter(
                    PushSubscription.id == subscription_id
                )

            row = query.first()
            if row is None:
                break

            delivery, notification, subscription = row
            processed += 1
            delivery.attempt_count += 1
            delivery.attempted_at = datetime.now(timezone.utc)

            try:
                endpoint = decrypt_subscription_secret(
                    subscription.endpoint_encrypted
                )
                p256dh = decrypt_subscription_secret(
                    subscription.p256dh_encrypted
                )
                auth = decrypt_subscription_secret(
                    subscription.auth_encrypted
                )

                payload = json.dumps({
                    "id": str(notification.id),
                    "title": notification.title,
                    "body": notification.body,
                    "url": notification.target_url,
                })

                webpush(
                    subscription_info={
                        "endpoint": endpoint,
                        "keys": {
                            "p256dh": p256dh,
                            "auth": auth,
                        },
                    },
                    data=payload,
                    vapid_private_key=vapid_key,
                    vapid_claims={"sub": subject},
                    timeout=10,
                    ttl=300,
                    requests_session=http_session,
                )

                delivery.status = "sent"
                delivery.error_message = None
                delivery.sent_at = datetime.now(timezone.utc)
                subscription.last_success_at = delivery.sent_at
                delivered += 1

            except WebPushException as exc:
                response = getattr(exc, "response", None)
                status_code = (
                    getattr(response, "status_code", None)
                    if response is not None
                    else None
                )

                delivery.error_message = _delivery_error(status_code)

                if status_code in (404, 410):
                    delivery.status = "gone"
                    subscription.is_active = False
                    failed += 1
                elif (
                    status_code is not None
                    and _retryable_status(status_code)
                    and delivery.attempt_count < MAX_ATTEMPTS
                ):
                    delivery.status = "pending"
                    deferred += 1
                else:
                    delivery.status = "failed"
                    failed += 1

            except requests.RequestException:
                delivery.error_message = _delivery_error()

                if delivery.attempt_count < MAX_ATTEMPTS:
                    delivery.status = "pending"
                    deferred += 1
                else:
                    delivery.status = "failed"
                    failed += 1

            except PushEndpointValidationError:
                delivery.status = "failed"
                delivery.error_message = "Push endpoint is not approved."
                failed += 1

            except (ValueError, TypeError):
                delivery.status = "failed"
                delivery.error_message = "Stored push subscription data is invalid."
                failed += 1

            db.session.commit()

    return {
        "processed": processed,
        "sent": delivered,
        "failed": failed,
        "deferred": deferred,
    }
