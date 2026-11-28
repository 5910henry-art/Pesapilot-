from uuid import UUID
from zoneinfo import ZoneInfo
from datetime import datetime, timezone

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func, case

from app.extensions import db
from app.models import (
    User,
    WorkspaceMember,
    Currency,
    FinancialAccount,
    LedgerAccount,
    AuditLog,
    JournalEntry,
    JournalLine,
    SavingsGoal,
    LoanPayment,
    Workspace,
    UserProfile,
    ExpenseCategory,
)

from . import finance_bp
from app.models.loan_disbursements import LoanDisbursement
from app.services.accounting import (
    AccountingError,
    create_draft_entry,
    post_entry,
    reverse_entry,
)


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

        account_ids = [account.id for account, _ in results]
        ledger_ids = [
            ledger.id for _, ledger in results if ledger is not None
        ]

        accounts_with_journal_lines = set()
        if ledger_ids:
            accounts_with_journal_lines = {
                row[0]
                for row in db.session.query(
                    JournalLine.ledger_account_id
                ).filter(
                    JournalLine.ledger_account_id.in_(ledger_ids)
                ).distinct().all()
            }

        accounts_with_savings_goals = {
            row[0]
            for row in db.session.query(
                SavingsGoal.financial_account_id
            ).filter(
                SavingsGoal.workspace_id == workspace_id,
                SavingsGoal.financial_account_id.in_(account_ids),
            ).distinct().all()
        } if account_ids else set()

        accounts_with_loan_disbursements = {
            row[0]
            for row in db.session.query(
                LoanDisbursement.financial_account_id
            ).filter(
                LoanDisbursement.workspace_id == workspace_id,
                LoanDisbursement.financial_account_id.in_(account_ids),
            ).distinct().all()
        } if account_ids else set()

        accounts_with_loan_payments = {
            row[0]
            for row in db.session.query(
                LoanPayment.financial_account_id
            ).filter(
                LoanPayment.workspace_id == workspace_id,
                LoanPayment.financial_account_id.in_(account_ids),
            ).distinct().all()
        } if account_ids else set()

        accounts = []

        for account, ledger in results:
            has_records = (
                (ledger is not None and ledger.id in accounts_with_journal_lines)
                or account.id in accounts_with_savings_goals
                or account.id in accounts_with_loan_disbursements
                or account.id in accounts_with_loan_payments
            )

            accounts.append({
                "id": str(account.id),
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency_code,
                "is_active": account.is_active,
                "has_records": has_records,
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




@finance_bp.patch("/accounts/<account_id>")
@jwt_required()
def update_financial_account(account_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    try:
        account_uuid = UUID(account_id)
        workspace_id = UUID(str(data.get("workspace_id", "")))
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid account, workspace or authentication ID.",
        }), 400

    editable_fields = {"name", "account_type", "currency", "is_active"}
    if not editable_fields.intersection(data):
        return jsonify({
            "success": False,
            "message": "Provide at least one account field to update.",
        }), 400

    name = None
    if "name" in data:
        name = data["name"]
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

    account_type = None
    if "account_type" in data:
        account_type = data["account_type"]
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

    currency_code = None
    if "currency" in data:
        currency_code = data["currency"]
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

    if "is_active" in data and not isinstance(data["is_active"], bool):
        return jsonify({
            "success": False,
            "message": "is_active must be true or false.",
        }), 400

    membership = get_workspace_membership(workspace_id, user_id)
    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to update accounts.",
        }), 403

    try:
        account = FinancialAccount.query.filter_by(
            id=account_uuid,
            workspace_id=workspace_id,
        ).with_for_update().first()

        if account is None:
            return jsonify({
                "success": False,
                "message": "Account not found.",
            }), 404

        ledger = LedgerAccount.query.filter_by(
            financial_account_id=account.id,
            workspace_id=workspace_id,
        ).with_for_update().first()

        type_changed = (
            account_type is not None
            and account_type != account.account_type
        )
        currency_changed = (
            currency_code is not None
            and currency_code != account.currency_code
        )

        has_history = bool(
            ledger and JournalLine.query.filter_by(
                ledger_account_id=ledger.id
            ).first()
        )

        if has_history and (type_changed or currency_changed):
            return jsonify({
                "success": False,
                "message": (
                    "Account type and currency cannot be changed after "
                    "transactions have been recorded. Create a new account instead."
                ),
            }), 409

        currency = None
        if currency_code is not None:
            currency = db.session.get(Currency, currency_code)
            if currency is None or not currency.is_active:
                return jsonify({
                    "success": False,
                    "message": "The selected currency is unavailable.",
                }), 400

        changes = {}

        if name is not None and name != account.name:
            changes["old_name"] = account.name
            changes["new_name"] = name
            account.name = name
            if ledger:
                ledger.name = name

        if type_changed:
            account_class, normal_balance = ACCOUNT_TYPES[account_type]
            changes["old_account_type"] = account.account_type
            changes["new_account_type"] = account_type
            account.account_type = account_type
            if ledger:
                ledger.account_class = account_class
                ledger.normal_balance = normal_balance

        if currency_changed:
            changes["old_currency"] = account.currency_code
            changes["new_currency"] = currency_code
            account.currency_code = currency_code
            if ledger:
                ledger.currency_code = currency_code

        if "is_active" in data:
            active = data["is_active"]
            if active != account.is_active:
                changes["old_is_active"] = account.is_active
                changes["new_is_active"] = active
                account.is_active = active
                if ledger:
                    ledger.is_active = active

        if not changes:
            return jsonify({
                "success": True,
                "message": "No account changes were needed.",
                "account": {
                    "id": str(account.id),
                    "name": account.name,
                    "account_type": account.account_type,
                    "currency": account.currency_code,
                    "is_active": account.is_active,
                },
            }), 200

        if "new_is_active" in changes:
            action = (
                "financial_account_reopened"
                if changes["new_is_active"]
                else "financial_account_closed"
            )
        else:
            action = "financial_account_updated"

        audit = AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=action,
            resource_type="financial_account",
            resource_id=str(account.id),
            details=changes,
            ip_address=request.remote_addr,
        )
        db.session.add(audit)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Account updated successfully.",
            "account": {
                "id": str(account.id),
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency_code,
                "is_active": account.is_active,
            },
        }), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "The account update conflicts with existing data.",
        }), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Financial account update failed")
        return jsonify({
            "success": False,
            "message": "The account could not be updated.",
        }), 500


