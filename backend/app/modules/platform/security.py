"""Authorization guards for PesaPilot platform administration."""

from functools import wraps
from uuid import UUID

from flask import jsonify
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.models import PlatformAdmin, User


def platform_owner_required():
    """Require an active user with an active platform-owner permission."""

    def decorator(view):
        @wraps(view)
        @jwt_required()
        def wrapped(*args, **kwargs):
            try:
                user_id = UUID(str(get_jwt_identity()))
            except (ValueError, TypeError, AttributeError):
                return jsonify({
                    "success": False,
                    "message": "Invalid authentication identity.",
                }), 401

            user = db.session.get(User, user_id)

            if user is None or not user.is_active:
                return jsonify({
                    "success": False,
                    "message": "Active user account required.",
                }), 403

            permission = db.session.get(PlatformAdmin, user.id)

            if (
                permission is None
                or not permission.is_active
                or permission.role != "owner"
            ):
                return jsonify({
                    "success": False,
                    "message": "Platform owner access required.",
                }), 403

            return view(*args, **kwargs)

        return wrapped

    return decorator
