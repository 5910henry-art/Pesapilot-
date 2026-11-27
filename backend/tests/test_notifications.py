import unittest
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
from unittest.mock import patch
from uuid import uuid4

from flask_jwt_extended import create_access_token
from pywebpush import WebPushException

from app.extensions import db
from app.models import User
from app.models.notifications import (
    NOTIFICATION_CATEGORIES,
    Notification,
    NotificationPreference,
    NotificationDelivery,
    PushSubscription,
)
from app.services.notifications import create_notification
from app.services.push_delivery import (
    _SafePushSession,
    deliver_pending_push_notifications,
)
from app.services.push_security import decrypt_subscription_secret


class NotificationsTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        from tests.test_app import PesaPilotTestCase
        PesaPilotTestCase.setUpClass.__func__(cls)

    @classmethod
    def tearDownClass(cls):
        from tests.test_app import PesaPilotTestCase
        PesaPilotTestCase.tearDownClass.__func__(cls)

    def setUp(self):
        from tests.test_app import PesaPilotTestCase
        PesaPilotTestCase.setUp(self)

    def tearDown(self):
        from tests.test_app import PesaPilotTestCase
        PesaPilotTestCase.tearDown(self)

    def _headers_for(self, user):
        token = create_access_token(identity=str(user.id))
        return {"Authorization": f"Bearer {token}"}

    def _notification(self, user, title="Test notification", dedupe=None):
        item = Notification(
            user_id=user.id,
            category="account_security",
            title=title,
            body="A test notification.",
            target_url="/settings",
            dedupe_key=dedupe,
        )
        db.session.add(item)
        db.session.commit()
        return item

    def _second_user(self):
        user = User(
            email=f"{uuid4().hex}@example.invalid",
            password_hash="test-password-hash",
            is_active=True,
        )
        db.session.add(user)
        db.session.commit()
        return user

    def _subscription_payload(self, endpoint):
        return {
            "endpoint": endpoint,
            "keys": {
                "p256dh": "A" * 65,
                "auth": "B" * 24,
            },
        }

    def test_notification_routes_require_authentication(self):
        response = self.client.get("/api/v1/notifications")
        self.assertEqual(response.status_code, 401)

        response = self.client.get("/api/v1/notifications/preferences")
        self.assertEqual(response.status_code, 401)

    def test_notification_list_and_unread_count_are_user_scoped(self):
        own = self._notification(self.user, "My notification")
        other_user = self._second_user()
        self._notification(other_user, "Another user's notification")

        response = self.client.get(
            "/api/v1/notifications",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["unread_count"], 1)
        self.assertEqual(len(response.json["notifications"]), 1)
        self.assertEqual(
            response.json["notifications"][0]["id"],
            str(own.id),
        )

        response = self.client.get(
            "/api/v1/notifications/unread-count",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["unread_count"], 1)

    def test_in_app_disabled_notifications_are_hidden_and_not_marked_read(self):
        visible = self._notification(self.user, "Visible notification")

        preference = NotificationPreference(
            user_id=self.user.id,
            category="bill_reminders",
            in_app_enabled=False,
            push_enabled=True,
        )
        hidden = Notification(
            user_id=self.user.id,
            category="bill_reminders",
            title="Push-only notification",
            body="This should not appear in the in-app centre.",
            target_url="/bills",
        )
        db.session.add_all([preference, hidden])
        db.session.commit()

        response = self.client.get(
            "/api/v1/notifications",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["unread_count"], 1)
        self.assertEqual(len(response.json["notifications"]), 1)
        self.assertEqual(
            response.json["notifications"][0]["id"],
            str(visible.id),
        )

        response = self.client.get(
            "/api/v1/notifications/unread-count",
            headers=self.headers,
        )
        self.assertEqual(response.json["unread_count"], 1)

        response = self.client.patch(
            f"/api/v1/notifications/{hidden.id}/read",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 404)

        response = self.client.post(
            "/api/v1/notifications/read-all",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["updated"], 1)

        db.session.refresh(visible)
        db.session.refresh(hidden)
        self.assertTrue(visible.is_read)
        self.assertFalse(hidden.is_read)


    def test_user_cannot_mark_another_users_notification_as_read(self):
        other_user = self._second_user()
        item = self._notification(other_user)

        response = self.client.patch(
            f"/api/v1/notifications/{item.id}/read",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 404)
        db.session.refresh(item)
        self.assertFalse(item.is_read)

    def test_mark_read_and_read_all_only_update_current_users_records(self):
        own = self._notification(self.user, "Own notification")
        other_user = self._second_user()
        other = self._notification(other_user, "Other notification")

        response = self.client.patch(
            f"/api/v1/notifications/{own.id}/read",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertTrue(response.json["notification"]["is_read"])

        unread = self._notification(self.user, "Second own notification")

        response = self.client.post(
            "/api/v1/notifications/read-all",
            headers=self.headers,
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json["updated"], 1)

        db.session.refresh(own)
        db.session.refresh(unread)
        db.session.refresh(other)
        self.assertTrue(own.is_read)
        self.assertTrue(unread.is_read)
        self.assertFalse(other.is_read)

    def test_preferences_default_to_enabled_for_all_categories(self):
        response = self.client.get(
            "/api/v1/notifications/preferences",
            headers=self.headers,
        )

        self.assertEqual(response.status_code, 200)
        preferences = response.json["preferences"]
        self.assertEqual(
            {item["category"] for item in preferences},
            set(NOTIFICATION_CATEGORIES),
        )
        self.assertTrue(all(item["in_app_enabled"] for item in preferences))
        self.assertTrue(all(item["push_enabled"] for item in preferences))

    def test_preferences_can_be_updated_and_invalid_values_are_rejected(self):
        response = self.client.patch(
            "/api/v1/notifications/preferences",
            headers=self.headers,
            json={
                "preferences": [{
                    "category": "bill_reminders",
                    "in_app_enabled": False,
                    "push_enabled": True,
                }],
            },
        )

        self.assertEqual(response.status_code, 200)
        saved = NotificationPreference.query.filter_by(
            user_id=self.user.id,
            category="bill_reminders",
        ).one()
        self.assertFalse(saved.in_app_enabled)
        self.assertTrue(saved.push_enabled)

        response = self.client.patch(
            "/api/v1/notifications/preferences",
            headers=self.headers,
            json={
                "preferences": [{
                    "category": "bill_reminders",
                    "push_enabled": "yes",
                }],
            },
        )
        self.assertEqual(response.status_code, 400)

    def test_duplicate_preference_categories_do_not_create_duplicate_rows(self):
        response = self.client.patch(
            "/api/v1/notifications/preferences",
            headers=self.headers,
            json={
                "preferences": [
                    {
                        "category": "bill_reminders",
                        "in_app_enabled": False,
                    },
                    {
                        "category": "bill_reminders",
                        "push_enabled": False,
                    },
                ],
            },
        )

        self.assertEqual(response.status_code, 200, response.json)

        saved = NotificationPreference.query.filter_by(
            user_id=self.user.id,
            category="bill_reminders",
        ).all()

        self.assertEqual(len(saved), 1)
        self.assertFalse(saved[0].in_app_enabled)
        self.assertFalse(saved[0].push_enabled)


    def test_push_subscription_rejects_non_https_endpoint(self):
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json=self._subscription_payload(
                "http://example.com/push/test"
            ),
        )

        self.assertEqual(response.status_code, 400)
        self.assertEqual(PushSubscription.query.count(), 0)

    def test_push_subscription_secrets_are_encrypted_at_rest(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/test-token"

        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json=self._subscription_payload(endpoint),
        )

        self.assertEqual(response.status_code, 201, response.json)
        subscription = PushSubscription.query.one()

        self.assertNotEqual(subscription.endpoint_encrypted, endpoint)
        self.assertNotEqual(subscription.p256dh_encrypted, "A" * 65)
        self.assertNotEqual(subscription.auth_encrypted, "B" * 24)

        self.assertEqual(
            decrypt_subscription_secret(subscription.endpoint_encrypted),
            endpoint,
        )
        self.assertEqual(
            decrypt_subscription_secret(subscription.p256dh_encrypted),
            "A" * 65,
        )
        self.assertEqual(
            decrypt_subscription_secret(subscription.auth_encrypted),
            "B" * 24,
        )

    def test_push_subscription_cannot_be_claimed_by_another_user(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/shared-token"

        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json=self._subscription_payload(endpoint),
        )
        self.assertEqual(response.status_code, 201)

        other_user = self._second_user()
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            headers=self._headers_for(other_user),
            json=self._subscription_payload(endpoint),
        )

        self.assertEqual(response.status_code, 409)
        self.assertEqual(PushSubscription.query.count(), 1)

    def test_unsubscribe_deactivates_only_current_users_subscription(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/owned-token"

        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json=self._subscription_payload(endpoint),
        )
        self.assertEqual(response.status_code, 201)

        subscription = PushSubscription.query.one()
        other_user = self._second_user()

        response = self.client.delete(
            "/api/v1/notifications/push/subscribe",
            headers=self._headers_for(other_user),
            json={"endpoint": endpoint},
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(subscription)
        self.assertTrue(subscription.is_active)

        response = self.client.delete(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json={"endpoint": endpoint},
        )
        self.assertEqual(response.status_code, 200)
        db.session.refresh(subscription)
        self.assertFalse(subscription.is_active)

    def test_unsubscribe_rejects_malformed_endpoint(self):
        response = self.client.delete(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json={"endpoint": "not-a-valid-url"},
        )

        self.assertEqual(response.status_code, 400)


    def test_notification_service_creates_persistent_notification(self):
        item = create_notification(
            user_id=self.user.id,
            category="bill_reminders",
            title="Bill reminder",
            body="A bill is approaching its due date.",
            target_url="/bills",
            dedupe_key="bill:123:reminder",
        )
        db.session.commit()

        saved = Notification.query.filter_by(
            user_id=self.user.id,
            dedupe_key="bill:123:reminder",
        ).one()

        self.assertEqual(item.id, saved.id)
        self.assertEqual(saved.category, "bill_reminders")
        self.assertEqual(saved.target_url, "/bills")
        self.assertFalse(saved.is_read)

    def test_notification_service_deduplicates_notifications(self):
        first = create_notification(
            self.user.id,
            "account_security",
            "New sign-in",
            "A new sign-in was detected.",
            dedupe_key="security:login:123",
        )
        second = create_notification(
            self.user.id,
            "account_security",
            "Different title",
            "Different body.",
            dedupe_key="security:login:123",
        )
        db.session.commit()

        self.assertEqual(first.id, second.id)
        self.assertEqual(
            Notification.query.filter_by(
                user_id=self.user.id,
                dedupe_key="security:login:123",
            ).count(),
            1,
        )

    def test_notification_service_queues_active_push_subscriptions(self):
        endpoint = "https://fcm.googleapis.com/fcm/send/service-test"

        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json=self._subscription_payload(endpoint),
        )
        self.assertEqual(response.status_code, 201, response.json)

        item = create_notification(
            self.user.id,
            "bill_reminders",
            "Bill reminder",
            "A bill is approaching its due date.",
            dedupe_key="bill:456:reminder",
        )
        db.session.commit()

        deliveries = NotificationDelivery.query.filter_by(
            notification_id=item.id,
        ).all()

        self.assertEqual(len(deliveries), 1)
        self.assertEqual(deliveries[0].status, "pending")
        self.assertEqual(deliveries[0].attempt_count, 0)

    def _queued_push_delivery(self):
        from py_vapid import Vapid

        test_vapid = Vapid()
        test_vapid.generate_keys()
        self.app.config["VAPID_PRIVATE_KEY"] = test_vapid
        self.app.config["VAPID_CLAIMS_SUBJECT"] = "mailto:test@example.invalid"

        endpoint = f"https://fcm.googleapis.com/fcm/send/{uuid4().hex}"
        response = self.client.post(
            "/api/v1/notifications/push/subscribe",
            headers=self.headers,
            json=self._subscription_payload(endpoint),
        )
        self.assertEqual(response.status_code, 201, response.json)

        item = create_notification(
            self.user.id,
            "bill_reminders",
            "Bill reminder",
            "A bill is approaching its due date.",
            target_url="/settings",
        )
        db.session.commit()

        delivery = NotificationDelivery.query.filter_by(
            notification_id=item.id,
        ).one()
        subscription = PushSubscription.query.filter_by(
            user_id=self.user.id,
        ).one()
        return delivery, subscription

    @patch("app.services.push_delivery.webpush")
    def test_push_delivery_success_marks_delivery_sent(self, send):
        delivery, subscription = self._queued_push_delivery()
        send.return_value = SimpleNamespace(status_code=201)

        result = deliver_pending_push_notifications()

        db.session.refresh(delivery)
        db.session.refresh(subscription)

        self.assertEqual(result["sent"], 1)
        self.assertEqual(delivery.status, "sent")
        self.assertEqual(delivery.attempt_count, 1)
        self.assertIsNotNone(delivery.sent_at)
        self.assertIsNotNone(subscription.last_success_at)
        self.assertEqual(send.call_args.kwargs["ttl"], 300)
        self.assertIn('"url": "/settings"', send.call_args.kwargs["data"])

    @patch("app.services.push_delivery.webpush")
    def test_expired_push_subscription_is_deactivated(self, send):
        delivery, subscription = self._queued_push_delivery()
        send.side_effect = WebPushException(
            "Push expired",
            response=SimpleNamespace(status_code=410),
        )

        result = deliver_pending_push_notifications()

        db.session.refresh(delivery)
        db.session.refresh(subscription)

        self.assertEqual(result["failed"], 1)
        self.assertEqual(delivery.status, "gone")
        self.assertEqual(delivery.attempt_count, 1)
        self.assertFalse(subscription.is_active)
        self.assertNotIn("fcm.googleapis.com", delivery.error_message)

    @patch("app.services.push_delivery.webpush")
    def test_retryable_push_failure_is_retried_after_cooldown(self, send):
        delivery, _ = self._queued_push_delivery()
        send.side_effect = WebPushException(
            "Temporary provider error",
            response=SimpleNamespace(status_code=503),
        )

        first_result = deliver_pending_push_notifications()

        db.session.refresh(delivery)
        self.assertEqual(first_result["deferred"], 1)
        self.assertEqual(delivery.status, "pending")
        self.assertEqual(delivery.attempt_count, 1)

        delivery.attempted_at = datetime.now(timezone.utc) - timedelta(minutes=6)
        db.session.commit()
        send.side_effect = None
        send.return_value = SimpleNamespace(status_code=201)

        second_result = deliver_pending_push_notifications()

        db.session.refresh(delivery)
        self.assertEqual(second_result["sent"], 1)
        self.assertEqual(delivery.status, "sent")
        self.assertEqual(delivery.attempt_count, 2)

    @patch("app.services.push_delivery.webpush")
    def test_permanent_push_failure_is_not_retried(self, send):
        delivery, _ = self._queued_push_delivery()
        send.side_effect = WebPushException(
            "Invalid subscription",
            response=SimpleNamespace(status_code=400),
        )

        result = deliver_pending_push_notifications()

        db.session.refresh(delivery)
        self.assertEqual(result["failed"], 1)
        self.assertEqual(delivery.status, "failed")
        self.assertEqual(delivery.attempt_count, 1)

    def test_push_delivery_rejects_invalid_subscription_id(self):
        with self.assertRaisesRegex(ValueError, "Invalid push subscription ID"):
            deliver_pending_push_notifications(
                batch_size=1,
                subscription_id="not-a-uuid",
            )

    @patch("requests.Session.post")
    def test_push_session_accepts_firefox_autopush_host(self, send):
        send.return_value = SimpleNamespace(status_code=201)

        with _SafePushSession() as session:
            response = session.post(
                "https://updates.push.services.mozilla.com/wpush/v2/test"
            )

        self.assertEqual(response.status_code, 201)
        send.assert_called_once_with(
            "https://updates.push.services.mozilla.com/wpush/v2/test",
            allow_redirects=False,
        )

    def test_push_session_rejects_unapproved_hosts(self):
        with _SafePushSession() as session:
            with self.assertRaises(ValueError):
                session.post("https://attacker.example/collect")

    def test_push_session_rejects_non_https_and_redirect_targets(self):
        with _SafePushSession() as session:
            with self.assertRaises(ValueError):
                session.post("http://fcm.googleapis.com/fcm/send/test")

            with self.assertRaises(ValueError):
                session.post("https://fcm.googleapis.com:8443/fcm/send/test")

    def test_notification_service_respects_notification_preferences(self):
        preference = NotificationPreference(
            user_id=self.user.id,
            category="bill_reminders",
            in_app_enabled=False,
            push_enabled=False,
        )
        db.session.add(preference)
        db.session.commit()

        item = create_notification(
            self.user.id,
            "bill_reminders",
            "Bill reminder",
            "A bill is approaching its due date.",
            dedupe_key="bill:789:reminder",
        )

        self.assertIsNone(item)
        self.assertEqual(
            Notification.query.filter_by(
                user_id=self.user.id,
                dedupe_key="bill:789:reminder",
            ).count(),
            0,
        )


if __name__ == "__main__":
    unittest.main()
