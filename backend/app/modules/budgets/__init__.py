from flask import Blueprint

budgets_bp = Blueprint(
    "budgets",
    __name__,
    url_prefix="/api/v1/budgets",
)

from . import routes
