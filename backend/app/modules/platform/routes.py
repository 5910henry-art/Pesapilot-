"""Platform-level administration routes."""

from flask import jsonify
from datetime import date, datetime

from uuid import UUID, uuid4

from sqlalchemy import or_

from flask import current_app, request
from flask_jwt_extended import get_jwt_identity, jwt_required

from app.extensions import db
from app.services.identity import encrypt_national_id
from app.services.platform_affordability import estimate_affordable_principal
from app.services.platform_affordability_evidence import build_affordability_evidence_report
from app.models import (
    PlatformLoan,
    PlatformLoanApplication,
    PlatformLoanProduct,
    PlatformLoanInstallment,
    PlatformLoanDisbursement,
    PlatformLoanPayment,
    PlatformInvestor,
    PlatformInvestorContribution,
    PlatformInvestorWithdrawal,
    PlatformLoanFundingAllocation,
    PlatformLoanApplicationDetails,
    PlatformLoanAffordabilityAssessment,
    PlatformFinancialAccount,
    PlatformLedgerAccount,
    PlatformJournalEntry,
    PlatformAccountingSettings,
)
from app.models.core import (
    Currency, User, utc_now, Workspace, WorkspaceMember, FinancialAccount
)
from app.services.platform_lending import (
    PlatformLendingValidationError,
    create_platform_loan_from_application,
    disburse_platform_loan,
    confirm_platform_loan_payment,
    reject_platform_loan_payment,
    reverse_platform_loan_payment,
    review_loan_application,
    submit_loan_application,
    _platform_loan_due_date,
    record_platform_investor_contribution,
    record_platform_investor_withdrawal,
    reverse_platform_investor_contribution,
    reverse_platform_investor_withdrawal,
    record_platform_loan_payment,
)

from . import platform_bp
from .security import platform_owner_required


@platform_bp.get("/owner/access")
@platform_owner_required()
def check_owner_access():
    """Verify database-backed PesaPilot platform-owner access."""

    return jsonify({
        "success": True,
        "role": "owner",
    }), 200



@platform_bp.get("/owner/reports/portfolio")
@platform_owner_required()
def get_owner_platform_portfolio_report():
    """Return platform lending portfolio metrics grouped by currency."""

    try:
        today = datetime.now().astimezone().date()

        loans = PlatformLoan.query.all()
        disbursements = PlatformLoanDisbursement.query.all()
        payments = PlatformLoanPayment.query.all()
        installments = PlatformLoanInstallment.query.all()

        reports = {}

        def currency_report(currency_code):
            return reports.setdefault(currency_code, {
                "currency_code": currency_code,
                "loan_count": 0,
                "approved_principal_minor": 0,
                "disbursed_principal_minor": 0,
                "outstanding_principal_minor": 0,
                "confirmed_repayments_minor": 0,
                "confirmed_principal_repaid_minor": 0,
                "interest_collected_minor": 0,
                "pending_repayments_minor": 0,
                "reversed_repayments_minor": 0,
                "failed_repayments_minor": 0,
                "overdue_installment_count": 0,
                "overdue_loan_count": 0,
                "loan_status_counts": {},
                "funding_sources": {
                    "pesapilot_capital": 0,
                    "external_investor": 0,
                },
            })

        for loan in loans:
            report = currency_report(loan.currency_code)
            report["loan_count"] += 1
            report["approved_principal_minor"] += loan.approved_principal_minor
            report["outstanding_principal_minor"] += loan.outstanding_principal_minor

            status = loan.status
            report["loan_status_counts"][status] = (
                report["loan_status_counts"].get(status, 0) + 1
            )

        for item in disbursements:
            report = currency_report(item.currency_code)
            report["disbursed_principal_minor"] += item.amount_minor
            report["funding_sources"][item.funding_source] += item.amount_minor

        for payment in payments:
            report = currency_report(payment.currency_code)

            if payment.status == "confirmed":
                report["confirmed_repayments_minor"] += payment.amount_minor
                report["confirmed_principal_repaid_minor"] += payment.principal_minor
                report["interest_collected_minor"] += payment.interest_minor
            elif payment.status == "pending":
                report["pending_repayments_minor"] += payment.amount_minor
            elif payment.status == "reversed":
                report["reversed_repayments_minor"] += payment.amount_minor
            elif payment.status == "failed":
                report["failed_repayments_minor"] += payment.amount_minor

        overdue_loan_ids = set()

        for installment in installments:
            if (
                installment.due_date < today
                and installment.status != "waived"
                and installment.paid_minor < installment.amount_minor
            ):
                loan = next(
                    (item for item in loans if item.id == installment.loan_id),
                    None,
                )
                if loan is not None:
                    report = currency_report(loan.currency_code)
                    report["overdue_installment_count"] += 1
                    overdue_loan_ids.add(loan.id)

        for loan in loans:
            if loan.id in overdue_loan_ids:
                currency_report(loan.currency_code)["overdue_loan_count"] += 1

        return jsonify({
            "success": True,
            "as_of_date": today.isoformat(),
            "currencies": [
                reports[key] for key in sorted(reports)
            ],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform portfolio report failed"
        )
        return jsonify({
            "success": False,
            "message": "Portfolio report could not be generated.",
        }), 500


@platform_bp.get("/owner/collections")
@platform_owner_required()
def list_owner_platform_collections():
    """List pending repayments and overdue installments."""

    currency_code = request.args.get("currency_code", "").strip().upper()

    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))

        if page < 1 or per_page < 1:
            raise ValueError

        per_page = min(per_page, 100)

    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Page and per_page must be positive integers.",
        }), 400

    try:
        today = datetime.now().astimezone().date()

        pending_query = (
            db.session.query(
                PlatformLoanPayment,
                PlatformLoan,
                User.email,
            )
            .join(
                PlatformLoan,
                PlatformLoan.id == PlatformLoanPayment.loan_id,
            )
            .join(User, User.id == PlatformLoan.customer_id)
            .filter(
                PlatformLoanPayment.status == "pending",
                PlatformLoan.status.in_(["active", "overdue"]),
            )
        )

        overdue_query = (
            db.session.query(
                PlatformLoanInstallment,
                PlatformLoan,
                User.email,
            )
            .join(
                PlatformLoan,
                PlatformLoan.id == PlatformLoanInstallment.loan_id,
            )
            .join(User, User.id == PlatformLoan.customer_id)
            .filter(
                PlatformLoan.status.in_(["active", "overdue"]),
                PlatformLoanInstallment.due_date < today,
                PlatformLoanInstallment.status != "waived",
                PlatformLoanInstallment.paid_minor
                < PlatformLoanInstallment.amount_minor,
            )
        )

        if currency_code:
            pending_query = pending_query.filter(
                PlatformLoanPayment.currency_code == currency_code
            )
            overdue_query = overdue_query.filter(
                PlatformLoan.currency_code == currency_code
            )

        pending_total = pending_query.count()
        overdue_total = overdue_query.count()

        pending_rows = (
            pending_query
            .order_by(
                PlatformLoanPayment.payment_date.asc(),
                PlatformLoanPayment.created_at.asc(),
                PlatformLoanPayment.id.asc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        overdue_rows = (
            overdue_query
            .order_by(
                PlatformLoanInstallment.due_date.asc(),
                PlatformLoanInstallment.loan_id.asc(),
                PlatformLoanInstallment.installment_number.asc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        pending_payments = []

        for payment, loan, customer_email in pending_rows:
            pending_payments.append({
                "id": str(payment.id),
                "loan_id": str(loan.id),
                "customer_id": str(loan.customer_id),
                "customer_email": customer_email,
                "currency_code": payment.currency_code,
                "amount_minor": payment.amount_minor,
                "payment_date": payment.payment_date.isoformat(),
                "payment_method": payment.payment_method,
                "reference": payment.reference,
                "recorded_at": (
                    payment.created_at.isoformat()
                    if payment.created_at else None
                ),
            })

        overdue_installments = []

        for installment, loan, customer_email in overdue_rows:
            overdue_installments.append({
                "id": str(installment.id),
                "loan_id": str(loan.id),
                "customer_id": str(loan.customer_id),
                "customer_email": customer_email,
                "currency_code": loan.currency_code,
                "installment_number": installment.installment_number,
                "due_date": installment.due_date.isoformat(),
                "amount_minor": installment.amount_minor,
                "paid_minor": installment.paid_minor,
                "remaining_minor": (
                    installment.amount_minor - installment.paid_minor
                ),
            })

        return jsonify({
            "success": True,
            "as_of_date": today.isoformat(),
            "pending_payments": pending_payments,
            "overdue_installments": overdue_installments,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "pending_payments_total": pending_total,
                "pending_payments_total_pages": (
                    pending_total + per_page - 1
                ) // per_page,
                "overdue_installments_total": overdue_total,
                "overdue_installments_total_pages": (
                    overdue_total + per_page - 1
                ) // per_page,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform owner collections listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Collections could not be loaded.",
        }), 500


@platform_bp.get("/owner/applications")
@platform_owner_required()
def list_owner_platform_applications():
    """List platform lending applications for the platform owner."""

    status = request.args.get("status", "").strip().lower()
    allowed_statuses = {"pending", "approved", "rejected", "withdrawn"}

    if status and status not in allowed_statuses:
        return jsonify({
            "success": False,
            "message": "Invalid application status.",
        }), 400

    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))

        if page < 1 or per_page < 1:
            raise ValueError

        per_page = min(per_page, 100)

    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Page and per_page must be positive integers.",
        }), 400

    try:
        query = (
            db.session.query(
                PlatformLoanApplication,
                User.email,
                PlatformLoanProduct.name,
                PlatformLoanProduct.currency_code,
            )
            .join(
                User,
                User.id == PlatformLoanApplication.customer_id,
            )
            .join(
                PlatformLoanProduct,
                PlatformLoanProduct.id == PlatformLoanApplication.product_id,
            )
        )

        if status:
            query = query.filter(PlatformLoanApplication.status == status)

        total = query.count()

        rows = (
            query
            .order_by(
                PlatformLoanApplication.submitted_at.desc(),
                PlatformLoanApplication.id.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        application_ids = [row[0].id for row in rows]

        details_by_application = {}
        assessments_by_application = {}

        if application_ids:
            detail_rows = (
                PlatformLoanApplicationDetails.query
                .filter(
                    PlatformLoanApplicationDetails.application_id.in_(
                        application_ids
                    )
                )
                .all()
            )
            details_by_application = {
                detail.application_id: detail
                for detail in detail_rows
            }

            assessment_rows = (
                PlatformLoanAffordabilityAssessment.query
                .filter(
                    PlatformLoanAffordabilityAssessment.application_id.in_(
                        application_ids
                    )
                )
                .order_by(
                    PlatformLoanAffordabilityAssessment.assessed_at.desc(),
                    PlatformLoanAffordabilityAssessment.version.desc(),
                )
                .all()
            )

            for assessment in assessment_rows:
                assessments_by_application.setdefault(
                    assessment.application_id,
                    assessment,
                )

        allocated_loans = {}

        if application_ids:
            existing_loans = (
                PlatformLoan.query
                .filter(
                    PlatformLoan.application_id.in_(application_ids)
                )
                .all()
            )
            allocated_loans = {
                loan.application_id: loan
                for loan in existing_loans
            }

        applications = []

        for application, customer_email, product_name, currency_code in rows:
            item = _application_response(application)
            item.update({
                "customer_id": str(application.customer_id),
                "customer_email": customer_email,
                "product_name": product_name,
                "currency_code": currency_code,
                "loan_allocated": application.id in allocated_loans,
                "allocated_loan_id": (
                    str(allocated_loans[application.id].id)
                    if application.id in allocated_loans else None
                ),
                "reviewed_by": (
                    str(application.reviewed_by)
                    if application.reviewed_by else None
                ),
            })

            details = details_by_application.get(application.id)
            if details:
                item["applicant"] = {
                    "full_name": details.full_name,
                    "phone": details.phone,
                    "email": details.email,
                    "residential_address": details.residential_address,
                    "employment_status": details.employment_status,
                    "employer_business_name": details.employer_business_name,
                    "monthly_income_minor": details.monthly_income_minor,
                    "monthly_expenses_minor": details.monthly_expenses_minor,
                    "existing_monthly_repayments_minor": (
                        details.existing_monthly_repayments_minor
                    ),
                    "explanation": details.explanation,
                    "alternative_contact": {
                        "name": details.alternative_contact_name,
                        "phone": details.alternative_contact_phone,
                        "relationship": (
                            details.alternative_contact_relationship
                        ),
                    } if details.alternative_contact_name else None,
                    "additional_notes": details.additional_notes,
                    "affordability_consent": details.affordability_consent,
                    "affordability_consent_at": (
                        details.affordability_consent_at.isoformat()
                        if details.affordability_consent_at else None
                    ),
                }
            else:
                item["applicant"] = None

            assessment = assessments_by_application.get(application.id)
            if assessment:
                item["affordability_assessment"] = {
                    "id": str(assessment.id),
                    "version": assessment.version,
                    "status": assessment.status,
                    "source_workspace_id": (
                        str(assessment.source_workspace_id)
                        if assessment.source_workspace_id else None
                    ),
                    "evidence_window_months": (
                        assessment.evidence_window_months
                    ),
                    "recommended_monthly_instalment_minor": (
                        assessment.recommended_monthly_instalment_minor
                    ),
                    "estimated_affordable_principal_minor": (
                        assessment.estimated_affordable_principal_minor
                    ),
                    "report": assessment.report,
                    "reasons": assessment.reasons,
                    "assessed_at": assessment.assessed_at.isoformat(),
                }
            else:
                item["affordability_assessment"] = None

            applications.append(item)

        return jsonify({
            "success": True,
            "applications": applications,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform owner applications listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Applications could not be loaded.",
        }), 500


@platform_bp.post("/owner/applications/<application_id>/decision")
@platform_owner_required()
def decide_platform_application(application_id):
    """Approve or reject a pending platform lending application."""

    try:
        application_uuid = UUID(application_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid application ID.",
        }), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    decision = data.get("decision")
    if not isinstance(decision, str):
        return jsonify({
            "success": False,
            "message": "Decision must be approved or rejected.",
        }), 400

    decision = decision.strip().lower()
    if decision not in {"approved", "rejected"}:
        return jsonify({
            "success": False,
            "message": "Decision must be approved or rejected.",
        }), 400

    try:
        reviewer_id = _customer_id()
        if reviewer_id is None:
            return jsonify({
                "success": False,
                "message": "Invalid authentication identity.",
            }), 401

        application = review_loan_application(
            application_id=application_uuid,
            reviewer_id=reviewer_id,
            decision=decision,
            decision_notes=data.get("decision_notes"),
            approved_principal_minor=data.get("approved_principal_minor"),
        )

        if application is None:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Application not found.",
            }), 404

        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"Application {decision} successfully.",
            "application": _application_response(application),
        }), 200

    except PlatformLendingValidationError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400 if (
            "Decision" in str(exc)
            or "notes" in str(exc)
            or "Approved principal" in str(exc)
            or "Rejected applications" in str(exc)
        ) else 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan application decision failed"
        )
        return jsonify({
            "success": False,
            "message": "Application decision could not be saved.",
        }), 500



