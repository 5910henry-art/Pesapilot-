from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation
from uuid import UUID

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.extensions import db
from app.models import (AuditLog, PersonalLoan, Workspace, WorkspaceMember, FinancialAccount, LedgerAccount, LoanDisbursement, JournalEntry, JournalLine)
from app.models.loan_repayments import LoanInstallment, LoanPayment, LoanPaymentAllocation
from app.services.loan_schedules import build_repayment_schedule
from app.services.accounting import create_draft_entry, post_entry
from app.services.loan_repayments import (
    build_payment_allocations,
    confirm_loan_payment,
)

from . import loans_bp


MAX_MINOR_UNITS = 9_223_372_036_854_775_807

LOAN_TYPES = {
    "soft", "interest_free", "interest_bearing",
    "emergency", "business", "personal",
}

INTEREST_METHODS = {"none", "flat", "reducing"}

FREQUENCIES = {
    "weekly", "biweekly", "monthly", "quarterly", "one_time",
}


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


def _parse_date(value):
    if not isinstance(value, str):
        return None
    try:
        return date.fromisoformat(value)
    except ValueError:
        return None


def _loan_response(loan):
    return {
        "id": str(loan.id),
        "workspace_id": str(loan.workspace_id),
        "direction": loan.direction,
        "counterparty_name": loan.counterparty_name,
        "counterparty_phone": loan.counterparty_phone,
        "loan_type": loan.loan_type,
        "principal_minor": loan.principal_minor,
        "currency_code": loan.currency_code,
        "interest_method": loan.interest_method,
        "interest_rate_percent": str(loan.interest_rate_percent),
        "repayment_frequency": loan.repayment_frequency,
        "term_count": loan.term_count,
        "installment_minor": loan.installment_minor,
        "start_date": loan.start_date.isoformat(),
        "due_date": loan.due_date.isoformat(),
        "status": loan.status,
        "description": loan.description,
        "created_at": loan.created_at.isoformat(),
        "updated_at": loan.updated_at.isoformat(),
    }


@loans_bp.get("")
@jwt_required()
def list_personal_loans():
    workspace_id = _workspace_id(request.args.get("workspace_id"))
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
        loans = (
            PersonalLoan.query
            .filter_by(workspace_id=workspace_id)
            .order_by(PersonalLoan.created_at.desc())
            .all()
        )

        return jsonify({
            "success": True,
            "count": len(loans),
            "loans": [_loan_response(loan) for loan in loans],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Personal loans listing failed")
        return jsonify({
            "success": False,
            "message": "Loans could not be loaded.",
        }), 500


@loans_bp.post("")
@jwt_required()
def create_personal_loan():
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
            "message": "Viewers cannot create personal loans.",
        }), 403

    workspace = db.session.get(Workspace, workspace_id)

    if workspace is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found.",
        }), 404

    direction = data.get("direction")
    counterparty_name = data.get("counterparty_name")
    counterparty_phone = data.get("counterparty_phone")
    loan_type = data.get("loan_type")
    principal = data.get("principal_minor")
    interest_method = data.get("interest_method", "none")
    interest_rate_value = data.get("interest_rate_percent", "0")
    frequency = data.get("repayment_frequency")
    term_count = data.get("term_count")
    installment = data.get("installment_minor")
    description = data.get("description")
    currency_code = data.get(
        "currency_code",
        workspace.functional_currency,
    )

    if direction not in {"borrowed", "lent"}:
        return jsonify({
            "success": False,
            "message": "Direction must be borrowed or lent.",
        }), 400

    if (
        not isinstance(counterparty_name, str)
        or not counterparty_name.strip()
        or len(counterparty_name.strip()) > 150
    ):
        return jsonify({
            "success": False,
            "message": "Counterparty name is required and must not exceed 150 characters.",
        }), 400

    if counterparty_phone is not None and (
        not isinstance(counterparty_phone, str)
        or len(counterparty_phone.strip()) > 30
    ):
        return jsonify({
            "success": False,
            "message": "Counterparty phone must not exceed 30 characters.",
        }), 400

    if loan_type not in LOAN_TYPES:
        return jsonify({
            "success": False,
            "message": "Invalid loan type.",
        }), 400

    if (
        isinstance(principal, bool)
        or not isinstance(principal, int)
        or principal <= 0
        or principal > MAX_MINOR_UNITS
    ):
        return jsonify({
            "success": False,
            "message": "Principal must be a positive integer in minor currency units.",
        }), 400

    if interest_method not in INTEREST_METHODS:
        return jsonify({
            "success": False,
            "message": "Invalid interest method.",
        }), 400

    try:
        interest_rate = Decimal(str(interest_rate_value))
    except (InvalidOperation, ValueError, TypeError):
        interest_rate = None

    if (
        interest_rate is None
        or not interest_rate.is_finite()
        or interest_rate < 0
        or interest_rate > Decimal("1000")
    ):
        return jsonify({
            "success": False,
            "message": "Interest rate must be between 0 and 1000 percent.",
        }), 400

    if interest_method == "none" and interest_rate != 0:
        return jsonify({
            "success": False,
            "message": "Interest rate must be zero when interest method is none.",
        }), 400

    if loan_type == "interest_free" and (
        interest_method != "none" or interest_rate != 0
    ):
        return jsonify({
            "success": False,
            "message": "Interest-free loans cannot have an interest rate.",
        }), 400

    if frequency not in FREQUENCIES:
        return jsonify({
            "success": False,
            "message": "Invalid repayment frequency.",
        }), 400

    if (
        isinstance(term_count, bool)
        or not isinstance(term_count, int)
        or term_count <= 0
        or term_count > 1200
    ):
        return jsonify({
            "success": False,
            "message": "Term count must be an integer between 1 and 1200.",
        }), 400

    if installment is not None and (
        isinstance(installment, bool)
        or not isinstance(installment, int)
        or installment <= 0
        or installment > MAX_MINOR_UNITS
    ):
        return jsonify({
            "success": False,
            "message": "Installment amount must be a positive integer in minor units.",
        }), 400

    if description is not None and (
        not isinstance(description, str) or len(description) > 2000
    ):
        return jsonify({
            "success": False,
            "message": "Description must not exceed 2000 characters.",
        }), 400

    start_date = _parse_date(data.get("start_date"))
    due_date = _parse_date(data.get("due_date"))

    if start_date is None or due_date is None:
        return jsonify({
            "success": False,
            "message": "Valid start_date and due_date are required in YYYY-MM-DD format.",
        }), 400

    if due_date < start_date:
        return jsonify({
            "success": False,
            "message": "Due date cannot be before the start date.",
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
            "message": "Loan currency must match the workspace functional currency.",
        }), 400

    try:
        loan = PersonalLoan(
            workspace_id=workspace_id,
            created_by=user_id,
            direction=direction,
            counterparty_name=counterparty_name.strip(),
            counterparty_phone=(
                counterparty_phone.strip()
                if counterparty_phone else None
            ),
            loan_type=loan_type,
            principal_minor=principal,
            currency_code=currency_code,
            interest_method=interest_method,
            interest_rate_percent=interest_rate,
            repayment_frequency=frequency,
            term_count=term_count,
            installment_minor=installment,
            start_date=start_date,
            due_date=due_date,
            status="draft",
            description=description.strip() if description else None,
        )

        db.session.add(loan)
        db.session.flush()

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="personal_loan_created",
            resource_type="personal_loan",
            resource_id=str(loan.id),
            details={
                "direction": direction,
                "loan_type": loan_type,
                "principal_minor": principal,
                "currency": currency_code,
                "counterparty_name": counterparty_name.strip(),
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Personal loan recorded successfully.",
            "loan": _loan_response(loan),
        }), 201

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "The loan could not be saved because some referenced data is invalid.",
        }), 400

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Personal loan creation failed")
        return jsonify({
            "success": False,
            "message": "Personal loan could not be created.",
        }), 500



