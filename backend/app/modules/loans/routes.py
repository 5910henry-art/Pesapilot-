from datetime import date, datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from flask import current_app, jsonify, request
from flask_jwt_extended import get_jwt_identity, jwt_required
from sqlalchemy.exc import IntegrityError
from sqlalchemy import func

from app.extensions import db
from app.models import (
    Currency,
    AuditLog,
    ExchangeRate,
    FinancialAccount,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LinkedLoanTransaction,
    LoanDisbursement,
    PersonalLoan,
    User,
    UserProfile,
    Workspace,
    WorkspaceMember,
)
from app.models.loan_repayments import LoanInstallment, LoanPayment, LoanPaymentAllocation
from app.services.loan_schedules import build_repayment_schedule
from app.services.accounting import AccountingError, create_draft_entry, post_entry, reverse_entry
from app.services.loan_repayments import (
    build_payment_allocations,
    confirm_loan_payment,
)
from app.services.notifications import create_notification

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


def _resolve_counterparty_user(value, current_user_id):
    if value is None or value == "":
        return None

    if not isinstance(value, str):
        return False

    try:
        linked_id = UUID(value)
    except (ValueError, TypeError, AttributeError):
        return False

    if linked_id == current_user_id:
        return False

    account = User.query.filter(
        User.id == linked_id,
        User.is_active.is_(True),
        (
            User.email_verified.is_(True)
            | User.phone_verified.is_(True)
        ),
    ).first()

    if account is None or db.session.get(UserProfile, linked_id) is None:
        return False

    return account


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


@loans_bp.get("/counterparties/search")
@jwt_required()
def search_loan_counterparty():
    user_id = _user_id()

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    query = request.args.get("q", "").strip()

    if len(query) < 5 or len(query) > 255:
        return jsonify({
            "success": False,
            "message": "Enter an exact verified email address or phone number.",
        }), 400

    account_query = User.query.join(
        UserProfile,
        UserProfile.user_id == User.id,
    ).filter(
        User.is_active.is_(True),
        User.id != user_id,
    )

    if "@" in query:
        account_query = account_query.filter(
            User.email_verified.is_(True),
            func.lower(User.email) == query.lower(),
        )
    else:
        account_query = account_query.filter(
            User.phone_verified.is_(True),
            User.phone == query,
        )

    account = account_query.first()

    if account is None:
        return jsonify({
            "success": True,
            "counterparty": None,
        }), 200

    profile = db.session.get(UserProfile, account.id)

    return jsonify({
        "success": True,
        "counterparty": {
            "id": str(account.id),
            "display_name": (
                f"{profile.first_name} {profile.last_name}".strip()
            ),
        },
    }), 200


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
        "counterparty_user_id": (
            str(loan.counterparty_user_id)
            if loan.counterparty_user_id else None
        ),
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
        "decision_reason": loan.decision_reason,
        "decided_by": str(loan.decided_by) if loan.decided_by else None,
        "decided_at": loan.decided_at.isoformat() if loan.decided_at else None,
        "created_at": loan.created_at.isoformat(),
        "updated_at": loan.updated_at.isoformat(),
    }