@platform_bp.post("/owner/applications/<application_id>/create-loan")
@platform_owner_required()
def create_owner_platform_loan(application_id):
    """Create a platform loan from an approved application."""

    try:
        application_uuid = UUID(application_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid application ID.",
        }), 400

    try:
        loan = create_platform_loan_from_application(
            application_id=application_uuid,
        )

        if loan is None:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Application not found.",
            }), 404

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Platform loan created successfully.",
            "loan": {
                "id": str(loan.id),
                "application_id": str(loan.application_id),
                "customer_id": str(loan.customer_id),
                "product_id": str(loan.product_id),
                "approved_principal_minor": loan.approved_principal_minor,
                "principal_minor": loan.principal_minor,
                "outstanding_principal_minor": loan.outstanding_principal_minor,
                "currency_code": loan.currency_code,
                "interest_method": loan.interest_method,
                "interest_rate_percent": str(loan.interest_rate_percent),
                "repayment_frequency": loan.repayment_frequency,
                "one_time_due_days": loan.one_time_due_days,
                "term_count": loan.term_count,
                "start_date": (
                    loan.start_date.isoformat() if loan.start_date else None
                ),
                "due_date": (
                    loan.due_date.isoformat() if loan.due_date else None
                ),
                "status": loan.status,
            },
        }), 201

    except PlatformLendingValidationError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan creation from application failed"
        )
        return jsonify({
            "success": False,
            "message": "Platform loan could not be created.",
        }), 500