@loans_bp.patch("/<uuid:loan_id>")
@jwt_required()
def update_personal_loan(loan_id):
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

    if membership.role not in {"owner", "admin"}:
        return jsonify({
            "success": False,
            "message": "Only workspace owners and admins can edit personal loans.",
        }), 403

    workspace = db.session.get(Workspace, workspace_id)

    if workspace is None:
        return jsonify({
            "success": False,
            "message": "Workspace not found.",
        }), 404

    loan = (
        PersonalLoan.query
        .filter_by(id=loan_id, workspace_id=workspace_id)
        .with_for_update()
        .first()
    )

    if loan is None:
        return jsonify({
            "success": False,
            "message": "Personal loan not found.",
        }), 404

    if loan.status != "draft":
        return jsonify({
            "success": False,
            "message": "Only draft loans can be edited.",
        }), 409

    if data.get("direction", loan.direction) != loan.direction:
        return jsonify({
            "success": False,
            "message": "Loan direction cannot be changed.",
        }), 400

    currency_code = data.get("currency_code", loan.currency_code)

    if (
        not isinstance(currency_code, str)
        or currency_code.strip().upper() != loan.currency_code
        or loan.currency_code != workspace.functional_currency
    ):
        return jsonify({
            "success": False,
            "message": "Loan currency cannot be changed and must match the workspace functional currency.",
        }), 400

    # Draft loans must not have financial activity.
    if LoanInstallment.query.filter_by(personal_loan_id=loan.id).first():
        return jsonify({
            "success": False,
            "message": "This loan already has repayment schedule records and cannot be edited.",
        }), 409

    if LoanPayment.query.filter_by(personal_loan_id=loan.id).first():
        return jsonify({
            "success": False,
            "message": "This loan already has repayment records and cannot be edited.",
        }), 409

    if LoanDisbursement.query.filter_by(
        workspace_id=workspace_id,
        personal_loan_id=loan.id,
    ).first():
        return jsonify({
            "success": False,
            "message": "This loan has already been disbursed and cannot be edited.",
        }), 409

    counterparty_name = data.get("counterparty_name")
    counterparty_phone = data.get("counterparty_phone")
    loan_type = data.get("loan_type")
    principal = data.get("principal_minor")
    interest_method = data.get("interest_method", "none")
    interest_rate_value = data.get("interest_rate_percent", "0")
    frequency = data.get("repayment_frequency")
    term_count = data.get("term_count")
    installment = data.get("installment_minor")
    description = data.get("description")

    if (
        not isinstance(counterparty_name, str)
        or not counterparty_name.strip()
        or len(counterparty_name.strip()) > 150
    ):
        return jsonify({
            "success": False,
            "message": "Counterparty name is required and must not exceed 150 characters.",
        }), 400

    if counterparty_phone is not None and (
        not isinstance(counterparty_phone, str)
        or len(counterparty_phone.strip()) > 30
    ):
        return jsonify({
            "success": False,
            "message": "Counterparty phone must not exceed 30 characters.",
        }), 400

    if loan_type not in LOAN_TYPES:
        return jsonify({
            "success": False,
            "message": "Invalid loan type.",
        }), 400

    if (
        isinstance(principal, bool)
        or not isinstance(principal, int)
        or principal <= 0
        or principal > MAX_MINOR_UNITS
    ):
        return jsonify({
            "success": False,
            "message": "Principal must be a positive integer in minor currency units.",
        }), 400

    if interest_method not in INTEREST_METHODS:
        return jsonify({
            "success": False,
            "message": "Invalid interest method.",
        }), 400

    try:
        interest_rate = Decimal(str(interest_rate_value))
    except (InvalidOperation, ValueError, TypeError):
        interest_rate = None

    if (
        interest_rate is None
        or not interest_rate.is_finite()
        or interest_rate < 0
        or interest_rate > Decimal("1000")
    ):
        return jsonify({
            "success": False,
            "message": "Interest rate must be between 0 and 1000 percent.",
        }), 400

    if interest_method == "none" and interest_rate != 0:
        return jsonify({
            "success": False,
            "message": "Interest rate must be zero when interest method is none.",
        }), 400

    if loan_type == "interest_free" and (
        interest_method != "none" or interest_rate != 0
    ):
        return jsonify({
            "success": False,
            "message": "Interest-free loans cannot have an interest rate.",
        }), 400

    if frequency not in FREQUENCIES:
        return jsonify({
            "success": False,
            "message": "Invalid repayment frequency.",
        }), 400

    if (
        isinstance(term_count, bool)
        or not isinstance(term_count, int)
        or term_count <= 0
        or term_count > 1200
    ):
        return jsonify({
            "success": False,
            "message": "Term count must be an integer between 1 and 1200.",
        }), 400

    if installment is not None and (
        isinstance(installment, bool)
        or not isinstance(installment, int)
        or installment <= 0
        or installment > MAX_MINOR_UNITS
    ):
        return jsonify({
            "success": False,
            "message": "Installment amount must be a positive integer in minor currency units.",
        }), 400

    if description is not None and (
        not isinstance(description, str) or len(description) > 2000
    ):
        return jsonify({
            "success": False,
            "message": "Description must not exceed 2000 characters.",
        }), 400

    start_date = _parse_date(data.get("start_date"))
    due_date = _parse_date(data.get("due_date"))

    if start_date is None or due_date is None:
        return jsonify({
            "success": False,
            "message": "Valid start_date and due_date are required in YYYY-MM-DD format.",
        }), 400

    if due_date < start_date:
        return jsonify({
            "success": False,
            "message": "Due date cannot be before the start date.",
        }), 400

    before = {
        "counterparty_name": loan.counterparty_name,
        "counterparty_phone": loan.counterparty_phone,
        "loan_type": loan.loan_type,
        "principal_minor": loan.principal_minor,
        "interest_method": loan.interest_method,
        "interest_rate_percent": str(loan.interest_rate_percent),
        "repayment_frequency": loan.repayment_frequency,
        "term_count": loan.term_count,
        "installment_minor": loan.installment_minor,
        "start_date": loan.start_date.isoformat(),
        "due_date": loan.due_date.isoformat(),
        "description": loan.description,
    }

    try:
        loan.counterparty_name = counterparty_name.strip()
        loan.counterparty_phone = (
            counterparty_phone.strip() if counterparty_phone else None
        )
        loan.loan_type = loan_type
        loan.principal_minor = principal
        loan.interest_method = interest_method
        loan.interest_rate_percent = interest_rate
        loan.repayment_frequency = frequency
        loan.term_count = term_count
        loan.installment_minor = installment
        loan.start_date = start_date
        loan.due_date = due_date
        loan.description = description.strip() if description else None

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="personal_loan_updated",
            resource_type="personal_loan",
            resource_id=str(loan.id),
            details={
                "before": before,
                "after": {
                    "counterparty_name": loan.counterparty_name,
                    "counterparty_phone": loan.counterparty_phone,
                    "loan_type": loan.loan_type,
                    "principal_minor": loan.principal_minor,
                    "interest_method": loan.interest_method,
                    "interest_rate_percent": str(loan.interest_rate_percent),
                    "repayment_frequency": loan.repayment_frequency,
                    "term_count": loan.term_count,
                    "installment_minor": loan.installment_minor,
                    "start_date": loan.start_date.isoformat(),
                    "due_date": loan.due_date.isoformat(),
                    "description": loan.description,
                },
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Draft personal loan updated successfully.",
            "loan": _loan_response(loan),
        }), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "The loan could not be updated because some referenced data is invalid.",
        }), 400

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Personal loan update failed")
        return jsonify({
            "success": False,
            "message": "Personal loan could not be updated.",
        }), 500



