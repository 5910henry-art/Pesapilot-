from uuid import UUID, uuid4
from datetime import date, datetime, timezone

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func, case
from sqlalchemy.exc import IntegrityError

from app.extensions import db
from app.services.accounting import (
    AccountingError,
    create_draft_entry,
    post_entry,
)
from app.models import (
    AuditLog,
    Currency,
    FinancialAccount,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    SavingsGoal,
    SavingsGoalMovement,
    Workspace,
    WorkspaceMember,
)

from . import savings_bp


MAX_MINOR_UNITS = 9_223_372_036_854_775_807


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


def _goal_allocation_balance(workspace_id, goal_id):
    balance = (
        db.session.query(
            func.coalesce(
                func.sum(
                    case(
                        (
                            SavingsGoalMovement.operation == "contribution",
                            SavingsGoalMovement.amount_minor,
                        ),
                        else_=-SavingsGoalMovement.amount_minor,
                    )
                ),
                0,
            )
        )
        .join(
            JournalEntry,
            JournalEntry.id == SavingsGoalMovement.journal_entry_id,
        )
        .filter(
            SavingsGoalMovement.workspace_id == workspace_id,
            SavingsGoalMovement.goal_id == goal_id,
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status == "posted",
        )
        .scalar()
    )
    return max(0, int(balance or 0))


def _goal_response(goal):
    account = FinancialAccount.query.filter_by(
        id=goal.financial_account_id,
        workspace_id=goal.workspace_id,
        account_type="savings",
    ).first()

    if account is None:
        raise ValueError("Savings account was not found.")

    ledger = LedgerAccount.query.filter_by(
        financial_account_id=account.id,
        workspace_id=goal.workspace_id,
        account_class="asset",
        normal_balance="debit",
    ).first()

    if ledger is None:
        raise ValueError("Savings ledger account was not found.")

    saved_minor = _goal_allocation_balance(
        goal.workspace_id, goal.id
    )
    remaining_minor = max(goal.target_amount_minor - saved_minor, 0)
    percentage = round(
        saved_minor / goal.target_amount_minor * 100,
        2,
    )

    return {
        "id": str(goal.id),
        "name": goal.name,
        "description": goal.description,
        "target_amount_minor": goal.target_amount_minor,
        "saved_amount_minor": saved_minor,
        "remaining_amount_minor": remaining_minor,
        "progress_percentage": percentage,
        "is_completed": saved_minor >= goal.target_amount_minor,
        "currency_code": goal.currency_code,
        "target_date": (
            goal.target_date.isoformat()
            if goal.target_date else None
        ),
        "status": goal.status,
        "financial_account_id": str(account.id),
        "financial_account_name": account.name,
        "ledger_account_id": str(ledger.id),
        "created_at": goal.created_at.isoformat(),
    }