@platform_bp.get("/owner/investors")
@platform_owner_required()
def list_owner_platform_investors():
    """List platform investors with their capital balances."""

    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        if page < 1 or per_page < 1:
            raise ValueError
        per_page = min(per_page, 100)
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Page and per_page must be positive integers.",
        }), 400

    try:
        query = PlatformInvestor.query
        search = request.args.get("search", "").strip()
        if search:
            query = query.filter(
                or_(
                    PlatformInvestor.name.ilike(f"%{search}%"),
                    PlatformInvestor.email.ilike(f"%{search}%"),
                    PlatformInvestor.phone.ilike(f"%{search}%"),
                )
            )

        active_filter = request.args.get("is_active")
        if active_filter in {"true", "false"}:
            query = query.filter(
                PlatformInvestor.is_active.is_(active_filter == "true")
            )

        total = query.count()
        investors = (
            query.order_by(
                PlatformInvestor.name.asc(),
                PlatformInvestor.id.asc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        results = []
        for investor in investors:
            contributions = PlatformInvestorContribution.query.filter_by(
                investor_id=investor.id,
                status="posted",
            ).all()
            withdrawals = PlatformInvestorWithdrawal.query.filter_by(
                investor_id=investor.id,
                status="posted",
            ).all()
            allocations = PlatformLoanFundingAllocation.query.filter(
                PlatformLoanFundingAllocation.investor_id == investor.id,
                PlatformLoanFundingAllocation.outstanding_minor > 0,
            ).all()

            currencies = set()
            for item in contributions + withdrawals:
                currencies.add(item.currency_code)
            for item in allocations:
                currencies.add(item.currency_code)

            capital = []
            for currency in sorted(currencies):
                contributed = sum(
                    item.amount_minor for item in contributions
                    if item.currency_code == currency
                )
                withdrawn = sum(
                    item.amount_minor for item in withdrawals
                    if item.currency_code == currency
                )
                allocated = sum(
                    item.outstanding_minor for item in allocations
                    if item.currency_code == currency
                )
                capital.append({
                    "currency_code": currency,
                    "contributed_minor": contributed,
                    "withdrawn_minor": withdrawn,
                    "allocated_minor": allocated,
                    "available_minor": contributed - withdrawn - allocated,
                })

            results.append({
                "id": str(investor.id),
                "name": investor.name,
                "email": investor.email,
                "phone": investor.phone,
                "notes": investor.notes,
                "is_active": investor.is_active,
                "created_at": (
                    investor.created_at.isoformat()
                    if investor.created_at else None
                ),
                "capital": capital,
            })

        return jsonify({
            "success": True,
            "investors": results,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Platform investor listing failed")
        return jsonify({
            "success": False,
            "message": "Investors could not be loaded.",
        }), 500


@platform_bp.post("/owner/investors")
@platform_owner_required()
def create_owner_platform_investor():
    """Register an investor in the platform lending operation."""

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return jsonify({
            "success": False,
            "message": "Investor name is required.",
        }), 400

    name = name.strip()
    if len(name) > 160:
        return jsonify({
            "success": False,
            "message": "Investor name cannot exceed 160 characters.",
        }), 400

    values = {}
    for field, limit in (("email", 254), ("phone", 30), ("notes", 5000)):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            return jsonify({
                "success": False,
                "message": f"Investor {field} must be text.",
            }), 400
        value = value.strip() if isinstance(value, str) else None
        if value and len(value) > limit:
            return jsonify({
                "success": False,
                "message": f"Investor {field} cannot exceed {limit} characters.",
            }), 400
        values[field] = value or None

    try:
        investor = PlatformInvestor(
            name=name,
            email=values["email"],
            phone=values["phone"],
            notes=values["notes"],
            is_active=True,
            created_by=owner_id,
        )
        db.session.add(investor)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Investor registered successfully.",
            "investor": {
                "id": str(investor.id),
                "name": investor.name,
                "email": investor.email,
                "phone": investor.phone,
                "notes": investor.notes,
                "is_active": investor.is_active,
                "created_at": (
                    investor.created_at.isoformat()
                    if investor.created_at else None
                ),
            },
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Platform investor registration failed")
        return jsonify({
            "success": False,
            "message": "Investor could not be registered.",
        }), 500


@platform_bp.get("/owner/investors/<investor_id>")
@platform_owner_required()
def get_owner_platform_investor(investor_id):
    """Return investor details, capital history, and active loan allocations."""

    try:
        investor_uuid = UUID(investor_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid investor ID.",
        }), 400

    try:
        investor = db.session.get(PlatformInvestor, investor_uuid)
        if investor is None:
            return jsonify({
                "success": False,
                "message": "Investor not found.",
            }), 404

        contributions = (
            PlatformInvestorContribution.query
            .filter_by(investor_id=investor.id)
            .order_by(
                PlatformInvestorContribution.contribution_date.desc(),
                PlatformInvestorContribution.id.desc(),
            )
            .all()
        )
        withdrawals = (
            PlatformInvestorWithdrawal.query
            .filter_by(investor_id=investor.id)
            .order_by(
                PlatformInvestorWithdrawal.withdrawal_date.desc(),
                PlatformInvestorWithdrawal.id.desc(),
            )
            .all()
        )
        allocations = (
            PlatformLoanFundingAllocation.query
            .filter_by(investor_id=investor.id)
            .order_by(PlatformLoanFundingAllocation.created_at.desc())
            .all()
        )

        currencies = {
            item.currency_code
            for item in contributions + withdrawals
        }
        currencies.update(item.currency_code for item in allocations)

        capital = []
        for currency in sorted(currencies):
            contributed = sum(
                item.amount_minor for item in contributions
                if item.currency_code == currency and item.status == "posted"
            )
            withdrawn = sum(
                item.amount_minor for item in withdrawals
                if item.currency_code == currency and item.status == "posted"
            )
            allocated = sum(
                item.outstanding_minor for item in allocations
                if item.currency_code == currency
            )
            capital.append({
                "currency_code": currency,
                "contributed_minor": contributed,
                "withdrawn_minor": withdrawn,
                "allocated_minor": allocated,
                "available_minor": contributed - withdrawn - allocated,
            })

        return jsonify({
            "success": True,
            "investor": {
                "id": str(investor.id),
                "name": investor.name,
                "email": investor.email,
                "phone": investor.phone,
                "notes": investor.notes,
                "is_active": investor.is_active,
                "created_at": (
                    investor.created_at.isoformat()
                    if investor.created_at else None
                ),
                "updated_at": (
                    investor.updated_at.isoformat()
                    if investor.updated_at else None
                ),
            },
            "capital": capital,
            "contributions": [{
                "id": str(item.id),
                "financial_account_id": str(item.financial_account_id),
                "journal_entry_id": str(item.journal_entry_id),
                "currency_code": item.currency_code,
                "amount_minor": item.amount_minor,
                "contribution_date": item.contribution_date.isoformat(),
                "reference": item.reference,
                "status": item.status,
                "recorded_by": str(item.recorded_by),
                "notes": item.notes,
            } for item in contributions],
            "withdrawals": [{
                "id": str(item.id),
                "financial_account_id": str(item.financial_account_id),
                "journal_entry_id": str(item.journal_entry_id),
                "currency_code": item.currency_code,
                "amount_minor": item.amount_minor,
                "withdrawal_date": item.withdrawal_date.isoformat(),
                "reference": item.reference,
                "status": item.status,
                "recorded_by": str(item.recorded_by),
                "notes": item.notes,
            } for item in withdrawals],
            "allocations": [{
                "id": str(item.id),
                "loan_id": str(item.loan_id),
                "disbursement_id": str(item.disbursement_id),
                "currency_code": item.currency_code,
                "amount_minor": item.amount_minor,
                "outstanding_minor": item.outstanding_minor,
                "created_at": (
                    item.created_at.isoformat() if item.created_at else None
                ),
            } for item in allocations],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Platform investor detail retrieval failed")
        return jsonify({
            "success": False,
            "message": "Investor details could not be loaded.",
        }), 500


def _record_owner_investor_capital_transaction(
    investor_id, transaction_type
):
    """Validate and record an investor contribution or withdrawal."""

    try:
        investor_uuid = UUID(investor_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid investor ID.",
        }), 400

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    try:
        account_id = UUID(str(data.get("financial_account_id", "")))
        amount_minor = data.get("amount_minor")
        currency_code = data.get("currency_code")
        if not isinstance(currency_code, str) or not currency_code.strip():
            raise ValueError
        currency_code = currency_code.strip().upper()

        raw_date = data.get(
            "contribution_date" if transaction_type == "contribution"
            else "withdrawal_date"
        )
        if not isinstance(raw_date, str):
            raise ValueError
        transaction_date = datetime.fromisoformat(
            raw_date.replace("Z", "+00:00")
        )
        if (
            transaction_date.tzinfo is None
            or transaction_date.utcoffset() is None
        ):
            raise ValueError

    except (ValueError, TypeError, OverflowError):
        return jsonify({
            "success": False,
            "message": (
                "A valid financial account ID, currency code, and timezone-aware "
                "ISO 8601 transaction date are required."
            ),
        }), 400

    service = (
        record_platform_investor_contribution
        if transaction_type == "contribution"
        else record_platform_investor_withdrawal
    )
    date_field = (
        "contribution_date" if transaction_type == "contribution"
        else "withdrawal_date"
    )

    try:
        transaction = service(
            investor_id=investor_uuid,
            financial_account_id=account_id,
            amount_minor=amount_minor,
            currency_code=currency_code,
            **{
                date_field: transaction_date,
                "recorded_by": owner_id,
                "reference": data.get("reference"),
                "notes": data.get("notes"),
            },
        )
        db.session.commit()

        return jsonify({
            "success": True,
            "message": (
                "Investor contribution recorded successfully."
                if transaction_type == "contribution"
                else "Investor withdrawal recorded successfully."
            ),
            transaction_type: {
                "id": str(transaction.id),
                "investor_id": str(transaction.investor_id),
                "financial_account_id": str(transaction.financial_account_id),
                "journal_entry_id": str(transaction.journal_entry_id),
                "currency_code": transaction.currency_code,
                "amount_minor": transaction.amount_minor,
                date_field: getattr(transaction, date_field).isoformat(),
                "reference": transaction.reference,
                "status": transaction.status,
                "recorded_by": str(transaction.recorded_by),
                "notes": transaction.notes,
            },
        }), 201

    except PlatformLendingValidationError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform investor %s recording failed",
            transaction_type,
        )
        return jsonify({
            "success": False,
            "message": "Investor capital transaction could not be recorded.",
        }), 500


@platform_bp.post("/owner/investors/<investor_id>/contributions")
@platform_owner_required()
def record_owner_platform_investor_contribution(investor_id):
    return _record_owner_investor_capital_transaction(
        investor_id,
        "contribution",
    )


@platform_bp.post("/owner/investors/<investor_id>/withdrawals")
@platform_owner_required()
def record_owner_platform_investor_withdrawal(investor_id):
    return _record_owner_investor_capital_transaction(
        investor_id,
        "withdrawal",
    )



def _reverse_owner_investor_capital_transaction(transaction_id, transaction_type):
    """Reverse an investor contribution or withdrawal."""

    try:
        transaction_uuid = UUID(transaction_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid investor transaction ID.",
        }), 400

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    reference = data.get("reference")
    raw_date = data.get("transaction_date")
    transaction_date = None

    if raw_date is not None:
        if not isinstance(raw_date, str):
            return jsonify({
                "success": False,
                "message": "Transaction date must be a timezone-aware ISO 8601 date.",
            }), 400
        try:
            transaction_date = datetime.fromisoformat(
                raw_date.replace("Z", "+00:00")
            )
            if (
                transaction_date.tzinfo is None
                or transaction_date.utcoffset() is None
            ):
                raise ValueError
        except (ValueError, TypeError, OverflowError):
            return jsonify({
                "success": False,
                "message": "Transaction date must be a timezone-aware ISO 8601 date.",
            }), 400

    service = (
        reverse_platform_investor_contribution
        if transaction_type == "contribution"
        else reverse_platform_investor_withdrawal
    )

    try:
        transaction = service(
            **{
                f"{transaction_type}_id": transaction_uuid,
                "reversed_by": owner_id,
                "reference": reference,
                "transaction_date": transaction_date,
            }
        )
        db.session.commit()

        return jsonify({
            "success": True,
            "message": f"Investor {transaction_type} reversed successfully.",
            transaction_type: {
                "id": str(transaction.id),
                "investor_id": str(transaction.investor_id),
                "journal_entry_id": str(transaction.journal_entry_id),
                "currency_code": transaction.currency_code,
                "amount_minor": transaction.amount_minor,
                "status": transaction.status,
                "reference": transaction.reference,
                "reversed_by": str(owner_id),
            },
        }), 200

    except PlatformLendingValidationError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform investor %s reversal failed",
            transaction_type,
        )
        return jsonify({
            "success": False,
            "message": "Investor capital transaction could not be reversed.",
        }), 500


@platform_bp.post("/owner/investors/contributions/<contribution_id>/reverse")
@platform_owner_required()
def reverse_owner_platform_investor_contribution(contribution_id):
    return _reverse_owner_investor_capital_transaction(
        contribution_id,
        "contribution",
    )


@platform_bp.post("/owner/investors/withdrawals/<withdrawal_id>/reverse")
@platform_owner_required()
def reverse_owner_platform_investor_withdrawal(withdrawal_id):
    return _reverse_owner_investor_capital_transaction(
        withdrawal_id,
        "withdrawal",
    )


@platform_bp.get("/owner/loans")
@platform_owner_required()
def list_owner_platform_loans():
    """List the platform lending portfolio for the platform owner."""

    status = request.args.get("status", "").strip().lower()
    allowed_statuses = {
        "created", "funding", "active", "paid",
        "overdue", "written_off", "cancelled",
    }

    if status and status not in allowed_statuses:
        return jsonify({
            "success": False,
            "message": "Invalid loan status.",
        }), 400

    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))

        if page < 1 or per_page < 1:
            raise ValueError

        per_page = min(per_page, 100)

    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Page and per_page must be positive integers.",
        }), 400

    try:
        query = (
            db.session.query(
                PlatformLoan,
                User.email,
                PlatformLoanProduct.name,
            )
            .join(User, User.id == PlatformLoan.customer_id)
            .join(
                PlatformLoanProduct,
                PlatformLoanProduct.id == PlatformLoan.product_id,
            )
        )

        if status:
            query = query.filter(PlatformLoan.status == status)

        total = query.count()

        rows = (
            query
            .order_by(
                PlatformLoan.created_at.desc(),
                PlatformLoan.id.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        loans = []

        for loan, customer_email, product_name in rows:
            loans.append({
                "id": str(loan.id),
                "application_id": str(loan.application_id),
                "customer_id": str(loan.customer_id),
                "customer_email": customer_email,
                "product_id": str(loan.product_id),
                "product_name": product_name,
                "approved_principal_minor": loan.approved_principal_minor,
                "disbursed_principal_minor": loan.principal_minor,
                "outstanding_principal_minor": loan.outstanding_principal_minor,
                "currency_code": loan.currency_code,
                "interest_method": loan.interest_method,
                "interest_rate_percent": str(loan.interest_rate_percent),
                "repayment_frequency": loan.repayment_frequency,
                "one_time_due_days": loan.one_time_due_days,
                "term_count": loan.term_count,
                "start_date": (
                    loan.start_date.isoformat() if loan.start_date else None
                ),
                "due_date": (
                    loan.due_date.isoformat() if loan.due_date else None
                ),
                "status": loan.status,
                "created_at": (
                    loan.created_at.isoformat() if loan.created_at else None
                ),
            })

        return jsonify({
            "success": True,
            "loans": loans,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform owner loans listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Loans could not be loaded.",
        }), 500


@platform_bp.get("/owner/loans/<loan_id>")
@platform_owner_required()
def get_owner_platform_loan(loan_id):
    """Return complete platform loan details for the platform owner."""

    try:
        loan_uuid = UUID(loan_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid loan ID.",
        }), 400

    try:
        row = (
            db.session.query(
                PlatformLoan,
                User.email,
                PlatformLoanProduct.name,
            )
            .join(User, User.id == PlatformLoan.customer_id)
            .join(
                PlatformLoanProduct,
                PlatformLoanProduct.id == PlatformLoan.product_id,
            )
            .filter(PlatformLoan.id == loan_uuid)
            .one_or_none()
        )

        if row is None:
            return jsonify({
                "success": False,
                "message": "Platform loan not found.",
            }), 404

        loan, customer_email, product_name = row

        installments = (
            PlatformLoanInstallment.query
            .filter_by(loan_id=loan.id)
            .order_by(
                PlatformLoanInstallment.installment_number.asc()
            )
            .all()
        )

        disbursements = (
            PlatformLoanDisbursement.query
            .filter_by(loan_id=loan.id)
            .order_by(
                PlatformLoanDisbursement.disbursement_date.asc(),
                PlatformLoanDisbursement.id.asc(),
            )
            .all()
        )

        payments = (
            PlatformLoanPayment.query
            .filter_by(loan_id=loan.id)
            .order_by(
                PlatformLoanPayment.payment_date.desc(),
                PlatformLoanPayment.created_at.desc(),
                PlatformLoanPayment.id.desc(),
            )
            .all()
        )

        confirmed_payments = [
            payment for payment in payments
            if payment.status == "confirmed"
        ]

        loan_data = {
            "id": str(loan.id),
            "application_id": str(loan.application_id),
            "customer_id": str(loan.customer_id),
            "customer_email": customer_email,
            "product_id": str(loan.product_id),
            "product_name": product_name,
            "approved_principal_minor": loan.approved_principal_minor,
            "disbursed_principal_minor": loan.principal_minor,
            "outstanding_principal_minor": loan.outstanding_principal_minor,
            "currency_code": loan.currency_code,
            "interest_method": loan.interest_method,
            "interest_rate_percent": str(loan.interest_rate_percent),
            "repayment_frequency": loan.repayment_frequency,
            "one_time_due_days": loan.one_time_due_days,
            "term_count": loan.term_count,
            "start_date": (
                loan.start_date.isoformat() if loan.start_date else None
            ),
            "due_date": (
                loan.due_date.isoformat() if loan.due_date else None
            ),
            "status": loan.status,
            "created_at": (
                loan.created_at.isoformat() if loan.created_at else None
            ),
            "updated_at": (
                loan.updated_at.isoformat() if loan.updated_at else None
            ),
        }

        installment_data = [{
            "id": str(item.id),
            "installment_number": item.installment_number,
            "due_date": item.due_date.isoformat(),
            "amount_minor": item.amount_minor,
            "principal_minor": item.principal_minor,
            "interest_minor": item.interest_minor,
            "paid_minor": item.paid_minor,
            "remaining_minor": item.amount_minor - item.paid_minor,
            "status": item.status,
        } for item in installments]

        disbursement_data = [{
            "id": str(item.id),
            "financial_account_id": str(item.financial_account_id),
            "journal_entry_id": str(item.journal_entry_id),
            "customer_workspace_id": (
                str(item.customer_workspace_id)
                if item.customer_workspace_id else None
            ),
            "customer_financial_account_id": (
                str(item.customer_financial_account_id)
                if item.customer_financial_account_id else None
            ),
            "customer_journal_entry_id": (
                str(item.customer_journal_entry_id)
                if item.customer_journal_entry_id else None
            ),
            "currency_code": item.currency_code,
            "amount_minor": item.amount_minor,
            "funding_source": item.funding_source,
            "disbursement_date": item.disbursement_date.isoformat(),
            "recorded_by": str(item.recorded_by),
            "notes": item.notes,
            "created_at": item.created_at.isoformat(),
        } for item in disbursements]

        payment_data = [{
            "id": str(item.id),
            "financial_account_id": (
                str(item.financial_account_id)
                if item.financial_account_id else None
            ),
            "journal_entry_id": (
                str(item.journal_entry_id)
                if item.journal_entry_id else None
            ),
            "currency_code": item.currency_code,
            "amount_minor": item.amount_minor,
            "principal_minor": item.principal_minor,
            "interest_minor": item.interest_minor,
            "payment_date": item.payment_date.isoformat(),
            "payment_method": item.payment_method,
            "reference": item.reference,
            "status": item.status,
            "recorded_by": str(item.recorded_by),
            "confirmed_by": (
                str(item.confirmed_by) if item.confirmed_by else None
            ),
            "notes": item.notes,
            "confirmed_at": (
                item.confirmed_at.isoformat()
                if item.confirmed_at else None
            ),
            "reversed_at": (
                item.reversed_at.isoformat()
                if item.reversed_at else None
            ),
            "created_at": item.created_at.isoformat(),
            "updated_at": item.updated_at.isoformat(),
        } for item in payments]

        return jsonify({
            "success": True,
            "loan": loan_data,
            "installments": installment_data,
            "disbursements": disbursement_data,
            "payments": payment_data,
            "summary": {
                "total_disbursed_minor": sum(
                    item.amount_minor for item in disbursements
                ),
                "outstanding_principal_minor": (
                    loan.outstanding_principal_minor
                ),
                "confirmed_repayments_minor": sum(
                    item.amount_minor for item in confirmed_payments
                ),
                "confirmed_principal_repaid_minor": sum(
                    item.principal_minor for item in confirmed_payments
                ),
                "confirmed_interest_collected_minor": sum(
                    item.interest_minor for item in confirmed_payments
                ),
                "pending_repayments_minor": sum(
                    item.amount_minor for item in payments
                    if item.status == "pending"
                ),
                "reversed_repayments_minor": sum(
                    item.amount_minor for item in payments
                    if item.status == "reversed"
                ),
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform owner loan detail retrieval failed"
        )
        return jsonify({
            "success": False,
            "message": "Loan details could not be loaded.",
        }), 500




@platform_bp.get("/owner/loans/<loan_id>/customer-accounts")
@platform_owner_required()
def get_owner_platform_loan_customer_accounts(loan_id):
    """List eligible personal receiving accounts for a platform loan borrower."""

    try:
        loan_uuid = UUID(loan_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid loan ID.",
        }), 400

    try:
        loan = db.session.get(PlatformLoan, loan_uuid)

        if loan is None:
            return jsonify({
                "success": False,
                "message": "Platform loan not found.",
            }), 404

        workspaces = (
            Workspace.query
            .join(
                WorkspaceMember,
                WorkspaceMember.workspace_id == Workspace.id,
            )
            .filter(
                WorkspaceMember.user_id == loan.customer_id,
                WorkspaceMember.role == "owner",
                Workspace.workspace_type == "personal",
            )
            .all()
        )

        if len(workspaces) != 1:
            return jsonify({
                "success": False,
                "message": (
                    "The borrower must have exactly one owned personal workspace."
                ),
            }), 409

        workspace = workspaces[0]

        accounts = (
            FinancialAccount.query
            .filter(
                FinancialAccount.workspace_id == workspace.id,
                FinancialAccount.is_active.is_(True),
                FinancialAccount.account_type.in_(
                    ["cash", "bank", "mobile_money"]
                ),
                FinancialAccount.currency_code == loan.currency_code,
            )
            .order_by(FinancialAccount.name.asc())
            .all()
        )

        return jsonify({
            "success": True,
            "currency_code": loan.currency_code,
            "accounts": [{
                "id": str(account.id),
                "name": account.name,
                "account_type": account.account_type,
                "currency_code": account.currency_code,
            } for account in accounts],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan borrower accounts could not be loaded"
        )
        return jsonify({
            "success": False,
            "message": "Borrower receiving accounts could not be loaded.",
        }), 500


@platform_bp.post("/owner/loans/<loan_id>/disburse")
@platform_owner_required()
def disburse_owner_platform_loan(loan_id):
    """Record a platform loan disbursement and its accounting entry."""

    try:
        loan_uuid = UUID(loan_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid loan ID.",
        }), 400

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    if "one_time_due_date" in data:
        return jsonify({
            "success": False,
            "message": (
                "Manual one-time due dates are no longer accepted. "
                "The loan product maturity period determines the due date."
            ),
        }), 400

    try:
        account_id = UUID(str(data.get("financial_account_id", "")))
        customer_account_id = UUID(
            str(data.get("customer_financial_account_id", ""))
        )
        amount_minor = data.get("amount_minor")
        funding_source = data.get("funding_source")

        raw_disbursement_date = data.get("disbursement_date")
        if not isinstance(raw_disbursement_date, str):
            raise ValueError("Disbursement date is required.")

        disbursement_date = datetime.fromisoformat(
            raw_disbursement_date.replace("Z", "+00:00")
        )

    except (ValueError, TypeError, OverflowError):
        return jsonify({
            "success": False,
            "message": "Invalid account ID or date format.",
        }), 400

    try:
        disbursement = disburse_platform_loan(
            loan_id=loan_uuid,
            financial_account_id=account_id,
            customer_financial_account_id=customer_account_id,
            amount_minor=amount_minor,
            funding_source=funding_source,
            investor_allocations=data.get("investor_allocations"),
            disbursement_date=disbursement_date,
            recorded_by=owner_id,
            notes=data.get("notes"),
        )

        if disbursement is None:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Platform loan not found.",
            }), 404

        loan = db.session.get(PlatformLoan, loan_uuid)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Platform loan disbursement recorded successfully.",
            "disbursement": {
                "id": str(disbursement.id),
                "loan_id": str(disbursement.loan_id),
                "financial_account_id": str(disbursement.financial_account_id),
                "journal_entry_id": str(disbursement.journal_entry_id),
                "customer_workspace_id": str(
                    disbursement.customer_workspace_id
                ),
                "customer_financial_account_id": str(
                    disbursement.customer_financial_account_id
                ),
                "customer_journal_entry_id": str(
                    disbursement.customer_journal_entry_id
                ),
                "amount_minor": disbursement.amount_minor,
                "currency_code": disbursement.currency_code,
                "funding_source": disbursement.funding_source,
                "disbursement_date": disbursement.disbursement_date.isoformat(),
                "recorded_by": str(disbursement.recorded_by),
                "notes": disbursement.notes,
            },
            "loan": {
                "id": str(loan.id),
                "approved_principal_minor": loan.approved_principal_minor,
                "principal_minor": loan.principal_minor,
                "outstanding_principal_minor": loan.outstanding_principal_minor,
                "currency_code": loan.currency_code,
                "start_date": (
                    loan.start_date.isoformat() if loan.start_date else None
                ),
                "due_date": (
                    loan.due_date.isoformat() if loan.due_date else None
                ),
                "status": loan.status,
            },
        }), 201

    except PlatformLendingValidationError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Platform loan disbursement failed")
        return jsonify({
            "success": False,
            "message": "Platform loan disbursement could not be recorded.",
        }), 500



