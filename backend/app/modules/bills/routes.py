from datetime import date, datetime, time, timezone
from uuid import UUID, uuid4

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy import func

from app.extensions import db
from app.models import (
    AuditLog,
    Bill,
    BillPayment,
    ExpenseCategory,
    FinancialAccount,
    LedgerAccount,
    JournalEntry,
    Workspace,
    WorkspaceMember,
)
from app.services.accounting import (
    AccountingError,
    create_draft_entry,
    post_entry,
)
from . import bills_bp


def _user_id():
    try:
        return UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return None


def _uuid(value):
    try:
        return UUID(str(value))
    except (ValueError, TypeError, AttributeError):
        return None


def _parse_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _membership(workspace_id, user_id):
    return WorkspaceMember.query.filter_by(
        workspace_id=workspace_id,
        user_id=user_id,
    ).first()


def _authorize(workspace_id, user_id):
    if workspace_id is None or user_id is None:
        return None, (jsonify({
            "success": False,
            "message": "A valid workspace ID and authentication are required.",
        }), 400)

    membership = _membership(workspace_id, user_id)

    if membership is None:
        return None, (jsonify({
            "success": False,
            "message": "Workspace not found or access denied.",
        }), 404)

    if membership.role == "viewer":
        return None, (jsonify({
            "success": False,
            "message": "You do not have permission to manage bills.",
        }), 403)

    return membership, None


def _payment_total(bill_id):
    return (
        db.session.query(
            func.coalesce(func.sum(BillPayment.amount_minor), 0)
        )
        .join(
            JournalEntry,
            (JournalEntry.id == BillPayment.journal_entry_id)
            & (JournalEntry.workspace_id == BillPayment.workspace_id),
        )
        .filter(
            BillPayment.bill_id == bill_id,
            JournalEntry.status == "posted",
        )
        .scalar()
    )


def _bill_response(bill):
    paid = int(_payment_total(bill.id))
    remaining = max(0, bill.amount_minor - paid)
    today = datetime.now(timezone.utc).date()

    if remaining == 0:
        status = "paid"
    elif paid > 0:
        status = "partially_paid"
    elif bill.due_date < today:
        status = "overdue"
    else:
        status = "unpaid"

    return {
        "id": str(bill.id),
        "workspace_id": str(bill.workspace_id),
        "category_id": str(bill.category_id),
        "payee_name": bill.payee_name,
        "title": bill.title,
        "description": bill.description,
        "reference": bill.reference,
        "amount_minor": bill.amount_minor,
        "amount_paid_minor": paid,
        "remaining_minor": remaining,
        "currency_code": bill.currency_code,
        "bill_date": bill.bill_date.isoformat(),
        "due_date": bill.due_date.isoformat(),
        "status": status,
        "journal_entry_id": str(bill.journal_entry_id),
        "created_at": bill.created_at.isoformat(),
        "updated_at": bill.updated_at.isoformat(),
    }


def _payment_response(payment):
    entry = db.session.get(JournalEntry, payment.journal_entry_id)

    return {
        "id": str(payment.id),
        "status": entry.status if entry else "unknown",
        "bill_id": str(payment.bill_id),
        "financial_account_id": str(payment.financial_account_id),
        "amount_minor": payment.amount_minor,
        "currency_code": payment.currency_code,
        "payment_date": payment.payment_date.isoformat(),
        "reference": payment.reference,
        "notes": payment.notes,
        "journal_entry_id": str(payment.journal_entry_id),
        "created_at": payment.created_at.isoformat(),
    }


def _text(value, field, maximum, required=True):
    if value is None and not required:
        return None

    if not isinstance(value, str):
        raise ValueError(f"{field} must be text.")

    value = value.strip()

    if required and not value:
        raise ValueError(f"{field} is required.")

    if len(value) > maximum:
        raise ValueError(f"{field} must not exceed {maximum} characters.")

    return value or None


@bills_bp.get("")
@jwt_required()
def list_bills():
    workspace_id = _uuid(request.args.get("workspace_id"))
    user_id = _user_id()

    _, error = _authorize(workspace_id, user_id)
    if error:
        return error

    bills = (
        Bill.query
        .filter_by(workspace_id=workspace_id)
        .order_by(Bill.due_date.asc(), Bill.created_at.desc())
        .all()
    )

    results = [_bill_response(bill) for bill in bills]

    return jsonify({
        "success": True,
        "count": len(results),
        "bills": results,
    }), 200