def _counterparty_loan_response(loan, viewer_workspace_id):
    """Serialize a linked loan for its lender's read-only view."""
    profile = UserProfile.query.filter_by(
        user_id=loan.created_by,
    ).first()

    borrower_name = "Borrower"
    if profile is not None:
        borrower_name = " ".join(
            part for part in (profile.first_name, profile.last_name)
            if part
        ).strip() or borrower_name

    data = _loan_response(loan)
    data.update({
        "workspace_id": str(viewer_workspace_id),
        "direction": "lent",
        "counterparty_name": borrower_name,
        "counterparty_phone": None,
        "counterparty_user_id": str(loan.created_by),
        "is_counterparty_view": True,
    })
    return data


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
        owned_loans = (
            PersonalLoan.query
            .filter_by(workspace_id=workspace_id)
            .order_by(PersonalLoan.created_at.desc())
            .all()
        )

        linked_loans = (
            PersonalLoan.query
            .filter(
                PersonalLoan.direction == "borrowed",
                PersonalLoan.counterparty_user_id == user_id,
                PersonalLoan.status.notin_(
                    ("draft", "pending_approval", "rejected")
                ),
            )
            .all()
        )

        loan_data = [_loan_response(loan) for loan in owned_loans]
        loan_data.extend(
            _counterparty_loan_response(loan, workspace_id)
            for loan in linked_loans
        )
        loan_data.sort(
            key=lambda item: item["created_at"],
            reverse=True,
        )

        return jsonify({
            "success": True,
            "count": len(loan_data),
            "loans": loan_data,
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
    counterparty_user = _resolve_counterparty_user(
        data.get("counterparty_user_id"), user_id
    )
    if counterparty_user is False:
        return jsonify({
            "success": False,
            "message": "The selected PesaPilot counterparty is invalid or unavailable.",
        }), 400
    if counterparty_user:
        profile = db.session.get(UserProfile, counterparty_user.id)
        counterparty_name = f"{profile.first_name} {profile.last_name}".strip()
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
            counterparty_user_id=(
                counterparty_user.id if counterparty_user else None
            ),
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
            status=(
                "pending_approval"
                if direction == "borrowed" and counterparty_user is not None
                else "draft"
            ),
            description=description.strip() if description else None,
        )

        db.session.add(loan)
        db.session.flush()

        is_approval_request = (
            direction == "borrowed" and counterparty_user is not None
        )
        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action=(
                "personal_loan_requested"
                if is_approval_request else "personal_loan_created"
            ),
            resource_type="personal_loan",
            resource_id=str(loan.id),
            details={
                "direction": direction,
                "loan_type": loan_type,
                "principal_minor": principal,
                "currency": currency_code,
                "counterparty_name": counterparty_name.strip(),
                "status": loan.status,
            },
            ip_address=request.remote_addr,
        ))

        if is_approval_request:
            requester_profile = db.session.get(UserProfile, user_id)
            requester_name = (
                f"{requester_profile.first_name} {requester_profile.last_name}".strip()
                if requester_profile else "A PesaPilot user"
            )
            create_notification(
                user_id=counterparty_user.id,
                category="loan_application_updates",
                title="New loan request",
                body=(
                    f"{requester_name} has sent you a loan request "
                    f"in {currency_code}. Please review it."
                ),
                target_url="/loans",
                dedupe_key=f"personal-loan-request:{loan.id}:created",
            )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": (
                "Loan request sent to the selected PesaPilot user for approval."
                if is_approval_request
                else "Personal loan recorded successfully."
            ),
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
    counterparty_user = _resolve_counterparty_user(
        data.get("counterparty_user_id"), user_id
    )
    if counterparty_user is False:
        return jsonify({
            "success": False,
            "message": "The selected PesaPilot counterparty is invalid or unavailable.",
        }), 400
    if counterparty_user:
        profile = db.session.get(UserProfile, counterparty_user.id)
        counterparty_name = f"{profile.first_name} {profile.last_name}".strip()
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
        "counterparty_user_id": (
            str(loan.counterparty_user_id)
            if loan.counterparty_user_id else None
        ),
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
        loan.counterparty_user_id = (
            counterparty_user.id if counterparty_user else None
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
                    "counterparty_user_id": (
                        str(loan.counterparty_user_id)
                        if loan.counterparty_user_id else None
                    ),
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





def _incoming_loan_response(loan):
    profile = (
        db.session.get(UserProfile, loan.created_by)
        if loan.created_by else None
    )
    requester_name = (
        f"{profile.first_name} {profile.last_name}".strip()
        if profile else "PesaPilot user"
    )

    return {
        "id": str(loan.id),
        "direction": loan.direction,
        "requester_name": requester_name,
        "principal_minor": loan.principal_minor,
        "currency_code": loan.currency_code,
        "loan_type": loan.loan_type,
        "interest_method": loan.interest_method,
        "interest_rate_percent": str(loan.interest_rate_percent),
        "repayment_frequency": loan.repayment_frequency,
        "term_count": loan.term_count,
        "installment_minor": loan.installment_minor,
        "start_date": loan.start_date.isoformat(),
        "due_date": loan.due_date.isoformat(),
        "status": loan.status,
        "description": loan.description,
        "decision_reason": loan.decision_reason,
        "created_at": loan.created_at.isoformat(),
    }


@loans_bp.get("/incoming-requests")
@jwt_required()
def list_incoming_loan_requests():
    user_id = _user_id()

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        loans = (
            PersonalLoan.query
            .filter_by(
                counterparty_user_id=user_id,
                direction="borrowed",
                status="pending_approval",
            )
            .order_by(PersonalLoan.created_at.desc())
            .all()
        )

        return jsonify({
            "success": True,
            "count": len(loans),
            "requests": [
                _incoming_loan_response(loan)
                for loan in loans
            ],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Incoming personal loan requests could not be loaded"
        )
        return jsonify({
            "success": False,
            "message": "Incoming loan requests could not be loaded.",
        }), 500


def _decide_incoming_loan(loan_id, approved):
    user_id = _user_id()

    if user_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    data = request.get_json(silent=True)
    if data is None:
        data = {}

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    reason = data.get("reason") if not approved else None

    if not approved and reason is not None:
        if not isinstance(reason, str) or len(reason.strip()) > 500:
            return jsonify({
                "success": False,
                "message": "Rejection reason must not exceed 500 characters.",
            }), 400
        reason = reason.strip() or None

    # Check lender identity before validating disbursement details.
    authorized_loan = PersonalLoan.query.filter_by(
        id=loan_id,
        counterparty_user_id=user_id,
        direction="borrowed",
    ).first()
    if authorized_loan is None:
        return jsonify({
            "success": False,
            "message": "Loan request not found.",
        }), 404

    lender_workspace_id = None
    account_id = None
    disbursement_date = None
    payment_method = None
    reference = None
    notes = None

    if approved:
        lender_workspace_id = _workspace_id(data.get("workspace_id"))
        account_id = _workspace_id(data.get("financial_account_id"))
        disbursement_date = _parse_date(data.get("disbursement_date"))
        payment_method = data.get("payment_method")
        reference = data.get("reference")
        notes = data.get("notes")

        if lender_workspace_id is None or account_id is None:
            return jsonify({
                "success": False,
                "message": (
                    "Valid workspace_id and financial_account_id "
                    "are required to record the disbursement."
                ),
            }), 400

        if disbursement_date is None:
            return jsonify({
                "success": False,
                "message": (
                    "A valid disbursement_date is required "
                    "in YYYY-MM-DD format."
                ),
            }), 400

        if payment_method not in {
            "cash", "bank", "mobile_money", "other"
        }:
            return jsonify({
                "success": False,
                "message": "Invalid payment_method.",
            }), 400

        if reference is not None:
            if not isinstance(reference, str) or len(reference.strip()) > 120:
                return jsonify({
                    "success": False,
                    "message": "Reference must not exceed 120 characters.",
                }), 400
            reference = reference.strip() or None

        if notes is not None:
            if not isinstance(notes, str) or len(notes) > 2000:
                return jsonify({
                    "success": False,
                    "message": "Notes must not exceed 2000 characters.",
                }), 400
            notes = notes.strip() or None

        membership = _membership(lender_workspace_id, user_id)

        if membership is None:
            return jsonify({
                "success": False,
                "message": "Workspace not found or access denied.",
            }), 404

        if membership.role not in {"owner", "admin"}:
            return jsonify({
                "success": False,
                "message": (
                    "Only workspace owners and admins can approve "
                    "and disburse linked loans."
                ),
            }), 403

    try:
        loan = (
            PersonalLoan.query
            .filter_by(
                id=loan_id,
                counterparty_user_id=user_id,
                direction="borrowed",
            )
            .with_for_update()
            .first()
        )

        if loan is None:
            return jsonify({
                "success": False,
                "message": "Loan request not found.",
            }), 404

        if loan.status != "pending_approval":
            return jsonify({
                "success": False,
                "message": (
                    "This loan request has already been decided "
                    "or is no longer pending."
                ),
            }), 409

        linked_disbursement = None
        schedule = []

        if approved:
            existing_count = LoanInstallment.query.filter_by(
                workspace_id=loan.workspace_id,
                personal_loan_id=loan.id,
            ).count()

            if existing_count:
                return jsonify({
                    "success": False,
                    "message": (
                        "A repayment schedule already exists for this request."
                    ),
                }), 409

            existing_disbursement = LinkedLoanTransaction.query.filter_by(
                loan_workspace_id=loan.workspace_id,
                personal_loan_id=loan.id,
                transaction_type="disbursement",
            ).filter(
                LinkedLoanTransaction.status != "disputed"
            ).first()

            if existing_disbursement is not None:
                return jsonify({
                    "success": False,
                    "message": (
                        "A linked disbursement already exists for this loan."
                    ),
                }), 409

            lender_workspace = (
                Workspace.query
                .filter_by(id=lender_workspace_id)
                .with_for_update()
                .first()
            )

            if lender_workspace is None:
                return jsonify({
                    "success": False,
                    "message": "Lender workspace not found.",
                }), 404

            if lender_workspace.functional_currency != loan.currency_code:
                return jsonify({
                    "success": False,
                    "message": (
                        "The lender workspace currency must match "
                        "the loan currency."
                    ),
                }), 400

            account = (
                FinancialAccount.query
                .filter_by(
                    id=account_id,
                    workspace_id=lender_workspace_id,
                )
                .with_for_update()
                .first()
            )

            if account is None or not account.is_active:
                return jsonify({
                    "success": False,
                    "message": "Financial account not found or inactive.",
                }), 400

            if account.account_type not in {
                "cash", "bank", "mobile_money"
            }:
                return jsonify({
                    "success": False,
                    "message": (
                        "Choose an active cash, bank, or mobile money account."
                    ),
                }), 400

            if account.currency_code != loan.currency_code:
                return jsonify({
                    "success": False,
                    "message": (
                        "Financial account currency must match the loan currency."
                    ),
                }), 400

            cash_ledger = LedgerAccount.query.filter_by(
                workspace_id=lender_workspace_id,
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
                    "message": (
                        "The selected financial account has no valid "
                        "active asset ledger."
                    ),
                }), 400

            receivable_ledger = LedgerAccount.query.filter_by(
                workspace_id=lender_workspace_id,
                code="1200",
                is_system=True,
            ).with_for_update().first()

            if (
                receivable_ledger is None
                or not receivable_ledger.is_active
                or receivable_ledger.account_class != "asset"
                or receivable_ledger.normal_balance != "debit"
                or receivable_ledger.currency_code != loan.currency_code
                or receivable_ledger.financial_account_id is not None
            ):
                return jsonify({
                    "success": False,
                    "message": (
                        "Required lender Accounts Receivable ledger "
                        "1200 is missing or incorrectly configured."
                    ),
                }), 400

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
                JournalEntry.workspace_id == lender_workspace_id,
                JournalEntry.status.in_(("posted", "reversed")),
            ).scalar()

            available_minor = int(available_minor or 0)
            required_minor = int(loan.principal_minor)

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

            journal = create_draft_entry(
                workspace_id=lender_workspace_id,
                created_by=user_id,
                reference=f"LOAN-DISB-{loan.id.hex}",
                description=(
                    f"Linked loan principal disbursement: "
                    f"{loan.counterparty_name}"
                ),
                transaction_date=datetime.combine(
                    disbursement_date,
                    datetime.min.time(),
                    tzinfo=timezone.utc,
                ),
                lines=[
                    {
                        "ledger_account_id": receivable_ledger.id,
                        "debit_minor": loan.principal_minor,
                        "credit_minor": 0,
                        "memo": "Linked loan principal receivable",
                    },
                    {
                        "ledger_account_id": cash_ledger.id,
                        "debit_minor": 0,
                        "credit_minor": loan.principal_minor,
                        "memo": "Linked loan principal disbursed",
                    },
                ],
            )

            post_entry(
                entry_id=journal.id,
                workspace_id=lender_workspace_id,
                posted_by=user_id,
            )

            linked_disbursement = LinkedLoanTransaction(
                loan_workspace_id=loan.workspace_id,
                personal_loan_id=loan.id,
                loan_payment_id=None,
                transaction_type="disbursement",
                amount_minor=loan.principal_minor,
                transaction_date=disbursement_date,
                payment_method=payment_method,
                reference=reference,
                notes=notes,
                status="pending",
                initiating_workspace_id=lender_workspace_id,
                initiating_account_id=account.id,
                initiating_journal_entry_id=journal.id,
                initiated_by=user_id,
            )
            db.session.add(linked_disbursement)
            db.session.flush()

            loan.status = "awaiting_confirmation"
            loan.decision_reason = None
            action = "personal_loan_disbursement_recorded"
            title = "Loan approved and disbursed"
            body = (
                "The lender has approved your loan and recorded the "
                "disbursement. Confirm receipt or dispute it in PesaPilot."
            )
            message = (
                "Loan approved and lender disbursement recorded. "
                "Awaiting your confirmation of receipt."
            )

        else:
            loan.status = "rejected"
            loan.decision_reason = reason
            action = "personal_loan_rejected"
            title = "Loan request declined"
            body = (
                f"Your loan request was declined: {reason}"
                if reason else "Your loan request was declined."
            )
            body = body[:500]
            message = "Loan request rejected."

        loan.decided_by = user_id
        loan.decided_at = datetime.now(timezone.utc)

        audit_details = {
            "decision": "approved" if approved else "rejected",
            "reason": reason,
            "installment_count": len(schedule),
        }

        if linked_disbursement is not None:
            audit_details["linked_disbursement_id"] = str(
                linked_disbursement.id
            )
            audit_details["amount_minor"] = loan.principal_minor
            audit_details["currency"] = loan.currency_code
            audit_details["disbursement_date"] = (
                disbursement_date.isoformat()
            )

        db.session.add(AuditLog(
            workspace_id=loan.workspace_id,
            user_id=user_id,
            action=action,
            resource_type="personal_loan",
            resource_id=str(loan.id),
            details=audit_details,
            ip_address=request.remote_addr,
        ))

        if loan.created_by:
            create_notification(
                user_id=loan.created_by,
                category="loan_application_updates",
                title=title,
                body=body,
                target_url="/loans",
                dedupe_key=(
                    f"personal-loan-request:{loan.id}:"
                    f"{'approved' if approved else 'rejected'}"
                ),
            )

        db.session.commit()

        response = {
            "success": True,
            "message": message,
            "request": _incoming_loan_response(loan),
            "installment_count": len(schedule),
        }

        if linked_disbursement is not None:
            response["disbursement"] = {
                "id": str(linked_disbursement.id),
                "loan_id": str(loan.id),
                "amount_minor": linked_disbursement.amount_minor,
                "currency_code": loan.currency_code,
                "disbursement_date": (
                    linked_disbursement.transaction_date.isoformat()
                ),
                "payment_method": linked_disbursement.payment_method,
                "reference": linked_disbursement.reference,
                "status": linked_disbursement.status,
            }

        return jsonify(response), 200

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
            "message": (
                "The loan decision or linked disbursement could not be saved."
            ),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Incoming personal loan decision failed"
        )
        return jsonify({
            "success": False,
            "message": "The loan decision could not be completed.",
        }), 500




