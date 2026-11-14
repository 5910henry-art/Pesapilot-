from uuid import UUID

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    User,
    WorkspaceMember,
    Currency,
    FinancialAccount,
    LedgerAccount,
    AuditLog,
)

from . import finance_bp


ACCOUNT_TYPES = {
    "cash": ("asset", "debit"),
    "bank": ("asset", "debit"),
    "mobile_money": ("asset", "debit"),
    "savings": ("asset", "debit"),
    "investment": ("asset", "debit"),
    "credit": ("liability", "credit"),
}


def get_workspace_membership(workspace_id, user_id):
    return WorkspaceMember.query.filter_by(
        workspace_id=workspace_id,
        user_id=user_id,
    ).first()


@finance_bp.get("/health")
def finance_health():
    return jsonify({
        "success": True,
        "module": "finance",
        "status": "ready",
    }), 200


@finance_bp.post("/accounts")
@jwt_required()
def create_financial_account():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    workspace_value = data.get("workspace_id")
    name = data.get("name")
    account_type = data.get("account_type")
    currency_code = data.get("currency", "KES")

    if not isinstance(workspace_value, str):
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
        }), 400

    try:
        workspace_id = UUID(workspace_value)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "The workspace ID is invalid.",
        }), 400

    if not isinstance(name, str):
        return jsonify({
            "success": False,
            "message": "Account name must be a string.",
        }), 400

    name = name.strip()

    if not name or len(name) > 150:
        return jsonify({
            "success": False,
            "message": "Account name is required and must not exceed 150 characters.",
        }), 400

    if not isinstance(account_type, str):
        return jsonify({
            "success": False,
            "message": "A valid account type is required.",
        }), 400

    account_type = account_type.strip().lower()

    if account_type not in ACCOUNT_TYPES:
        return jsonify({
            "success": False,
            "message": "Unsupported account type.",
            "allowed_types": list(ACCOUNT_TYPES),
        }), 400

    if not isinstance(currency_code, str):
        return jsonify({
            "success": False,
            "message": "Currency must be a three-letter currency code.",
        }), 400

    currency_code = currency_code.strip().upper()

    if len(currency_code) != 3 or not currency_code.isalpha():
        return jsonify({
            "success": False,
            "message": "Currency must be a valid three-letter code.",
        }), 400

    try:
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    membership = get_workspace_membership(workspace_id, user_id)

    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to create accounts.",
        }), 403

    currency = db.session.get(Currency, currency_code)

    if currency is None or not currency.is_active:
        return jsonify({
            "success": False,
            "message": "The selected currency is unavailable.",
        }), 400

    account_class, normal_balance = ACCOUNT_TYPES[account_type]

    try:
        account = FinancialAccount(
            workspace_id=workspace_id,
            name=name,
            account_type=account_type,
            currency_code=currency_code,
            is_active=True,
        )

        db.session.add(account)
        db.session.flush()

        ledger_account = LedgerAccount(
            workspace_id=workspace_id,
            financial_account_id=account.id,
            code=f"FA-{account.id.hex[:20].upper()}",
            name=name,
            account_class=account_class,
            normal_balance=normal_balance,
            currency_code=currency_code,
            is_system=False,
            is_active=True,
        )

        audit = AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="financial_account_created",
            resource_type="financial_account",
            resource_id=str(account.id),
            details={
                "name": name,
                "account_type": account_type,
                "currency": currency_code,
                "ledger_account_class": account_class,
            },
            ip_address=request.remote_addr,
        )

        db.session.add_all([ledger_account, audit])
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Financial account created successfully.",
            "account": {
                "id": str(account.id),
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency_code,
                "is_active": account.is_active,
                "ledger_account": {
                    "id": str(ledger_account.id),
                    "code": ledger_account.code,
                    "account_class": ledger_account.account_class,
                    "normal_balance": ledger_account.normal_balance,
                },
                "created_at": account.created_at.isoformat(),
            },
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "The account could not be created because of a data conflict.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Financial account creation failed"
        )
        return jsonify({
            "success": False,
            "message": "Financial account creation failed. Please try again.",
        }), 500


@finance_bp.get("/accounts")
@jwt_required()
def list_financial_accounts():
    workspace_value = request.args.get("workspace_id", "")

    try:
        workspace_id = UUID(workspace_value)
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
        }), 400

    membership = get_workspace_membership(workspace_id, user_id)

    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    try:
        results = (
            db.session.query(FinancialAccount, LedgerAccount)
            .outerjoin(
                LedgerAccount,
                (
                    (LedgerAccount.financial_account_id == FinancialAccount.id)
                    & (LedgerAccount.workspace_id == workspace_id)
                ),
            )
            .filter(
                FinancialAccount.workspace_id == workspace_id
            )
            .order_by(FinancialAccount.created_at.desc())
            .all()
        )

        accounts = []

        for account, ledger in results:
            accounts.append({
                "id": str(account.id),
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency_code,
                "is_active": account.is_active,
                "created_at": account.created_at.isoformat(),
                "ledger_account": (
                    {
                        "id": str(ledger.id),
                        "code": ledger.code,
                        "account_class": ledger.account_class,
                        "normal_balance": ledger.normal_balance,
                    }
                    if ledger else None
                ),
            })

        return jsonify({
            "success": True,
            "count": len(accounts),
            "accounts": accounts,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Financial account listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Financial accounts could not be loaded.",
        }), 500