@finance_bp.delete("/accounts/<account_id>")
@jwt_required()
def delete_financial_account(account_id):
    try:
        account_uuid = UUID(account_id)
        workspace_id = UUID(request.args.get("workspace_id", ""))
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid account, workspace or authentication ID.",
        }), 400

    membership = get_workspace_membership(workspace_id, user_id)
    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to delete accounts.",
        }), 403

    try:
        account = FinancialAccount.query.filter_by(
            id=account_uuid,
            workspace_id=workspace_id,
        ).with_for_update().first()

        if account is None:
            return jsonify({
                "success": False,
                "message": "Account not found.",
            }), 404

        ledger = LedgerAccount.query.filter_by(
            financial_account_id=account.id,
            workspace_id=workspace_id,
        ).with_for_update().first()

        references = []

        if ledger and JournalLine.query.filter_by(
            ledger_account_id=ledger.id
        ).first():
            references.append("transaction history")

        if SavingsGoal.query.filter_by(
            financial_account_id=account.id,
            workspace_id=workspace_id,
        ).first():
            references.append("savings goals")

        if LoanDisbursement.query.filter_by(
            financial_account_id=account.id,
            workspace_id=workspace_id,
        ).first():
            references.append("loan disbursements")

        if LoanPayment.query.filter_by(
            financial_account_id=account.id,
            workspace_id=workspace_id,
        ).first():
            references.append("loan payments")

        if references:
            return jsonify({
                "success": False,
                "message": (
                    "This account has linked financial records and cannot "
                    "be permanently deleted. Close it instead."
                ),
                "references": references,
            }), 409

        audit = AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="financial_account_deleted",
            resource_type="financial_account",
            resource_id=str(account.id),
            details={
                "name": account.name,
                "account_type": account.account_type,
                "currency": account.currency_code,
            },
            ip_address=request.remote_addr,
        )
        db.session.add(audit)

        if ledger:
            db.session.delete(ledger)

        db.session.delete(account)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Unused account permanently deleted.",
        }), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": (
                "The account is still referenced by financial records. "
                "Close it instead."
            ),
        }), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Financial account deletion failed")
        return jsonify({
            "success": False,
            "message": "The account could not be deleted.",
        }), 500