@loans_bp.post("/<uuid:loan_id>/approve")
@jwt_required()
def approve_incoming_loan_request(loan_id):
    return _decide_incoming_loan(loan_id, approved=True)


@loans_bp.post("/<uuid:loan_id>/reject")
@jwt_required()
def reject_incoming_loan_request(loan_id):
    return _decide_incoming_loan(loan_id, approved=False)


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

    loan = PersonalLoan.query.filter_by(id=loan_id, workspace_id=workspace_id).with_for_update().first()
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

    is_linked = (
        loan.direction == "borrowed"
        and loan.counterparty_user_id is not None
    )
    linked_account = None
    allocation_amount = amount

    if is_linked:
        if payment_source != "registered":
            return jsonify({
                "success": False,
                "message": "Linked repayments must use a registered account.",
            }), 400

        account_id = _workspace_id(data.get("financial_account_id"))
        if account_id is None:
            return jsonify({
                "success": False,
                "message": "Select the account sending this repayment.",
            }), 400

        linked_account = FinancialAccount.query.filter_by(
            id=account_id,
            workspace_id=workspace_id,
        ).with_for_update().first()

        if (
            linked_account is None
            or not linked_account.is_active
            or linked_account.account_type not in {"cash", "bank", "mobile_money"}
            or linked_account.currency_code != loan.currency_code
        ):
            return jsonify({
                "success": False,
                "message": "Choose an active cash, bank, or mobile money account in the loan currency.",
            }), 400

        account_ledgers = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            financial_account_id=linked_account.id,
            is_active=True,
        ).with_for_update().all()

        if len(account_ledgers) != 1:
            return jsonify({
                "success": False,
                "message": "The selected account must have exactly one active asset ledger.",
            }), 400

        cash_ledger = account_ledgers[0]
        if (
            not cash_ledger.is_active
            or cash_ledger.account_class != "asset"
            or cash_ledger.normal_balance != "debit"
            or cash_ledger.currency_code != loan.currency_code
        ):
            return jsonify({
                "success": False,
                "message": "The selected account has no valid active asset ledger.",
            }), 400

        available_minor = db.session.query(
            func.coalesce(
                func.sum(JournalLine.debit_minor - JournalLine.credit_minor),
                0,
            )
        ).join(
            JournalEntry,
            JournalEntry.id == JournalLine.journal_entry_id,
        ).filter(
            JournalLine.ledger_account_id == cash_ledger.id,
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status == "posted",
        ).scalar()
        available_minor = int(available_minor or 0)

        reserved_account_minor = db.session.query(
            func.coalesce(func.sum(LinkedLoanTransaction.amount_minor), 0)
        ).filter(
            LinkedLoanTransaction.transaction_type == "repayment",
            LinkedLoanTransaction.status == "pending",
            LinkedLoanTransaction.initiating_workspace_id == workspace_id,
            LinkedLoanTransaction.initiating_account_id == linked_account.id,
        ).scalar()
        reserved_account_minor = int(reserved_account_minor or 0)

        if available_minor < amount + reserved_account_minor:
            return jsonify({
                "success": False,
                "message": "Insufficient available funds after pending linked repayments are reserved.",
                "available_minor": available_minor,
                "reserved_minor": reserved_account_minor,
                "required_minor": amount,
                "currency_code": loan.currency_code,
            }), 409

        pending_loan_minor = db.session.query(
            func.coalesce(func.sum(LinkedLoanTransaction.amount_minor), 0)
        ).filter(
            LinkedLoanTransaction.loan_workspace_id == workspace_id,
            LinkedLoanTransaction.personal_loan_id == loan.id,
            LinkedLoanTransaction.transaction_type == "repayment",
            LinkedLoanTransaction.status == "pending",
        ).scalar()
        pending_loan_minor = int(pending_loan_minor or 0)

        allocation_amount = amount + pending_loan_minor
        if allocation_amount > MAX_MINOR_UNITS:
            return jsonify({
                "success": False,
                "message": "Pending repayments exceed the supported amount limit.",
            }), 400

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
        build_payment_allocations(installments, confirmed_allocations, allocation_amount)

        if is_linked:
            payment_source = "linked"

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

        linked_transaction = None
        if is_linked:
            linked_transaction = LinkedLoanTransaction(
                loan_workspace_id=workspace_id,
                personal_loan_id=loan.id,
                loan_payment_id=payment.id,
                transaction_type="repayment",
                amount_minor=amount,
                transaction_date=payment_date,
                payment_method=payment_method,
                reference=reference.strip(),
                notes=notes.strip() if notes else None,
                status="pending",
                initiating_workspace_id=workspace_id,
                initiating_account_id=linked_account.id,
                initiated_by=user_id,
            )
            db.session.add(linked_transaction)
            db.session.flush()

            create_notification(
                user_id=loan.counterparty_user_id,
                category="loan_repayments",
                title="Loan repayment awaiting confirmation",
                body=(
                    f"A repayment of {loan.currency_code} has been recorded "
                    "and is awaiting your confirmation."
                ),
                target_url="/loans",
                dedupe_key=f"linked-loan-repayment:{linked_transaction.id}:pending",
            )

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
                "linked_transaction_id": (
                    str(linked_transaction.id) if linked_transaction else None
                ),
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

    linked_transaction = LinkedLoanTransaction.query.filter_by(
        loan_workspace_id=workspace_id,
        personal_loan_id=loan.id,
        loan_payment_id=payment.id,
        transaction_type="repayment",
    ).first()

    if linked_transaction is not None:
        return jsonify({
            "success": False,
            "message": "Linked repayments must be managed through the linked-loan confirmation workflow.",
        }), 409

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

    linked_transaction = LinkedLoanTransaction.query.filter_by(
        loan_workspace_id=workspace_id,
        personal_loan_id=loan.id,
        loan_payment_id=payment.id,
        transaction_type="repayment",
    ).first()

    if linked_transaction is not None:
        return jsonify({
            "success": False,
            "message": "Linked repayments must be managed through the linked-loan confirmation workflow.",
        }), 409

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

    linked_transaction = LinkedLoanTransaction.query.filter_by(
        loan_workspace_id=workspace_id,
        personal_loan_id=loan.id,
        loan_payment_id=payment.id,
        transaction_type="repayment",
    ).first()

    if linked_transaction is not None:
        return jsonify({
            "success": False,
            "message": "Linked repayments must be managed through the linked-loan confirmation workflow.",
        }), 409

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

        recipient_ids = {
            participant_id
            for participant_id in (
                loan.created_by,
                loan.counterparty_user_id,
            )
            if participant_id is not None and participant_id != user_id
        }
        for recipient_id in recipient_ids:
            create_notification(
                user_id=recipient_id,
                category="loan_repayments",
                title="Loan repayment confirmed",
                body="A repayment on your linked personal loan has been confirmed.",
                target_url="/loans",
                dedupe_key=f"personal-loan-payment-confirmed:{confirmed.id}",
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


@loans_bp.post("/<uuid:loan_id>/linked-disbursement/confirm")
@jwt_required()
def confirm_linked_loan_disbursement(loan_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A valid JSON request is required."}), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    account_id = _workspace_id(data.get("financial_account_id"))
    user_id = _user_id()

    if workspace_id is None or account_id is None:
        return jsonify({"success": False, "message": "Valid workspace_id and financial_account_id are required."}), 400

    membership = _membership(workspace_id, user_id)
    if membership is None:
        return jsonify({"success": False, "message": "Workspace membership is required."}), 404
    if membership.role == "viewer":
        return jsonify({"success": False, "message": "Viewers cannot confirm loan disbursements."}), 403

    try:
        loan = PersonalLoan.query.filter_by(
            id=loan_id, workspace_id=workspace_id
        ).with_for_update().first()

        if loan is None:
            return jsonify({"success": False, "message": "Loan not found."}), 404
        if loan.direction != "borrowed" or loan.created_by != user_id:
            return jsonify({"success": False, "message": "Only the borrower can confirm receipt."}), 403
        if loan.status != "awaiting_confirmation":
            return jsonify({"success": False, "message": "This loan is not awaiting disbursement confirmation."}), 409

        linked = LinkedLoanTransaction.query.filter_by(
            loan_workspace_id=workspace_id,
            personal_loan_id=loan.id,
            transaction_type="disbursement",
            status="pending",
        ).with_for_update().first()

        if linked is None or linked.initiating_journal_entry_id is None:
            return jsonify({"success": False, "message": "No pending disbursement is available to confirm."}), 409

        account = FinancialAccount.query.filter_by(
            id=account_id, workspace_id=workspace_id
        ).with_for_update().first()

        if (
            account is None or not account.is_active
            or account.account_type not in {"cash", "bank", "mobile_money"}
            or account.currency_code != loan.currency_code
        ):
            return jsonify({"success": False, "message": "Choose an active cash, bank, or mobile-money account in the loan currency."}), 400

        cash_ledger = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            financial_account_id=account.id,
        ).with_for_update().first()

        liability = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            code="2000",
            is_system=True,
        ).with_for_update().first()

        if (
            cash_ledger is None or not cash_ledger.is_active
            or cash_ledger.account_class != "asset"
            or cash_ledger.normal_balance != "debit"
            or cash_ledger.currency_code != loan.currency_code
        ):
            return jsonify({"success": False, "message": "The selected account has no valid active asset ledger."}), 400

        if (
            liability is None or not liability.is_active
            or liability.account_class != "liability"
            or liability.normal_balance != "credit"
            or liability.currency_code != loan.currency_code
            or liability.financial_account_id is not None
        ):
            return jsonify({"success": False, "message": "The required loan payable system ledger is unavailable."}), 400

        workspace = Workspace.query.filter_by(id=workspace_id).first()
        if workspace is None:
            return jsonify({"success": False, "message": "Workspace not found."}), 404

        fx_rate = None
        if workspace.functional_currency != loan.currency_code:
            rate_record = ExchangeRate.query.filter(
                ExchangeRate.base_currency == loan.currency_code,
                ExchangeRate.quote_currency == workspace.functional_currency,
                ExchangeRate.effective_at <= datetime.combine(
                    linked.transaction_date, datetime.max.time(), tzinfo=timezone.utc
                ),
            ).order_by(ExchangeRate.effective_at.desc()).first()

            if rate_record is None:
                return jsonify({"success": False, "message": "No exchange rate is configured for the disbursement date."}), 400
            fx_rate = rate_record.rate

        existing_count = LoanInstallment.query.filter_by(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
        ).count()
        if existing_count:
            return jsonify({"success": False, "message": "A repayment schedule already exists for this loan."}), 409

        schedule = build_repayment_schedule(loan)
        journal = create_draft_entry(
            workspace_id=workspace_id,
            created_by=user_id,
            reference=f"LINKED-DISB-RECEIPT-{linked.id.hex}",
            description=f"Receipt of loan principal from {loan.counterparty_name}",
            transaction_date=datetime.combine(
                linked.transaction_date, datetime.min.time(), tzinfo=timezone.utc
            ),
            lines=[
                {
                    "ledger_account_id": cash_ledger.id,
                    "debit_minor": linked.amount_minor,
                    "credit_minor": 0,
                    "exchange_rate": fx_rate,
                    "memo": "Linked loan principal received",
                },
                {
                    "ledger_account_id": liability.id,
                    "debit_minor": 0,
                    "credit_minor": linked.amount_minor,
                    "exchange_rate": fx_rate,
                    "memo": "Loan payable recognized",
                },
            ],
        )
        post_entry(
            entry_id=journal.id,
            workspace_id=workspace_id,
            posted_by=user_id,
        )

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

        linked.status = "confirmed"
        linked.confirming_workspace_id = workspace_id
        linked.confirming_account_id = account.id
        linked.confirming_journal_entry_id = journal.id
        linked.confirmed_by = user_id
        linked.confirmed_at = datetime.now(timezone.utc)
        loan.status = "active"

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="linked_loan_disbursement_confirmed",
            resource_type="linked_loan_transaction",
            resource_id=str(linked.id),
            details={
                "loan_id": str(loan.id),
                "amount_minor": linked.amount_minor,
                "journal_entry_id": str(journal.id),
                "installment_count": len(schedule),
            },
            ip_address=request.remote_addr,
        ))

        if loan.counterparty_user_id:
            create_notification(
                user_id=loan.counterparty_user_id,
                category="loan_application_updates",
                title="Loan receipt confirmed",
                body="The borrower confirmed receipt of your loan disbursement.",
                target_url="/loans",
                dedupe_key=f"linked-disbursement-confirmed:{linked.id}",
            )

        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Loan receipt confirmed and repayment schedule created.",
            "loan_status": loan.status,
            "journal_entry_id": str(journal.id),
            "installment_count": len(schedule),
        }), 200

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except AccountingError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "The disbursement could not be confirmed because of a data conflict."}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Linked loan disbursement confirmation failed.")
        return jsonify({"success": False, "message": "The disbursement could not be confirmed."}), 500


