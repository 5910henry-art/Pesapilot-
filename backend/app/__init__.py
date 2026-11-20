from dotenv import load_dotenv

load_dotenv(override=True)

from flask import Flask, jsonify
from sqlalchemy import event, text

from .config import get_config
from .extensions import db, migrate, jwt, cors
from .modules.auth import auth_bp
from .modules.finance import finance_bp
from .modules.budgets import budgets_bp
from .modules.savings import savings_bp
from .modules.loans import loans_bp
from .modules.bills import bills_bp
from .modules.ai_manager import ai_manager_bp
from .modules.platform import platform_bp


def create_app(test_config=None):
    app = Flask(__name__)
    app.config.from_object(get_config())

    if test_config:
        app.config.update(test_config)

    from . import models

    db.init_app(app)

    with app.app_context():
        if db.engine.dialect.name == "postgresql":
            @event.listens_for(db.engine, "begin")
            def set_pesapilot_schema(connection):
                connection.exec_driver_sql(
                    "SET LOCAL search_path TO pesapilot"
                )
                connection.exec_driver_sql(
                    "SET LOCAL TIME ZONE 'UTC'"
                )
    migrate.init_app(app, db)
    jwt.init_app(app)

    from uuid import UUID
    from datetime import datetime, timezone
    from .models import User, RefreshToken

    @jwt.token_in_blocklist_loader
    def is_user_token_revoked(jwt_header, jwt_payload):
        identity = jwt_payload.get("sub")

        try:
            user_id = UUID(str(identity))
        except (ValueError, TypeError, AttributeError):
            return True

        user = db.session.get(User, user_id)

        if user is None or not user.is_active:
            return True

        # Legacy tokens without a session claim remain valid
        # until their normal expiration.
        session_claim = jwt_payload.get("sid")
        if session_claim is None:
            return False

        try:
            session_id = UUID(str(session_claim))
        except (ValueError, TypeError, AttributeError):
            return True

        now = datetime.now(timezone.utc)

        active_session = db.session.query(RefreshToken.id).filter(
            RefreshToken.user_id == user_id,
            RefreshToken.session_id == session_id,
            RefreshToken.revoked_at.is_(None),
            RefreshToken.expires_at > now,
        ).first()

        return active_session is None

    app.register_blueprint(auth_bp)
    app.register_blueprint(finance_bp)
    app.register_blueprint(budgets_bp)
    app.register_blueprint(savings_bp)
    app.register_blueprint(loans_bp)
    app.register_blueprint(bills_bp)
    app.register_blueprint(ai_manager_bp)
    app.register_blueprint(platform_bp)

    cors.init_app(
        app,
        resources={
            r"/api/*": {
                "origins": app.config["CORS_ORIGINS"]
            }
        },
    )

    @app.get("/api/v1/health")
    def health():
        return jsonify({
            "success": True,
            "application": "PesaPilot",
            "status": "running",
        }), 200

    @app.get("/api/v1/health/db")
    def database_health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify({
                "success": True,
                "database": "connected",
            }), 200
        except Exception:
            db.session.rollback()
            app.logger.exception("Database health check failed")
            return jsonify({
                "success": False,
                "database": "unavailable",
            }), 503

    return app