@loans_bp.post("/<uuid:loan_id>/activate")
@jwt_required()
def activate_personal_loan(loan_id):
    data = request.get_json(silent=True) or {}
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

    if membership.role not in {"owner", "admin"}:
        return jsonify({
            "success": False,
            "message": "Only workspace owners and admins can activate loans.",
        }), 403

    try:
        loan = (
            PersonalLoan.query
            .filter_by(id=loan_id, workspace_id=workspace_id)
            .with_for_update()
            .first()
        )

        if loan is None:
            return jsonify({
                "success": False,
                "message": "Loan not found.",
            }), 404

        if loan.status != "draft":
            return jsonify({
                "success": False,
                "message": "Only draft loans can be activated.",
            }), 409

        existing_count = LoanInstallment.query.filter_by(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
        ).count()

        if existing_count:
            return jsonify({
                "success": False,
                "message": "A repayment schedule already exists for this loan.",
            }), 409

        schedule = build_repayment_schedule(loan)

        for item in schedule:
            db.session.add(LoanInstallment(
                workspace_id=workspace_id,
                personal_loan_id=loan.id,
                installment_number=item["installment_number"],
                due_date=item["due_date"],
                amount_minor=item["amount_minor"],
                principal_minor=item["principal_minor"],
                interest_minor=item["interest_minor"],
                paid_minor=0,
                status="scheduled",
            ))

        loan.status = "active"

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="personal_loan_activated",
            resource_type="personal_loan",
            resource_id=str(loan.id),
            details={
                "installment_count": len(schedule),
                "principal_minor": loan.principal_minor,
                "currency": loan.currency_code,
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Loan activated and repayment schedule generated.",
            "loan": _loan_response(loan),
            "installment_count": len(schedule),
            "installments": [
                {
                    "installment_number": item["installment_number"],
                    "due_date": item["due_date"].isoformat(),
                    "amount_minor": item["amount_minor"],
                    "principal_minor": item["principal_minor"],
                    "interest_minor": item["interest_minor"],
                }
                for item in schedule
            ],
        }), 200

    except ValueError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "The repayment schedule could not be saved.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Personal loan activation failed")
        return jsonify({
            "success": False,
            "message": "Loan could not be activated.",
        }), 500


