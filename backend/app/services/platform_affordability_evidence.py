"""Collect authorized financial evidence for platform-loan affordability."""

from collections import defaultdict
from datetime import date, datetime, time, timedelta, timezone
from decimal import Decimal

from sqlalchemy import func, select

from ..extensions import db
from ..models.core import (
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Workspace,
    WorkspaceMember,
)
from ..models.loans import PersonalLoan
from ..models.loan_repayments import LoanInstallment
from ..models.platform_lending import PlatformLoanProduct
from ..models.platform_loan_records import (
    PlatformLoan,
    PlatformLoanInstallment,
)
from .platform_affordability import (
    EVIDENCE_WINDOW_MONTHS,
    calculate_affordability_capacity,
    classify_affordability,
)


def _six_month_start(as_of):
    month_index = as_of.year * 12 + as_of.month - 1 - EVIDENCE_WINDOW_MONTHS
    year, month_index = divmod(month_index, 12)
    month = month_index + 1
    import calendar
    return date(year, month, min(as_of.day, calendar.monthrange(year, month)[1]))


def _monthly_equivalent(amount_minor, frequency, start_date=None, due_date=None, as_of=None):
    amount = Decimal(amount_minor)
    ratios = {
        "weekly": (52, 12),
        "biweekly": (26, 12),
        "monthly": (1, 1),
        "quarterly": (1, 3),
    }
    if frequency in ratios:
        numerator, denominator = ratios[frequency]
        return int(amount * numerator // denominator)

    if frequency == "one_time" and due_date and as_of:
        days_remaining = max(1, (due_date - as_of).days)
        months_remaining = max(Decimal(1), Decimal(days_remaining) / Decimal("30.4375"))
        return int(amount / months_remaining)

    return 0


def _percentage_difference(declared, evidenced):
    denominator = max(abs(declared), abs(evidenced), 1)
    return Decimal(abs(declared - evidenced)) / Decimal(denominator)


def collect_personal_workspace_evidence(
    *,
    customer_id,
    workspace_id,
    declared_monthly_income_minor,
    declared_monthly_expenses_minor,
    as_of=None,
):
    """Read six months of posted income/expense evidence from an owned personal workspace.

    The caller must obtain explicit customer consent before calling this function.
    Business workspaces and workspaces not owned by the customer are rejected.
    """
    as_of = as_of or date.today()
    if not isinstance(as_of, date):
        raise ValueError("Assessment date must be a date.")

    workspace = db.session.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    ).scalar_one_or_none()

    if workspace is None or workspace.workspace_type != "personal":
        raise ValueError("Affordability evidence requires a personal workspace.")

    membership = db.session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == customer_id,
            WorkspaceMember.role == "owner",
        )
    ).scalar_one_or_none()

    if membership is None:
        raise ValueError("The applicant must own the selected personal workspace.")

    start_date = _six_month_start(as_of)
    start_at = datetime.combine(start_date, time.min, tzinfo=timezone.utc)
    end_at = datetime.combine(as_of + timedelta(days=1), time.min, tzinfo=timezone.utc)

    rows = db.session.execute(
        select(JournalEntry, JournalLine, LedgerAccount)
        .join(
            JournalLine,
            JournalLine.journal_entry_id == JournalEntry.id,
        )
        .join(
            LedgerAccount,
            LedgerAccount.id == JournalLine.ledger_account_id,
        )
        .where(
            JournalEntry.workspace_id == workspace_id,
            LedgerAccount.workspace_id == workspace_id,
            JournalEntry.status == "posted",
            JournalEntry.reversal_of_id.is_(None),
            JournalEntry.transaction_date >= start_at,
            JournalEntry.transaction_date < end_at,
            LedgerAccount.account_class.in_(("income", "expense")),
            LedgerAccount.is_active.is_(True),
        )
    ).all()

    monthly = defaultdict(lambda: {"income": 0, "expenses": 0})
    for entry, line, account in rows:
        month_key = entry.transaction_date.year, entry.transaction_date.month
        if account.account_class == "income":
            monthly[month_key]["income"] += (
                line.functional_credit_minor - line.functional_debit_minor
            )
        else:
            monthly[month_key]["expenses"] += (
                line.functional_debit_minor - line.functional_credit_minor
            )

    income_total = sum(max(0, item["income"]) for item in monthly.values())
    expenses_total = sum(max(0, item["expenses"]) for item in monthly.values())
    months_with_records = len(monthly)

    evidenced_income = income_total // EVIDENCE_WINDOW_MONTHS
    evidenced_expenses = expenses_total // EVIDENCE_WINDOW_MONTHS

    declared_income = int(declared_monthly_income_minor)
    declared_expenses = int(declared_monthly_expenses_minor)

    sufficient = (
        months_with_records >= 3
        and any(item["income"] > 0 for item in monthly.values())
        and any(item["expenses"] > 0 for item in monthly.values())
    )

    discrepancy = sufficient and (
        _percentage_difference(declared_income, evidenced_income) > Decimal("0.20")
        or _percentage_difference(declared_expenses, evidenced_expenses) > Decimal("0.20")
    )

    return {
        "workspace_id": str(workspace_id),
        "evidence_window_months": EVIDENCE_WINDOW_MONTHS,
        "months_with_records": months_with_records,
        "record_count": len(rows),
        "declared_monthly_income_minor": declared_income,
        "declared_monthly_expenses_minor": declared_expenses,
        "evidenced_average_monthly_income_minor": evidenced_income,
        "evidenced_average_monthly_expenses_minor": evidenced_expenses,
        "has_sufficient_records": sufficient,
        "has_material_discrepancy": discrepancy,
        "income_difference_ratio": str(
            _percentage_difference(declared_income, evidenced_income)
        ),
        "expenses_difference_ratio": str(
            _percentage_difference(declared_expenses, evidenced_expenses)
        ),
        "period_start": start_date.isoformat(),
        "period_end": as_of.isoformat(),
    }