@bills_bp.post("")
@jwt_required()
def create_bill():
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    workspace_id = _uuid(data.get("workspace_id"))
    user_id = _user_id()

    _, error = _authorize(workspace_id, user_id)
    if error:
        return error

    category_id = _uuid(data.get("category_id"))
    amount = data.get("amount_minor")
    bill_date = _parse_date(data.get("bill_date"))
    due_date = _parse_date(data.get("due_date"))

    if category_id is None:
        return jsonify({
            "success": False,
            "message": "A valid expense category is required.",
        }), 400

    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        return jsonify({
            "success": False,
            "message": "amount_minor must be a positive integer.",
        }), 400

    if bill_date is None or due_date is None:
        return jsonify({
            "success": False,
            "message": "Valid bill_date and due_date are required (YYYY-MM-DD).",
        }), 400

    if due_date < bill_date:
        return jsonify({
            "success": False,
            "message": "The due date cannot be before the bill date.",
        }), 400

    try:
        payee = _text(data.get("payee_name"), "Payee name", 150)
        title = _text(data.get("title"), "Bill title", 150)
        description = _text(
            data.get("description"), "Description", 2000, required=False
        )
        external_reference = _text(
            data.get("reference"), "Reference", 100, required=False
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    try:
        workspace = db.session.get(Workspace, workspace_id)

        category = ExpenseCategory.query.filter_by(
            id=category_id,
            workspace_id=workspace_id,
            is_active=True,
        ).first()

        if category is None:
            return jsonify({
                "success": False,
                "message": "Active expense category not found.",
            }), 400

        expense_ledger = LedgerAccount.query.filter_by(
            id=category.ledger_account_id,
            workspace_id=workspace_id,
        ).with_for_update().first()

        payable_ledger = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            code="2100",
            is_system=True,
        ).with_for_update().first()

        currency = workspace.functional_currency

        if (
            expense_ledger is None
            or not expense_ledger.is_active
            or expense_ledger.account_class != "expense"
            or expense_ledger.normal_balance != "debit"
            or expense_ledger.currency_code != currency
        ):
            return jsonify({
                "success": False,
                "message": "The category does not have a valid expense ledger in the workspace currency.",
            }), 400

        if (
            payable_ledger is None
            or not payable_ledger.is_active
            or payable_ledger.account_class != "liability"
            or payable_ledger.normal_balance != "credit"
            or payable_ledger.currency_code != currency
            or payable_ledger.financial_account_id is not None
        ):
            return jsonify({
                "success": False,
                "message": "The workspace Accounts Payable ledger (2100) is missing or incorrectly configured.",
            }), 400

        supplied_currency = data.get("currency_code", currency)
        if supplied_currency != currency:
            return jsonify({
                "success": False,
                "message": f"Bills currently use the workspace currency ({currency}).",
            }), 400

        bill_id = uuid4()
        journal_reference = f"BILL-{bill_id.hex[:24].upper()}"
        transaction_date = datetime.combine(
            bill_date, time.min, tzinfo=timezone.utc
        )

        entry = create_draft_entry(
            workspace_id=workspace_id,
            created_by=user_id,
            reference=journal_reference,
            description=f"Bill: {title} - {payee}",
            transaction_date=transaction_date,
            lines=[
                {
                    "ledger_account_id": str(expense_ledger.id),
                    "debit_minor": amount,
                    "credit_minor": 0,
                    "memo": f"Bill expense: {title}",
                },
                {
                    "ledger_account_id": str(payable_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": amount,
                    "memo": f"Amount payable to {payee}",
                },
            ],
        )

        post_entry(
            entry_id=entry.id,
            workspace_id=workspace_id,
            posted_by=user_id,
        )

        bill = Bill(
            id=bill_id,
            workspace_id=workspace_id,
            category_id=category_id,
            payee_name=payee,
            title=title,
            description=description,
            reference=external_reference,
            amount_minor=amount,
            currency_code=currency,
            bill_date=bill_date,
            due_date=due_date,
            journal_entry_id=entry.id,
            created_by=user_id,
        )

        db.session.add(bill)
        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="bill_created",
            resource_type="bill",
            resource_id=str(bill.id),
            details={
                "title": title,
                "payee_name": payee,
                "amount_minor": amount,
                "currency_code": currency,
                "category_id": str(category_id),
                "journal_entry_id": str(entry.id),
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Bill created successfully.",
            "bill": _bill_response(bill),
        }), 201

    except (AccountingError, ValueError) as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Bill creation failed")
        return jsonify({
            "success": False,
            "message": "Unable to create bill. No accounting changes were saved.",
        }), 500


@bills_bp.get("/<bill_id>")
@jwt_required()
def get_bill(bill_id):
    workspace_id = _uuid(request.args.get("workspace_id"))
    user_id = _user_id()

    _, error = _authorize(workspace_id, user_id)
    if error:
        return error

    bill_uuid = _uuid(bill_id)
    if bill_uuid is None:
        return jsonify({
            "success": False,
            "message": "Invalid bill ID.",
        }), 400

    bill = Bill.query.filter_by(
        id=bill_uuid,
        workspace_id=workspace_id,
    ).first()

    if bill is None:
        return jsonify({
            "success": False,
            "message": "Bill not found.",
        }), 404

    payments = (
        BillPayment.query
        .filter_by(workspace_id=workspace_id, bill_id=bill.id)
        .order_by(BillPayment.payment_date.desc(), BillPayment.created_at.desc())
        .all()
    )

    result = _bill_response(bill)
    result["payments"] = [_payment_response(p) for p in payments]

    return jsonify({"success": True, "bill": result}), 200