@loans_bp.post("/<uuid:loan_id>/disburse")
@jwt_required()
def disburse_personal_loan(loan_id):
    data = request.get_json(silent=True) or {}
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

    if membership.role not in {"owner", "admin"}:
        return jsonify({
            "success": False,
            "message": "Only workspace owners and admins can disburse loans.",
        }), 403

    account_id = _workspace_id(data.get("financial_account_id"))
    disbursement_date = _parse_date(data.get("disbursement_date"))
    payment_method = data.get("payment_method")
    notes = data.get("notes")

    if account_id is None:
        return jsonify({
            "success": False,
            "message": "A valid financial_account_id is required.",
        }), 400

    if disbursement_date is None:
        return jsonify({
            "success": False,
            "message": "A valid disbursement_date is required in YYYY-MM-DD format.",
        }), 400

    if payment_method not in {"cash", "bank", "mobile_money", "other"}:
        return jsonify({
            "success": False,
            "message": "Invalid payment_method.",
        }), 400

    if notes is not None and (
        not isinstance(notes, str) or len(notes) > 2000
    ):
        return jsonify({
            "success": False,
            "message": "Notes must not exceed 2000 characters.",
        }), 400

    try:
        loan = (
            PersonalLoan.query
            .filter_by(id=loan_id, workspace_id=workspace_id)
            .with_for_update()
            .first()
        )

        if loan is None:
            return jsonify({
                "success": False,
                "message": "Loan not found.",
            }), 404

        if loan.status != "active":
            return jsonify({
                "success": False,
                "message": "Only active loans can be disbursed.",
            }), 409

        existing = LoanDisbursement.query.filter_by(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
        ).first()

        if existing is not None:
            return jsonify({
                "success": False,
                "message": "This loan has already been disbursed.",
            }), 409

        account = (
            FinancialAccount.query
            .filter_by(
                id=account_id,
                workspace_id=workspace_id,
            )
            .with_for_update()
            .first()
        )

        if account is None or not account.is_active:
            return jsonify({
                "success": False,
                "message": "Financial account not found or inactive.",
            }), 400

        if account.account_type not in {"cash", "bank", "mobile_money"}:
            return jsonify({
                "success": False,
                "message": "Choose an active cash, bank, or mobile money account.",
            }), 400

        if account.currency_code != loan.currency_code:
            return jsonify({
                "success": False,
                "message": "Financial account currency must match the loan currency.",
            }), 400

        cash_ledger = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            financial_account_id=account.id,
        ).with_for_update().first()

        if (
            cash_ledger is None
            or not cash_ledger.is_active
            or cash_ledger.account_class != "asset"
            or cash_ledger.normal_balance != "debit"
            or cash_ledger.currency_code != loan.currency_code
        ):
            return jsonify({
                "success": False,
                "message": "The selected financial account has no valid active asset ledger.",
            }), 400

        if loan.direction == "lent":
            system_code = "1200"
            expected_class = "asset"
            expected_balance = "debit"
        elif loan.direction == "borrowed":
            system_code = "2000"
            expected_class = "liability"
            expected_balance = "credit"
        else:
            return jsonify({
                "success": False,
                "message": "Unsupported loan direction.",
            }), 400

        system_ledger = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            code=system_code,
            is_system=True,
        ).with_for_update().first()

        if (
            system_ledger is None
            or not system_ledger.is_active
            or system_ledger.account_class != expected_class
            or system_ledger.normal_balance != expected_balance
            or system_ledger.currency_code != loan.currency_code
            or system_ledger.financial_account_id is not None
        ):
            return jsonify({
                "success": False,
                "message": (
                    f"Required system ledger {system_code} is missing, "
                    "inactive, or incorrectly configured."
                ),
            }), 400

        amount = loan.principal_minor

        if loan.direction == "lent":
            # Lending sends money out, so the selected account must
            # have enough available funds before the journal is posted.
            available_minor = db.session.query(
                func.coalesce(
                    func.sum(
                        JournalLine.debit_minor - JournalLine.credit_minor
                    ),
                    0,
                )
            ).join(
                JournalEntry,
                JournalEntry.id == JournalLine.journal_entry_id,
            ).filter(
                JournalLine.ledger_account_id == cash_ledger.id,
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status.in_(("posted", "reversed")),
            ).scalar()

            available_minor = int(available_minor or 0)
            required_minor = int(amount)

            if available_minor < required_minor:
                db.session.rollback()
                return jsonify({
                    "success": False,
                    "message": (
                        "Insufficient funds in the selected account "
                        "to disburse this loan."
                    ),
                    "available_minor": available_minor,
                    "required_minor": required_minor,
                    "currency_code": loan.currency_code,
                }), 409

            lines = [
                {
                    "ledger_account_id": system_ledger.id,
                    "debit_minor": amount,
                    "credit_minor": 0,
                    "memo": "Loan principal receivable",
                },
                {
                    "ledger_account_id": cash_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount,
                    "memo": "Loan principal disbursed",
                },
            ]
        else:
            lines = [
                {
                    "ledger_account_id": cash_ledger.id,
                    "debit_minor": amount,
                    "credit_minor": 0,
                    "memo": "Loan principal received",
                },
                {
                    "ledger_account_id": system_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount,
                    "memo": "Loan payable recognized",
                },
            ]

        journal = create_draft_entry(
            workspace_id=workspace_id,
            created_by=user_id,
            reference=f"LOAN-DISB-{loan.id.hex}",
            description=f"Principal disbursement: {loan.counterparty_name}",
            transaction_date=datetime.combine(
                disbursement_date,
                datetime.min.time(),
                tzinfo=timezone.utc,
            ),
            lines=lines,
        )

        post_entry(
            entry_id=journal.id,
            workspace_id=workspace_id,
            posted_by=user_id,
        )

        disbursement = LoanDisbursement(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
            financial_account_id=account.id,
            journal_entry_id=journal.id,
            amount_minor=amount,
            disbursement_date=disbursement_date,
            payment_method=payment_method,
            recorded_by=user_id,
            notes=notes.strip() if notes and notes.strip() else None,
        )

        db.session.add(disbursement)
        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="personal_loan_disbursed",
            resource_type="personal_loan",
            resource_id=str(loan.id),
            details={
                "disbursement_id": str(disbursement.id),
                "journal_entry_id": str(journal.id),
                "financial_account_id": str(account.id),
                "amount_minor": amount,
                "currency": loan.currency_code,
                "direction": loan.direction,
                "disbursement_date": disbursement_date.isoformat(),
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Loan principal disbursed and accounting entry posted.",
            "disbursement": {
                "id": str(disbursement.id),
                "loan_id": str(loan.id),
                "financial_account_id": str(account.id),
                "journal_entry_id": str(journal.id),
                "amount_minor": disbursement.amount_minor,
                "currency_code": loan.currency_code,
                "disbursement_date": disbursement.disbursement_date.isoformat(),
                "payment_method": disbursement.payment_method,
                "status": journal.status,
            },
        }), 201

    except ValueError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400

    except IntegrityError:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": "Disbursement already exists or referenced data is invalid.",
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Personal loan disbursement failed")
        return jsonify({
            "success": False,
            "message": "Loan disbursement could not be completed.",
        }), 500