def collect_existing_monthly_debt(*, customer_id, workspace_id=None, as_of=None):
    """Calculate existing monthly obligations without mixing lending domains."""
    as_of = as_of or date.today()
    personal_monthly = 0
    platform_monthly = 0
    overdue_personal = 0
    overdue_platform = 0

    if workspace_id is not None:
        borrowed_loans = db.session.execute(
            select(PersonalLoan).where(
                PersonalLoan.workspace_id == workspace_id,
                PersonalLoan.created_by == customer_id,
                PersonalLoan.direction == "borrowed",
                PersonalLoan.status.in_(("active", "overdue", "restructured")),
            )
        ).scalars().all()

        for loan in borrowed_loans:
            if loan.installment_minor:
                personal_monthly += _monthly_equivalent(
                    loan.installment_minor,
                    loan.repayment_frequency,
                    loan.start_date,
                    loan.due_date,
                    as_of,
                )

            overdue_balance = db.session.execute(
                select(func.coalesce(func.sum(
                    LoanInstallment.amount_minor - LoanInstallment.paid_minor
                ), 0)).where(
                    LoanInstallment.workspace_id == workspace_id,
                    LoanInstallment.personal_loan_id == loan.id,
                    LoanInstallment.status == "overdue",
                )
            ).scalar_one()
            overdue_personal += int(overdue_balance or 0)

    platform_loans = db.session.execute(
        select(PlatformLoan).where(
            PlatformLoan.customer_id == customer_id,
            PlatformLoan.status.in_(("active", "overdue")),
        )
    ).scalars().all()

    for loan in platform_loans:
        installments = db.session.execute(
            select(PlatformLoanInstallment).where(
                PlatformLoanInstallment.loan_id == loan.id,
                PlatformLoanInstallment.status != "waived",
                PlatformLoanInstallment.paid_minor < PlatformLoanInstallment.amount_minor,
            )
        ).scalars().all()

        for installment in installments:
            remaining = installment.amount_minor - installment.paid_minor
            if installment.due_date < as_of and remaining > 0:
                overdue_platform += remaining

        if installments:
            # Use the contractual installment amount, not the remaining
            # balance divided by an assumed number of months.
            future_installments = [
                item for item in installments
                if item.due_date >= as_of
            ]
            if future_installments:
                schedule_amounts = [
                    item.amount_minor for item in future_installments
                ]
                average_installment = (
                    sum(schedule_amounts) // len(schedule_amounts)
                )
                actual_final_due_date = max(
                    item.due_date for item in future_installments
                )
                platform_monthly += _monthly_equivalent(
                    average_installment,
                    loan.repayment_frequency,
                    loan.start_date,
                    actual_final_due_date,
                    as_of,
                )

    return {
        "existing_personal_monthly_repayments_minor": personal_monthly,
        "existing_platform_monthly_repayments_minor": platform_monthly,
        "existing_monthly_repayments_minor": personal_monthly + platform_monthly,
        "overdue_personal_balance_minor": overdue_personal,
        "overdue_platform_balance_minor": overdue_platform,
        "has_serious_repayment_concerns": (
            overdue_personal > 0 or overdue_platform > 0
        ),
    }