@loans_bp.post("/<uuid:loan_id>/linked-disbursement/dispute")
@jwt_required()
def dispute_linked_loan_disbursement(loan_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A valid JSON request is required."}), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    reason = data.get("reason")
    user_id = _user_id()

    if workspace_id is None:
        return jsonify({"success": False, "message": "A valid workspace_id is required."}), 400
    if not isinstance(reason, str) or not reason.strip() or len(reason.strip()) > 2000:
        return jsonify({"success": False, "message": "A dispute reason is required and must not exceed 2000 characters."}), 400

    membership = _membership(workspace_id, user_id)
    if membership is None:
        return jsonify({"success": False, "message": "Workspace membership is required."}), 404
    if membership.role == "viewer":
        return jsonify({"success": False, "message": "Viewers cannot dispute loan disbursements."}), 403

    try:
        loan = PersonalLoan.query.filter_by(
            id=loan_id, workspace_id=workspace_id
        ).with_for_update().first()

        if loan is None:
            return jsonify({"success": False, "message": "Loan not found."}), 404
        if loan.direction != "borrowed" or loan.created_by != user_id:
            return jsonify({"success": False, "message": "Only the borrower can dispute receipt."}), 403
        if loan.status != "awaiting_confirmation":
            return jsonify({"success": False, "message": "This loan is not awaiting disbursement confirmation."}), 409

        linked = LinkedLoanTransaction.query.filter_by(
            loan_workspace_id=workspace_id,
            personal_loan_id=loan.id,
            transaction_type="disbursement",
            status="pending",
        ).with_for_update().first()

        if linked is None or linked.initiating_journal_entry_id is None:
            return jsonify({"success": False, "message": "No pending disbursement is available to dispute."}), 409

        now = datetime.now(timezone.utc)
        reversal = reverse_entry(
            entry_id=linked.initiating_journal_entry_id,
            workspace_id=linked.initiating_workspace_id,
            reversed_by=user_id,
            reference=f"LINKED-DISB-DISPUTE-{linked.id.hex}",
            description=f"Reversal of disputed loan disbursement: {loan.counterparty_name}",
            transaction_date=now,
        )

        linked.status = "disputed"
        linked.reversal_journal_entry_id = reversal.id
        linked.reversed_by = user_id
        linked.reversed_at = now
        linked.disputed_by = user_id
        linked.dispute_reason = reason.strip()
        loan.status = "disputed"

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="linked_loan_disbursement_disputed",
            resource_type="linked_loan_transaction",
            resource_id=str(linked.id),
            details={
                "loan_id": str(loan.id),
                "reason": reason.strip(),
                "reversal_journal_entry_id": str(reversal.id),
            },
            ip_address=request.remote_addr,
        ))

        if loan.counterparty_user_id:
            create_notification(
                user_id=loan.counterparty_user_id,
                category="loan_application_updates",
                title="Loan disbursement disputed",
                body="The borrower disputed receipt of your loan disbursement. The disbursement accounting entry has been reversed.",
                target_url="/loans",
                dedupe_key=f"linked-disbursement-disputed:{linked.id}",
            )

        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Disbursement disputed and lender accounting reversed.",
            "loan_status": loan.status,
            "reversal_journal_entry_id": str(reversal.id),
        }), 200

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except AccountingError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "The disbursement dispute could not be recorded because of a data conflict."}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Linked loan disbursement dispute failed.")
        return jsonify({"success": False, "message": "The disbursement could not be disputed."}), 500




