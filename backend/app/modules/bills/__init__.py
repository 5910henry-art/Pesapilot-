from flask import Blueprint

bills_bp = Blueprint(
    "bills",
    __name__,
    url_prefix="/api/v1/bills",
)

from . import routes