@platform_bp.post("/owner/collections/payments/<payment_id>/confirm")
@platform_owner_required()
def confirm_owner_platform_loan_payment(payment_id):
    """Confirm a pending platform loan repayment."""

    try:
        payment_uuid = UUID(payment_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid payment ID.",
        }), 400

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    try:
        account_id = UUID(str(data.get("financial_account_id", "")))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "A valid financial account ID is required.",
        }), 400

    try:
        payment = confirm_platform_loan_payment(
            payment_id=payment_uuid,
            financial_account_id=account_id,
            confirmed_by=owner_id,
        )

        if payment is None:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Platform loan payment not found.",
            }), 404

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Platform loan repayment confirmed successfully.",
            "payment": {
                "id": str(payment.id),
                "loan_id": str(payment.loan_id),
                "financial_account_id": str(payment.financial_account_id),
                "journal_entry_id": str(payment.journal_entry_id),
                "currency_code": payment.currency_code,
                "amount_minor": payment.amount_minor,
                "principal_minor": payment.principal_minor,
                "interest_minor": payment.interest_minor,
                "payment_date": payment.payment_date.isoformat(),
                "payment_method": payment.payment_method,
                "reference": payment.reference,
                "status": payment.status,
                "confirmed_by": str(payment.confirmed_by),
                "confirmed_at": (
                    payment.confirmed_at.isoformat()
                    if payment.confirmed_at else None
                ),
            },
        }), 200

    except (PlatformLendingValidationError, ValueError) as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan repayment confirmation failed"
        )
        return jsonify({
            "success": False,
            "message": "Platform loan repayment could not be confirmed.",
        }), 500