@loans_bp.post("/<uuid:loan_id>/payments")
@jwt_required()
def record_personal_loan_payment(loan_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A valid JSON request is required."}), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({"success": False, "message": "A valid workspace ID is required."}), 400

    membership = _membership(workspace_id, user_id)
    if not membership:
        return jsonify({"success": False, "message": "Workspace membership is required."}), 404

    if membership.role == "viewer":
        return jsonify({"success": False, "message": "You do not have permission to record repayments."}), 403

    loan = PersonalLoan.query.filter_by(id=loan_id, workspace_id=workspace_id).first()
    if not loan:
        return jsonify({"success": False, "message": "Loan not found."}), 404

    if loan.status not in {"active", "overdue", "restructured"}:
        return jsonify({"success": False, "message": "Repayments can only be recorded for active loans."}), 400

    amount = data.get("amount_minor")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or amount > MAX_MINOR_UNITS:
        return jsonify({"success": False, "message": "Enter a valid positive repayment amount."}), 400

    payment_date = _parse_date(data.get("payment_date"))
    if payment_date is None:
        return jsonify({"success": False, "message": "A valid payment date is required."}), 400

    payment_method = data.get("payment_method", "other")
    if payment_method not in {"cash", "bank", "mobile_money", "other"}:
        return jsonify({"success": False, "message": "Invalid payment method."}), 400

    payment_source = data.get("payment_source", "registered")
    if payment_source not in {"registered", "external"}:
        return jsonify({"success": False, "message": "Invalid payment source."}), 400

    reference = data.get("reference")
    notes = data.get("notes")

    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 120
    ):
        return jsonify({
            "success": False,
            "message": "A payment reference is required and must not exceed 120 characters.",
        }), 400

    if notes is not None and (not isinstance(notes, str) or len(notes.strip()) > 2000):
        return jsonify({"success": False, "message": "Payment notes must not exceed 2000 characters."}), 400

    installments = LoanInstallment.query.filter_by(
        personal_loan_id=loan.id,
        workspace_id=workspace_id,
    ).order_by(
        LoanInstallment.due_date,
        LoanInstallment.installment_number,
    ).all()

    confirmed_allocations = (
        db.session.query(LoanPaymentAllocation)
        .join(LoanPayment, LoanPayment.id == LoanPaymentAllocation.payment_id)
        .filter(
            LoanPayment.personal_loan_id == loan.id,
            LoanPayment.workspace_id == workspace_id,
            LoanPayment.status == "confirmed",
        )
        .all()
    )

    try:
        build_payment_allocations(installments, confirmed_allocations, amount)

        payment = LoanPayment(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
            amount_minor=amount,
            principal_minor=amount,
            interest_minor=0,
            payment_date=payment_date,
            payment_method=payment_method,
            payment_source=payment_source,
            reference=reference.strip(),
            status="pending",
            recorded_by=user_id,
            notes=notes.strip() if notes else None,
        )

        db.session.add(payment)
        db.session.flush()

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="loan_payment_recorded",
            resource_type="personal_loan",
            resource_id=str(loan.id),
            details={
                "payment_id": str(payment.id),
                "amount_minor": amount,
                "currency": loan.currency_code,
                "payment_date": payment_date.isoformat(),
                "payment_method": payment_method,
                "payment_source": payment_source,
                "reference": reference.strip(),
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Repayment recorded and awaiting confirmation.",
            "payment": {
                "id": str(payment.id),
                "amount_minor": payment.amount_minor,
                "payment_date": payment.payment_date.isoformat(),
                "payment_method": payment.payment_method,
                "payment_source": payment.payment_source,
                "reference": payment.reference,
                "status": payment.status,
                "notes": payment.notes,
            },
        }), 201

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400

    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "The repayment could not be recorded because of a data conflict."}), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Loan repayment recording failed.")
        return jsonify({"success": False, "message": "The repayment could not be recorded."}), 500




