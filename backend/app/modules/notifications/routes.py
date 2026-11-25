from datetime import datetime, timezone
from ipaddress import ip_address
from urllib.parse import urlsplit
from uuid import UUID

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models.notifications import (
    NOTIFICATION_CATEGORIES,
    Notification,
    NotificationPreference,
    PushSubscription,
)
from app.services.push_security import (
    encrypt_subscription_secret,
    hash_push_endpoint,
)
from . import notifications_bp


def _user_id():
    try:
        return UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return None


def _error(message, status):
    return jsonify({"success": False, "message": message}), status


def _valid_push_endpoint(endpoint):
    if not isinstance(endpoint, str) or not endpoint or len(endpoint) > 4000:
        return False

    try:
        parsed = urlsplit(endpoint)
        hostname = parsed.hostname
        port = parsed.port

        if (
            parsed.scheme != "https"
            or not parsed.netloc
            or not hostname
            or parsed.username is not None
            or parsed.password is not None
            or port not in (None, 443)
            or hostname == "localhost"
            or hostname.endswith((".localhost", ".local"))
            or "." not in hostname
        ):
            return False

        try:
            ip_address(hostname)
            return False
        except ValueError:
            return True

    except ValueError:
        return False


def _notification_response(item):
    return {
        "id": str(item.id),
        "category": item.category,
        "title": item.title,
        "body": item.body,
        "target_url": item.target_url,
        "is_read": item.is_read,
        "created_at": item.created_at.isoformat(),
        "read_at": item.read_at.isoformat() if item.read_at else None,
    }


def _preference_response(category, preference):
    return {
        "category": category,
        "in_app_enabled": (
            preference.in_app_enabled if preference else True
        ),
        "push_enabled": (
            preference.push_enabled if preference else True
        ),
    }


def _in_app_notifications(user_id):
    return (
        Notification.query
        .outerjoin(
            NotificationPreference,
            (NotificationPreference.user_id == Notification.user_id)
            & (NotificationPreference.category == Notification.category),
        )
        .filter(Notification.user_id == user_id)
        .filter(
            or_(
                NotificationPreference.id.is_(None),
                NotificationPreference.in_app_enabled.is_(True),
            )
        )
    )


@notifications_bp.get("")
@jwt_required()
def list_notifications():
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    try:
        limit = min(max(int(request.args.get("limit", 30)), 1), 100)
        offset = max(int(request.args.get("offset", 0)), 0)
    except (ValueError, TypeError):
        return _error("Invalid pagination values.", 400)

    query = _in_app_notifications(user_id)
    items = (
        query.order_by(Notification.created_at.desc())
        .offset(offset)
        .limit(limit)
        .all()
    )
    unread_count = query.filter(Notification.is_read.is_(False)).count()

    return jsonify({
        "success": True,
        "notifications": [_notification_response(item) for item in items],
        "unread_count": unread_count,
        "limit": limit,
        "offset": offset,
    }), 200



@notifications_bp.get("/unread-count")
@jwt_required()
def unread_count():
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    count = _in_app_notifications(user_id).filter(
        Notification.is_read.is_(False),
    ).count()

    return jsonify({"success": True, "unread_count": count}), 200



@notifications_bp.patch("/<uuid:notification_id>/read")
@jwt_required()
def mark_notification_read(notification_id):
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    item = _in_app_notifications(user_id).filter(
        Notification.id == notification_id,
    ).first()

    if item is None:
        return _error("Notification not found.", 404)

    if not item.is_read:
        item.is_read = True
        item.read_at = datetime.now(timezone.utc)
        db.session.commit()

    return jsonify({
        "success": True,
        "notification": _notification_response(item),
    }), 200



@notifications_bp.post("/read-all")
@jwt_required()
def mark_all_notifications_read():
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    visible_ids = _in_app_notifications(user_id).with_entities(
        Notification.id
    )

    updated = (
        Notification.query
        .filter(
            Notification.id.in_(visible_ids),
            Notification.is_read.is_(False),
        )
        .update(
            {"is_read": True, "read_at": datetime.now(timezone.utc)},
            synchronize_session=False,
        )
    )
    db.session.commit()

    return jsonify({"success": True, "updated": updated}), 200



@notifications_bp.get("/preferences")
@jwt_required()
def get_notification_preferences():
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    saved = {
        item.category: item
        for item in NotificationPreference.query.filter_by(
            user_id=user_id
        ).all()
    }

    return jsonify({
        "success": True,
        "preferences": [
            _preference_response(category, saved.get(category))
            for category in NOTIFICATION_CATEGORIES
        ],
    }), 200