@platform_bp.post("/owner/collections/payments/<payment_id>/reject")
@platform_owner_required()
def reject_owner_platform_loan_payment(payment_id):
    """Reject a pending customer-reported platform loan repayment."""

    try:
        payment_uuid = UUID(payment_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid payment ID.",
        }), 400

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    reason = data.get("reason")
    if not isinstance(reason, str) or not reason.strip():
        return jsonify({
            "success": False,
            "message": "A rejection reason is required.",
        }), 400

    reason = reason.strip()
    if len(reason) > 500:
        return jsonify({
            "success": False,
            "message": "Rejection reason cannot exceed 500 characters.",
        }), 400

    try:
        payment = reject_platform_loan_payment(
            payment_id=payment_uuid,
            rejected_by=owner_id,
            reason=reason,
        )

        if payment is None:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Platform loan payment not found.",
            }), 404

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Reported repayment rejected successfully.",
            "payment": {
                "id": str(payment.id),
                "loan_id": str(payment.loan_id),
                "amount_minor": payment.amount_minor,
                "currency_code": payment.currency_code,
                "reference": payment.reference,
                "status": payment.status,
                "rejected_by": str(payment.rejected_by),
                "rejected_at": (
                    payment.rejected_at.isoformat()
                    if payment.rejected_at else None
                ),
                "rejection_reason": payment.rejection_reason,
            },
        }), 200

    except (PlatformLendingValidationError, ValueError) as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan repayment rejection failed"
        )
        return jsonify({
            "success": False,
            "message": "Platform loan repayment could not be rejected.",
        }), 500


@platform_bp.post("/owner/collections/payments/<payment_id>/reverse")
@platform_owner_required()
def reverse_owner_platform_loan_payment(payment_id):
    """Reverse a confirmed platform loan repayment."""

    try:
        payment_uuid = UUID(payment_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid payment ID.",
        }), 400

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    reference = data.get("reference")
    if not isinstance(reference, str) or not reference.strip():
        return jsonify({
            "success": False,
            "message": "A reversal reference is required.",
        }), 400

    reference = reference.strip()
    if len(reference) > 100:
        return jsonify({
            "success": False,
            "message": "Reversal reference cannot exceed 100 characters.",
        }), 400

    raw_transaction_date = data.get("transaction_date")
    transaction_date = None

    if raw_transaction_date is not None:
        if not isinstance(raw_transaction_date, str):
            return jsonify({
                "success": False,
                "message": "Transaction date must be an ISO 8601 timestamp.",
            }), 400

        try:
            transaction_date = datetime.fromisoformat(
                raw_transaction_date.replace("Z", "+00:00")
            )
        except (ValueError, TypeError, OverflowError):
            return jsonify({
                "success": False,
                "message": "Invalid transaction date.",
            }), 400

        if (
            transaction_date.tzinfo is None
            or transaction_date.utcoffset() is None
        ):
            return jsonify({
                "success": False,
                "message": "Transaction date must include a timezone.",
            }), 400

    try:
        reversal = reverse_platform_loan_payment(
            payment_id=payment_uuid,
            reversed_by=owner_id,
            reference=reference,
            transaction_date=transaction_date,
        )

        payment = db.session.get(
            PlatformLoanPayment,
            payment_uuid,
        )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Platform loan repayment reversed successfully.",
            "reversal": {
                "journal_entry_id": str(reversal.id),
                "reference": reversal.reference,
                "status": reversal.status,
                "transaction_date": reversal.transaction_date.isoformat(),
            },
            "payment": {
                "id": str(payment.id),
                "loan_id": str(payment.loan_id),
                "amount_minor": payment.amount_minor,
                "principal_minor": payment.principal_minor,
                "interest_minor": payment.interest_minor,
                "status": payment.status,
                "reversed_at": (
                    payment.reversed_at.isoformat()
                    if payment.reversed_at else None
                ),
            },
        }), 200

    except (PlatformLendingValidationError, ValueError) as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan repayment reversal failed"
        )
        return jsonify({
            "success": False,
            "message": "Platform loan repayment could not be reversed.",
        }), 500


def _customer_id():
    try:
        return UUID(str(get_jwt_identity()))
    except (ValueError, TypeError, AttributeError):
        return None


def _application_response(application):
    return {
        "id": str(application.id),
        "product_id": str(application.product_id),
        "requested_principal_minor": application.requested_principal_minor,
        "approved_principal_minor": application.approved_principal_minor,
        "term_count": application.term_count,
        "purpose": application.purpose,
        "status": application.status,
        "decision_notes": application.decision_notes,
        "submitted_at": application.submitted_at.isoformat(),
        "reviewed_at": (
            application.reviewed_at.isoformat()
            if application.reviewed_at else None
        ),
        "withdrawn_at": (
            application.withdrawn_at.isoformat()
            if application.withdrawn_at else None
        ),
    }


@platform_bp.get("/loans")
@jwt_required()
def list_customer_platform_loans():
    """List only the authenticated customer's platform loans."""

    customer_id = _customer_id()
    if customer_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        if page < 1 or per_page < 1:
            raise ValueError
        per_page = min(per_page, 100)
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Page and per_page must be positive integers.",
        }), 400

    try:
        query = (
            db.session.query(PlatformLoan, PlatformLoanProduct.name)
            .join(
                PlatformLoanProduct,
                PlatformLoanProduct.id == PlatformLoan.product_id,
            )
            .filter(PlatformLoan.customer_id == customer_id)
        )

        total = query.count()
        rows = (
            query
            .order_by(
                PlatformLoan.created_at.desc(),
                PlatformLoan.id.desc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        loans = [{
            "id": str(loan.id),
            "product_name": product_name,
            "approved_principal_minor": loan.approved_principal_minor,
            "disbursed_principal_minor": loan.principal_minor,
            "outstanding_principal_minor": loan.outstanding_principal_minor,
            "currency_code": loan.currency_code,
            "interest_method": loan.interest_method,
            "interest_rate_percent": str(loan.interest_rate_percent),
            "repayment_frequency": loan.repayment_frequency,
            "one_time_due_days": loan.one_time_due_days,
            "term_count": loan.term_count,
            "start_date": loan.start_date.isoformat() if loan.start_date else None,
            "due_date": loan.due_date.isoformat() if loan.due_date else None,
            "status": loan.status,
            "created_at": loan.created_at.isoformat() if loan.created_at else None,
        } for loan, product_name in rows]

        return jsonify({
            "success": True,
            "loans": loans,
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Customer platform loans listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Loans could not be loaded.",
        }), 500


@platform_bp.get("/loans/<loan_id>")
@jwt_required()
def get_customer_platform_loan(loan_id):
    """Return loan, installment and repayment details for its customer."""

    customer_id = _customer_id()
    if customer_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        loan_uuid = UUID(loan_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid loan ID.",
        }), 400

    try:
        row = (
            db.session.query(PlatformLoan, PlatformLoanProduct.name)
            .join(
                PlatformLoanProduct,
                PlatformLoanProduct.id == PlatformLoan.product_id,
            )
            .filter(
                PlatformLoan.id == loan_uuid,
                PlatformLoan.customer_id == customer_id,
            )
            .one_or_none()
        )

        if row is None:
            return jsonify({
                "success": False,
                "message": "Platform loan not found.",
            }), 404

        loan, product_name = row

        installments = (
            PlatformLoanInstallment.query
            .filter_by(loan_id=loan.id)
            .order_by(PlatformLoanInstallment.installment_number.asc())
            .all()
        )

        payments = (
            PlatformLoanPayment.query
            .filter_by(loan_id=loan.id)
            .order_by(
                PlatformLoanPayment.payment_date.desc(),
                PlatformLoanPayment.created_at.desc(),
                PlatformLoanPayment.id.desc(),
            )
            .all()
        )

        confirmed_payments = [
            payment for payment in payments
            if payment.status == "confirmed"
        ]

        return jsonify({
            "success": True,
            "loan": {
                "id": str(loan.id),
                "product_name": product_name,
                "approved_principal_minor": loan.approved_principal_minor,
                "disbursed_principal_minor": loan.principal_minor,
                "outstanding_principal_minor": loan.outstanding_principal_minor,
                "currency_code": loan.currency_code,
                "interest_method": loan.interest_method,
                "interest_rate_percent": str(loan.interest_rate_percent),
                "repayment_frequency": loan.repayment_frequency,
                "one_time_due_days": loan.one_time_due_days,
                "term_count": loan.term_count,
                "start_date": loan.start_date.isoformat() if loan.start_date else None,
                "due_date": loan.due_date.isoformat() if loan.due_date else None,
                "status": loan.status,
                "created_at": loan.created_at.isoformat() if loan.created_at else None,
            },
            "installments": [{
                "id": str(item.id),
                "installment_number": item.installment_number,
                "due_date": item.due_date.isoformat(),
                "amount_minor": item.amount_minor,
                "principal_minor": item.principal_minor,
                "interest_minor": item.interest_minor,
                "paid_minor": item.paid_minor,
                "remaining_minor": item.amount_minor - item.paid_minor,
                "status": item.status,
            } for item in installments],
            "payments": [{
                "id": str(item.id),
                "currency_code": item.currency_code,
                "amount_minor": item.amount_minor,
                "principal_minor": item.principal_minor,
                "interest_minor": item.interest_minor,
                "payment_date": item.payment_date.isoformat(),
                "payment_method": item.payment_method,
                "reference": item.reference,
                "status": item.status,
                "notes": item.notes,
                "confirmed_at": item.confirmed_at.isoformat() if item.confirmed_at else None,
                "reversed_at": item.reversed_at.isoformat() if item.reversed_at else None,
                "created_at": item.created_at.isoformat() if item.created_at else None,
            } for item in payments],
            "summary": {
                "confirmed_repayments_minor": sum(
                    item.amount_minor for item in confirmed_payments
                ),
                "confirmed_principal_repaid_minor": sum(
                    item.principal_minor for item in confirmed_payments
                ),
                "confirmed_interest_paid_minor": sum(
                    item.interest_minor for item in confirmed_payments
                ),
                "pending_repayments_minor": sum(
                    item.amount_minor for item in payments
                    if item.status == "pending"
                ),
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Customer platform loan detail retrieval failed"
        )
        return jsonify({
            "success": False,
            "message": "Loan details could not be loaded.",
        }), 500


@platform_bp.post("/loans/<loan_id>/payments")
@jwt_required()
def submit_platform_loan_repayment(loan_id):
    """Allow a customer to report a repayment for their own platform loan."""

    customer_id = _customer_id()
    if customer_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        loan_uuid = UUID(loan_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid loan ID.",
        }), 400

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    amount_minor = data.get("amount_minor")
    payment_method = data.get("payment_method")
    raw_date = data.get("payment_date")

    if type(amount_minor) is not int or amount_minor <= 0:
        return jsonify({
            "success": False,
            "message": "Payment amount must be a positive whole number in minor units.",
        }), 400

    if not isinstance(raw_date, str):
        return jsonify({
            "success": False,
            "message": "A valid payment date in YYYY-MM-DD format is required.",
        }), 400

    try:
        payment_date = date.fromisoformat(raw_date)
    except ValueError:
        return jsonify({
            "success": False,
            "message": "A valid payment date in YYYY-MM-DD format is required.",
        }), 400

    if (
        not isinstance(payment_method, str)
        or payment_method not in {"cash", "bank", "mobile_money", "other"}
    ):
        return jsonify({
            "success": False,
            "message": "Payment method must be cash, bank, mobile_money, or other.",
        }), 400

    loan = PlatformLoan.query.filter_by(
        id=loan_uuid,
        customer_id=customer_id,
    ).first()

    if loan is None:
        return jsonify({
            "success": False,
            "message": "Platform loan not found.",
        }), 404

    try:
        payment = record_platform_loan_payment(
            loan_id=loan_uuid,
            amount_minor=amount_minor,
            payment_date=payment_date,
            payment_method=payment_method,
            recorded_by=customer_id,
            reference=data.get("reference"),
            notes=data.get("notes"),
        )
        if payment is None:
            db.session.rollback()
            return jsonify({
                "success": False,
                "message": "Platform loan not found.",
            }), 404

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Repayment reported successfully. It is pending confirmation.",
            "payment": {
                "id": str(payment.id),
                "loan_id": str(payment.loan_id),
                "currency_code": payment.currency_code,
                "amount_minor": payment.amount_minor,
                "payment_date": payment.payment_date.isoformat(),
                "payment_method": payment.payment_method,
                "reference": payment.reference,
                "status": payment.status,
                "notes": payment.notes,
                "created_at": payment.created_at.isoformat(),
            },
        }), 201

    except PlatformLendingValidationError as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 409

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Customer platform loan repayment submission failed"
        )
        return jsonify({
            "success": False,
            "message": "Repayment could not be reported.",
        }), 500