def _parse_workspace_id(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _journal_entry_response(entry, include_lines=False):
    result = {
        "id": str(entry.id),
        "workspace_id": str(entry.workspace_id),
        "reference": entry.reference,
        "description": entry.description,
        "status": entry.status,
        "transaction_date": entry.transaction_date.isoformat(),
        "created_at": entry.created_at.isoformat(),
        "posted_at": (
            entry.posted_at.isoformat()
            if entry.posted_at else None
        ),
        "reversal_of_id": (
            str(entry.reversal_of_id)
            if entry.reversal_of_id else None
        ),
    }

    if include_lines:
        lines = (
            JournalLine.query
            .filter_by(journal_entry_id=entry.id)
            .order_by(JournalLine.id.asc())
            .all()
        )

        ledger_ids = {line.ledger_account_id for line in lines}
        ledger_accounts = {
            account.id: account
            for account in LedgerAccount.query.filter(
                LedgerAccount.id.in_(ledger_ids)
            ).all()
        } if ledger_ids else {}

        result["lines"] = [
            {
                "id": str(line.id),
                "ledger_account_id": str(line.ledger_account_id),
                "ledger_account_name": (
                    ledger_accounts[line.ledger_account_id].name
                    if line.ledger_account_id in ledger_accounts else None
                ),
                "ledger_account_code": (
                    ledger_accounts[line.ledger_account_id].code
                    if line.ledger_account_id in ledger_accounts else None
                ),
                "ledger_account_class": (
                    ledger_accounts[line.ledger_account_id].account_class
                    if line.ledger_account_id in ledger_accounts else None
                ),
                "normal_balance": (
                    ledger_accounts[line.ledger_account_id].normal_balance
                    if line.ledger_account_id in ledger_accounts else None
                ),
                "currency": line.currency_code,
                "debit_minor": line.debit_minor,
                "credit_minor": line.credit_minor,
                "functional_debit_minor": line.functional_debit_minor,
                "functional_credit_minor": line.functional_credit_minor,
                "exchange_rate": str(line.exchange_rate),
                "memo": line.memo,
            }
            for line in lines
        ]

    return result


@finance_bp.post("/entries")
@jwt_required()
def create_journal_entry():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    workspace_id = _parse_workspace_id(data.get("workspace_id"))

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
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
            "message": "You do not have permission to create transactions.",
        }), 403

    # Expense entries must use a valid workspace expense category.
    lines = data.get("lines")

    if not isinstance(lines, list):
        return jsonify({
            "success": False,
            "message": "Transaction lines must be a list.",
        }), 400

    line_ids = []

    try:
        for item in lines:
            if not isinstance(item, dict):
                raise ValueError()

            line_ids.append(UUID(str(item.get("ledger_account_id"))))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Every transaction line must have a valid ledger account ID.",
        }), 400

    expense_accounts = LedgerAccount.query.filter(
        LedgerAccount.workspace_id == workspace_id,
        LedgerAccount.account_class == "expense",
        LedgerAccount.id.in_(set(line_ids)),
    ).all()

    expense_account_ids = {account.id for account in expense_accounts}

    category_id_value = data.get("category_id")
    category_ledger = None

    if category_id_value is not None:
        try:
            category_id = UUID(str(category_id_value))
        except (ValueError, TypeError, AttributeError):
            return jsonify({
                "success": False,
                "message": "Invalid expense category ID.",
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
            }), 400

        category_ledger = LedgerAccount.query.filter_by(
            id=category.ledger_account_id,
            workspace_id=workspace_id,
            account_class="expense",
            normal_balance="debit",
            is_active=True,
        ).first()

        if category_ledger is None:
            return jsonify({
                "success": False,
                "message": "The selected expense category has no valid ledger account.",
            }), 400

        category_lines = [
            item for item in lines
            if str(item.get("ledger_account_id")) == str(category_ledger.id)
        ]

        if len(category_lines) != 1:
            return jsonify({
                "success": False,
                "message": "The transaction must contain exactly one line for the selected expense category.",
            }), 400

        category_line = category_lines[0]
        debit = category_line.get("debit_minor")
        credit = category_line.get("credit_minor")

        if (
            isinstance(debit, bool)
            or not isinstance(debit, int)
            or debit <= 0
            or isinstance(credit, bool)
            or credit != 0
        ):
            return jsonify({
                "success": False,
                "message": "The selected expense category must be debited.",
            }), 400

        if expense_account_ids - {category_ledger.id}:
            return jsonify({
                "success": False,
                "message": "An expense transaction cannot use another expense ledger alongside its selected category.",
            }), 400

    elif any(
        isinstance(item.get("debit_minor"), int)
        and not isinstance(item.get("debit_minor"), bool)
        and item["debit_minor"] > 0
        and UUID(str(item.get("ledger_account_id"))) in expense_account_ids
        for item in lines
    ):
        return jsonify({
            "success": False,
            "message": "Select an expense category before recording this expense.",
        }), 400

    date_value = data.get("transaction_date")

    if not isinstance(date_value, str):
        return jsonify({
            "success": False,
            "message": "A timezone-aware ISO 8601 transaction date is required.",
        }), 400

    try:
        transaction_date = datetime.fromisoformat(
            date_value.replace("Z", "+00:00")
        )
    except ValueError:
        return jsonify({
            "success": False,
            "message": "Invalid transaction date.",
        }), 400

    if transaction_date.tzinfo is None:
        return jsonify({
            "success": False,
            "message": "Transaction date must include a timezone.",
        }), 400

    try:
        entry = create_draft_entry(
            workspace_id=workspace_id,
            created_by=user_id,
            reference=data.get("reference"),
            description=data.get("description"),
            transaction_date=transaction_date,
            lines=data.get("lines"),
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Draft transaction created successfully.",
            "entry": _journal_entry_response(entry, include_lines=True),
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
            "message": "Transaction reference already exists in this workspace.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Journal entry creation failed")
        return jsonify({
            "success": False,
            "message": "Transaction could not be created.",
        }), 500