@savings_bp.get("")
@jwt_required()
def list_savings_goals():
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

    try:
        goals = (
            SavingsGoal.query
            .filter_by(workspace_id=workspace_id)
            .order_by(SavingsGoal.created_at.desc())
            .all()
        )

        return jsonify({
            "success": True,
            "count": len(goals),
            "goals": [_goal_response(goal) for goal in goals],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Savings goals listing failed")
        return jsonify({
            "success": False,
            "message": "Savings goals could not be loaded.",
        }), 500


@savings_bp.post("")
@jwt_required()
def create_savings_goal():
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
            "message": "You do not have permission to create savings goals.",
        }), 403

    name = data.get("name")
    description = data.get("description")
    target = data.get("target_amount_minor")
    workspace = db.session.get(Workspace, workspace_id)
    if workspace is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found.",
        }), 404
    currency_code = data.get("currency", workspace.functional_currency)
    target_date_value = data.get("target_date")

    if not isinstance(name, str) or not name.strip() or len(name.strip()) > 150:
        return jsonify({
            "success": False,
            "message": "Goal name is required and must not exceed 150 characters.",
        }), 400

    if description is not None and (
        not isinstance(description, str) or len(description) > 2000
    ):
        return jsonify({
            "success": False,
            "message": "Description must not exceed 2000 characters.",
        }), 400

    if (
        isinstance(target, bool)
        or not isinstance(target, int)
        or target <= 0
        or target > MAX_MINOR_UNITS
    ):
        return jsonify({
            "success": False,
            "message": "Target amount must be a positive integer in minor units.",
        }), 400

    if not isinstance(currency_code, str):
        return jsonify({
            "success": False,
            "message": "A valid currency code is required.",
        }), 400

    currency_code = currency_code.strip().upper()

    if currency_code != workspace.functional_currency:
        return jsonify({
            "success": False,
            "message": (
                "Savings goals must use the workspace functional currency "
                f"({workspace.functional_currency}) in this version."
            ),
        }), 400

    if len(currency_code) != 3 or not currency_code.isalpha():
        return jsonify({
            "success": False,
            "message": "A valid currency code is required.",
        }), 400

    target_date = None

    if target_date_value is not None:
        if not isinstance(target_date_value, str):
            return jsonify({
                "success": False,
                "message": "Target date must use YYYY-MM-DD format.",
            }), 400
        try:
            target_date = date.fromisoformat(target_date_value)
        except ValueError:
            return jsonify({
                "success": False,
                "message": "Target date must use YYYY-MM-DD format.",
            }), 400

    currency = db.session.get(Currency, currency_code)

    if currency is None or not currency.is_active:
        return jsonify({
            "success": False,
            "message": "The selected currency is unavailable.",
        }), 400

    try:
        supplied_account_id = data.get("financial_account_id")

        if supplied_account_id is None:
            account = FinancialAccount(
                workspace_id=workspace_id,
                name=f"Savings - {name.strip()}"[:150],
                account_type="savings",
                currency_code=currency_code,
                is_active=True,
            )
            db.session.add(account)
            db.session.flush()

            ledger = LedgerAccount(
                workspace_id=workspace_id,
                financial_account_id=account.id,
                code=f"FA-{account.id.hex[:20].upper()}",
                name=account.name,
                account_class="asset",
                normal_balance="debit",
                currency_code=currency_code,
                is_system=False,
                is_active=True,
            )
            db.session.add(ledger)
            db.session.flush()
        else:
            try:
                selected_account_id = UUID(str(supplied_account_id))
            except (ValueError, TypeError, AttributeError):
                return jsonify({
                    "success": False,
                    "message": "The selected Savings account ID is invalid.",
                }), 400

            account = (
                FinancialAccount.query
                .filter_by(
                    id=selected_account_id,
                    workspace_id=workspace_id,
                    account_type="savings",
                    currency_code=currency_code,
                    is_active=True,
                )
                .first()
            )

            if account is None:
                return jsonify({
                    "success": False,
                    "message": (
                        "Choose an active Savings account in this workspace "
                        "and currency."
                    ),
                }), 400

            ledger = LedgerAccount.query.filter_by(
                financial_account_id=account.id,
                workspace_id=workspace_id,
                account_class="asset",
                normal_balance="debit",
                currency_code=currency_code,
                is_active=True,
            ).first()

            if ledger is None:
                return jsonify({
                    "success": False,
                    "message": "The selected Savings account has no active asset ledger.",
                }), 400

        goal = SavingsGoal(
            workspace_id=workspace_id,
            name=name.strip(),
            description=description.strip() if description else None,
            target_amount_minor=target,
            currency_code=currency_code,
            target_date=target_date,
            financial_account_id=account.id,
            status="active",
            created_by=user_id,
        )
        db.session.add(goal)
        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="savings_goal_created",
            resource_type="savings_goal",
            resource_id=str(goal.id),
            details={
                "name": goal.name,
                "target_amount_minor": target,
                "currency": currency_code,
                "financial_account_id": str(account.id),
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Savings goal created successfully.",
            "goal": _goal_response(goal),
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "A savings goal with this name may already exist.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Savings goal creation failed")
        return jsonify({
            "success": False,
            "message": "Savings goal could not be created.",
        }), 500


def _ledger_balance(workspace_id, ledger_id):
    balance = (
        db.session.query(
            func.coalesce(
                func.sum(
                    JournalLine.debit_minor - JournalLine.credit_minor
                ),
                0,
            )
        )
        .join(
            JournalEntry,
            JournalEntry.id == JournalLine.journal_entry_id,
        )
        .filter(
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status.in_(("posted", "reversed")),
            JournalLine.ledger_account_id == ledger_id,
        )
        .scalar()
    )
    return int(balance or 0)


