from flask import Flask, jsonify
from sqlalchemy import text
from dotenv import load_dotenv

from .config import get_config
from .extensions import db, migrate, jwt, cors

load_dotenv(override=True)


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    from . import models

    db.init_app(app)
    migrate.init_app(app, db)
    jwt.init_app(app)
    cors.init_app(
        app,
        resources={r"/api/*": {"origins": app.config["CORS_ORIGINS"]}}
    )

    @app.get("/api/v1/health")
    def health():
        return jsonify({
            "success": True,
            "application": "PesaPilot",
            "status": "running"
        }), 200

    @app.get("/api/v1/health/db")
    def database_health():
        try:
            db.session.execute(text("SELECT 1"))
            return jsonify({
                "success": True,
                "database": "connected"
            }), 200
        except Exception:
            db.session.rollback()
            app.logger.exception("Database health check failed")
            return jsonify({
                "success": False,
                "database": "unavailable"
            }), 503

    return app
