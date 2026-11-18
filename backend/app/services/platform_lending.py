"""Business logic for PesaPilot's platform-owned lending operation."""

import calendar
from datetime import date, datetime, timedelta, timezone
from uuid import uuid4, UUID

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError

from ..extensions import db
from ..models import (
    PlatformFinancialAccount,
    PlatformJournalEntry,
    PlatformLedgerAccount,
    PlatformLoan,
    PlatformLoanApplication,
    PlatformLoanDisbursement,
    PlatformLoanInstallment,
    PlatformLoanPayment,
    PlatformLoanPaymentAllocation,
    PlatformLoanProduct,
    PlatformInvestor,
    PlatformInvestorContribution,
    PlatformInvestorWithdrawal,
    PlatformLoanFundingAllocation,
    PlatformLoanFundingRelease,
    Workspace,
    WorkspaceMember,
    FinancialAccount,
    LedgerAccount,
    ExchangeRate,
    Currency,
)
from .platform_accounting import (
    PlatformAccountingError,
    create_draft_entry,
    post_entry,
    reverse_entry,
)
from .loan_schedules import build_repayment_schedule
from .accounting import (
    AccountingError,
    create_draft_entry as create_customer_draft_entry,
    post_entry as post_customer_entry,
)


class PlatformLendingError(Exception):
    """Base exception for platform lending operations."""


class PlatformLendingValidationError(PlatformLendingError):
    """Raised when an application fails business validation."""


def submit_loan_application(
    *,
    customer_id,
    product_id,
    requested_principal_minor,
    term_count,
    purpose,
):
    """Validate and create a customer's loan application."""

    product = db.session.execute(
        select(PlatformLoanProduct).where(
            PlatformLoanProduct.id == product_id
        )
    ).scalar_one_or_none()

    if product is None or not product.is_active:
        raise PlatformLendingValidationError(
            "The selected loan product is unavailable."
        )

    if product.currency_code is None:
        raise PlatformLendingValidationError(
            "The loan product has no configured currency."
        )

    if requested_principal_minor < product.min_principal_minor:
        raise PlatformLendingValidationError(
            "The requested amount is below the product minimum."
        )

    if requested_principal_minor > product.max_principal_minor:
        raise PlatformLendingValidationError(
            "The requested amount exceeds the product maximum."
        )

    if term_count < product.min_term_count or term_count > product.max_term_count:
        raise PlatformLendingValidationError(
            "The requested repayment term is outside the product limits."
        )

    if not isinstance(purpose, str) or not purpose.strip():
        raise PlatformLendingValidationError(
            "Please provide the purpose of the loan."
        )

    if len(purpose.strip()) > 500:
        raise PlatformLendingValidationError(
            "The loan purpose must not exceed 500 characters."
        )

    existing_pending = db.session.execute(
        select(PlatformLoanApplication.id).where(
            PlatformLoanApplication.customer_id == customer_id,
            PlatformLoanApplication.status == "pending",
        ).limit(1)
    ).scalar_one_or_none()

    if existing_pending is not None:
        raise PlatformLendingValidationError(
            "You already have a pending loan application."
        )

    application = PlatformLoanApplication(
        customer_id=customer_id,
        product_id=product.id,
        requested_principal_minor=requested_principal_minor,
        term_count=term_count,
        purpose=purpose.strip(),
        status="pending",
    )

    db.session.add(application)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise PlatformLendingValidationError(
            "You already have a pending loan application."
        ) from exc

    return application

def review_loan_application(
    *,
    application_id,
    reviewer_id,
    decision,
    decision_notes=None,
    approved_principal_minor=None,
):
    """Record an owner's approval or rejection of a pending application."""

    if decision not in {"approved", "rejected"}:
        raise PlatformLendingValidationError(
            "Decision must be approved or rejected."
        )

    if decision_notes is not None:
        if not isinstance(decision_notes, str):
            raise PlatformLendingValidationError(
                "Decision notes must be text."
            )
        decision_notes = decision_notes.strip()
        if len(decision_notes) > 2000:
            raise PlatformLendingValidationError(
                "Decision notes must not exceed 2000 characters."
            )

    application = db.session.execute(
        select(PlatformLoanApplication)
        .where(PlatformLoanApplication.id == application_id)
        .with_for_update()
    ).scalar_one_or_none()

    if application is None:
        return None

    if application.status != "pending":
        raise PlatformLendingValidationError(
            "Only pending applications can be reviewed."
        )

    from ..models.core import utc_now

    if decision == "approved":
        if approved_principal_minor is None:
            approved_principal_minor = application.requested_principal_minor
        elif (
            type(approved_principal_minor) is not int
            or approved_principal_minor <= 0
            or approved_principal_minor > application.requested_principal_minor
        ):
            raise PlatformLendingValidationError(
                "Approved principal must be a positive integer no greater "
                "than the requested principal."
            )
    elif approved_principal_minor is not None:
        raise PlatformLendingValidationError(
            "Rejected applications cannot have an approved principal."
        )

    application.approved_principal_minor = approved_principal_minor
    application.status = decision
    application.decision_notes = decision_notes or None
    application.reviewed_by = reviewer_id
    application.reviewed_at = utc_now()

    db.session.flush()
    return application

def create_platform_loan_from_application(*, application_id):
    """Create an unfunded platform loan from an approved application."""

    application = db.session.execute(
        select(PlatformLoanApplication)
        .where(PlatformLoanApplication.id == application_id)
        .with_for_update()
    ).scalar_one_or_none()

    if application is None:
        return None

    if application.status != "approved":
        raise PlatformLendingValidationError(
            "Only approved applications can become platform loans."
        )

    approved_principal = application.approved_principal_minor
    if (
        type(approved_principal) is not int
        or approved_principal <= 0
        or approved_principal > application.requested_principal_minor
    ):
        raise PlatformLendingValidationError(
            "The application has an invalid approved principal."
        )

    existing_loan = db.session.execute(
        select(PlatformLoan.id).where(
            PlatformLoan.application_id == application.id
        ).limit(1)
    ).scalar_one_or_none()

    if existing_loan is not None:
        raise PlatformLendingValidationError(
            "A platform loan already exists for this application."
        )

    product = db.session.execute(
        select(PlatformLoanProduct).where(
            PlatformLoanProduct.id == application.product_id
        )
    ).scalar_one_or_none()

    if product is None:
        raise PlatformLendingValidationError(
            "The loan product for this application no longer exists."
        )

    loan = PlatformLoan(
        application_id=application.id,
        customer_id=application.customer_id,
        product_id=product.id,
        approved_principal_minor=approved_principal,
        principal_minor=0,
        outstanding_principal_minor=0,
        currency_code=product.currency_code,
        interest_method=product.interest_method,
        interest_rate_percent=product.interest_rate_percent,
        repayment_frequency=product.repayment_frequency,
        one_time_due_days=product.one_time_due_days,
        term_count=application.term_count,
        status="created",
    )

    db.session.add(loan)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise PlatformLendingValidationError(
            "A platform loan already exists for this application."
        ) from exc

    return loan



