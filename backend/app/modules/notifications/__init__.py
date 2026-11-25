from flask import Blueprint

notifications_bp = Blueprint(
    "notifications",
    __name__,
    url_prefix="/api/v1/notifications",
)

from . import routes
