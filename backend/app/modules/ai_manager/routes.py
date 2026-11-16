from uuid import UUID

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.services.ai_manager.data_service import build_financial_snapshot
from app.services.ai_manager.rules_engine import generate_insights
from app.services.ai_manager.providers import (
    AIProviderError,
    AIProviderUnavailable,
    get_ai_provider,
    sanitize_financial_context,
)
from . import ai_manager_bp


def _uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


@ai_manager_bp.get("/overview")
@jwt_required()
def get_ai_manager_overview():
    workspace_id = _uuid(request.args.get("workspace_id"))
    user_id = _uuid(get_jwt_identity())

    if workspace_id is None or user_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID and authentication are required.",
        }), 400

    try:
        snapshot = build_financial_snapshot(
            workspace_id=workspace_id,
            user_id=user_id,
        )

        insights = generate_insights(snapshot)

        return jsonify({
            "success": True,
            "data": {
                "snapshot": snapshot,
                "insights": insights,
                "insight_count": len(insights),
                "generated_by": "rules",
                "read_only": True,
            },
        }), 200

    except PermissionError:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 404

    except Exception:
        from flask import current_app

        current_app.logger.exception(
            "AI Manager overview generation failed."
        )

        return jsonify({
            "success": False,
            "message": "Unable to generate your financial overview.",
        }), 500


@ai_manager_bp.post("/ask")
@jwt_required()
def ask_ai_manager_question():
    user_id = _uuid(get_jwt_identity())
    payload = request.get_json(silent=True) or {}

    workspace_id = _uuid(payload.get("workspace_id"))
    question = payload.get("question")
    consent = payload.get("external_ai_consent")

    if user_id is None or workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID and authentication are required.",
        }), 400

    if not isinstance(question, str) or not question.strip():
        return jsonify({
            "success": False,
            "message": "Please enter a financial question.",
        }), 400

    question = question.strip()

    if len(question) > 2000:
        return jsonify({
            "success": False,
            "message": "Your question must not exceed 2,000 characters.",
        }), 400

    if consent is not True:
        return jsonify({
            "success": False,
            "message": (
                "Explicit consent is required before sending financial "
                "information to the external AI provider."
            ),
        }), 400

    try:
        # Check provider availability before building financial context.
        provider = get_ai_provider(current_app.config)

        snapshot = build_financial_snapshot(
            workspace_id=workspace_id,
            user_id=user_id,
        )

        financial_context = sanitize_financial_context(snapshot)
        answer = provider.generate_answer(
            question=question,
            financial_context=financial_context,
        )

        return jsonify({
            "success": True,
            "data": {
                "answer": answer,
                "generated_by": current_app.config.get(
                    "AI_PROVIDER", "external"
                ),
                "read_only": True,
                "external_ai_used": True,
            },
        }), 200

    except PermissionError:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    except ValueError as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 404

    except AIProviderUnavailable as exc:
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 503

    except AIProviderError as exc:
        current_app.logger.warning(
            "AI Manager external provider request failed: %s", exc
        )
        return jsonify({
            "success": False,
            "message": (
                "The external AI service is temporarily unavailable. "
                "Please try again later."
            ),
        }), 503

    except Exception:
        current_app.logger.exception(
            "AI Manager question request failed."
        )
        return jsonify({
            "success": False,
            "message": "Unable to answer your question right now.",
        }), 500