@notifications_bp.patch("/preferences")
@jwt_required()
def update_notification_preferences():
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    data = request.get_json(silent=True)
    if not isinstance(data, dict) or not isinstance(
        data.get("preferences"), list
    ):
        return _error("A preferences array is required.", 400)

    try:
        for item in data["preferences"]:
            if not isinstance(item, dict):
                raise ValueError("Each preference must be an object.")

            category = item.get("category")
            if category not in NOTIFICATION_CATEGORIES:
                raise ValueError("An unsupported notification category was supplied.")

            values = {}
            for field in ("in_app_enabled", "push_enabled"):
                if field in item:
                    if not isinstance(item[field], bool):
                        raise ValueError(f"{field} must be a boolean.")
                    values[field] = item[field]

            preference = NotificationPreference.query.filter_by(
                user_id=user_id,
                category=category,
            ).first()

            if preference is None:
                preference = NotificationPreference(
                    user_id=user_id,
                    category=category,
                )
                db.session.add(preference)

            for field, value in values.items():
                setattr(preference, field, value)

        db.session.commit()

    except ValueError as exc:
        db.session.rollback()
        return _error(str(exc), 400)
    except IntegrityError:
        db.session.rollback()
        return _error("Preferences could not be saved.", 409)

    return get_notification_preferences()


@notifications_bp.get("/push/public-key")
@jwt_required()
def get_push_public_key():
    public_key = current_app.config.get("VAPID_PUBLIC_KEY")
    if not public_key:
        return _error("Push notifications are not configured.", 503)

    return jsonify({"success": True, "public_key": public_key}), 200


@notifications_bp.post("/push/subscribe")
@jwt_required()
def subscribe_push():
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return _error("A valid subscription object is required.", 400)

    endpoint = data.get("endpoint")
    keys = data.get("keys")

    if (
        not isinstance(endpoint, str)
        or len(endpoint) > 4000
        or not isinstance(keys, dict)
    ):
        return _error("The push subscription is invalid.", 400)

    if not _valid_push_endpoint(endpoint):
        return _error("The push endpoint must be a valid public HTTPS URL.", 400)

    p256dh = keys.get("p256dh")
    auth = keys.get("auth")
    if (
        not isinstance(p256dh, str) or not 20 <= len(p256dh) <= 200
        or not isinstance(auth, str) or not 10 <= len(auth) <= 100
    ):
        return _error("The push encryption keys are invalid.", 400)

    expiration = data.get("expirationTime")
    expiration_time = None
    if expiration is not None:
        if (
            isinstance(expiration, bool)
            or not isinstance(expiration, (int, float))
            or expiration <= 0
        ):
            return _error("The subscription expiration time is invalid.", 400)
        try:
            expiration_time = datetime.fromtimestamp(
                expiration / 1000, tz=timezone.utc
            )
        except (ValueError, OverflowError, OSError):
            return _error("The subscription expiration time is invalid.", 400)

    endpoint_hash = hash_push_endpoint(endpoint)
    subscription = PushSubscription.query.filter_by(
        endpoint_hash=endpoint_hash
    ).first()

    if subscription is not None and subscription.user_id != user_id:
        return _error("This push subscription is already registered.", 409)

    try:
        if subscription is None:
            subscription = PushSubscription(
                user_id=user_id,
                endpoint_hash=endpoint_hash,
                endpoint_encrypted=encrypt_subscription_secret(endpoint),
                p256dh_encrypted=encrypt_subscription_secret(p256dh),
                auth_encrypted=encrypt_subscription_secret(auth),
            )
            db.session.add(subscription)
        else:
            subscription.endpoint_encrypted = encrypt_subscription_secret(endpoint)
            subscription.p256dh_encrypted = encrypt_subscription_secret(p256dh)
            subscription.auth_encrypted = encrypt_subscription_secret(auth)

        subscription.expiration_time = expiration_time
        subscription.user_agent = (
            request.headers.get("User-Agent", "")[:500] or None
        )
        subscription.is_active = True
        db.session.commit()

    except IntegrityError:
        db.session.rollback()
        return _error("The push subscription could not be saved.", 409)
    except (RuntimeError, ValueError):
        db.session.rollback()
        current_app.logger.exception("Push subscription encryption failed")
        return _error("Push subscription could not be secured.", 500)

    return jsonify({
        "success": True,
        "subscription_id": str(subscription.id),
    }), 201


@notifications_bp.delete("/push/subscribe")
@jwt_required()
def unsubscribe_push():
    user_id = _user_id()
    if user_id is None:
        return _error("Invalid authentication identity.", 401)

    data = request.get_json(silent=True) or {}
    endpoint = data.get("endpoint")

    if not _valid_push_endpoint(endpoint):
        return _error("A valid HTTPS push endpoint is required.", 400)

    subscription = PushSubscription.query.filter_by(
        user_id=user_id,
        endpoint_hash=hash_push_endpoint(endpoint),
    ).first()

    if subscription is not None:
        subscription.is_active = False
        db.session.commit()

    return jsonify({"success": True}), 200