def _transfer_savings(goal_id, operation):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    source_account_id = _workspace_id(data.get("source_account_id"))
    user_id = _user_id()
    amount = data.get("amount_minor")

    if workspace_id is None or source_account_id is None:
        return jsonify({
            "success": False,
            "message": "Valid workspace and source account IDs are required.",
        }), 400

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    if (
        isinstance(amount, bool)
        or not isinstance(amount, int)
        or amount <= 0
        or amount > MAX_MINOR_UNITS
    ):
        return jsonify({
            "success": False,
            "message": "Amount must be a positive integer in minor units.",
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
            "message": "You do not have permission to move savings.",
        }), 403

    try:
        # Serialize transfers against this goal to protect its balance.
        goal = (
            SavingsGoal.query
            .filter_by(id=goal_id, workspace_id=workspace_id)
            .with_for_update()
            .first()
        )

        if goal is None:
            return jsonify({
                "success": False,
                "message": "Savings goal not found.",
            }), 404

        if goal.status != "active":
            return jsonify({
                "success": False,
                "message": "Only active savings goals can receive or release funds.",
            }), 400

        workspace = db.session.get(Workspace, workspace_id)

        if workspace is None:
            return jsonify({
                "success": False,
                "message": "Workspace not found.",
            }), 404

        savings_account = (
            FinancialAccount.query
            .filter_by(
                id=goal.financial_account_id,
                workspace_id=workspace_id,
                account_type="savings",
                is_active=True,
            )
            .with_for_update()
            .first()
        )

        if savings_account is None:
            return jsonify({
                "success": False,
                "message": "The linked savings account is unavailable.",
            }), 400

        source_account = (
            FinancialAccount.query
            .filter_by(
                id=source_account_id,
                workspace_id=workspace_id,
                is_active=True,
            )
            .with_for_update()
            .first()
        )

        if source_account is None:
            return jsonify({
                "success": False,
                "message": "Source account not found or inactive.",
            }), 404

        if source_account.account_type not in (
            "cash", "bank", "mobile_money"
        ):
            return jsonify({
                "success": False,
                "message": "Choose an active cash, bank, or mobile money account.",
            }), 400

        if source_account.id == savings_account.id:
            return jsonify({
                "success": False,
                "message": "The savings account cannot be its own source.",
            }), 400

        if (
            goal.currency_code != workspace.functional_currency
            or source_account.currency_code != workspace.functional_currency
        ):
            return jsonify({
                "success": False,
                "message": "Both accounts must use the workspace functional currency.",
            }), 400

        savings_ledger = (
            LedgerAccount.query
            .filter_by(
                financial_account_id=savings_account.id,
                workspace_id=workspace_id,
                account_class="asset",
                normal_balance="debit",
                currency_code=goal.currency_code,
                is_active=True,
            )
            .with_for_update()
            .first()
        )

        source_ledger = (
            LedgerAccount.query
            .filter_by(
                financial_account_id=source_account.id,
                workspace_id=workspace_id,
                account_class="asset",
                normal_balance="debit",
                currency_code=source_account.currency_code,
                is_active=True,
            )
            .with_for_update()
            .first()
        )

        if savings_ledger is None or source_ledger is None:
            return jsonify({
                "success": False,
                "message": "A linked ledger account is unavailable.",
            }), 400

        savings_balance = _ledger_balance(
            workspace_id, savings_ledger.id
        )
        source_balance = _ledger_balance(
            workspace_id, source_ledger.id
        )

        if operation == "contribution" and source_balance < amount:
            return jsonify({
                "success": False,
                "message": "The source account has insufficient funds.",
                "available_amount_minor": source_balance,
            }), 400

        goal_balance = _goal_allocation_balance(
            workspace_id, goal.id
        )

        if operation == "withdrawal" and goal_balance < amount:
            return jsonify({
                "success": False,
                "message": "The savings goal has insufficient funds.",
                "available_amount_minor": goal_balance,
            }), 400

        reference = f"SAV-{uuid4().hex.upper()}"
        is_contribution = operation == "contribution"

        if is_contribution:
            lines = [
                {
                    "ledger_account_id": savings_ledger.id,
                    "debit_minor": amount,
                    "credit_minor": 0,
                    "memo": f"Contribution to {goal.name}",
                },
                {
                    "ledger_account_id": source_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount,
                    "memo": f"Transfer to savings goal {goal.name}",
                },
            ]
            description = f"Savings contribution: {goal.name}"
        else:
            lines = [
                {
                    "ledger_account_id": source_ledger.id,
                    "debit_minor": amount,
                    "credit_minor": 0,
                    "memo": f"Withdrawal from {goal.name}",
                },
                {
                    "ledger_account_id": savings_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount,
                    "memo": f"Transfer from savings goal {goal.name}",
                },
            ]
            description = f"Savings withdrawal: {goal.name}"

        entry = create_draft_entry(
            workspace_id=workspace_id,
            created_by=user_id,
            reference=reference,
            description=description,
            transaction_date=datetime.now(timezone.utc),
            lines=lines,
        )

        post_entry(
            entry_id=entry.id,
            workspace_id=workspace_id,
            posted_by=user_id,
        )

        db.session.add(SavingsGoalMovement(
            workspace_id=workspace_id,
            goal_id=goal.id,
            journal_entry_id=entry.id,
            amount_minor=amount,
            operation=operation,
        ))
        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=(
                "savings_contribution"
                if is_contribution else "savings_withdrawal"
            ),
            resource_type="savings_goal",
            resource_id=str(goal.id),
            details={
                "amount_minor": amount,
                "currency": goal.currency_code,
                "source_account_id": str(source_account.id),
                "journal_entry_id": str(entry.id),
                "reference": reference,
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": (
                "Savings contribution recorded successfully."
                if is_contribution
                else "Savings withdrawal recorded successfully."
            ),
            "reference": reference,
            "journal_entry_id": str(entry.id),
            "goal": _goal_response(goal),
        }), 201

    except AccountingError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "The savings transfer encountered a data conflict.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Savings %s failed", operation
        )
        return jsonify({
            "success": False,
            "message": "The savings transfer could not be completed.",
        }), 500


@savings_bp.post("/<goal_id>/contributions")
@jwt_required()
def contribute_to_savings(goal_id):
    parsed_goal_id = _workspace_id(goal_id)

    if parsed_goal_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid savings goal ID.",
        }), 400

    return _transfer_savings(parsed_goal_id, "contribution")


@savings_bp.post("/<goal_id>/withdrawals")
@jwt_required()
def withdraw_from_savings(goal_id):
    parsed_goal_id = _workspace_id(goal_id)

    if parsed_goal_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid savings goal ID.",
        }), 400

    return _transfer_savings(parsed_goal_id, "withdrawal")
