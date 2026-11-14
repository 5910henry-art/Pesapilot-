from dotenv import load_dotenv

load_dotenv(override=True)

from flask import Flask, jsonify
from sqlalchemy import event, text

from .config import get_config
from .extensions import db, migrate, jwt, cors
from .modules.auth import auth_bp
from .modules.finance import finance_bp


def create_app():
    app = Flask(__name__)
    app.config.from_object(get_config())

    from . import models

    db.init_app(app)

    with app.app_context():
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

    app.register_blueprint(auth_bp)
    app.register_blueprint(finance_bp)

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