@finance_bp.get("/accounts/<account_id>/transactions")
@jwt_required()
def list_account_transactions(account_id):
    workspace_id = _parse_workspace_id(request.args.get("workspace_id"))

    if workspace_id is None:
        return jsonify({"success": False, "message": "A valid workspace ID is required."}), 400

    try:
        parsed_account_id = UUID(account_id)
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "message": "Invalid account or authentication ID."}), 400

    if get_workspace_membership(workspace_id, user_id) is None:
        return jsonify({"success": False, "message": "Workspace not found or access denied."}), 404

    try:
        limit = int(request.args.get("limit", "50"))
        if limit < 1 or limit > 100:
            raise ValueError()
    except (ValueError, TypeError):
        return jsonify({"success": False, "message": "Limit must be between 1 and 100."}), 400

    try:
        account = FinancialAccount.query.filter_by(
            id=parsed_account_id,
            workspace_id=workspace_id,
        ).first()

        if account is None:
            return jsonify({"success": False, "message": "Account not found."}), 404

        ledger = LedgerAccount.query.filter_by(
            financial_account_id=account.id,
            workspace_id=workspace_id,
        ).first()

        if ledger is None:
            return jsonify({"success": True, "count": 0, "transactions": []}), 200

        rows = (
            db.session.query(JournalEntry, JournalLine)
            .join(JournalLine, JournalLine.journal_entry_id == JournalEntry.id)
            .filter(
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status == "posted",
                JournalLine.ledger_account_id == ledger.id,
            )
            .order_by(
                JournalEntry.transaction_date.desc(),
                JournalEntry.created_at.desc(),
            )
            .limit(limit)
            .all()
        )

        transactions = [
            {
                "id": str(entry.id),
                "reference": entry.reference,
                "description": entry.description,
                "status": entry.status,
                "transaction_date": entry.transaction_date.isoformat(),
                "created_at": entry.created_at.isoformat(),
                "currency": line.currency_code,
                "debit_minor": line.debit_minor,
                "credit_minor": line.credit_minor,
                "memo": line.memo,
            }
            for entry, line in rows
        ]

        return jsonify({
            "success": True,
            "count": len(transactions),
            "transactions": transactions,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Account transaction listing failed")
        return jsonify({
            "success": False,
            "message": "Account transactions could not be loaded.",
        }), 500


@finance_bp.get("/entries")
@jwt_required()
def list_journal_entries():
    workspace_id = _parse_workspace_id(
        request.args.get("workspace_id")
    )

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
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

    status = request.args.get("status")

    if status and status not in {"draft", "posted", "reversed"}:
        return jsonify({
            "success": False,
            "message": "Invalid transaction status.",
        }), 400

    try:
        limit_value = request.args.get("limit", "50")
        limit = int(limit_value)

        if limit < 1 or limit > 100:
            raise ValueError()

    except (ValueError, TypeError):
        return jsonify({
            "success": False,
            "message": "Limit must be between 1 and 100.",
        }), 400

    try:
        query = JournalEntry.query.filter_by(
            workspace_id=workspace_id
        )

        if status:
            query = query.filter_by(status=status)

        entries = (
            query.order_by(
                JournalEntry.transaction_date.desc(),
                JournalEntry.created_at.desc(),
            )
            .limit(limit)
            .all()
        )

        return jsonify({
            "success": True,
            "count": len(entries),
            "entries": [
                _journal_entry_response(entry, include_lines=True)
                for entry in entries
            ],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Journal entry listing failed")
        return jsonify({
            "success": False,
            "message": "Transactions could not be loaded.",
        }), 500


@finance_bp.delete("/entries/<entry_id>")
@jwt_required()
def clear_draft_entry(entry_id):
    try:
        parsed_entry_id = UUID(entry_id)
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({"success": False, "message": "Invalid transaction or authentication ID."}), 400

    workspace_id = _parse_workspace_id(request.args.get("workspace_id"))
    if workspace_id is None:
        return jsonify({"success": False, "message": "A valid workspace_id is required."}), 400

    membership = get_workspace_membership(workspace_id, user_id)
    if membership is None:
        return jsonify({"success": False, "message": "Workspace not found or access denied."}), 404
    if membership.role == "viewer":
        return jsonify({"success": False, "message": "You do not have permission to clear transactions."}), 403

    entry = JournalEntry.query.filter_by(
        id=parsed_entry_id, workspace_id=workspace_id
    ).with_for_update().first()

    if entry is None:
        return jsonify({"success": False, "message": "Transaction not found."}), 404
    if entry.status != "draft":
        return jsonify({"success": False, "message": "Only draft transactions can be cleared."}), 400

    try:
        JournalLine.query.filter_by(journal_entry_id=entry.id).delete(
            synchronize_session=False
        )
        db.session.delete(entry)
        db.session.commit()
        return jsonify({"success": True, "message": "Draft transaction cleared."}), 200
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Draft transaction clearing failed")
        return jsonify({"success": False, "message": "Draft transaction could not be cleared."}), 500


@finance_bp.post("/entries/<entry_id>/post")
@jwt_required()
def post_journal_entry(entry_id):
    try:
        parsed_entry_id = UUID(entry_id)
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid transaction or authentication ID.",
        }), 400

    workspace_id = _parse_workspace_id(
        request.args.get("workspace_id")
    )

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace_id query parameter is required.",
        }), 400

    membership = get_workspace_membership(workspace_id, user_id)

    if membership is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to post transactions.",
        }), 403

    try:
        entry = post_entry(
            entry_id=parsed_entry_id,
            workspace_id=workspace_id,
            posted_by=user_id,
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Transaction posted successfully.",
            "entry": _journal_entry_response(entry),
        }), 200

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
            "message": "Transaction could not be posted because of a data conflict.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Journal entry posting failed")
        return jsonify({
            "success": False,
            "message": "Transaction could not be posted.",
        }), 500



