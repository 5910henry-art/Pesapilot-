from uuid import UUID, uuid4
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.models import (
    AuditLog,
    ExpenseCategory,
    Budget,
    Currency,
    JournalEntry,
    JournalLine,
    UserProfile,
    LedgerAccount,
    Workspace,
    WorkspaceMember,
)

from . import budgets_bp


def _user_id():
    try:
        return UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return None


def _workspace_id(value):
    if not isinstance(value, str):
        return None

    try:
        return UUID(value)
    except (ValueError, TypeError, AttributeError):
        return None


def _membership(workspace_id, user_id):
    return WorkspaceMember.query.filter_by(
        workspace_id=workspace_id,
        user_id=user_id,
    ).first()


def _category_response(category):
    ledger = LedgerAccount.query.filter_by(
        id=category.ledger_account_id,
        workspace_id=category.workspace_id,
        account_class="expense",
        normal_balance="debit",
        is_active=True,
    ).first()

    return {
        "id": str(category.id),
        "name": category.name,
        "is_active": category.is_active,
        "created_at": category.created_at.isoformat(),
        "ledger_account": {
            "id": str(ledger.id),
            "code": ledger.code,
            "name": ledger.name,
        } if ledger else None,
    }


@budgets_bp.get("/categories")
@jwt_required()
def list_expense_categories():
    workspace_id = _workspace_id(
        request.args.get("workspace_id")
    )
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
        }), 400

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    membership = _membership(workspace_id, user_id)

    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    categories = (
        ExpenseCategory.query
        .filter_by(workspace_id=workspace_id)
        .order_by(ExpenseCategory.name.asc())
        .all()
    )

    return jsonify({
        "success": True,
        "categories": [
            _category_response(category)
            for category in categories
        ],
    }), 200


@budgets_bp.post("/categories")
@jwt_required()
def create_expense_category():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    name = data.get("name")
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
        }), 400

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    if not isinstance(name, str):
        return jsonify({
            "success": False,
            "message": "Category name must be a string.",
        }), 400

    name = name.strip()

    if not name or len(name) > 100:
        return jsonify({
            "success": False,
            "message": "Category name is required and must not exceed 100 characters.",
        }), 400

    membership = _membership(workspace_id, user_id)

    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to create categories.",
        }), 403

    workspace = db.session.get(Workspace, workspace_id)

    if workspace is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found.",
        }), 404

    duplicate = ExpenseCategory.query.filter(
        ExpenseCategory.workspace_id == workspace_id,
        func.lower(ExpenseCategory.name) == name.lower(),
    ).first()

    if duplicate:
        return jsonify({
            "success": False,
            "message": "A category with this name already exists.",
        }), 409

    try:
        ledger_id = uuid4()

        ledger = LedgerAccount(
            id=ledger_id,
            workspace_id=workspace_id,
            code=f"EXP-{ledger_id.hex[:20].upper()}",
            name=f"Expenses - {name}",
            account_class="expense",
            normal_balance="debit",
            currency_code=workspace.functional_currency,
            is_system=False,
            is_active=True,
        )

        db.session.add(ledger)
        db.session.flush()

        category = ExpenseCategory(
            workspace_id=workspace_id,
            name=name,
            ledger_account_id=ledger.id,
            is_active=True,
        )

        db.session.add(category)
        db.session.flush()

        audit = AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="expense_category_created",
            resource_type="expense_category",
            resource_id=str(category.id),
            details={
                "name": name,
                "ledger_account_id": str(ledger.id),
                "ledger_account_code": ledger.code,
                "currency": workspace.functional_currency,
            },
            ip_address=request.remote_addr,
        )

        db.session.add(audit)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Expense category created successfully.",
            "category": _category_response(category),
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "A category with this name already exists or a data conflict occurred.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Expense category creation failed"
        )
        return jsonify({
            "success": False,
            "message": "Category creation failed. Please try again.",
        }), 500