@bills_bp.post("/<bill_id>/payments")
@jwt_required()
def record_bill_payment(bill_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    workspace_id = _uuid(data.get("workspace_id"))
    user_id = _user_id()

    _, error = _authorize(workspace_id, user_id)
    if error:
        return error

    bill_uuid = _uuid(bill_id)
    account_id = _uuid(data.get("financial_account_id"))
    amount = data.get("amount_minor")
    payment_date = _parse_date(data.get("payment_date"))

    if bill_uuid is None or account_id is None:
        return jsonify({
            "success": False,
            "message": "Valid bill and financial account IDs are required.",
        }), 400

    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0:
        return jsonify({
            "success": False,
            "message": "amount_minor must be a positive integer.",
        }), 400

    if payment_date is None:
        return jsonify({
            "success": False,
            "message": "A valid payment_date is required (YYYY-MM-DD).",
        }), 400

    try:
        reference = _text(
            data.get("reference"), "Reference", 100, required=False
        )
        notes = _text(
            data.get("notes"), "Notes", 2000, required=False
        )
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    try:
        bill = Bill.query.filter_by(
            id=bill_uuid,
            workspace_id=workspace_id,
        ).with_for_update().first()

        if bill is None:
            return jsonify({
                "success": False,
                "message": "Bill not found.",
            }), 404

        paid = int(_payment_total(bill.id))
        remaining = bill.amount_minor - paid

        if remaining <= 0:
            return jsonify({
                "success": False,
                "message": "This bill has already been fully paid.",
            }), 409

        if amount > remaining:
            return jsonify({
                "success": False,
                "message": "Payment exceeds the outstanding bill balance.",
                "remaining_minor": remaining,
            }), 400

        account = FinancialAccount.query.filter_by(
            id=account_id,
            workspace_id=workspace_id,
        ).with_for_update().first()

        if (
            account is None
            or not account.is_active
            or account.account_type not in {"cash", "bank", "mobile_money"}
        ):
            return jsonify({
                "success": False,
                "message": "Choose an active cash, bank or mobile money account.",
            }), 400

        if account.currency_code != bill.currency_code:
            return jsonify({
                "success": False,
                "message": "Payment account currency must match the bill currency.",
            }), 400

        cash_ledger = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            financial_account_id=account.id,
        ).with_for_update().first()

        payable_ledger = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            code="2100",
            is_system=True,
        ).with_for_update().first()

        if (
            cash_ledger is None
            or not cash_ledger.is_active
            or cash_ledger.account_class != "asset"
            or cash_ledger.normal_balance != "debit"
            or cash_ledger.currency_code != bill.currency_code
        ):
            return jsonify({
                "success": False,
                "message": "The selected account has no valid active asset ledger.",
            }), 400

        if (
            payable_ledger is None
            or not payable_ledger.is_active
            or payable_ledger.account_class != "liability"
            or payable_ledger.normal_balance != "credit"
            or payable_ledger.currency_code != bill.currency_code
            or payable_ledger.financial_account_id is not None
        ):
            return jsonify({
                "success": False,
                "message": "The Accounts Payable ledger is unavailable.",
            }), 400

        payment_id = uuid4()
        journal_reference = f"BPAY-{payment_id.hex[:24].upper()}"

        entry = create_draft_entry(
            workspace_id=workspace_id,
            created_by=user_id,
            reference=journal_reference,
            description=f"Payment for bill: {bill.title}",
            transaction_date=datetime.combine(
                payment_date, time.min, tzinfo=timezone.utc
            ),
            lines=[
                {
                    "ledger_account_id": str(payable_ledger.id),
                    "debit_minor": amount,
                    "credit_minor": 0,
                    "memo": f"Payment against {bill.title}",
                },
                {
                    "ledger_account_id": str(cash_ledger.id),
                    "debit_minor": 0,
                    "credit_minor": amount,
                    "memo": f"Paid to {bill.payee_name}",
                },
            ],
        )

        post_entry(
            entry_id=entry.id,
            workspace_id=workspace_id,
            posted_by=user_id,
        )

        payment = BillPayment(
            id=payment_id,
            workspace_id=workspace_id,
            bill_id=bill.id,
            financial_account_id=account.id,
            amount_minor=amount,
            currency_code=bill.currency_code,
            payment_date=payment_date,
            reference=reference,
            notes=notes,
            journal_entry_id=entry.id,
            created_by=user_id,
        )

        db.session.add(payment)
        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="bill_payment_recorded",
            resource_type="bill_payment",
            resource_id=str(payment.id),
            details={
                "bill_id": str(bill.id),
                "amount_minor": amount,
                "currency_code": bill.currency_code,
                "financial_account_id": str(account.id),
                "journal_entry_id": str(entry.id),
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Bill payment recorded successfully.",
            "payment": _payment_response(payment),
            "bill": _bill_response(bill),
        }), 201

    except (AccountingError, ValueError) as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Bill payment failed")
        return jsonify({
            "success": False,
            "message": "Unable to record payment. No accounting changes were saved.",
        }), 500