@loans_bp.patch("/<uuid:loan_id>/payments/<uuid:payment_id>")
@jwt_required()
def update_pending_loan_payment(loan_id, payment_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A valid JSON request is required."}), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({"success": False, "message": "A valid workspace ID is required."}), 400

    membership = _membership(workspace_id, user_id)
    if not membership:
        return jsonify({"success": False, "message": "Workspace membership is required."}), 404

    if membership.role == "viewer":
        return jsonify({"success": False, "message": "You do not have permission to edit repayments."}), 403

    loan = PersonalLoan.query.filter_by(
        id=loan_id, workspace_id=workspace_id
    ).with_for_update().first()

    if not loan:
        return jsonify({"success": False, "message": "Loan not found."}), 404

    payment = LoanPayment.query.filter_by(
        id=payment_id,
        personal_loan_id=loan.id,
        workspace_id=workspace_id,
    ).with_for_update().first()

    if not payment:
        return jsonify({"success": False, "message": "Repayment not found for this loan."}), 404

    if payment.status != "pending":
        return jsonify({"success": False, "message": "Only pending repayments can be edited."}), 409

    if payment.financial_account_id is not None or payment.journal_entry_id is not None:
        return jsonify({"success": False, "message": "This repayment has accounting records and cannot be edited."}), 409

    amount = data.get("amount_minor")
    if isinstance(amount, bool) or not isinstance(amount, int) or amount <= 0 or amount > MAX_MINOR_UNITS:
        return jsonify({"success": False, "message": "Enter a valid positive repayment amount."}), 400

    payment_date = _parse_date(data.get("payment_date"))
    if payment_date is None:
        return jsonify({"success": False, "message": "A valid payment date is required."}), 400

    payment_method = data.get("payment_method")
    if payment_method not in {"cash", "bank", "mobile_money", "other"}:
        return jsonify({"success": False, "message": "Invalid payment method."}), 400

    payment_source = data.get("payment_source", payment.payment_source)
    if payment_source not in {"registered", "external"}:
        return jsonify({"success": False, "message": "Invalid payment source."}), 400

    reference = data.get("reference")
    notes = data.get("notes")

    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 120
    ):
        return jsonify({
            "success": False,
            "message": "A payment reference is required and must not exceed 120 characters.",
        }), 400

    if notes is not None and (not isinstance(notes, str) or len(notes.strip()) > 2000):
        return jsonify({"success": False, "message": "Payment notes must not exceed 2000 characters."}), 400

    try:
        installments = LoanInstallment.query.filter_by(
            personal_loan_id=loan.id,
            workspace_id=workspace_id,
        ).order_by(
            LoanInstallment.due_date,
            LoanInstallment.installment_number,
        ).with_for_update().all()

        confirmed_allocations = (
            db.session.query(LoanPaymentAllocation)
            .join(LoanPayment, LoanPayment.id == LoanPaymentAllocation.payment_id)
            .filter(
                LoanPayment.personal_loan_id == loan.id,
                LoanPayment.workspace_id == workspace_id,
                LoanPayment.status == "confirmed",
            )
            .all()
        )

        build_payment_allocations(installments, confirmed_allocations, amount)

        old_values = {
            "amount_minor": payment.amount_minor,
            "payment_date": payment.payment_date.isoformat(),
            "payment_method": payment.payment_method,
            "payment_source": payment.payment_source,
            "reference": payment.reference,
            "notes": payment.notes,
        }

        payment.amount_minor = amount
        payment.principal_minor = amount
        payment.interest_minor = 0
        payment.payment_date = payment_date
        payment.payment_method = payment_method
        payment.payment_source = payment_source
        payment.reference = reference.strip()
        payment.notes = notes.strip() if notes and notes.strip() else None

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="loan_payment_updated",
            resource_type="loan_payment",
            resource_id=str(payment.id),
            details={
                "loan_id": str(loan.id),
                "previous": old_values,
                "updated": {
                    "amount_minor": amount,
                    "payment_date": payment_date.isoformat(),
                    "payment_method": payment_method,
                    "payment_source": payment_source,
                    "reference": payment.reference,
                    "notes": payment.notes,
                },
            },
            ip_address=request.remote_addr,
        ))

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Pending repayment updated.",
            "payment": {
                "id": str(payment.id),
                "amount_minor": payment.amount_minor,
                "payment_date": payment.payment_date.isoformat(),
                "payment_method": payment.payment_method,
                "payment_source": payment.payment_source,
                "reference": payment.reference,
                "status": payment.status,
                "notes": payment.notes,
            },
        }), 200

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "The repayment could not be updated because of a data conflict."}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Pending loan repayment update failed.")
        return jsonify({"success": False, "message": "The repayment could not be updated."}), 500


