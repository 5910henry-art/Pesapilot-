from flask import Blueprint

platform_bp = Blueprint(
    "platform",
    __name__,
    url_prefix="/api/v1/platform",
)

from . import routes