def _owner_product_response(product):
    return {
        "id": str(product.id),
        "name": product.name,
        "code": product.code,
        "description": product.description,
        "currency_code": product.currency_code,
        "min_principal_minor": product.min_principal_minor,
        "max_principal_minor": product.max_principal_minor,
        "interest_method": product.interest_method,
        "interest_rate_percent": str(product.interest_rate_percent),
        "repayment_frequency": product.repayment_frequency,
        "one_time_due_days": product.one_time_due_days,
        "min_term_count": product.min_term_count,
        "max_term_count": product.max_term_count,
        "origination_fee_minor": product.origination_fee_minor,
        "late_payment_fee_minor": product.late_payment_fee_minor,
        "allow_early_repayment": product.allow_early_repayment,
        "is_active": product.is_active,
        "created_at": product.created_at.isoformat() if product.created_at else None,
        "updated_at": product.updated_at.isoformat() if product.updated_at else None,
    }


def _validate_owner_product_payload(data, existing=None):
    if not isinstance(data, dict) or not data:
        raise ValueError("A non-empty JSON object is required.")

    allowed = {
        "name", "code", "description", "currency_code",
        "min_principal_minor", "max_principal_minor",
        "interest_method", "interest_rate_percent",
        "repayment_frequency", "one_time_due_days",
        "min_term_count", "max_term_count",
        "origination_fee_minor", "late_payment_fee_minor",
        "allow_early_repayment", "is_active",
    }
    unknown = set(data) - allowed
    if unknown:
        raise ValueError(f"Unsupported product fields: {', '.join(sorted(unknown))}.")

    required = {
        "name", "code", "currency_code",
        "min_principal_minor", "max_principal_minor",
        "interest_method", "interest_rate_percent",
        "repayment_frequency", "min_term_count", "max_term_count",
    }
    if existing is None and not required.issubset(data):
        missing = sorted(required - set(data))
        raise ValueError(f"Missing required fields: {', '.join(missing)}.")

    values = {}
    defaults = {
        "description": None,
        "one_time_due_days": None,
        "origination_fee_minor": 0,
        "late_payment_fee_minor": 0,
        "allow_early_repayment": True,
        "is_active": True,
    }

    for field in allowed:
        if field in data:
            values[field] = data[field]
        elif existing is not None:
            values[field] = getattr(existing, field)
        elif field in defaults:
            values[field] = defaults[field]

    for field, limit in (("name", 120), ("code", 40)):
        value = values.get(field)
        if not isinstance(value, str) or not value.strip():
            raise ValueError(f"Product {field} is required.")
        value = value.strip()
        if len(value) > limit:
            raise ValueError(f"Product {field} cannot exceed {limit} characters.")
        values[field] = value.upper() if field == "code" else value

    description = values.get("description")
    if description is not None and not isinstance(description, str):
        raise ValueError("Product description must be text.")
    if isinstance(description, str):
        description = description.strip() or None
        if description and len(description) > 5000:
            raise ValueError("Product description cannot exceed 5000 characters.")
    values["description"] = description

    currency = values.get("currency_code")
    if not isinstance(currency, str) or not currency.strip():
        raise ValueError("A currency code is required.")
    currency = currency.strip().upper()
    if db.session.get(Currency, currency) is None:
        raise ValueError("The selected currency does not exist.")
    if not db.session.get(Currency, currency).is_active:
        raise ValueError("The selected currency is inactive.")
    values["currency_code"] = currency

    integer_fields = (
        "min_principal_minor", "max_principal_minor",
        "min_term_count", "max_term_count",
        "origination_fee_minor", "late_payment_fee_minor",
    )
    for field in integer_fields:
        value = values.get(field)
        if type(value) is not int or value < 0:
            raise ValueError(f"{field} must be a non-negative integer.")

    if values["min_principal_minor"] <= 0:
        raise ValueError("Minimum principal must be greater than zero.")
    if values["max_principal_minor"] < values["min_principal_minor"]:
        raise ValueError("Maximum principal cannot be below minimum principal.")
    if values["min_term_count"] <= 0:
        raise ValueError("Minimum term count must be greater than zero.")
    if values["max_term_count"] < values["min_term_count"]:
        raise ValueError("Maximum term count cannot be below minimum term count.")

    method = values.get("interest_method")
    if method not in {"none", "flat", "reducing"}:
        raise ValueError("Invalid interest method.")

    try:
        from decimal import Decimal, InvalidOperation
        rate = Decimal(str(values.get("interest_rate_percent")))
    except (InvalidOperation, TypeError, ValueError):
        raise ValueError("Interest rate must be a valid decimal number.")
    if not rate.is_finite() or rate < 0:
        raise ValueError("Interest rate must be a finite non-negative number.")
    if method == "none" and rate != 0:
        raise ValueError("Interest rate must be zero when interest method is none.")
    values["interest_rate_percent"] = rate

    if values.get("repayment_frequency") not in {
        "weekly", "biweekly", "monthly", "quarterly", "one_time"
    }:
        raise ValueError("Invalid repayment frequency.")

    maturity_days = values.get("one_time_due_days")
    if values["repayment_frequency"] == "one_time":
        if (
            values["min_term_count"] != 1
            or values["max_term_count"] != 1
        ):
            raise ValueError(
                "One-time products must have exactly one installment."
            )
        if maturity_days is not None and (
            type(maturity_days) is not int or maturity_days <= 0
        ):
            raise ValueError(
                "One-time maturity period must be a positive whole number of days."
            )
        if values.get("is_active") and maturity_days is None:
            raise ValueError(
                "An active one-time product requires a maturity period in days."
            )
    else:
        if maturity_days is not None:
            raise ValueError(
                "Maturity period in days is only valid for one-time products."
            )
        values["one_time_due_days"] = None

    for field in ("allow_early_repayment", "is_active"):
        if type(values.get(field)) is not bool:
            raise ValueError(f"{field} must be true or false.")

    return values




@platform_bp.get("/owner/accounting-settings")
@platform_owner_required()
def get_owner_accounting_settings():
    """Return the platform's functional currency configuration."""

    settings = db.session.get(PlatformAccountingSettings, 1)
    currency = (
        db.session.get(Currency, settings.functional_currency_code)
        if settings else None
    )

    return jsonify({
        "success": True,
        "configured": bool(settings and currency and currency.is_active),
        "functional_currency": (
            {
                "code": currency.code,
                "name": currency.name,
                "minor_unit": currency.minor_unit,
            }
            if settings and currency and currency.is_active else None
        ),
        "currencies": [
            {
                "code": item.code,
                "name": item.name,
                "minor_unit": item.minor_unit,
            }
            for item in Currency.query.filter_by(is_active=True)
            .order_by(Currency.code.asc()).all()
        ],
    }), 200


@platform_bp.put("/owner/accounting-settings")
@platform_owner_required()
def update_owner_accounting_settings():
    """Set the platform's functional currency."""

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    currency_code = data.get("functional_currency_code")
    if not isinstance(currency_code, str):
        return jsonify({
            "success": False,
            "message": "A valid functional currency code is required.",
        }), 400

    currency_code = currency_code.strip().upper()
    if len(currency_code) != 3:
        return jsonify({
            "success": False,
            "message": "Currency code must contain three letters.",
        }), 400

    try:
        currency = db.session.get(Currency, currency_code)
        if currency is None or not currency.is_active:
            return jsonify({
                "success": False,
                "message": "The selected currency is unavailable.",
            }), 400

        settings = db.session.get(
            PlatformAccountingSettings, 1
        )

        if settings is None:
            settings = PlatformAccountingSettings(
                id=1,
                functional_currency_code=currency_code,
                updated_by=owner_id,
            )
            db.session.add(settings)
        else:
            if (
                settings.functional_currency_code != currency_code
                and PlatformJournalEntry.query.limit(1).first() is not None
            ):
                return jsonify({
                    "success": False,
                    "message": (
                        "The functional currency cannot be changed after "
                        "platform journal entries have been created."
                    ),
                }), 409

            settings.functional_currency_code = currency_code
            settings.updated_by = owner_id

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Platform accounting settings saved.",
            "functional_currency": {
                "code": currency.code,
                "name": currency.name,
                "minor_unit": currency.minor_unit,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform accounting settings update failed"
        )
        return jsonify({
            "success": False,
            "message": "Platform accounting settings could not be saved.",
        }), 500