@finance_bp.get("/accounts/balances")
@jwt_required()
def list_account_balances():
    workspace_id = _parse_workspace_id(
        request.args.get("workspace_id")
    )

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
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

    try:
        accounts = (
            LedgerAccount.query
            .filter_by(workspace_id=workspace_id)
            .order_by(LedgerAccount.code.asc())
            .all()
        )

        totals = (
            db.session.query(
                JournalLine.ledger_account_id,
                func.coalesce(func.sum(JournalLine.debit_minor), 0),
                func.coalesce(func.sum(JournalLine.credit_minor), 0),
                func.coalesce(
                    func.sum(JournalLine.functional_debit_minor), 0
                ),
                func.coalesce(
                    func.sum(JournalLine.functional_credit_minor), 0
                ),
            )
            .join(
                JournalEntry,
                JournalEntry.id == JournalLine.journal_entry_id,
            )
            .filter(
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status.in_(("posted", "reversed")),
            )
            .group_by(JournalLine.ledger_account_id)
            .all()
        )

        totals_by_account = {
            row[0]: {
                "debit": int(row[1]),
                "credit": int(row[2]),
                "functional_debit": int(row[3]),
                "functional_credit": int(row[4]),
            }
            for row in totals
        }

        results = []

        for account in accounts:
            values = totals_by_account.get(account.id, {
                "debit": 0,
                "credit": 0,
                "functional_debit": 0,
                "functional_credit": 0,
            })

            if account.normal_balance == "debit":
                balance = values["debit"] - values["credit"]
                functional_balance = (
                    values["functional_debit"]
                    - values["functional_credit"]
                )
            else:
                balance = values["credit"] - values["debit"]
                functional_balance = (
                    values["functional_credit"]
                    - values["functional_debit"]
                )

            results.append({
                "ledger_account_id": str(account.id),
                "code": account.code,
                "name": account.name,
                "account_class": account.account_class,
                "normal_balance": account.normal_balance,
                "currency": account.currency_code,
                "is_active": account.is_active,
                "debit_total_minor": values["debit"],
                "credit_total_minor": values["credit"],
                "balance_minor": balance,
                "functional_debit_total_minor": values["functional_debit"],
                "functional_credit_total_minor": values["functional_credit"],
                "functional_balance_minor": functional_balance,
            })

        return jsonify({
            "success": True,
            "workspace_id": str(workspace_id),
            "count": len(results),
            "accounts": results,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Account balance calculation failed")
        return jsonify({
            "success": False,
            "message": "Account balances could not be calculated.",
        }), 500



@finance_bp.post("/entries/<entry_id>/reverse")
@jwt_required()
def reverse_journal_entry(entry_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    try:
        parsed_entry_id = UUID(entry_id)
        user_id = UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid transaction or authentication ID.",
        }), 400

    workspace_id = _parse_workspace_id(data.get("workspace_id"))

    if workspace_id is None:
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

    if membership.role == "viewer":
        return jsonify({
            "success": False,
            "message": "You do not have permission to reverse transactions.",
        }), 403

    date_value = data.get("transaction_date")
    transaction_date = None

    if date_value is not None:
        if not isinstance(date_value, str):
            return jsonify({
                "success": False,
                "message": "Reversal date must be an ISO 8601 string.",
            }), 400

        try:
            transaction_date = datetime.fromisoformat(
                date_value.replace("Z", "+00:00")
            )
        except ValueError:
            return jsonify({
                "success": False,
                "message": "Invalid reversal date.",
            }), 400

        if transaction_date.tzinfo is None:
            return jsonify({
                "success": False,
                "message": "Reversal date must include a timezone.",
            }), 400

    try:
        reversal = reverse_entry(
            entry_id=parsed_entry_id,
            workspace_id=workspace_id,
            reversed_by=user_id,
            reference=data.get("reference"),
            description=data.get("description"),
            transaction_date=transaction_date,
        )

        db.session.commit()

        original = db.session.get(
            JournalEntry,
            parsed_entry_id,
        )

        return jsonify({
            "success": True,
            "message": "Transaction reversed successfully.",
            "original": _journal_entry_response(original),
            "reversal": _journal_entry_response(
                reversal,
                include_lines=True,
            ),
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
            "message": "Reversal reference already exists or this transaction has already been reversed.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Journal reversal failed")
        return jsonify({
            "success": False,
            "message": "Transaction could not be reversed.",
        }), 500

@finance_bp.get("/dashboard")
@jwt_required()
def dashboard_summary():
    workspace_id = _parse_workspace_id(
        request.args.get("workspace_id")
    )

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
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

    try:
        workspace = Workspace.query.filter_by(
            id=workspace_id
        ).first()

        if workspace is None:
            return jsonify({
                "success": False,
                "message": "Workspace not found.",
            }), 404

        profile = UserProfile.query.filter_by(
            user_id=user_id
        ).first()

        timezone_name = (
            profile.timezone if profile and profile.timezone else "UTC"
        )

        try:
            user_timezone = ZoneInfo(timezone_name)
        except Exception:
            user_timezone = ZoneInfo("UTC")
            timezone_name = "UTC"

        now_local = datetime.now(timezone.utc).astimezone(user_timezone)

        month_start_local = datetime(
            now_local.year,
            now_local.month,
            1,
            tzinfo=user_timezone,
        )

        if now_local.month == 12:
            next_month_local = datetime(
                now_local.year + 1, 1, 1, tzinfo=user_timezone
            )
        else:
            next_month_local = datetime(
                now_local.year,
                now_local.month + 1,
                1,
                tzinfo=user_timezone,
            )

        month_start_utc = month_start_local.astimezone(timezone.utc)
        next_month_utc = next_month_local.astimezone(timezone.utc)

        posted_statuses = ("posted", "reversed")

        # Current balances for every ledger account.
        balance_rows = (
            db.session.query(
                LedgerAccount.id,
                LedgerAccount.account_class,
                LedgerAccount.normal_balance,
                func.coalesce(func.sum(
                    case(
                        (
                            JournalEntry.id.is_not(None),
                            JournalLine.functional_debit_minor,
                        ),
                        else_=0,
                    )
                ), 0).label("debits"),
                func.coalesce(func.sum(
                    case(
                        (
                            JournalEntry.id.is_not(None),
                            JournalLine.functional_credit_minor,
                        ),
                        else_=0,
                    )
                ), 0).label("credits"),
            )
            .outerjoin(
                JournalLine,
                JournalLine.ledger_account_id == LedgerAccount.id,
            )
            .outerjoin(
                JournalEntry,
                (JournalEntry.id == JournalLine.journal_entry_id)
                & (JournalEntry.workspace_id == workspace_id)
                & (JournalEntry.status.in_(posted_statuses)),
            )
            .filter(LedgerAccount.workspace_id == workspace_id)
            .group_by(
                LedgerAccount.id,
                LedgerAccount.account_class,
                LedgerAccount.normal_balance,
            )
            .all()
        )

        functional_balances = {}
        total_assets = 0

        for row in balance_rows:
            debits = int(row.debits)
            credits = int(row.credits)

            balance = (
                debits - credits
                if row.normal_balance == "debit"
                else credits - debits
            )

            functional_balances[row.id] = balance

            if row.account_class == "asset":
                total_assets += balance

        # Savings total comes only from financial accounts
        # explicitly classified as savings.
        savings_rows = (
            db.session.query(LedgerAccount.id)
            .join(
                FinancialAccount,
                FinancialAccount.id == LedgerAccount.financial_account_id,
            )
            .filter(
                FinancialAccount.workspace_id == workspace_id,
                FinancialAccount.account_type == "savings",
                FinancialAccount.is_active.is_(True),
            )
            .all()
        )

        total_savings = sum(
            functional_balances.get(row.id, 0)
            for row in savings_rows
        )

        # Monthly income and expense are calculated from functional
        # currency journal lines. Reversal entries naturally offset
        # their original transactions.
        monthly = (
            db.session.query(
                func.coalesce(
                    func.sum(
                        case(
                            (
                                LedgerAccount.account_class == "income",
                                JournalLine.functional_credit_minor
                                - JournalLine.functional_debit_minor,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("income"),
                func.coalesce(
                    func.sum(
                        case(
                            (
                                LedgerAccount.account_class == "expense",
                                JournalLine.functional_debit_minor
                                - JournalLine.functional_credit_minor,
                            ),
                            else_=0,
                        )
                    ),
                    0,
                ).label("expenses"),
            )
            .join(
                JournalEntry,
                JournalEntry.id == JournalLine.journal_entry_id,
            )
            .join(
                LedgerAccount,
                LedgerAccount.id == JournalLine.ledger_account_id,
            )
            .filter(
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status.in_(posted_statuses),
                JournalEntry.transaction_date >= month_start_utc,
                JournalEntry.transaction_date < next_month_utc,
            )
            .one()
        )

        recent_entries = (
            JournalEntry.query
            .filter_by(workspace_id=workspace_id)
            .filter(JournalEntry.status.in_(posted_statuses))
            .order_by(
                JournalEntry.transaction_date.desc(),
                JournalEntry.created_at.desc(),
            )
            .limit(8)
            .all()
        )

        # Calculate dashboard amounts from actual financial-account movements.
        recent_amounts = {}
        if recent_entries:
            entry_ids = [entry.id for entry in recent_entries]

            recent_lines = (
                db.session.query(JournalLine, LedgerAccount)
                .join(
                    LedgerAccount,
                    LedgerAccount.id == JournalLine.ledger_account_id,
                )
                .filter(
                    JournalLine.journal_entry_id.in_(entry_ids),
                    LedgerAccount.financial_account_id.isnot(None),
                )
                .all()
            )

            account_movements = {}
            for line, ledger in recent_lines:
                key = (line.journal_entry_id, ledger.financial_account_id)
                movement = (
                    int(line.functional_debit_minor or 0)
                    - int(line.functional_credit_minor or 0)
                )
                account_movements[key] = (
                    account_movements.get(key, 0) + movement
                )

            movements_by_entry = {}
            for (entry_id, account_id), movement in account_movements.items():
                if movement:
                    movements_by_entry.setdefault(entry_id, []).append(movement)

            for entry in recent_entries:
                movements = movements_by_entry.get(entry.id, [])
                incoming = sum(max(value, 0) for value in movements)
                outgoing = sum(max(-value, 0) for value in movements)

                if len(movements) >= 2 and incoming and outgoing:
                    direction = "transfer"
                    amount = min(incoming, outgoing)
                else:
                    net = incoming - outgoing
                    direction = (
                        "inflow" if net > 0
                        else "outflow" if net < 0
                        else "unknown"
                    )
                    amount = abs(net)

                recent_amounts[entry.id] = {
                    "amount_minor": amount if direction != "unknown" else None,
                    "direction": direction,
                }

        currency = Currency.query.filter_by(
            code=workspace.functional_currency
        ).first()

        return jsonify({
            "success": True,
            "workspace_id": str(workspace_id),
            "currency": workspace.functional_currency,
            "minor_unit": currency.minor_unit if currency else 2,
            "timezone": timezone_name,
            "period": {
                "start": month_start_local.date().isoformat(),
                "end": next_month_local.date().isoformat(),
            },
            "summary": {
                "total_assets_minor": total_assets,
                "monthly_income_minor": int(monthly.income),
                "monthly_expenses_minor": int(monthly.expenses),
                "total_savings_minor": total_savings,
            },
            "recent_transactions": [
                {
                    **_journal_entry_response(entry),
                    **recent_amounts.get(
                        entry.id,
                        {"amount_minor": None, "direction": "unknown"},
                    ),
                }
                for entry in recent_entries
            ],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Dashboard summary calculation failed"
        )
        return jsonify({
            "success": False,
            "message": "Dashboard summary could not be calculated.",
        }), 500
