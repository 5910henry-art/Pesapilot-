from flask import Blueprint

ai_manager_bp = Blueprint(
    "ai_manager",
    __name__,
    url_prefix="/api/v1/ai-manager",
)

from . import routes
