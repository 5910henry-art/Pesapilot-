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
from .modules.notifications import notifications_bp


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
    from datetime import date, datetime, timezone
    import click
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
    app.register_blueprint(notifications_bp)

    @app.cli.command("platform-assess-late-fees")
    @click.option(
        "--as-of-date",
        required=True,
        help="Assessment date in YYYY-MM-DD format.",
    )
    @click.option(
        "--actor-id",
        required=True,
        help="UUID of the owner/operator recording the accounting entries.",
    )
    @click.option(
        "--batch-size",
        default=500,
        type=click.IntRange(1, 5000),
        show_default=True,
    )
    def platform_assess_late_fees(as_of_date, actor_id, batch_size):
        """Assess configured one-time fees on overdue installments."""
        from .services.platform_lending import (
            assess_overdue_platform_loan_fees,
        )

        try:
            assessment_date = date.fromisoformat(as_of_date)
            actor_uuid = UUID(actor_id)

            count = assess_overdue_platform_loan_fees(
                as_of_date=assessment_date,
                assessed_by=actor_uuid,
                batch_size=batch_size,
            )
            db.session.commit()
            click.echo(
                f"Assessed {count} late fee(s) as of "
                f"{assessment_date.isoformat()}."
            )
        except Exception:
            db.session.rollback()
            raise

    @app.cli.command("send-bill-reminders")
    def send_bill_reminders_command():
        """Create due-date reminders for outstanding bills."""
        from .services.bill_reminders import send_bill_reminders

        try:
            result = send_bill_reminders()
            db.session.commit()
            click.echo(
                "Bill reminder run complete: "
                f"bills_checked={result['bills_checked']}, "
                f"created={result['notifications_created']}, "
                f"already_notified={result['already_notified']}, "
                f"preferences_disabled={result['preferences_disabled']}, "
                f"fully_paid_or_invalid={result['fully_paid_or_invalid']}."
            )
        except Exception:
            db.session.rollback()
            raise

    @app.cli.command("deliver-push-notifications")
    @click.option(
        "--batch-size",
        default=50,
        type=click.IntRange(1, 500),
        show_default=True,
        help="Maximum number of push deliveries to process.",
    )
    def deliver_push_notifications(batch_size):
        """Deliver queued Android/Web Push notifications."""
        from .services.push_delivery import (
            deliver_pending_push_notifications,
        )

        try:
            result = deliver_pending_push_notifications(
                batch_size=batch_size,
            )
            db.session.commit()
            click.echo(
                "Push delivery run complete: "
                f"processed={result['processed']}, "
                f"sent={result['sent']}, "
                f"failed={result['failed']}, "
                f"deferred={result['deferred']}."
            )
        except Exception:
            db.session.rollback()
            raise

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
