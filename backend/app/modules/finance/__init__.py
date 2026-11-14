from flask import Blueprint

finance_bp = Blueprint(
    "finance",
    __name__,
    url_prefix="/api/v1/finance",
)

from . import routes