def _budget_response(budget, timezone_name):
    category = ExpenseCategory.query.filter_by(
        id=budget.category_id,
        workspace_id=budget.workspace_id,
    ).first()

    if category is None:
        raise ValueError("Budget expense category was not found.")

    ledger = LedgerAccount.query.filter_by(
        id=category.ledger_account_id,
        workspace_id=budget.workspace_id,
        account_class="expense",
        normal_balance="debit",
        is_active=True,
    ).first()

    if ledger is None:
        raise ValueError(
            "Budget category has no valid active expense ledger account."
        )

    try:
        user_timezone = ZoneInfo(timezone_name)
    except Exception:
        user_timezone = ZoneInfo("UTC")

    start_local = datetime.combine(
        budget.period_start,
        time.min,
        tzinfo=user_timezone,
    )

    end_local = datetime.combine(
        budget.period_end + timedelta(days=1),
        time.min,
        tzinfo=user_timezone,
    )

    start_utc = start_local.astimezone(timezone.utc)
    end_utc = end_local.astimezone(timezone.utc)

    actual_minor = (
        db.session.query(
            func.coalesce(
                func.sum(
                    JournalLine.functional_debit_minor
                    - JournalLine.functional_credit_minor
                ),
                0,
            )
        )
        .join(
            JournalEntry,
            JournalEntry.id == JournalLine.journal_entry_id,
        )
        .filter(
            JournalEntry.workspace_id == budget.workspace_id,
            JournalEntry.status.in_(("posted", "reversed")),
            JournalEntry.transaction_date >= start_utc,
            JournalEntry.transaction_date < end_utc,
            JournalLine.ledger_account_id == ledger.id,
        )
        .scalar()
    )

    actual_minor = int(actual_minor or 0)
    remaining_minor = budget.amount_minor - actual_minor

    percentage = round(
        (actual_minor / budget.amount_minor) * 100,
        2,
    )

    return {
        "id": str(budget.id),
        "category": {
            "id": str(category.id),
            "name": category.name,
        },
        "amount_minor": budget.amount_minor,
        "actual_minor": actual_minor,
        "remaining_minor": remaining_minor,
        "usage_percentage": percentage,
        "is_over_budget": actual_minor > budget.amount_minor,
        "currency_code": budget.currency_code,
        "period_start": budget.period_start.isoformat(),
        "period_end": budget.period_end.isoformat(),
        "is_active": budget.is_active,
        "created_at": budget.created_at.isoformat(),
    }


@budgets_bp.get("")
@jwt_required()
def list_budgets():
    workspace_id = _workspace_id(
        request.args.get("workspace_id")
    )
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
        }), 400

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    if _membership(workspace_id, user_id) is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    profile = db.session.get(UserProfile, user_id)
    timezone_name = (
        profile.timezone
        if profile and profile.timezone
        else "UTC"
    )

    budgets = (
        Budget.query
        .filter_by(
            workspace_id=workspace_id,
            is_active=True,
        )
        .order_by(
            Budget.period_start.desc(),
            Budget.created_at.desc(),
        )
        .all()
    )

    return jsonify({
        "success": True,
        "budgets": [
            _budget_response(budget, timezone_name)
            for budget in budgets
        ],
    }), 200


@budgets_bp.post("")
@jwt_required()
def create_budget():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    category_id = _workspace_id(data.get("category_id"))
    user_id = _user_id()

    if workspace_id is None or category_id is None:
        return jsonify({
            "success": False,
            "message": "Valid workspace and category IDs are required.",
        }), 400

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    membership = _membership(workspace_id, user_id)

    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to create budgets.",
        }), 403

    amount_minor = data.get("amount_minor")

    if (
        isinstance(amount_minor, bool)
        or not isinstance(amount_minor, int)
        or amount_minor <= 0
        or amount_minor > 9223372036854775807
    ):
        return jsonify({
            "success": False,
            "message": "Budget amount must be a positive integer in minor units.",
        }), 400

    try:
        period_start = date.fromisoformat(data.get("period_start", ""))
        period_end = date.fromisoformat(data.get("period_end", ""))
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Valid ISO dates are required for the budget period.",
        }), 400

    if period_end < period_start:
        return jsonify({
            "success": False,
            "message": "Budget end date cannot be before its start date.",
        }), 400

    category = ExpenseCategory.query.filter_by(
        id=category_id,
        workspace_id=workspace_id,
        is_active=True,
    ).first()

    if category is None:
        return jsonify({
            "success": False,
            "message": "Active expense category not found in this workspace.",
        }), 404

    workspace = db.session.get(Workspace, workspace_id)

    if workspace is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found.",
        }), 404

    currency = Currency.query.filter_by(
        code=workspace.functional_currency,
        is_active=True,
    ).first()

    if currency is None:
        return jsonify({
            "success": False,
            "message": "Workspace functional currency is unavailable.",
        }), 400

    duplicate = Budget.query.filter_by(
        category_id=category_id,
        period_start=period_start,
        period_end=period_end,
    ).first()

    if duplicate:
        return jsonify({
            "success": False,
            "message": "A budget already exists for this category and period.",
        }), 409

    try:
        budget = Budget(
            workspace_id=workspace_id,
            category_id=category_id,
            amount_minor=amount_minor,
            currency_code=workspace.functional_currency,
            period_start=period_start,
            period_end=period_end,
            is_active=True,
            created_by=user_id,
        )

        db.session.add(budget)
        db.session.flush()

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="budget_created",
            resource_type="budget",
            resource_id=str(budget.id),
            details={
                "category_id": str(category_id),
                "amount_minor": amount_minor,
                "currency_code": workspace.functional_currency,
                "period_start": period_start.isoformat(),
                "period_end": period_end.isoformat(),
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        profile = db.session.get(UserProfile, user_id)
        timezone_name = (
            profile.timezone
            if profile and profile.timezone
            else "UTC"
        )

        return jsonify({
            "success": True,
            "message": "Budget created successfully.",
            "budget": _budget_response(budget, timezone_name),
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "A budget already exists for this category and period.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Budget creation failed")
        return jsonify({
            "success": False,
            "message": "Budget creation failed. Please try again.",
        }), 500