@platform_bp.post("/owner/financial-accounts")
@platform_owner_required()
def create_owner_platform_financial_account():
    """Create a platform financial account and its asset ledger atomically."""

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON request body is required.",
        }), 400

    name = data.get("name")
    if not isinstance(name, str) or not name.strip():
        return jsonify({
            "success": False,
            "message": "Account name is required.",
        }), 400

    name = name.strip()
    if len(name) > 150:
        return jsonify({
            "success": False,
            "message": "Account name cannot exceed 150 characters.",
        }), 400

    account_type = data.get("account_type")
    prefixes = {
        "cash": "CASH",
        "bank": "BANK",
        "mobile_money": "MOBILE",
    }
    if account_type not in prefixes:
        return jsonify({
            "success": False,
            "message": "Account type must be cash, bank or mobile_money.",
        }), 400

    currency_code = data.get("currency_code")
    if not isinstance(currency_code, str):
        return jsonify({
            "success": False,
            "message": "A valid currency code is required.",
        }), 400
    currency_code = currency_code.strip().upper()

    if len(currency_code) != 3:
        return jsonify({
            "success": False,
            "message": "Currency code must contain three letters.",
        }), 400

    optional_values = {}
    for field, limit in (
        ("institution_name", 150),
        ("display_reference", 100),
    ):
        value = data.get(field)
        if value is not None and not isinstance(value, str):
            return jsonify({
                "success": False,
                "message": f"{field} must be text.",
            }), 400
        value = value.strip() if isinstance(value, str) else None
        if value and len(value) > limit:
            return jsonify({
                "success": False,
                "message": f"{field} cannot exceed {limit} characters.",
            }), 400
        optional_values[field] = value or None

    try:
        currency = db.session.get(Currency, currency_code)
        if currency is None or not currency.is_active:
            return jsonify({
                "success": False,
                "message": "The selected currency is unavailable.",
            }), 400

        account = PlatformFinancialAccount(
            name=name,
            account_type=account_type,
            currency_code=currency_code,
            institution_name=optional_values["institution_name"],
            display_reference=optional_values["display_reference"],
            is_active=True,
        )
        db.session.add(account)
        db.session.flush()

        ledger = PlatformLedgerAccount(
            financial_account_id=account.id,
            code=f"PL-{prefixes[account_type]}-{uuid4().hex[:12].upper()}",
            name=name,
            account_class="asset",
            normal_balance="debit",
            currency_code=currency_code,
            is_system=False,
            is_active=True,
        )
        db.session.add(ledger)

        # Investor contributions and withdrawals use one shared liability
        # ledger per currency, separate from individual cash accounts.
        capital_code = f"PL-INVESTOR-CAPITAL-{currency_code}"
        capital_ledger = PlatformLedgerAccount.query.filter_by(
            code=capital_code
        ).one_or_none()

        if capital_ledger is None:
            capital_ledger = PlatformLedgerAccount(
                financial_account_id=None,
                code=capital_code,
                name="External Investor Capital",
                account_class="liability",
                normal_balance="credit",
                currency_code=currency_code,
                is_system=True,
                is_active=True,
            )
            db.session.add(capital_ledger)
        elif not (
            capital_ledger.financial_account_id is None
            and capital_ledger.currency_code == currency_code
            and capital_ledger.account_class == "liability"
            and capital_ledger.normal_balance == "credit"
            and capital_ledger.is_system is True
            and capital_ledger.is_active is True
        ):
            raise ValueError(
                f"Investor-capital ledger {capital_code} exists "
                "but is incorrectly configured."
            )

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Platform account created successfully.",
            "account": {
                "id": str(account.id),
                "name": account.name,
                "account_type": account.account_type,
                "currency_code": account.currency_code,
                "institution_name": account.institution_name,
                "display_reference": account.display_reference,
                "is_active": account.is_active,
                "ledger_account_id": str(ledger.id),
                "ledger_code": ledger.code,
            },
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform owner financial account creation failed"
        )
        return jsonify({
            "success": False,
            "message": "Platform account could not be created.",
        }), 500


@platform_bp.get("/owner/financial-accounts")
@platform_owner_required()
def list_owner_platform_financial_accounts():
    """List active platform-owned financial accounts for owner operations."""
    try:
        currency_code = request.args.get("currency_code", "").strip().upper()

        query = PlatformFinancialAccount.query.filter(
            PlatformFinancialAccount.is_active.is_(True)
        )

        if currency_code:
            query = query.filter(
                PlatformFinancialAccount.currency_code == currency_code
            )

        accounts = query.order_by(
            PlatformFinancialAccount.currency_code.asc(),
            PlatformFinancialAccount.name.asc(),
            PlatformFinancialAccount.id.asc(),
        ).all()

        return jsonify({
            "success": True,
            "accounts": [
                {
                    "id": str(account.id),
                    "name": account.name,
                    "account_type": account.account_type,
                    "currency_code": account.currency_code,
                    "institution_name": account.institution_name,
                    "display_reference": account.display_reference,
                }
                for account in accounts
            ],
            "currencies": [
                {
                    "code": currency.code,
                    "name": currency.name,
                    "minor_unit": currency.minor_unit,
                }
                for currency in Currency.query.filter_by(
                    is_active=True
                ).order_by(Currency.code.asc()).all()
            ],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform owner financial account listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Platform financial accounts could not be loaded.",
        }), 500


@platform_bp.get("/owner/products")
@platform_owner_required()
def list_owner_platform_loan_products():
    """List all platform lending products, including inactive products."""
    try:
        page = int(request.args.get("page", 1))
        per_page = int(request.args.get("per_page", 20))
        if page < 1 or per_page < 1:
            raise ValueError
        per_page = min(per_page, 100)
    except (TypeError, ValueError):
        return jsonify({
            "success": False,
            "message": "Page and per_page must be positive integers.",
        }), 400

    active_filter = request.args.get("is_active")
    if active_filter not in (None, "", "true", "false"):
        return jsonify({
            "success": False,
            "message": "is_active must be true or false.",
        }), 400

    try:
        query = PlatformLoanProduct.query
        if active_filter:
            query = query.filter(
                PlatformLoanProduct.is_active == (active_filter == "true")
            )

        total = query.count()
        products = (
            query.order_by(
                PlatformLoanProduct.currency_code.asc(),
                PlatformLoanProduct.name.asc(),
                PlatformLoanProduct.id.asc(),
            )
            .offset((page - 1) * per_page)
            .limit(per_page)
            .all()
        )

        return jsonify({
            "success": True,
            "products": [_owner_product_response(item) for item in products],
            "pagination": {
                "page": page,
                "per_page": per_page,
                "total": total,
                "total_pages": (total + per_page - 1) // per_page,
            },
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Platform owner product listing failed")
        return jsonify({
            "success": False,
            "message": "Loan products could not be loaded.",
        }), 500


@platform_bp.post("/owner/products")
@platform_owner_required()
def create_owner_platform_loan_product():
    """Create a platform lending product."""
    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    try:
        values = _validate_owner_product_payload(data)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    if PlatformLoanProduct.query.filter_by(code=values["code"]).first():
        return jsonify({
            "success": False,
            "message": "A loan product with this code already exists.",
        }), 409

    try:
        product = PlatformLoanProduct(
            **values,
            created_by=owner_id,
            updated_by=owner_id,
        )
        db.session.add(product)
        db.session.commit()
        return jsonify({
            "success": True,
            "message": "Loan product created successfully.",
            "product": _owner_product_response(product),
        }), 201

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Platform owner product creation failed")
        return jsonify({
            "success": False,
            "message": "Loan product could not be created.",
        }), 500


@platform_bp.patch("/owner/products/<product_id>")
@platform_owner_required()
def update_owner_platform_loan_product(product_id):
    """Update a platform lending product or its active status."""
    try:
        product_uuid = UUID(str(product_id))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid product ID.",
        }), 400

    owner_id = _customer_id()
    if owner_id is None:
        return jsonify({
            "success": False,
            "message": "A valid owner identity is required.",
        }), 401

    data = request.get_json(silent=True)
    try:
        product = (
            PlatformLoanProduct.query
            .filter_by(id=product_uuid)
            .with_for_update()
            .first()
        )
        if product is None:
            return jsonify({
                "success": False,
                "message": "Loan product not found.",
            }), 404

        values = _validate_owner_product_payload(data, existing=product)
    except ValueError as exc:
        return jsonify({"success": False, "message": str(exc)}), 400

    duplicate = PlatformLoanProduct.query.filter(
        PlatformLoanProduct.code == values["code"],
        PlatformLoanProduct.id != product_uuid,
    ).first()
    if duplicate:
        return jsonify({
            "success": False,
            "message": "A loan product with this code already exists.",
        }), 409

    try:
        for field, value in values.items():
            setattr(product, field, value)
        product.updated_by = owner_id
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Loan product updated successfully.",
            "product": _owner_product_response(product),
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception("Platform owner product update failed")
        return jsonify({
            "success": False,
            "message": "Loan product could not be updated.",
        }), 500


@platform_bp.get("/products")
@jwt_required()
def list_customer_platform_loan_products():
    """List active PesaPilot lending products available to customers."""

    if _customer_id() is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        products = (
            PlatformLoanProduct.query
            .filter_by(is_active=True)
            .order_by(
                PlatformLoanProduct.currency_code.asc(),
                PlatformLoanProduct.name.asc(),
            )
            .all()
        )

        return jsonify({
            "success": True,
            "count": len(products),
            "products": [
                {
                    "id": str(product.id),
                    "name": product.name,
                    "code": product.code,
                    "description": product.description,
                    "currency_code": product.currency_code,
                    "min_principal_minor": product.min_principal_minor,
                    "max_principal_minor": product.max_principal_minor,
                    "interest_method": product.interest_method,
                    "interest_rate_percent": str(
                        product.interest_rate_percent
                    ),
                    "repayment_frequency": product.repayment_frequency,
                    "one_time_due_days": product.one_time_due_days,
                    "min_term_count": product.min_term_count,
                    "max_term_count": product.max_term_count,
                    "origination_fee_minor": product.origination_fee_minor,
                    "late_payment_fee_minor": product.late_payment_fee_minor,
                    "allow_early_repayment": product.allow_early_repayment,
                }
                for product in products
            ],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Customer platform loan products listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Loan products could not be loaded.",
        }), 500