def _platform_loan_due_date(
    start_date,
    frequency,
    term_count,
    one_time_due_days=None,
):
    """Calculate the final due date using the loan's stored product terms."""

    if frequency == "one_time":
        if term_count != 1:
            raise PlatformLendingValidationError(
                "A one-time loan must have exactly one installment."
            )
        if type(one_time_due_days) is not int or one_time_due_days <= 0:
            raise PlatformLendingValidationError(
                "The one-time loan product must have a positive maturity period in days."
            )
        return start_date + timedelta(days=one_time_due_days)

    if one_time_due_days is not None:
        raise PlatformLendingValidationError(
            "A maturity period in days is only valid for one-time loans."
        )

    if frequency == "weekly":
        return start_date + timedelta(days=7 * term_count)
    if frequency == "biweekly":
        return start_date + timedelta(days=14 * term_count)

    months_per_period = {
        "monthly": 1,
        "quarterly": 3,
    }.get(frequency)

    if months_per_period is None:
        raise PlatformLendingValidationError(
            "The loan has an unsupported repayment frequency."
        )

    months = months_per_period * term_count
    month_index = start_date.month - 1 + months
    year = start_date.year + month_index // 12
    month = month_index % 12 + 1
    day = min(start_date.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)



def _get_platform_loan_customer_disbursement_context(
    *,
    loan,
    customer_financial_account_id,
    disbursement_date,
):
    """Validate the borrower's personal receiving account and FX rate."""

    if not isinstance(customer_financial_account_id, UUID):
        raise PlatformLendingValidationError(
            "A valid borrower receiving-account ID is required."
        )

    workspaces = db.session.execute(
        select(Workspace)
        .join(
            WorkspaceMember,
            WorkspaceMember.workspace_id == Workspace.id,
        )
        .where(
            WorkspaceMember.user_id == loan.customer_id,
            WorkspaceMember.role == "owner",
            Workspace.workspace_type == "personal",
        )
        .with_for_update()
    ).scalars().all()

    if len(workspaces) != 1:
        raise PlatformLendingValidationError(
            "The borrower must have exactly one owned personal workspace."
        )

    workspace = workspaces[0]

    customer_account = db.session.execute(
        select(FinancialAccount)
        .where(
            FinancialAccount.id == customer_financial_account_id,
            FinancialAccount.workspace_id == workspace.id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if (
        customer_account is None
        or not customer_account.is_active
        or customer_account.account_type
        not in {"cash", "bank", "mobile_money"}
    ):
        raise PlatformLendingValidationError(
            "The selected borrower receiving account is unavailable or ineligible."
        )

    if customer_account.currency_code != loan.currency_code:
        raise PlatformLendingValidationError(
            "The borrower's receiving account currency must match the loan currency."
        )

    cash_ledgers = db.session.execute(
        select(LedgerAccount)
        .where(
            LedgerAccount.workspace_id == workspace.id,
            LedgerAccount.financial_account_id == customer_account.id,
            LedgerAccount.account_class == "asset",
            LedgerAccount.normal_balance == "debit",
            LedgerAccount.currency_code == loan.currency_code,
            LedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalars().all()

    if len(cash_ledgers) != 1:
        raise PlatformLendingValidationError(
            "The borrower's receiving account must have exactly one active "
            "debit-balance asset ledger."
        )

    liability_ledgers = db.session.execute(
        select(LedgerAccount)
        .where(
            LedgerAccount.workspace_id == workspace.id,
            LedgerAccount.financial_account_id.is_(None),
            LedgerAccount.code == "2000",
            LedgerAccount.account_class == "liability",
            LedgerAccount.normal_balance == "credit",
            LedgerAccount.currency_code == loan.currency_code,
            LedgerAccount.is_system.is_(True),
            LedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalars().all()

    if len(liability_ledgers) != 1:
        raise PlatformLendingValidationError(
            "The borrower's active 2000 Loans and Credit Payable ledger "
            "must be configured for the loan currency."
        )

    if workspace.functional_currency == loan.currency_code:
        fx_rate = None
    else:
        fx_rate = db.session.execute(
            select(ExchangeRate.rate)
            .where(
                ExchangeRate.base_currency == loan.currency_code,
                ExchangeRate.quote_currency == workspace.functional_currency,
                ExchangeRate.effective_at <= disbursement_date,
            )
            .order_by(ExchangeRate.effective_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if fx_rate is None:
            raise PlatformLendingValidationError(
                "No exchange rate is configured from "
                f"{loan.currency_code} to {workspace.functional_currency} "
                "on or before the disbursement date."
            )

    return workspace, customer_account, cash_ledgers[0], liability_ledgers[0], fx_rate


def disburse_platform_loan(
    *,
    loan_id,
    financial_account_id,
    customer_financial_account_id,
    amount_minor,
    funding_source,
    disbursement_date,
    recorded_by,
    notes=None,
    investor_allocations=None,
):
    """Record and account for a platform loan funding tranche."""

    if type(amount_minor) is not int or amount_minor <= 0:
        raise PlatformLendingValidationError(
            "Disbursement amount must be a positive integer in minor units."
        )

    if funding_source not in {"pesapilot_capital", "external_investor"}:
        raise PlatformLendingValidationError(
            "Funding source must be pesapilot_capital or external_investor."
        )

    if funding_source == "pesapilot_capital":
        if investor_allocations not in (None, []):
            raise PlatformLendingValidationError(
                "PesaPilot-funded disbursements cannot include investor allocations."
            )
        normalized_investor_allocations = []
    else:
        if not isinstance(investor_allocations, list) or not investor_allocations:
            raise PlatformLendingValidationError(
                "External investor funding requires at least one investor allocation."
            )

        normalized_investor_allocations = []
        seen_investors = set()

        for item in investor_allocations:
            if not isinstance(item, dict):
                raise PlatformLendingValidationError(
                    "Each investor allocation must be an object."
                )

            try:
                investor_id = UUID(str(item.get("investor_id", "")))
            except (ValueError, TypeError, AttributeError):
                raise PlatformLendingValidationError(
                    "Each investor allocation requires a valid investor ID."
                )

            allocation_amount = item.get("amount_minor")
            if type(allocation_amount) is not int or allocation_amount <= 0:
                raise PlatformLendingValidationError(
                    "Investor allocation amounts must be positive integers."
                )

            if investor_id in seen_investors:
                raise PlatformLendingValidationError(
                    "An investor can only appear once in a disbursement."
                )

            seen_investors.add(investor_id)
            normalized_investor_allocations.append({
                "investor_id": investor_id,
                "amount_minor": allocation_amount,
            })

        if sum(
            item["amount_minor"] for item in normalized_investor_allocations
        ) != amount_minor:
            raise PlatformLendingValidationError(
                "Investor allocations must equal the disbursement amount exactly."
            )

    if (
        not isinstance(disbursement_date, datetime)
        or disbursement_date.tzinfo is None
        or disbursement_date.utcoffset() is None
    ):
        raise PlatformLendingValidationError(
            "A timezone-aware disbursement date is required."
        )

    if notes is not None:
        if not isinstance(notes, str):
            raise PlatformLendingValidationError("Disbursement notes must be text.")
        notes = notes.strip() or None

    loan = db.session.execute(
        select(PlatformLoan)
        .where(PlatformLoan.id == loan_id)
        .with_for_update()
    ).scalar_one_or_none()

    if loan is None:
        return None

    if loan.status not in {"created", "funding"}:
        raise PlatformLendingValidationError(
            "Only created or partially funded loans can be disbursed."
        )

    account = db.session.execute(
        select(PlatformFinancialAccount)
        .where(PlatformFinancialAccount.id == financial_account_id)
        .with_for_update()
    ).scalar_one_or_none()

    if account is None or not account.is_active:
        raise PlatformLendingValidationError(
            "The selected platform financial account is unavailable."
        )

    if account.currency_code != loan.currency_code:
        raise PlatformLendingValidationError(
            "The financial account currency must match the loan currency."
        )

    (
        customer_workspace,
        customer_account,
        customer_cash_ledger,
        customer_liability_ledger,
        customer_fx_rate,
    ) = _get_platform_loan_customer_disbursement_context(
        loan=loan,
        customer_financial_account_id=customer_financial_account_id,
        disbursement_date=disbursement_date,
    )

    existing_funding_allocations = db.session.execute(
        select(PlatformLoanFundingAllocation)
        .where(PlatformLoanFundingAllocation.loan_id == loan.id)
        .order_by(PlatformLoanFundingAllocation.id)
        .with_for_update()
    ).scalars().all()

    tracked_outstanding = sum(
        item.outstanding_minor for item in existing_funding_allocations
    )
    if tracked_outstanding != loan.outstanding_principal_minor:
        raise PlatformLendingValidationError(
            "Existing loan funding allocations do not reconcile with outstanding "
            "principal. This loan requires funding-history reconciliation before "
            "another disbursement."
        )

    allocations_by_investor = {
        item["investor_id"]: item
        for item in normalized_investor_allocations
    }

    for investor_id in sorted(allocations_by_investor):
        allocation = allocations_by_investor[investor_id]
        investor = db.session.execute(
            select(PlatformInvestor)
            .where(PlatformInvestor.id == investor_id)
            .with_for_update()
        ).scalar_one_or_none()

        if investor is None or not investor.is_active:
            raise PlatformLendingValidationError(
                "An allocated investor does not exist or is inactive."
            )

        contributions = db.session.execute(
            select(PlatformInvestorContribution.amount_minor).where(
                PlatformInvestorContribution.investor_id == investor_id,
                PlatformInvestorContribution.currency_code == loan.currency_code,
                PlatformInvestorContribution.status == "posted",
            )
        ).scalars().all()

        withdrawals = db.session.execute(
            select(PlatformInvestorWithdrawal.amount_minor).where(
                PlatformInvestorWithdrawal.investor_id == investor_id,
                PlatformInvestorWithdrawal.currency_code == loan.currency_code,
                PlatformInvestorWithdrawal.status == "posted",
            )
        ).scalars().all()

        allocated = db.session.execute(
            select(PlatformLoanFundingAllocation.outstanding_minor).where(
                PlatformLoanFundingAllocation.investor_id == investor_id,
                PlatformLoanFundingAllocation.currency_code == loan.currency_code,
                PlatformLoanFundingAllocation.outstanding_minor > 0,
            )
        ).scalars().all()

        available_minor = (
            sum(contributions) - sum(withdrawals) - sum(allocated)
        )
        if allocation["amount_minor"] > available_minor:
            raise PlatformLendingValidationError(
                f"Investor {investor.name} has insufficient available capital "
                f"in {loan.currency_code}."
            )

    remaining = loan.approved_principal_minor - loan.principal_minor
    if amount_minor > remaining:
        raise PlatformLendingValidationError(
            "Disbursement exceeds the loan's remaining approved principal."
        )

    cash_ledger = db.session.execute(
        select(PlatformLedgerAccount)
        .where(
            PlatformLedgerAccount.financial_account_id == account.id,
            PlatformLedgerAccount.currency_code == loan.currency_code,
            PlatformLedgerAccount.is_active.is_(True),
            PlatformLedgerAccount.account_class == "asset",
            PlatformLedgerAccount.normal_balance == "debit",
        )
        .with_for_update()
    ).scalar_one_or_none()

    if cash_ledger is None:
        raise PlatformLendingValidationError(
            "The selected financial account has no active debit-balance asset ledger."
        )

    receivable_ledger = db.session.execute(
        select(PlatformLedgerAccount)
        .where(
            PlatformLedgerAccount.code == f"PL-LOANS-RECEIVABLE-{loan.currency_code}",
            PlatformLedgerAccount.financial_account_id.is_(None),
            PlatformLedgerAccount.currency_code == loan.currency_code,
            PlatformLedgerAccount.account_class == "asset",
            PlatformLedgerAccount.normal_balance == "debit",
            PlatformLedgerAccount.is_system.is_(True),
            PlatformLedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if receivable_ledger is None:
        raise PlatformLendingValidationError(
            f"The active PL-LOANS-RECEIVABLE-{loan.currency_code} system ledger "
            "is not configured for the loan currency."
        )

    final_principal = loan.principal_minor + amount_minor
    fully_funded = final_principal == loan.approved_principal_minor
    start_date = None
    due_date = None
    schedule = []

    if fully_funded:
        start_date = disbursement_date.astimezone(timezone.utc).date()
        due_date = _platform_loan_due_date(
            start_date,
            loan.repayment_frequency,
            loan.term_count,
            loan.one_time_due_days,
        )

        schedule_loan = type(
            "PlatformScheduleLoan",
            (),
            {
                "principal_minor": final_principal,
                "term_count": loan.term_count,
                "start_date": start_date,
                "due_date": due_date,
                "repayment_frequency": loan.repayment_frequency,
                "interest_rate_percent": loan.interest_rate_percent,
                "interest_method": loan.interest_method,
            },
        )()

        try:
            schedule = build_repayment_schedule(schedule_loan)
        except ValueError as exc:
            raise PlatformLendingValidationError(
                f"Unable to generate the repayment schedule: {exc}"
            ) from exc

    entry = create_draft_entry(
        created_by=recorded_by,
        reference=f"PL-DISB-{uuid4().hex}",
        description=f"Platform loan disbursement {loan.id}",
        transaction_date=disbursement_date,
        lines=[
            {
                "ledger_account_id": receivable_ledger.id,
                "debit_minor": amount_minor,
                "credit_minor": 0,
                "memo": "Platform loan principal disbursed",
            },
            {
                "ledger_account_id": cash_ledger.id,
                "debit_minor": 0,
                "credit_minor": amount_minor,
                "memo": "Funds paid from platform financial account",
            },
        ],
    )

    post_entry(entry_id=entry.id, posted_by=recorded_by)

    disbursement = PlatformLoanDisbursement(
        loan_id=loan.id,
        financial_account_id=account.id,
        journal_entry_id=entry.id,
        currency_code=loan.currency_code,
        amount_minor=amount_minor,
        funding_source=funding_source,
        disbursement_date=disbursement_date,
        recorded_by=recorded_by,
        notes=notes,
    )
    db.session.add(disbursement)
    db.session.flush()

    try:
        customer_entry = create_customer_draft_entry(
            workspace_id=customer_workspace.id,
            created_by=recorded_by,
            reference=f"PL-DISB-CUSTOMER-{disbursement.id.hex}",
            description=f"Platform loan disbursement {loan.id}",
            transaction_date=disbursement_date,
            lines=[
                {
                    "ledger_account_id": customer_cash_ledger.id,
                    "debit_minor": amount_minor,
                    "credit_minor": 0,
                    "exchange_rate": customer_fx_rate,
                    "memo": "Platform loan proceeds received",
                },
                {
                    "ledger_account_id": customer_liability_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount_minor,
                    "exchange_rate": customer_fx_rate,
                    "memo": "Platform loan payable",
                },
            ],
        )

        post_customer_entry(
            entry_id=customer_entry.id,
            workspace_id=customer_workspace.id,
            posted_by=recorded_by,
        )
    except AccountingError as exc:
        raise PlatformLendingValidationError(
            f"Borrower accounting could not be posted: {exc}"
        ) from exc

    disbursement.customer_workspace_id = customer_workspace.id
    disbursement.customer_financial_account_id = customer_account.id
    disbursement.customer_journal_entry_id = customer_entry.id

    if funding_source == "pesapilot_capital":
        db.session.add(PlatformLoanFundingAllocation(
            loan_id=loan.id,
            disbursement_id=disbursement.id,
            funding_source="pesapilot_capital",
            investor_id=None,
            currency_code=loan.currency_code,
            amount_minor=amount_minor,
            outstanding_minor=amount_minor,
        ))
    else:
        for allocation in normalized_investor_allocations:
            db.session.add(PlatformLoanFundingAllocation(
                loan_id=loan.id,
                disbursement_id=disbursement.id,
                funding_source="external_investor",
                investor_id=allocation["investor_id"],
                currency_code=loan.currency_code,
                amount_minor=allocation["amount_minor"],
                outstanding_minor=allocation["amount_minor"],
            ))

    loan.principal_minor = final_principal
    loan.outstanding_principal_minor += amount_minor

    if fully_funded:
        loan.start_date = start_date
        loan.due_date = due_date
        loan.status = "active"

        for item in schedule:
            db.session.add(PlatformLoanInstallment(
                loan_id=loan.id,
                installment_number=item["installment_number"],
                due_date=item["due_date"],
                amount_minor=item["amount_minor"],
                principal_minor=item["principal_minor"],
                interest_minor=item["interest_minor"],
            ))
    else:
        loan.status = "funding"

    db.session.flush()
    return disbursement



def _platform_investor_available_capital(investor_id, currency_code):
    contributions = db.session.execute(
        select(PlatformInvestorContribution.amount_minor).where(
            PlatformInvestorContribution.investor_id == investor_id,
            PlatformInvestorContribution.currency_code == currency_code,
            PlatformInvestorContribution.status == "posted",
        )
    ).scalars().all()

    withdrawals = db.session.execute(
        select(PlatformInvestorWithdrawal.amount_minor).where(
            PlatformInvestorWithdrawal.investor_id == investor_id,
            PlatformInvestorWithdrawal.currency_code == currency_code,
            PlatformInvestorWithdrawal.status == "posted",
        )
    ).scalars().all()

    allocated = db.session.execute(
        select(PlatformLoanFundingAllocation.outstanding_minor).where(
            PlatformLoanFundingAllocation.investor_id == investor_id,
            PlatformLoanFundingAllocation.currency_code == currency_code,
            PlatformLoanFundingAllocation.outstanding_minor > 0,
        )
    ).scalars().all()

    return sum(contributions) - sum(withdrawals) - sum(allocated)


def _get_platform_investor_accounting_ledgers(account):
    cash_ledger = db.session.execute(
        select(PlatformLedgerAccount)
        .where(
            PlatformLedgerAccount.financial_account_id == account.id,
            PlatformLedgerAccount.currency_code == account.currency_code,
            PlatformLedgerAccount.is_active.is_(True),
            PlatformLedgerAccount.account_class == "asset",
            PlatformLedgerAccount.normal_balance == "debit",
        )
        .with_for_update()
    ).scalar_one_or_none()

    if cash_ledger is None:
        raise PlatformLendingValidationError(
            "The selected financial account has no active debit-balance asset ledger."
        )

    capital_ledger = db.session.execute(
        select(PlatformLedgerAccount)
        .where(
            PlatformLedgerAccount.code == (
                f"PL-INVESTOR-CAPITAL-{account.currency_code}"
            ),
            PlatformLedgerAccount.financial_account_id.is_(None),
            PlatformLedgerAccount.currency_code == account.currency_code,
            PlatformLedgerAccount.account_class == "liability",
            PlatformLedgerAccount.normal_balance == "credit",
            PlatformLedgerAccount.is_system.is_(True),
            PlatformLedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if capital_ledger is None:
        raise PlatformLendingValidationError(
            "The active investor-capital liability system ledger is not configured "
            f"for {account.currency_code}."
        )

    return cash_ledger, capital_ledger


def _validate_platform_investor_transaction(
    *, amount_minor, transaction_date, reference, notes
):
    if type(amount_minor) is not int or amount_minor <= 0:
        raise PlatformLendingValidationError(
            "Amount must be a positive integer in minor units."
        )

    if (
        not isinstance(transaction_date, datetime)
        or transaction_date.tzinfo is None
        or transaction_date.utcoffset() is None
    ):
        raise PlatformLendingValidationError(
            "A timezone-aware transaction date is required."
        )

    if reference is not None:
        if not isinstance(reference, str):
            raise PlatformLendingValidationError("Reference must be text.")
        reference = reference.strip() or None
        if reference is not None and len(reference) > 100:
            raise PlatformLendingValidationError(
                "Reference cannot exceed 100 characters."
            )

    if notes is not None:
        if not isinstance(notes, str):
            raise PlatformLendingValidationError("Notes must be text.")
        notes = notes.strip() or None
        if notes is not None and len(notes) > 5000:
            raise PlatformLendingValidationError(
                "Notes cannot exceed 5000 characters."
            )

    return reference, notes


def record_platform_investor_contribution(
    *,
    investor_id,
    financial_account_id,
    amount_minor,
    currency_code,
    contribution_date,
    recorded_by,
    reference=None,
    notes=None,
):
    """Record investor capital received and its liability accounting entry."""

    reference, notes = _validate_platform_investor_transaction(
        amount_minor=amount_minor,
        transaction_date=contribution_date,
        reference=reference,
        notes=notes,
    )

    investor = db.session.execute(
        select(PlatformInvestor)
        .where(PlatformInvestor.id == investor_id)
        .with_for_update()
    ).scalar_one_or_none()

    if investor is None or not investor.is_active:
        raise PlatformLendingValidationError(
            "The selected investor does not exist or is inactive."
        )

    account = db.session.execute(
        select(PlatformFinancialAccount)
        .where(PlatformFinancialAccount.id == financial_account_id)
        .with_for_update()
    ).scalar_one_or_none()

    if account is None or not account.is_active:
        raise PlatformLendingValidationError(
            "The selected platform financial account is unavailable."
        )

    if account.currency_code != currency_code:
        raise PlatformLendingValidationError(
            "The financial account currency must match the contribution currency."
        )

    try:
        cash_ledger, capital_ledger = (
            _get_platform_investor_accounting_ledgers(account)
        )

        entry = create_draft_entry(
            created_by=recorded_by,
            reference=f"PL-INV-CONTRIB-{uuid4().hex}",
            description=f"Investor capital contribution for {investor.name}",
            transaction_date=contribution_date,
            lines=[
                {
                    "ledger_account_id": cash_ledger.id,
                    "debit_minor": amount_minor,
                    "credit_minor": 0,
                    "memo": "Investor capital received",
                },
                {
                    "ledger_account_id": capital_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount_minor,
                    "memo": "Investor capital liability recognized",
                },
            ],
        )
        post_entry(entry_id=entry.id, posted_by=recorded_by)

        contribution = PlatformInvestorContribution(
            investor_id=investor.id,
            financial_account_id=account.id,
            journal_entry_id=entry.id,
            currency_code=currency_code,
            amount_minor=amount_minor,
            contribution_date=contribution_date,
            reference=reference,
            status="posted",
            recorded_by=recorded_by,
            notes=notes,
        )
        db.session.add(contribution)
        db.session.flush()
        return contribution
    except PlatformAccountingError as exc:
        raise PlatformLendingValidationError(str(exc)) from exc
def record_platform_investor_withdrawal(
    *,
    investor_id,
    financial_account_id,
    amount_minor,
    currency_code,
    withdrawal_date,
    recorded_by,
    reference=None,
    notes=None,
):
    """Record an investor capital withdrawal from available unallocated funds."""

    reference, notes = _validate_platform_investor_transaction(
        amount_minor=amount_minor,
        transaction_date=withdrawal_date,
        reference=reference,
        notes=notes,
    )

    investor = db.session.execute(
        select(PlatformInvestor)
        .where(PlatformInvestor.id == investor_id)
        .with_for_update()
    ).scalar_one_or_none()

    if investor is None:
        raise PlatformLendingValidationError("The selected investor does not exist.")

    available_minor = _platform_investor_available_capital(
        investor.id,
        currency_code,
    )
    if amount_minor > available_minor:
        raise PlatformLendingValidationError(
            f"Withdrawal exceeds the investor's available unallocated capital "
            f"of {available_minor} minor units in {currency_code}."
        )

    account = db.session.execute(
        select(PlatformFinancialAccount)
        .where(PlatformFinancialAccount.id == financial_account_id)
        .with_for_update()
    ).scalar_one_or_none()

    if account is None or not account.is_active:
        raise PlatformLendingValidationError(
            "The selected platform financial account is unavailable."
        )

    if account.currency_code != currency_code:
        raise PlatformLendingValidationError(
            "The financial account currency must match the withdrawal currency."
        )

    try:
        cash_ledger, capital_ledger = (
            _get_platform_investor_accounting_ledgers(account)
        )

        entry = create_draft_entry(
            created_by=recorded_by,
            reference=f"PL-INV-WITHDRAW-{uuid4().hex}",
            description=f"Investor capital withdrawal for {investor.name}",
            transaction_date=withdrawal_date,
            lines=[
                {
                    "ledger_account_id": capital_ledger.id,
                    "debit_minor": amount_minor,
                    "credit_minor": 0,
                    "memo": "Investor capital liability reduced",
                },
                {
                    "ledger_account_id": cash_ledger.id,
                    "debit_minor": 0,
                    "credit_minor": amount_minor,
                    "memo": "Investor capital withdrawn",
                },
            ],
        )
        post_entry(entry_id=entry.id, posted_by=recorded_by)

        withdrawal = PlatformInvestorWithdrawal(
            investor_id=investor.id,
            financial_account_id=account.id,
            journal_entry_id=entry.id,
            currency_code=currency_code,
            amount_minor=amount_minor,
            withdrawal_date=withdrawal_date,
            reference=reference,
            status="posted",
            recorded_by=recorded_by,
            notes=notes,
        )
        db.session.add(withdrawal)
        db.session.flush()
        return withdrawal
    except PlatformAccountingError as exc:
        raise PlatformLendingValidationError(str(exc)) from exc

def _validate_platform_investor_reversal(reference, transaction_date):
    if not isinstance(reference, str):
        raise PlatformLendingValidationError(
            "A reversal reference is required."
        )

    reference = reference.strip()
    if not reference or len(reference) > 100:
        raise PlatformLendingValidationError(
            "Reversal reference must contain between 1 and 100 characters."
        )

    if transaction_date is None:
        transaction_date = datetime.now(timezone.utc)

    if (
        not isinstance(transaction_date, datetime)
        or transaction_date.tzinfo is None
        or transaction_date.utcoffset() is None
    ):
        raise PlatformLendingValidationError(
            "A timezone-aware reversal date is required."
        )

    return reference, transaction_date


def reverse_platform_investor_contribution(
    *,
    contribution_id,
    reversed_by,
    reference,
    transaction_date=None,
):
    """Reverse a contribution without making allocated investor capital insolvent."""

    reference, transaction_date = _validate_platform_investor_reversal(
        reference,
        transaction_date,
    )

    investor_id = db.session.execute(
        select(PlatformInvestorContribution.investor_id).where(
            PlatformInvestorContribution.id == contribution_id
        )
    ).scalar_one_or_none()

    if investor_id is None:
        raise PlatformLendingValidationError(
            "The investor contribution does not exist."
        )

    investor = db.session.execute(
        select(PlatformInvestor)
        .where(PlatformInvestor.id == investor_id)
        .with_for_update()
    ).scalar_one_or_none()

    if investor is None:
        raise PlatformLendingValidationError(
            "The contribution's investor does not exist."
        )

    contribution = db.session.execute(
        select(PlatformInvestorContribution)
        .where(PlatformInvestorContribution.id == contribution_id)
        .with_for_update()
    ).scalar_one_or_none()

    if contribution is None or contribution.investor_id != investor.id:
        raise PlatformLendingValidationError(
            "The investor contribution could not be verified."
        )

    if contribution.status != "posted" or not contribution.journal_entry_id:
        raise PlatformLendingValidationError(
            "Only posted investor contributions with an accounting entry can be reversed."
        )

    available_minor = _platform_investor_available_capital(
        investor.id,
        contribution.currency_code,
    )
    if available_minor < contribution.amount_minor:
        raise PlatformLendingValidationError(
            "The contribution cannot be reversed because some of its capital "
            "has been withdrawn or remains allocated to outstanding loans."
        )

    try:
        reverse_entry(
            entry_id=contribution.journal_entry_id,
            reversed_by=reversed_by,
            reference=reference,
            description=f"Reversal of investor contribution for {investor.name}",
            transaction_date=transaction_date,
        )
        contribution.status = "reversed"
        db.session.flush()
        return contribution
    except PlatformAccountingError as exc:
        raise PlatformLendingValidationError(str(exc)) from exc


def reverse_platform_investor_withdrawal(
    *,
    withdrawal_id,
    reversed_by,
    reference,
    transaction_date=None,
):
    """Reverse an investor withdrawal and restore the investor's available capital."""

    reference, transaction_date = _validate_platform_investor_reversal(
        reference,
        transaction_date,
    )

    investor_id = db.session.execute(
        select(PlatformInvestorWithdrawal.investor_id).where(
            PlatformInvestorWithdrawal.id == withdrawal_id
        )
    ).scalar_one_or_none()

    if investor_id is None:
        raise PlatformLendingValidationError(
            "The investor withdrawal does not exist."
        )

    investor = db.session.execute(
        select(PlatformInvestor)
        .where(PlatformInvestor.id == investor_id)
        .with_for_update()
    ).scalar_one_or_none()

    if investor is None:
        raise PlatformLendingValidationError(
            "The withdrawal's investor does not exist."
        )

    withdrawal = db.session.execute(
        select(PlatformInvestorWithdrawal)
        .where(PlatformInvestorWithdrawal.id == withdrawal_id)
        .with_for_update()
    ).scalar_one_or_none()

    if withdrawal is None or withdrawal.investor_id != investor.id:
        raise PlatformLendingValidationError(
            "The investor withdrawal could not be verified."
        )

    if withdrawal.status != "posted" or not withdrawal.journal_entry_id:
        raise PlatformLendingValidationError(
            "Only posted investor withdrawals with an accounting entry can be reversed."
        )

    try:
        reverse_entry(
            entry_id=withdrawal.journal_entry_id,
            reversed_by=reversed_by,
            reference=reference,
            description=f"Reversal of investor withdrawal for {investor.name}",
            transaction_date=transaction_date,
        )
        withdrawal.status = "reversed"
        db.session.flush()
        return withdrawal
    except PlatformAccountingError as exc:
        raise PlatformLendingValidationError(str(exc)) from exc


def record_platform_loan_payment(
    *,
    loan_id,
    amount_minor,
    payment_date,
    payment_method,
    recorded_by,
    reference=None,
    notes=None,
):
    """Record a pending repayment instruction or manually reported receipt."""

    if type(amount_minor) is not int or amount_minor <= 0:
        raise PlatformLendingValidationError(
            "Payment amount must be a positive integer."
        )

    if type(payment_date) is not date:
        raise PlatformLendingValidationError(
            "A valid payment date is required."
        )

    if payment_method not in {"cash", "bank", "mobile_money", "other"}:
        raise PlatformLendingValidationError(
            "Payment method must be cash, bank, mobile_money, or other."
        )

    if reference is not None:
        if not isinstance(reference, str) or not reference.strip():
            raise PlatformLendingValidationError(
                "Payment reference must be non-empty text."
            )
        reference = reference.strip()
        if len(reference) > 100:
            raise PlatformLendingValidationError(
                "Payment reference must not exceed 100 characters."
            )

    if notes is not None:
        if not isinstance(notes, str):
            raise PlatformLendingValidationError(
                "Payment notes must be text."
            )
        notes = notes.strip()
        if len(notes) > 5000:
            raise PlatformLendingValidationError(
                "Payment notes must not exceed 5000 characters."
            )

    loan = db.session.execute(
        select(PlatformLoan)
        .where(PlatformLoan.id == loan_id)
        .with_for_update()
    ).scalar_one_or_none()

    if loan is None:
        return None

    if loan.status not in {"active", "overdue"}:
        raise PlatformLendingValidationError(
            "Repayments can only be recorded for active or overdue loans."
        )

    installments = db.session.execute(
        select(PlatformLoanInstallment)
        .where(
            PlatformLoanInstallment.loan_id == loan.id,
            PlatformLoanInstallment.status.in_(
                ["scheduled", "partially_paid", "overdue"]
            ),
        )
        .order_by(
            PlatformLoanInstallment.due_date.asc(),
            PlatformLoanInstallment.installment_number.asc(),
        )
        .with_for_update()
    ).scalars().all()

    outstanding_schedule = sum(
        installment.amount_minor - installment.paid_minor
        for installment in installments
    )

    if outstanding_schedule <= 0:
        raise PlatformLendingValidationError(
            "This loan has no outstanding scheduled repayments."
        )

    if amount_minor > outstanding_schedule:
        raise PlatformLendingValidationError(
            "Payment exceeds the outstanding scheduled repayment balance."
        )

    payment = PlatformLoanPayment(
        loan_id=loan.id,
        currency_code=loan.currency_code,
        amount_minor=amount_minor,
        payment_date=payment_date,
        payment_method=payment_method,
        reference=reference,
        status="pending",
        recorded_by=recorded_by,
        notes=notes,
    )

    db.session.add(payment)

    try:
        db.session.flush()
    except IntegrityError as exc:
        db.session.rollback()
        raise PlatformLendingValidationError(
            "A repayment with this reference already exists for the loan."
        ) from exc

    return payment

def confirm_platform_loan_payment(
    *,
    payment_id,
    financial_account_id,
    confirmed_by,
):
    """Confirm a pending platform repayment and post its accounting entry."""

    payment = db.session.execute(
        select(PlatformLoanPayment)
        .where(PlatformLoanPayment.id == payment_id)
        .with_for_update()
    ).scalar_one_or_none()

    if payment is None:
        return None

    if payment.status != "pending":
        raise PlatformLendingValidationError(
            "Only pending platform loan payments can be confirmed."
        )

    loan = db.session.execute(
        select(PlatformLoan)
        .where(PlatformLoan.id == payment.loan_id)
        .with_for_update()
    ).scalar_one_or_none()

    if loan is None:
        raise PlatformLendingValidationError("Platform loan not found.")

    if loan.status not in {"active", "overdue"}:
        raise PlatformLendingValidationError(
            "This platform loan cannot receive repayments."
        )

    account = db.session.execute(
        select(PlatformFinancialAccount)
        .where(PlatformFinancialAccount.id == financial_account_id)
        .with_for_update()
    ).scalar_one_or_none()

    if account is None or not account.is_active:
        raise PlatformLendingValidationError(
            "An active platform financial account is required."
        )

    if account.account_type not in {"cash", "bank", "mobile_money"}:
        raise PlatformLendingValidationError(
            "Repayments require a cash, bank, or mobile-money account."
        )

    if (
        payment.payment_method != "other"
        and payment.payment_method != account.account_type
    ):
        raise PlatformLendingValidationError(
            "Payment method does not match the selected financial account."
        )

    if not (
        loan.currency_code
        == payment.currency_code
        == account.currency_code
    ):
        raise PlatformLendingValidationError(
            "Loan, payment, and financial account currencies must match."
        )

    cash_ledger = db.session.execute(
        select(PlatformLedgerAccount)
        .where(
            PlatformLedgerAccount.financial_account_id == account.id,
            PlatformLedgerAccount.currency_code == loan.currency_code,
            PlatformLedgerAccount.is_active.is_(True),
            PlatformLedgerAccount.account_class == "asset",
            PlatformLedgerAccount.normal_balance == "debit",
        )
        .with_for_update()
    ).scalar_one_or_none()

    if cash_ledger is None:
        raise PlatformLendingValidationError(
            "The selected account has no active debit-balance asset ledger."
        )

    receivable_ledger = db.session.execute(
        select(PlatformLedgerAccount)
        .where(
            PlatformLedgerAccount.code == f"PL-LOANS-RECEIVABLE-{loan.currency_code}",
            PlatformLedgerAccount.financial_account_id.is_(None),
            PlatformLedgerAccount.currency_code == loan.currency_code,
            PlatformLedgerAccount.account_class == "asset",
            PlatformLedgerAccount.normal_balance == "debit",
            PlatformLedgerAccount.is_system.is_(True),
            PlatformLedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if receivable_ledger is None:
        raise PlatformLendingValidationError(
            f"The active PL-LOANS-RECEIVABLE-{loan.currency_code} system ledger "
            "is not configured for the loan currency."
        )

    interest_ledger = db.session.execute(
        select(PlatformLedgerAccount)
        .where(
            PlatformLedgerAccount.code == f"PL-LOANS-INTEREST-INCOME-{loan.currency_code}",
            PlatformLedgerAccount.financial_account_id.is_(None),
            PlatformLedgerAccount.currency_code == loan.currency_code,
            PlatformLedgerAccount.account_class == "income",
            PlatformLedgerAccount.normal_balance == "credit",
            PlatformLedgerAccount.is_system.is_(True),
            PlatformLedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if interest_ledger is None:
        raise PlatformLendingValidationError(
            f"The active PL-LOANS-INTEREST-INCOME-{loan.currency_code} system "
            "ledger is not configured for the loan currency."
        )

    installments = db.session.execute(
        select(PlatformLoanInstallment)
        .where(PlatformLoanInstallment.loan_id == loan.id)
        .order_by(
            PlatformLoanInstallment.due_date.asc(),
            PlatformLoanInstallment.installment_number.asc(),
        )
        .with_for_update()
    ).scalars().all()

    if not installments:
        raise PlatformLendingValidationError(
            "This platform loan has no repayment schedule."
        )

    previous_allocations = db.session.execute(
        select(PlatformLoanPaymentAllocation)
        .join(
            PlatformLoanPayment,
            PlatformLoanPayment.id
            == PlatformLoanPaymentAllocation.payment_id,
        )
        .where(
            PlatformLoanPaymentAllocation.loan_id == loan.id,
            PlatformLoanPayment.status == "confirmed",
        )
    ).scalars().all()

    allocated_by_installment = {
        item.id: {"amount": 0, "principal": 0, "interest": 0}
        for item in installments
    }

    for allocation in previous_allocations:
        balances = allocated_by_installment.get(
            allocation.installment_id
        )
        if balances is None:
            raise PlatformLendingValidationError(
                "A repayment allocation references an unknown installment."
            )

        balances["amount"] += allocation.amount_minor
        balances["principal"] += allocation.principal_minor
        balances["interest"] += allocation.interest_minor

    remaining_by_installment = {}

    for installment in installments:
        allocated = allocated_by_installment[installment.id]

        if allocated["amount"] != installment.paid_minor:
            raise PlatformLendingValidationError(
                "Installment balance does not match confirmed repayment "
                "allocations."
            )

        remaining_interest = (
            installment.interest_minor - allocated["interest"]
        )
        remaining_principal = (
            installment.principal_minor - allocated["principal"]
        )

        if remaining_interest < 0 or remaining_principal < 0:
            raise PlatformLendingValidationError(
                "Confirmed allocations exceed an installment component."
            )

        remaining_by_installment[installment.id] = {
            "interest": remaining_interest,
            "principal": remaining_principal,
        }

    outstanding_schedule = sum(
        balances["interest"] + balances["principal"]
        for balances in remaining_by_installment.values()
    )

    if payment.amount_minor > outstanding_schedule:
        raise PlatformLendingValidationError(
            "Payment exceeds the outstanding scheduled repayment balance."
        )

    amount_remaining = payment.amount_minor
    allocations = []
    principal_total = 0
    interest_total = 0

    # FIFO by due date; interest is allocated before principal.
    for installment in installments:
        if amount_remaining <= 0:
            break

        balances = remaining_by_installment[installment.id]

        interest_paid = min(
            amount_remaining,
            balances["interest"],
        )
        amount_remaining -= interest_paid

        principal_paid = min(
            amount_remaining,
            balances["principal"],
        )
        amount_remaining -= principal_paid

        allocation_amount = interest_paid + principal_paid

        if allocation_amount:
            allocations.append({
                "installment": installment,
                "amount_minor": allocation_amount,
                "principal_minor": principal_paid,
                "interest_minor": interest_paid,
            })
            principal_total += principal_paid
            interest_total += interest_paid

    if amount_remaining != 0:
        raise PlatformLendingValidationError(
            "The repayment could not be fully allocated to the schedule."
        )

    if principal_total + interest_total != payment.amount_minor:
        raise PlatformLendingValidationError(
            "Repayment allocation does not balance."
        )

    if principal_total > loan.outstanding_principal_minor:
        raise PlatformLendingValidationError(
            "Principal repayment exceeds the outstanding loan principal."
        )

    funding_allocations = db.session.execute(
        select(PlatformLoanFundingAllocation)
        .where(
            PlatformLoanFundingAllocation.loan_id == loan.id,
            PlatformLoanFundingAllocation.outstanding_minor > 0,
        )
        .order_by(PlatformLoanFundingAllocation.id)
        .with_for_update()
    ).scalars().all()

    if funding_allocations:
        total_tracked_outstanding = sum(
            item.outstanding_minor for item in funding_allocations
        )

        if total_tracked_outstanding != loan.outstanding_principal_minor:
            raise PlatformLendingValidationError(
                "Loan funding allocations do not reconcile with outstanding "
                "principal. Repayment confirmation requires funding-history "
                "reconciliation."
            )

        if principal_total:
            release_parts = []
            released_total = 0

            for funding in funding_allocations:
                numerator = principal_total * funding.outstanding_minor
                release_amount, remainder = divmod(
                    numerator,
                    total_tracked_outstanding,
                )
                release_parts.append({
                    "funding": funding,
                    "amount_minor": release_amount,
                    "remainder": remainder,
                })
                released_total += release_amount

            residual = principal_total - released_total

            for part in sorted(
                release_parts,
                key=lambda item: (
                    -item["remainder"],
                    str(item["funding"].id),
                ),
            )[:residual]:
                part["amount_minor"] += 1

            for part in release_parts:
                release_amount = part["amount_minor"]
                if release_amount <= 0:
                    continue

                funding = part["funding"]
                if release_amount > funding.outstanding_minor:
                    raise PlatformLendingValidationError(
                        "A funding release exceeds its outstanding allocation."
                    )

                funding.outstanding_minor -= release_amount
                db.session.add(PlatformLoanFundingRelease(
                    loan_id=loan.id,
                    payment_id=payment.id,
                    funding_allocation_id=funding.id,
                    amount_minor=release_amount,
                ))

    lines = [
        {
            "ledger_account_id": cash_ledger.id,
            "debit_minor": payment.amount_minor,
            "credit_minor": 0,
            "memo": "Platform loan repayment received",
        }
    ]

    if principal_total:
        lines.append({
            "ledger_account_id": receivable_ledger.id,
            "debit_minor": 0,
            "credit_minor": principal_total,
            "memo": "Platform loan principal repaid",
        })

    if interest_total:
        lines.append({
            "ledger_account_id": interest_ledger.id,
            "debit_minor": 0,
            "credit_minor": interest_total,
            "memo": "Platform loan interest received",
        })

    journal = create_draft_entry(
        created_by=confirmed_by,
        reference=f"PL-LOAN-PAYMENT-{payment.id}",
        description=f"Repayment for platform loan {loan.id}",
        transaction_date=datetime.combine(
            payment.payment_date,
            datetime.min.time(),
            tzinfo=timezone.utc,
        ),
        lines=lines,
    )

    post_entry(
        entry_id=journal.id,
        posted_by=confirmed_by,
    )

    for allocation in allocations:
        installment = allocation["installment"]

        db.session.add(PlatformLoanPaymentAllocation(
            loan_id=loan.id,
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

    loan.outstanding_principal_minor -= principal_total

    if all(
        item.status in {"paid", "waived"}
        for item in installments
    ):
        loan.status = "paid"
    elif any(item.status == "overdue" for item in installments):
        loan.status = "overdue"
    else:
        loan.status = "active"

    payment.principal_minor = principal_total
    payment.interest_minor = interest_total
    payment.financial_account_id = account.id
    payment.journal_entry_id = journal.id
    payment.confirmed_by = confirmed_by
    payment.confirmed_at = datetime.now(timezone.utc)
    payment.status = "confirmed"

    db.session.flush()
    return payment

def reverse_platform_loan_payment(
    *,
    payment_id,
    reversed_by,
    reference,
    transaction_date=None,
):
    """Reverse a confirmed platform repayment and restore loan balances."""

    if transaction_date is None:
        transaction_date = datetime.now(timezone.utc)

    if (
        not isinstance(transaction_date, datetime)
        or transaction_date.tzinfo is None
    ):
        raise PlatformLendingValidationError(
            "A timezone-aware reversal date is required."
        )

    payment = db.session.execute(
        select(PlatformLoanPayment)
        .where(PlatformLoanPayment.id == payment_id)
        .with_for_update()
    ).scalar_one_or_none()

    if payment is None:
        raise PlatformLendingValidationError(
            "Platform loan payment not found."
        )

    if payment.status != "confirmed":
        raise PlatformLendingValidationError(
            "Only confirmed platform loan payments can be reversed."
        )

    if payment.journal_entry_id is None:
        raise PlatformLendingValidationError(
            "The confirmed payment has no accounting journal."
        )

    loan = db.session.execute(
        select(PlatformLoan)
        .where(PlatformLoan.id == payment.loan_id)
        .with_for_update()
    ).scalar_one_or_none()

    if loan is None:
        raise PlatformLendingValidationError("Platform loan not found.")

    if loan.status not in {"active", "overdue", "paid"}:
        raise PlatformLendingValidationError(
            "This platform loan cannot have a repayment reversed."
        )

    later_payment = db.session.execute(
        select(PlatformLoanPayment.id)
        .where(
            PlatformLoanPayment.loan_id == loan.id,
            PlatformLoanPayment.status == "confirmed",
            PlatformLoanPayment.id != payment.id,
            PlatformLoanPayment.confirmed_at >= payment.confirmed_at,
        )
        .order_by(PlatformLoanPayment.confirmed_at.asc())
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()

    if later_payment is not None:
        raise PlatformLendingValidationError(
            "A later confirmed repayment exists. Reverse later repayments "
            "first."
        )

    allocations = db.session.execute(
        select(PlatformLoanPaymentAllocation)
        .where(
            PlatformLoanPaymentAllocation.payment_id == payment.id,
            PlatformLoanPaymentAllocation.loan_id == loan.id,
        )
        .with_for_update()
    ).scalars().all()

    if not allocations:
        raise PlatformLendingValidationError(
            "The confirmed payment has no repayment allocations."
        )

    allocated_amount = sum(item.amount_minor for item in allocations)
    allocated_principal = sum(
        item.principal_minor for item in allocations
    )
    allocated_interest = sum(
        item.interest_minor for item in allocations
    )

    if (
        allocated_amount != payment.amount_minor
        or allocated_principal != payment.principal_minor
        or allocated_interest != payment.interest_minor
    ):
        raise PlatformLendingValidationError(
            "Payment totals do not match its repayment allocations."
        )

    installments = {}

    for allocation in allocations:
        installment = db.session.execute(
            select(PlatformLoanInstallment)
            .where(
                PlatformLoanInstallment.id == allocation.installment_id,
                PlatformLoanInstallment.loan_id == loan.id,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if installment is None:
            raise PlatformLendingValidationError(
                "A repayment allocation references an invalid installment."
            )

        installments[installment.id] = installment

    for allocation in allocations:
        installment = installments[allocation.installment_id]

        if installment.paid_minor < allocation.amount_minor:
            raise PlatformLendingValidationError(
                "Installment paid balance is insufficient for reversal."
            )

    if (
        loan.outstanding_principal_minor + allocated_principal
        > loan.principal_minor
    ):
        raise PlatformLendingValidationError(
            "Reversal would exceed the loan's disbursed principal."
        )

    funding_releases = db.session.execute(
        select(PlatformLoanFundingRelease)
        .where(
            PlatformLoanFundingRelease.payment_id == payment.id,
            PlatformLoanFundingRelease.loan_id == loan.id,
            PlatformLoanFundingRelease.is_reversed.is_(False),
        )
        .order_by(PlatformLoanFundingRelease.funding_allocation_id)
        .with_for_update()
    ).scalars().all()

    tracked_funding_allocation_id = db.session.execute(
        select(PlatformLoanFundingAllocation.id)
        .where(PlatformLoanFundingAllocation.loan_id == loan.id)
        .limit(1)
        .with_for_update()
    ).scalar_one_or_none()

    if (
        tracked_funding_allocation_id is not None
        and allocated_principal > 0
        and not funding_releases
    ):
        raise PlatformLendingValidationError(
            "Tracked loan funding allocations have no active release records "
            "for this repayment. Funding history reconciliation is required."
        )

    if funding_releases:
        released_principal = sum(
            item.amount_minor for item in funding_releases
        )

        if released_principal != allocated_principal:
            raise PlatformLendingValidationError(
                "Funding releases do not match the repayment principal."
            )

        funding_ids = {
            item.funding_allocation_id for item in funding_releases
        }

        funding_rows = db.session.execute(
            select(PlatformLoanFundingAllocation)
            .where(
                PlatformLoanFundingAllocation.loan_id == loan.id,
                PlatformLoanFundingAllocation.id.in_(funding_ids),
            )
            .order_by(PlatformLoanFundingAllocation.id)
            .with_for_update()
        ).scalars().all()

        funding_by_id = {item.id: item for item in funding_rows}

        if len(funding_by_id) != len(funding_ids):
            raise PlatformLendingValidationError(
                "A repayment funding release references a missing allocation."
            )

        for release in funding_releases:
            funding = funding_by_id[release.funding_allocation_id]

            if (
                funding.outstanding_minor + release.amount_minor
                > funding.amount_minor
            ):
                raise PlatformLendingValidationError(
                    "Reversing this repayment would exceed the original "
                    "funding allocation."
                )

            funding.outstanding_minor += release.amount_minor
            release.is_reversed = True
            release.reversed_at = transaction_date

    reversal = reverse_entry(
        entry_id=payment.journal_entry_id,
        reversed_by=reversed_by,
        reference=reference,
        description=f"Reversal of platform loan payment {payment.id}",
        transaction_date=transaction_date,
    )

    reversal_date = transaction_date.astimezone(timezone.utc).date()

    for allocation in allocations:
        installment = installments[allocation.installment_id]
        installment.paid_minor -= allocation.amount_minor

    for installment in installments.values():
        if installment.status == "waived":
            continue

        if installment.paid_minor == installment.amount_minor:
            installment.status = "paid"
        elif installment.due_date < reversal_date:
            installment.status = "overdue"
        elif installment.paid_minor > 0:
            installment.status = "partially_paid"
        else:
            installment.status = "scheduled"

    loan.outstanding_principal_minor += allocated_principal

    all_installments = db.session.execute(
        select(PlatformLoanInstallment)
        .where(PlatformLoanInstallment.loan_id == loan.id)
    ).scalars().all()

    if all(
        item.status in {"paid", "waived"}
        for item in all_installments
    ):
        loan.status = "paid"
    elif any(item.status == "overdue" for item in all_installments):
        loan.status = "overdue"
    else:
        loan.status = "active"

    payment.status = "reversed"
    payment.reversed_at = datetime.now(timezone.utc)

    db.session.flush()
    return reversal



def reject_platform_loan_payment(
    *,
    payment_id,
    rejected_by,
    reason,
):
    """Reject a pending customer-reported platform loan repayment."""

    if not isinstance(reason, str) or not reason.strip():
        raise PlatformLendingValidationError(
            "A rejection reason is required."
        )

    reason = reason.strip()
    if len(reason) > 500:
        raise PlatformLendingValidationError(
            "Rejection reason cannot exceed 500 characters."
        )

    payment = db.session.execute(
        select(PlatformLoanPayment)
        .where(PlatformLoanPayment.id == payment_id)
        .with_for_update()
    ).scalar_one_or_none()

    if payment is None:
        return None

    if payment.status != "pending":
        raise PlatformLendingValidationError(
            "Only pending repayments can be rejected."
        )

    # A pending report must not already have accounting or allocation effects.
    if (
        payment.financial_account_id is not None
        or payment.journal_entry_id is not None
        or payment.confirmed_by is not None
        or payment.confirmed_at is not None
        or payment.principal_minor != 0
        or payment.interest_minor != 0
    ):
        raise PlatformLendingValidationError(
            "This pending repayment has accounting data and cannot be rejected."
        )

    existing_allocation = db.session.execute(
        select(PlatformLoanPaymentAllocation.id)
        .where(PlatformLoanPaymentAllocation.payment_id == payment.id)
        .limit(1)
    ).scalar_one_or_none()

    if existing_allocation is not None:
        raise PlatformLendingValidationError(
            "This pending repayment already has allocations and cannot be rejected."
        )

    payment.status = "rejected"
    payment.rejected_by = rejected_by
    payment.rejected_at = datetime.now(timezone.utc)
    payment.rejection_reason = reason

    db.session.flush()
    return payment