def build_affordability_evidence_report(
    *,
    customer_id,
    declared_monthly_income_minor,
    declared_monthly_expenses_minor,
    declared_existing_monthly_repayments_minor,
    consent,
    workspace_id=None,
    as_of=None,
):
    """Combine declarations and optional personal-workspace evidence."""
    as_of = as_of or date.today()

    if consent:
        if workspace_id is None:
            raise ValueError("Select a personal workspace to provide consented evidence.")
        workspace_evidence = collect_personal_workspace_evidence(
            customer_id=customer_id,
            workspace_id=workspace_id,
            declared_monthly_income_minor=declared_monthly_income_minor,
            declared_monthly_expenses_minor=declared_monthly_expenses_minor,
            as_of=as_of,
        )
    else:
        if workspace_id is not None:
            raise ValueError("A workspace cannot be supplied without consent.")
        workspace_evidence = {
            "workspace_id": None,
            "evidence_window_months": EVIDENCE_WINDOW_MONTHS,
            "months_with_records": 0,
            "record_count": 0,
            "declared_monthly_income_minor": int(declared_monthly_income_minor),
            "declared_monthly_expenses_minor": int(declared_monthly_expenses_minor),
            "evidenced_average_monthly_income_minor": None,
            "evidenced_average_monthly_expenses_minor": None,
            "has_sufficient_records": False,
            "has_material_discrepancy": False,
            "period_start": None,
            "period_end": None,
        }

    debt_evidence = collect_existing_monthly_debt(
        customer_id=customer_id,
        workspace_id=workspace_id if consent else None,
        as_of=as_of,
    )

    declared_existing = int(declared_existing_monthly_repayments_minor)
    known_existing = debt_evidence["existing_monthly_repayments_minor"]
    effective_existing = max(declared_existing, known_existing)

    capacity = calculate_affordability_capacity(
        monthly_income_minor=declared_monthly_income_minor,
        monthly_expenses_minor=declared_monthly_expenses_minor,
        existing_monthly_repayments_minor=effective_existing,
    )

    status = classify_affordability(
        has_sufficient_records=workspace_evidence["has_sufficient_records"],
        has_material_discrepancy=workspace_evidence["has_material_discrepancy"],
        has_serious_repayment_concerns=debt_evidence["has_serious_repayment_concerns"],
    )

    return {
        "status": status,
        "capacity": capacity,
        "workspace_evidence": workspace_evidence,
        "debt_evidence": debt_evidence,
        "declared_existing_monthly_repayments_minor": declared_existing,
        "effective_existing_monthly_repayments_minor": effective_existing,
        "assessment_is_advisory": True,
        "automatic_approval_or_rejection": False,
    }