@platform_bp.post("/applications")
@jwt_required()
def submit_platform_application():
    """Submit declarations and an advisory affordability assessment atomically."""

    customer_id = _customer_id()

    if customer_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    data = request.get_json(silent=True)

    if not isinstance(data, dict):
        return jsonify({
            "success": False,
            "message": "A valid JSON object is required.",
        }), 400

    # Never accept an application containing an unencrypted identity number.
    if not current_app.config.get("IDENTITY_ENCRYPTION_KEY"):
        return jsonify({
            "success": False,
            "message": "Secure identity storage is not configured. Please try again later.",
        }), 503

    try:
        product_id = UUID(str(data.get("product_id")))
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "A valid product ID is required.",
        }), 400

    product = PlatformLoanProduct.query.filter_by(
        id=product_id,
        is_active=True,
    ).first()

    if product is None:
        return jsonify({
            "success": False,
            "message": "The selected loan product is unavailable.",
        }), 400

    def required_text(field, label, maximum):
        value = data.get(field)
        if not isinstance(value, str):
            raise ValueError(f"{label} is required.")
        value = value.strip()
        if not value:
            raise ValueError(f"{label} is required.")
        if len(value) > maximum:
            raise ValueError(f"{label} must not exceed {maximum} characters.")
        return value

    def optional_text(field, label, maximum):
        value = data.get(field)
        if value is None or value == "":
            return None
        if not isinstance(value, str):
            raise ValueError(f"{label} must be text.")
        value = value.strip()
        if not value:
            return None
        if len(value) > maximum:
            raise ValueError(f"{label} must not exceed {maximum} characters.")
        return value

    def required_minor(field, label):
        value = data.get(field)
        if type(value) is not int or value < 0:
            raise ValueError(
                f"{label} must be a non-negative whole number in minor units."
            )
        return value

    try:
        principal = data.get("requested_principal_minor")
        term_count = data.get("term_count")

        if type(principal) is not int or principal <= 0:
            raise ValueError(
                "Requested principal must be a positive whole number in minor units."
            )
        if type(term_count) is not int or term_count <= 0:
            raise ValueError("Repayment term must be a positive integer.")

        purpose = required_text("purpose", "Loan purpose", 500)
        full_name = required_text("full_name", "Full name", 150)
        national_id = required_text("national_id", "National ID number", 100)
        phone = required_text("phone", "Phone number", 30)
        residential_address = required_text(
            "residential_address", "Residential address", 500
        )

        email = optional_text("email", "Email address", 255)
        if email and ("@" not in email or email.startswith("@") or email.endswith("@")):
            raise ValueError("Enter a valid email address.")

        employment_status = required_text(
            "employment_status", "Employment status", 30
        ).lower()

        allowed_employment = {
            "employed", "self_employed", "unemployed",
            "student", "retired", "other",
        }
        if employment_status not in allowed_employment:
            raise ValueError("Select a valid employment status.")

        employer_name = optional_text(
            "employer_business_name", "Employer or business name", 200
        )
        if employment_status in {"employed", "self_employed"} and not employer_name:
            raise ValueError(
                "Employer or business name is required for this employment status."
            )

        monthly_income = required_minor(
            "monthly_income_minor", "Monthly income"
        )
        monthly_expenses = required_minor(
            "monthly_expenses_minor", "Monthly expenses"
        )
        existing_repayments = data.get(
            "existing_monthly_repayments_minor", 0
        )
        if type(existing_repayments) is not int or existing_repayments < 0:
            raise ValueError(
                "Existing monthly repayments must be a non-negative whole number in minor units."
            )

        explanation = optional_text(
            "explanation", "Additional explanation", 5000
        )
        alternative_name = optional_text(
            "alternative_contact_name", "Alternative contact name", 150
        )
        alternative_phone = optional_text(
            "alternative_contact_phone", "Alternative contact phone", 30
        )
        alternative_relationship = optional_text(
            "alternative_contact_relationship",
            "Alternative contact relationship",
            80,
        )

        alternative_values = (
            alternative_name,
            alternative_phone,
            alternative_relationship,
        )
        if any(alternative_values) and not all(alternative_values):
            raise ValueError(
                "Provide the alternative contact's name, phone number and relationship, or leave all three blank."
            )

        additional_notes = optional_text(
            "additional_notes", "Additional notes", 5000
        )

        consent = data.get("affordability_consent", False)
        if type(consent) is not bool:
            raise ValueError("Affordability consent must be true or false.")

        workspace_id = None
        if consent:
            try:
                workspace_id = UUID(str(data.get("affordability_workspace_id")))
            except (ValueError, TypeError, AttributeError):
                raise ValueError(
                    "Select the personal workspace you want to share."
                ) from None
        elif data.get("affordability_workspace_id") not in (None, ""):
            raise ValueError(
                "A workspace cannot be selected without your consent."
            )

        encrypted_national_id = encrypt_national_id(national_id)

        application = submit_loan_application(
            customer_id=customer_id,
            product_id=product_id,
            requested_principal_minor=principal,
            term_count=term_count,
            purpose=purpose,
        )

        details = PlatformLoanApplicationDetails(
            application_id=application.id,
            customer_id=customer_id,
            full_name=full_name,
            national_id_encrypted=encrypted_national_id,
            phone=phone,
            email=email,
            residential_address=residential_address,
            employment_status=employment_status,
            employer_business_name=employer_name,
            monthly_income_minor=monthly_income,
            monthly_expenses_minor=monthly_expenses,
            existing_monthly_repayments_minor=existing_repayments,
            explanation=explanation,
            alternative_contact_name=alternative_name,
            alternative_contact_phone=alternative_phone,
            alternative_contact_relationship=alternative_relationship,
            additional_notes=additional_notes,
            affordability_consent=consent,
            affordability_workspace_id=workspace_id,
            affordability_consent_at=utc_now() if consent else None,
        )
        db.session.add(details)
        db.session.flush()

        evidence_report = build_affordability_evidence_report(
            customer_id=customer_id,
            declared_monthly_income_minor=monthly_income,
            declared_monthly_expenses_minor=monthly_expenses,
            declared_existing_monthly_repayments_minor=existing_repayments,
            consent=consent,
            workspace_id=workspace_id,
        )

        assessment_date = date.today()
        maturity_date = _platform_loan_due_date(
            assessment_date,
            product.repayment_frequency,
            term_count,
            product.one_time_due_days,
        )

        estimate = estimate_affordable_principal(
            monthly_capacity_minor=(
                evidence_report["capacity"][
                    "recommended_monthly_instalment_minor"
                ]
            ),
            maximum_principal_minor=product.max_principal_minor,
            term_count=term_count,
            start_date=assessment_date,
            due_date=maturity_date,
            repayment_frequency=product.repayment_frequency,
            interest_method=product.interest_method,
            interest_rate_percent=product.interest_rate_percent,
        )

        previous_version = (
            db.session.query(
                db.func.max(PlatformLoanAffordabilityAssessment.version)
            )
            .filter_by(application_id=application.id)
            .scalar()
            or 0
        )

        assessment = PlatformLoanAffordabilityAssessment(
            application_id=application.id,
            version=previous_version + 1,
            status=evidence_report["status"],
            source_workspace_id=workspace_id if consent else None,
            evidence_window_months=6,
            recommended_monthly_instalment_minor=(
                evidence_report["capacity"][
                    "recommended_monthly_instalment_minor"
                ]
            ),
            estimated_affordable_principal_minor=(
                estimate["estimated_affordable_principal_minor"]
            ),
            report={
                **evidence_report,
                "principal_estimate": {
                    "estimated_affordable_principal_minor":
                        estimate["estimated_affordable_principal_minor"],
                    "maximum_scheduled_instalment_minor":
                        estimate["maximum_scheduled_instalment_minor"],
                },
                "employment_status": employment_status,
                "employer_business_name": employer_name,
                "assessment_is_advisory": True,
                "automatic_approval_or_rejection": False,
            },
            reasons=[
                "This assessment is advisory and requires human review.",
                "Declared income and expenses are not independently verified.",
            ],
        )
        db.session.add(assessment)
        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Loan application submitted successfully.",
            "application": _application_response(application),
            "affordability_assessment": {
                "status": assessment.status,
                "version": assessment.version,
                "recommended_monthly_instalment_minor":
                    assessment.recommended_monthly_instalment_minor,
                "estimated_affordable_principal_minor":
                    assessment.estimated_affordable_principal_minor,
            },
        }), 201

    except (ValueError, PlatformLendingValidationError) as exc:
        db.session.rollback()
        return jsonify({
            "success": False,
            "message": str(exc),
        }), 400

    except RuntimeError:
        db.session.rollback()
        current_app.logger.exception(
            "Platform application identity encryption failed"
        )
        return jsonify({
            "success": False,
            "message": "Secure identity storage is unavailable. Please try again later.",
        }), 503

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan application submission failed"
        )
        return jsonify({
            "success": False,
            "message": "Loan application could not be submitted.",
        }), 500


@platform_bp.get("/applications")
@jwt_required()
def list_platform_applications():
    customer_id = _customer_id()

    if customer_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        applications = (
            PlatformLoanApplication.query
            .filter_by(customer_id=customer_id)
            .order_by(PlatformLoanApplication.submitted_at.desc())
            .all()
        )

        return jsonify({
            "success": True,
            "count": len(applications),
            "applications": [
                _application_response(item) for item in applications
            ],
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan applications listing failed"
        )
        return jsonify({
            "success": False,
            "message": "Applications could not be loaded.",
        }), 500


@platform_bp.delete("/applications/<application_id>")
@jwt_required()
def withdraw_platform_application(application_id):
    customer_id = _customer_id()

    if customer_id is None:
        return jsonify({
            "success": False,
            "message": "Invalid authentication identity.",
        }), 401

    try:
        application_uuid = UUID(application_id)
    except (ValueError, TypeError, AttributeError):
        return jsonify({
            "success": False,
            "message": "Invalid application ID.",
        }), 400

    try:
        application = PlatformLoanApplication.query.filter_by(
            id=application_uuid,
            customer_id=customer_id,
        ).with_for_update().first()

        if application is None:
            return jsonify({
                "success": False,
                "message": "Application not found.",
            }), 404

        if application.status != "pending":
            return jsonify({
                "success": False,
                "message": "Only pending applications can be withdrawn.",
            }), 409

        application.status = "withdrawn"
        application.withdrawn_at = utc_now()

        db.session.commit()

        return jsonify({
            "success": True,
            "message": "Application withdrawn successfully.",
            "application": _application_response(application),
        }), 200

    except Exception:
        db.session.rollback()
        current_app.logger.exception(
            "Platform loan application withdrawal failed"
        )
        return jsonify({
            "success": False,
            "message": "Application could not be withdrawn.",
        }), 500
