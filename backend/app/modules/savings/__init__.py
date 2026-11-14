from flask import Blueprint

savings_bp = Blueprint(
    "savings",
    __name__,
    url_prefix="/api/v1/savings",
)

from . import routes