@loans_bp.post("/<uuid:loan_id>/linked-repayments/<uuid:payment_id>/confirm")
@jwt_required()
def confirm_linked_loan_repayment(loan_id, payment_id):
    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({"success": False, "message": "A valid JSON request is required."}), 400

    workspace_id = _workspace_id(data.get("workspace_id"))
    account_id = _workspace_id(data.get("financial_account_id"))
    user_id = _user_id()

    if workspace_id is None or account_id is None:
        return jsonify({"success": False, "message": "Valid workspace_id and financial_account_id are required."}), 400
    if user_id is None:
        return jsonify({"success": False, "message": "Invalid authentication identity."}), 401

    membership = _membership(workspace_id, user_id)
    if membership is None:
        return jsonify({"success": False, "message": "Workspace membership is required."}), 404
    if membership.role not in {"owner", "admin"}:
        return jsonify({"success": False, "message": "Only workspace owners or admins can confirm linked repayments."}), 403

    try:
        loan = PersonalLoan.query.filter_by(
            id=loan_id,
            direction="borrowed",
            counterparty_user_id=user_id,
        ).with_for_update().first()

        if loan is None:
            return jsonify({"success": False, "message": "Linked loan not found."}), 404

        if loan.status not in {"active", "overdue", "restructured"}:
            return jsonify({"success": False, "message": "This loan cannot receive repayments."}), 409

        disbursement = LinkedLoanTransaction.query.filter_by(
            loan_workspace_id=loan.workspace_id,
            personal_loan_id=loan.id,
            transaction_type="disbursement",
            status="confirmed",
        ).first()

        if disbursement is None:
            return jsonify({"success": False, "message": "The borrower has not confirmed receipt of the loan disbursement."}), 409

        linked = LinkedLoanTransaction.query.filter_by(
            loan_workspace_id=loan.workspace_id,
            personal_loan_id=loan.id,
            loan_payment_id=payment_id,
            transaction_type="repayment",
            status="pending",
        ).with_for_update().first()

        if linked is None:
            return jsonify({"success": False, "message": "No pending linked repayment was found."}), 404

        payment = LoanPayment.query.filter_by(
            id=payment_id,
            personal_loan_id=loan.id,
            workspace_id=loan.workspace_id,
        ).with_for_update().first()

        if payment is None:
            return jsonify({"success": False, "message": "Repayment not found."}), 404

        if (
            payment.status != "pending"
            or payment.payment_source != "linked"
            or payment.financial_account_id is not None
            or payment.journal_entry_id is not None
            or payment.amount_minor != linked.amount_minor
            or payment.payment_date != linked.transaction_date
            or payment.payment_method != linked.payment_method
            or payment.reference != linked.reference
        ):
            return jsonify({"success": False, "message": "The pending repayment details are inconsistent."}), 409

        workspace = Workspace.query.filter_by(id=workspace_id).first()
        if workspace is None:
            return jsonify({"success": False, "message": "Lender workspace not found."}), 404

        account = FinancialAccount.query.filter_by(
            id=account_id,
            workspace_id=workspace_id,
        ).with_for_update().first()

        if (
            account is None
            or not account.is_active
            or account.account_type not in {"cash", "bank", "mobile_money"}
            or account.currency_code != loan.currency_code
        ):
            return jsonify({"success": False, "message": "Choose an active cash, bank, or mobile-money account in the loan currency."}), 400

        account_ledgers = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            financial_account_id=account.id,
            is_active=True,
        ).with_for_update().all()

        if len(account_ledgers) != 1:
            return jsonify({"success": False, "message": "The selected account must have exactly one active ledger."}), 400

        cash_ledger = account_ledgers[0]
        if (
            cash_ledger.account_class != "asset"
            or cash_ledger.normal_balance != "debit"
            or cash_ledger.currency_code != loan.currency_code
        ):
            return jsonify({"success": False, "message": "The selected account has no valid active asset ledger."}), 400

        fx_rate = None
        if workspace.functional_currency != loan.currency_code:
            rate_record = ExchangeRate.query.filter(
                ExchangeRate.base_currency == loan.currency_code,
                ExchangeRate.quote_currency == workspace.functional_currency,
                ExchangeRate.effective_at <= datetime.combine(
                    linked.transaction_date,
                    datetime.max.time(),
                    tzinfo=timezone.utc,
                ),
            ).order_by(ExchangeRate.effective_at.desc()).first()

            if rate_record is None:
                return jsonify({"success": False, "message": "No exchange rate is configured for the repayment date."}), 400
            fx_rate = rate_record.rate

        installments = LoanInstallment.query.filter_by(
            workspace_id=loan.workspace_id,
            personal_loan_id=loan.id,
        ).order_by(
            LoanInstallment.due_date,
            LoanInstallment.installment_number,
        ).with_for_update().all()

        if not installments:
            return jsonify({"success": False, "message": "This loan has no repayment schedule."}), 409

        previous_allocations = LoanPaymentAllocation.query.filter_by(
            workspace_id=loan.workspace_id,
            personal_loan_id=loan.id,
        ).all()

        allocated_by_installment = {item.id: 0 for item in installments}
        for previous in previous_allocations:
            if previous.installment_id not in allocated_by_installment:
                return jsonify({"success": False, "message": "A previous repayment allocation references an invalid installment."}), 409
            allocated_by_installment[previous.installment_id] += previous.amount_minor

        for installment in installments:
            if allocated_by_installment[installment.id] != installment.paid_minor:
                return jsonify({"success": False, "message": "Installment balances do not match their payment allocations."}), 409

        allocations = build_payment_allocations(
            installments,
            previous_allocations,
            payment.amount_minor,
        )

        principal_total = sum(item["principal_minor"] for item in allocations)
        interest_total = sum(item["interest_minor"] for item in allocations)

        if principal_total + interest_total != payment.amount_minor:
            raise ValueError("Repayment allocation does not balance.")

        source_currency = db.session.get(Currency, loan.currency_code)
        functional_currency = db.session.get(Currency, workspace.functional_currency)

        if source_currency is None or functional_currency is None:
            raise ValueError("Repayment currency configuration is unavailable.")

        conversion_rate = Decimal(str(fx_rate)) if fx_rate is not None else Decimal("1")

        def convert_component(amount_minor):
            if amount_minor == 0:
                return 0
            converted = (
                Decimal(amount_minor)
                * conversion_rate
                * (
                    Decimal(10)
                    ** (
                        functional_currency.minor_unit
                        - source_currency.minor_unit
                    )
                )
            )
            result = int(converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP))
            if result < 0 or result > MAX_MINOR_UNITS:
                raise ValueError("Repayment functional amount is outside the supported range.")
            return result

        total_functional = convert_component(payment.amount_minor)
        principal_functional = convert_component(principal_total)
        interest_functional = convert_component(interest_total)

        if total_functional <= 0:
            raise ValueError("Repayment is too small in functional currency.")

        rounding_difference = total_functional - principal_functional - interest_functional
        if abs(rounding_difference) > 1:
            raise ValueError("Repayment FX rounding difference is unexpected.")

        if rounding_difference:
            if principal_total >= interest_total:
                principal_functional += rounding_difference
            else:
                interest_functional += rounding_difference

        if (
            principal_functional < 0
            or interest_functional < 0
            or principal_functional + interest_functional != total_functional
        ):
            raise ValueError("Repayment FX allocation is invalid.")

        receivable = LedgerAccount.query.filter_by(
            workspace_id=workspace_id,
            code="1200",
            is_system=True,
            is_active=True,
        ).with_for_update().first()

        if (
            receivable is None
            or receivable.account_class != "asset"
            or receivable.normal_balance != "debit"
            or receivable.currency_code != loan.currency_code
            or receivable.financial_account_id is not None
        ):
            return jsonify({"success": False, "message": "The lender's accounts-receivable system ledger is unavailable or incorrectly configured."}), 400

        income = None
        if interest_total:
            income = LedgerAccount.query.filter_by(
                workspace_id=workspace_id,
                code="4000",
                is_system=True,
                is_active=True,
            ).with_for_update().first()

            if (
                income is None
                or income.account_class != "income"
                or income.normal_balance != "credit"
                or income.currency_code != loan.currency_code
                or income.financial_account_id is not None
            ):
                return jsonify({"success": False, "message": "The lender's interest-income system ledger is unavailable or incorrectly configured."}), 400

        lines = [
            {
                "ledger_account_id": str(cash_ledger.id),
                "debit_minor": payment.amount_minor,
                "credit_minor": 0,
                "exchange_rate": fx_rate,
                "memo": "Linked loan repayment received",
            },
            {
                "ledger_account_id": str(receivable.id),
                "debit_minor": 0,
                "credit_minor": principal_total,
                "exchange_rate": fx_rate,
                "functional_amount_minor": principal_functional,
                "memo": "Loan principal repaid",
            },
        ]

        if interest_total:
            lines.append({
                "ledger_account_id": str(income.id),
                "debit_minor": 0,
                "credit_minor": interest_total,
                "exchange_rate": fx_rate,
                "functional_amount_minor": interest_functional,
                "memo": "Loan interest received",
            })

        journal = create_draft_entry(
            workspace_id=workspace_id,
            created_by=user_id,
            reference=f"LINKED-LOAN-REPAYMENT-{linked.id.hex}",
            description=f"Linked loan repayment received from {loan.counterparty_name}",
            transaction_date=datetime.combine(
                linked.transaction_date,
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

        installments_by_id = {item.id: item for item in installments}
        for allocation in allocations:
            installment = installments_by_id[allocation["installment_id"]]
            db.session.add(LoanPaymentAllocation(
                workspace_id=loan.workspace_id,
                personal_loan_id=loan.id,
                payment_id=payment.id,
                installment_id=installment.id,
                amount_minor=allocation["amount_minor"],
                principal_minor=allocation["principal_minor"],
                interest_minor=allocation["interest_minor"],
            ))
            installment.paid_minor += allocation["amount_minor"]

        for installment in installments:
            if installment.status == "waived":
                continue
            if installment.paid_minor == installment.amount_minor:
                installment.status = "paid"
            elif installment.due_date < payment.payment_date:
                installment.status = "overdue"
            elif installment.paid_minor > 0:
                installment.status = "partially_paid"
            else:
                installment.status = "scheduled"

        payment.principal_minor = principal_total
        payment.interest_minor = interest_total
        payment.status = "confirmed"
        # Deliberately leave borrower-scoped financial_account_id and
        # journal_entry_id null. Lender accounting is stored on `linked`.

        linked.status = "confirmed"
        linked.confirming_workspace_id = workspace_id
        linked.confirming_account_id = account.id
        linked.confirming_journal_entry_id = journal.id
        linked.confirmed_by = user_id
        linked.confirmed_at = datetime.now(timezone.utc)

        if all(item.status in {"paid", "waived"} for item in installments):
            loan.status = "paid"
        elif loan.status != "restructured":
            loan.status = (
                "overdue"
                if any(item.status == "overdue" for item in installments)
                else "active"
            )

        db.session.add(AuditLog(
            workspace_id=workspace_id,
            user_id=user_id,
            action="linked_loan_repayment_confirmed",
            resource_type="linked_loan_transaction",
            resource_id=str(linked.id),
            details={
                "loan_id": str(loan.id),
                "payment_id": str(payment.id),
                "amount_minor": payment.amount_minor,
                "principal_minor": principal_total,
                "interest_minor": interest_total,
                "journal_entry_id": str(journal.id),
            },
            ip_address=request.remote_addr,
        ))

        create_notification(
            user_id=loan.created_by,
            category="loan_repayments",
            title="Loan repayment confirmed",
            body="The lender confirmed receipt of your loan repayment.",
            target_url="/loans",
            dedupe_key=f"linked-loan-repayment-confirmed:{linked.id}",
        )

        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Loan repayment confirmed and lender accounting updated.",
            "loan_status": loan.status,
            "principal_minor": principal_total,
            "interest_minor": interest_total,
            "journal_entry_id": str(journal.id),
        }), 200

    except ValueError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except AccountingError as exc:
        db.session.rollback()
        return jsonify({"success": False, "message": str(exc)}), 400
    except IntegrityError:
        db.session.rollback()
        return jsonify({"success": False, "message": "The repayment could not be confirmed because of a data conflict."}), 409
    except Exception:
        db.session.rollback()
        current_app.logger.exception("Linked loan repayment confirmation failed.")
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

        is_counterparty_view = False
        if loan is None:
            loan = PersonalLoan.query.filter(
                PersonalLoan.id == loan_id,
                PersonalLoan.direction == "borrowed",
                PersonalLoan.counterparty_user_id == user_id,
                PersonalLoan.status.notin_(
                    ("draft", "pending_approval", "rejected")
                ),
            ).first()
            is_counterparty_view = loan is not None

        if loan is None:
            return jsonify({
                "success": False,
                "message": "Loan not found.",
            }), 404

        installments = (
            LoanInstallment.query
            .filter_by(
                workspace_id=loan.workspace_id,
                personal_loan_id=loan.id,
            )
            .order_by(
                LoanInstallment.installment_number.asc()
            )
            .all()
        )

        disbursement = LoanDisbursement.query.filter_by(
            workspace_id=loan.workspace_id,
            personal_loan_id=loan.id,
        ).first()

        linked_transactions = LinkedLoanTransaction.query.filter_by(
            loan_workspace_id=loan.workspace_id,
            personal_loan_id=loan.id,
        ).all()
        linked_disbursement = next(
            (item for item in linked_transactions if item.transaction_type == "disbursement"),
            None,
        )
        linked_repayments = {
            item.loan_payment_id: item
            for item in linked_transactions
            if item.transaction_type == "repayment" and item.loan_payment_id is not None
        }

        payments = (
            LoanPayment.query
            .filter_by(
                workspace_id=loan.workspace_id,
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
                    LoanPaymentAllocation.workspace_id == loan.workspace_id,
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
                "financial_account_id": (None if is_counterparty_view else str(disbursement.financial_account_id)),
                "journal_entry_id": (None if is_counterparty_view else str(disbursement.journal_entry_id)),
                "amount_minor": disbursement.amount_minor,
                "disbursement_date": disbursement.disbursement_date.isoformat(),
                "payment_method": disbursement.payment_method,
                "notes": disbursement.notes,
            }

        linked_disbursement_data = None
        if linked_disbursement is not None:
            linked_disbursement_data = {
                "id": str(linked_disbursement.id),
                "amount_minor": linked_disbursement.amount_minor,
                "transaction_date": linked_disbursement.transaction_date.isoformat(),
                "payment_method": linked_disbursement.payment_method,
                "reference": linked_disbursement.reference,
                "notes": linked_disbursement.notes,
                "status": linked_disbursement.status,
                "confirmed_at": (
                    linked_disbursement.confirmed_at.isoformat()
                    if linked_disbursement.confirmed_at else None
                ),
                "dispute_reason": linked_disbursement.dispute_reason,
            }

        payment_data = [
            {
                "id": str(payment.id),
                "linked_transaction_id": (
                    str(linked_repayments[payment.id].id)
                    if payment.id in linked_repayments else None
                ),
                "linked_status": (
                    linked_repayments[payment.id].status
                    if payment.id in linked_repayments else None
                ),
                "financial_account_id": (
                    str(payment.financial_account_id)
                    if payment.financial_account_id and not is_counterparty_view
                    else None
                ),
                "journal_entry_id": (
                    str(payment.journal_entry_id)
                    if payment.journal_entry_id and not is_counterparty_view
                    else None
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
            "loan": (
                _counterparty_loan_response(loan, workspace_id)
                if is_counterparty_view
                else _loan_response(loan)
            ),
            "disbursement": disbursement_data,
            "linked_disbursement": linked_disbursement_data,
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
