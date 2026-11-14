from uuid import UUID
from datetime import datetime, timezone

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

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
)

from . import finance_bp
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

        result["lines"] = [
            {
                "id": str(line.id),
                "ledger_account_id": str(line.ledger_account_id),
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
                _journal_entry_response(entry)
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
