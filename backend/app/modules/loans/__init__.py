from flask import Blueprint

loans_bp = Blueprint(
    "loans",
    __name__,
    url_prefix="/api/v1/loans",
)

from . import routes