@loans_bp.delete("/<uuid:loan_id>/payments/<uuid:payment_id>")
@jwt_required()
def delete_pending_loan_payment(loan_id, payment_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A valid JSON request is required."}), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({"success": False, "message": "A valid workspace ID is required."}), 400

    membership = _membership(workspace_id, user_id)
    if not membership:
        return jsonify({"success": False, "message": "Workspace membership is required."}), 404

    if membership.role == "viewer":
        return jsonify({"success": False, "message": "You do not have permission to delete repayments."}), 403

    loan = PersonalLoan.query.filter_by(
        id=loan_id, workspace_id=workspace_id
    ).with_for_update().first()

    if not loan:
        return jsonify({"success": False, "message": "Loan not found."}), 404

    payment = LoanPayment.query.filter_by(
        id=payment_id,
        personal_loan_id=loan.id,
        workspace_id=workspace_id,
    ).with_for_update().first()

    if not payment:
        return jsonify({"success": False, "message": "Repayment not found for this loan."}), 404

    if payment.status != "pending":
        return jsonify({"success": False, "message": "Only pending repayments can be deleted."}), 409

    if payment.financial_account_id is not None or payment.journal_entry_id is not None:
        return jsonify({"success": False, "message": "This repayment has accounting records and cannot be deleted."}), 409

    try:
        allocations = LoanPaymentAllocation.query.filter_by(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
            payment_id=payment.id,
        ).all()

        if allocations:
            return jsonify({"success": False, "message": "This repayment has installment allocations and cannot be deleted."}), 409

        deleted_details = {
            "loan_id": str(loan.id),
            "amount_minor": payment.amount_minor,
            "payment_date": payment.payment_date.isoformat(),
            "payment_method": payment.payment_method,
            "reference": payment.reference,
        }

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="loan_payment_deleted",
            resource_type="loan_payment",
            resource_id=str(payment.id),
            details=deleted_details,
            ip_address=request.remote_addr,
        ))

        db.session.delete(payment)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Pending repayment deleted.",
            "payment_id": str(payment_id),
        }), 200

    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "The repayment could not be deleted because of a data conflict."}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Pending loan repayment deletion failed.")
        return jsonify({"success": False, "message": "The repayment could not be deleted."}), 500


