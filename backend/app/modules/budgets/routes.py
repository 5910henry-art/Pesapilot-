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
    BudgetCategoryAllocation,
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



def _budget_actual_for_ledger(
    budget,
    ledger,
    user_timezone,
):
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

    actual = (
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
            JournalEntry.transaction_date >= (
                start_local.astimezone(timezone.utc)
            ),
            JournalEntry.transaction_date < (
                end_local.astimezone(timezone.utc)
            ),
            JournalLine.ledger_account_id == ledger.id,
        )
        .scalar()
    )

    return int(actual or 0)


def _budget_response(budget, timezone_name):
    try:
        user_timezone = ZoneInfo(timezone_name)
    except Exception:
        user_timezone = ZoneInfo("UTC")

    allocations = (
        BudgetCategoryAllocation.query
        .filter_by(
            workspace_id=budget.workspace_id,
            budget_id=budget.id,
        )
        .order_by(BudgetCategoryAllocation.created_at.asc())
        .all()
    )

    if not allocations:
        raise ValueError(
            "Budget has no category allocations."
        )

    category_data = []

    for allocation in allocations:
        category = ExpenseCategory.query.filter_by(
            id=allocation.category_id,
            workspace_id=budget.workspace_id,
        ).first()

        if category is None:
            raise ValueError(
                "Budget expense category was not found."
            )

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

        actual_minor = _budget_actual_for_ledger(
            budget,
            ledger,
            user_timezone,
        )

        allocated_minor = int(allocation.amount_minor)
        remaining_minor = allocated_minor - actual_minor

        percentage = round(
            actual_minor / allocated_minor * 100,
            2,
        ) if allocated_minor else 0

        category_data.append({
            "id": str(allocation.id),
            "category_id": str(category.id),
            "category": {
                "id": str(category.id),
                "name": category.name,
            },
            "amount_minor": allocated_minor,
            "actual_minor": actual_minor,
            "remaining_minor": remaining_minor,
            "usage_percentage": percentage,
            "is_over_budget": actual_minor > allocated_minor,
        })

    total_allocated = sum(
        item["amount_minor"] for item in category_data
    )
    total_actual = sum(
        item["actual_minor"] for item in category_data
    )
    total_remaining = total_allocated - total_actual

    total_percentage = round(
        total_actual / total_allocated * 100,
        2,
    ) if total_allocated else 0

    single_category = (
        category_data[0]["category"]
        if len(category_data) == 1
        else None
    )

    return {
        "id": str(budget.id),
        "category": single_category,
        "categories": category_data,
        "amount_minor": total_allocated,
        "actual_minor": total_actual,
        "remaining_minor": total_remaining,
        "usage_percentage": total_percentage,
        "is_over_budget": total_actual > total_allocated,
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

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to create budgets.",
        }), 403

    # Accept the new categories array or the legacy single-category payload.
    if "categories" in data:
        if "category_id" in data or "amount_minor" in data:
            return jsonify({
                "success": False,
                "message": (
                    "Use either categories or the legacy category_id and "
                    "amount_minor fields, not both."
                ),
            }), 400

        raw_categories = data.get("categories")

        if (
            not isinstance(raw_categories, list)
            or not raw_categories
            or len(raw_categories) > 100
        ):
            return jsonify({
                "success": False,
                "message": (
                    "Provide between 1 and 100 category allocations."
                ),
            }), 400
    else:
        raw_categories = [{
            "category_id": data.get("category_id"),
            "amount_minor": data.get("amount_minor"),
        }]

    allocations = []
    seen_category_ids = set()
    total_amount = 0
    maximum_amount = 9223372036854775807

    for item in raw_categories:
        if not isinstance(item, dict):
            return jsonify({
                "success": False,
                "message": "Each category allocation must be an object.",
            }), 400

        category_id = _workspace_id(item.get("category_id"))
        amount_minor = item.get("amount_minor")

        if category_id is None:
            return jsonify({
                "success": False,
                "message": "Every allocation requires a valid category ID.",
            }), 400

        if category_id in seen_category_ids:
            return jsonify({
                "success": False,
                "message": "A category can only appear once per budget.",
            }), 400

        if (
            isinstance(amount_minor, bool)
            or not isinstance(amount_minor, int)
            or amount_minor <= 0
            or amount_minor > maximum_amount
        ):
            return jsonify({
                "success": False,
                "message": (
                    "Every category amount must be a positive integer "
                    "in minor units."
                ),
            }), 400

        if total_amount > maximum_amount - amount_minor:
            return jsonify({
                "success": False,
                "message": "The total budget amount is too large.",
            }), 400

        seen_category_ids.add(category_id)
        total_amount += amount_minor
        allocations.append({
            "category_id": category_id,
            "amount_minor": amount_minor,
        })

    try:
        period_start = date.fromisoformat(
            data.get("period_start", "")
        )
        period_end = date.fromisoformat(
            data.get("period_end", "")
        )
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

    # Validate every category within this workspace.
    for allocation in allocations:
        category = ExpenseCategory.query.filter_by(
            id=allocation["category_id"],
            workspace_id=workspace_id,
            is_active=True,
        ).first()

        if category is None:
            return jsonify({
                "success": False,
                "message": (
                    "An active expense category was not found in this workspace."
                ),
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
        workspace_id=workspace_id,
        period_start=period_start,
        period_end=period_end,
    ).first()

    if duplicate:
        return jsonify({
            "success": False,
            "message": (
                "A budget already exists for this workspace and period."
            ),
        }), 409

    try:
        budget = Budget(
            workspace_id=workspace_id,
            category_id=(
                allocations[0]["category_id"]
                if len(allocations) == 1
                else None
            ),
            amount_minor=total_amount,
            currency_code=workspace.functional_currency,
            period_start=period_start,
            period_end=period_end,
            is_active=True,
            created_by=user_id,
        )

        db.session.add(budget)
        db.session.flush()

        for allocation in allocations:
            db.session.add(BudgetCategoryAllocation(
                workspace_id=workspace_id,
                budget_id=budget.id,
                category_id=allocation["category_id"],
                amount_minor=allocation["amount_minor"],
            ))

        db.session.flush()

        audit_details = {
            "categories": [
                {
                    "category_id": str(item["category_id"]),
                    "amount_minor": item["amount_minor"],
                }
                for item in allocations
            ],
            "amount_minor": total_amount,
            "currency_code": workspace.functional_currency,
            "period_start": period_start.isoformat(),
            "period_end": period_end.isoformat(),
        }

        if len(allocations) == 1:
            audit_details["category_id"] = str(
                allocations[0]["category_id"]
            )

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="budget_created",
            resource_type="budget",
            resource_id=str(budget.id),
            details=audit_details,
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
            "message": (
                "A budget already exists for this workspace and period, "
                "or one of its allocations is invalid."
            ),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Budget creation failed")
        return jsonify({
            "success": False,
            "message": "Budget creation failed. Please try again.",
        }), 500