@loans_bp.post("/<uuid:loan_id>/payments/<uuid:payment_id>/confirm")
@jwt_required()
def confirm_personal_loan_payment(loan_id, payment_id):
    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A valid JSON request is required."}), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    raw_account_id = data.get("financial_account_id")
    financial_account_id = _workspace_id(raw_account_id)
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({
            "success": False,
            "message": "A valid workspace ID is required.",
        }), 400

    membership = _membership(workspace_id, user_id)
    if not membership:
        return jsonify({"success": False, "message": "Workspace membership is required."}), 404

    if membership.role == "viewer":
        return jsonify({"success": False, "message": "You do not have permission to confirm repayments."}), 403

    loan = PersonalLoan.query.filter_by(
        id=loan_id,
        workspace_id=workspace_id,
    ).first()

    if not loan:
        return jsonify({"success": False, "message": "Loan not found."}), 404

    payment = LoanPayment.query.filter_by(
        id=payment_id,
        personal_loan_id=loan.id,
        workspace_id=workspace_id,
    ).first()

    if not payment:
        return jsonify({"success": False, "message": "Repayment not found for this loan."}), 404

    if payment.payment_source == "registered":
        if financial_account_id is None:
            return jsonify({
                "success": False,
                "message": "Select a registered account for this repayment.",
            }), 400
    elif payment.payment_source == "external":
        if raw_account_id not in (None, ""):
            return jsonify({
                "success": False,
                "message": "External repayments must not use a registered account.",
            }), 400
        financial_account_id = None
    else:
        return jsonify({
            "success": False,
            "message": "The repayment has an invalid payment source.",
        }), 400

    try:
        confirmed = confirm_loan_payment(
            payment_id=payment_id,
            workspace_id=workspace_id,
            confirmed_by=user_id,
            financial_account_id=financial_account_id,
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Repayment confirmed and accounting updated.",
            "payment": {
                "id": str(confirmed.id),
                "financial_account_id": (
                    str(confirmed.financial_account_id)
                    if confirmed.financial_account_id else None
                ),
                "payment_source": confirmed.payment_source,
                "journal_entry_id": str(confirmed.journal_entry_id),
                "amount_minor": confirmed.amount_minor,
                "principal_minor": confirmed.principal_minor,
                "interest_minor": confirmed.interest_minor,
                "payment_date": confirmed.payment_date.isoformat(),
                "payment_method": confirmed.payment_method,
                "reference": confirmed.reference,
                "status": confirmed.status,
                "notes": confirmed.notes,
            },
        }), 200

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400

    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "The repayment could not be confirmed because of a data conflict."}), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Loan repayment confirmation failed.")
        return jsonify({"success": False, "message": "The repayment could not be confirmed."}), 500


@loans_bp.get("/<uuid:loan_id>")
@jwt_required()
def get_personal_loan(loan_id):
    workspace_id = _workspace_id(request.args.get("workspace_id"))
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
        loan = PersonalLoan.query.filter_by(
            id=loan_id,
            workspace_id=workspace_id,
        ).first()

        if loan is None:
            return jsonify({
                "success": False,
                "message": "Loan not found.",
            }), 404

        installments = (
            LoanInstallment.query
            .filter_by(
                workspace_id=workspace_id,
                personal_loan_id=loan.id,
            )
            .order_by(
                LoanInstallment.installment_number.asc()
            )
            .all()
        )

        disbursement = LoanDisbursement.query.filter_by(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
        ).first()

        payments = (
            LoanPayment.query
            .filter_by(
                workspace_id=workspace_id,
                personal_loan_id=loan.id,
            )
            .order_by(
                LoanPayment.payment_date.desc(),
                LoanPayment.created_at.desc(),
            )
            .all()
        )

        payment_ids = [payment.id for payment in payments]

        allocations = []
        if payment_ids:
            allocation_rows = (
                LoanPaymentAllocation.query
                .filter(
                    LoanPaymentAllocation.workspace_id == workspace_id,
                    LoanPaymentAllocation.personal_loan_id == loan.id,
                    LoanPaymentAllocation.payment_id.in_(payment_ids),
                )
                .order_by(LoanPaymentAllocation.created_at.asc())
                .all()
            )

            allocations = [
                {
                    "id": str(item.id),
                    "payment_id": str(item.payment_id),
                    "installment_id": str(item.installment_id),
                    "amount_minor": item.amount_minor,
                    "principal_minor": item.principal_minor,
                    "interest_minor": item.interest_minor,
                }
                for item in allocation_rows
            ]

        disbursement_data = None
        if disbursement is not None:
            disbursement_data = {
                "id": str(disbursement.id),
                "financial_account_id": str(disbursement.financial_account_id),
                "journal_entry_id": str(disbursement.journal_entry_id),
                "amount_minor": disbursement.amount_minor,
                "disbursement_date": disbursement.disbursement_date.isoformat(),
                "payment_method": disbursement.payment_method,
                "notes": disbursement.notes,
            }

        payment_data = [
            {
                "id": str(payment.id),
                "financial_account_id": (
                    str(payment.financial_account_id)
                    if payment.financial_account_id else None
                ),
                "journal_entry_id": (
                    str(payment.journal_entry_id)
                    if payment.journal_entry_id else None
                ),
                "amount_minor": payment.amount_minor,
                "principal_minor": payment.principal_minor,
                "interest_minor": payment.interest_minor,
                "payment_date": payment.payment_date.isoformat(),
                "payment_method": payment.payment_method,
                "payment_source": payment.payment_source,
                "reference": payment.reference,
                "status": payment.status,
                "notes": payment.notes,
                "allocations": [
                    item for item in allocations
                    if item["payment_id"] == str(payment.id)
                ],
            }
            for payment in payments
        ]

        return jsonify({
            "success": True,
            "loan": _loan_response(loan),
            "disbursement": disbursement_data,
            "installments": [
                {
                    "id": str(item.id),
                    "installment_number": item.installment_number,
                    "due_date": item.due_date.isoformat(),
                    "amount_minor": item.amount_minor,
                    "principal_minor": item.principal_minor,
                    "interest_minor": item.interest_minor,
                    "paid_minor": item.paid_minor,
                    "outstanding_minor": item.amount_minor - item.paid_minor,
                    "status": item.status,
                }
                for item in installments
            ],
            "payments": payment_data,
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Personal loan details retrieval failed")
        return jsonify({
            "success": False,
            "message": "Loan details could not be loaded.",
        }), 500
