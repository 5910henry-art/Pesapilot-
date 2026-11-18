"""Transparent affordability calculations for PesaPilot platform lending."""

from decimal import Decimal, ROUND_FLOOR


MAX_DEBT_SERVICE_RATIO = Decimal("0.35")
EVIDENCE_WINDOW_MONTHS = 6

ASSESSMENT_STATUSES = {
    "verified",
    "needs_review",
    "high_risk",
    "insufficient_records",
}


def _amount(value, field_name):
    try:
        amount = int(value)
    except (TypeError, ValueError):
        raise ValueError(f"{field_name} must be a whole number.") from None

    if amount < 0:
        raise ValueError(f"{field_name} cannot be negative.")

    return amount


def calculate_affordability_capacity(
    *,
    monthly_income_minor,
    monthly_expenses_minor,
    existing_monthly_repayments_minor=0,
    debt_service_ratio=MAX_DEBT_SERVICE_RATIO,
):
    """Calculate the maximum instalment using declared monthly figures.

    The recommended instalment must satisfy both:
    1. Total monthly debt service must not exceed 35% of income.
    2. Total monthly debt service must not exceed disposable income.
    """

    income = _amount(monthly_income_minor, "Monthly income")
    expenses = _amount(monthly_expenses_minor, "Monthly expenses")
    existing_repayments = _amount(
        existing_monthly_repayments_minor,
        "Existing monthly repayments",
    )

    ratio = Decimal(str(debt_service_ratio))
    if ratio <= 0 or ratio > 1:
        raise ValueError("Debt service ratio must be greater than 0 and at most 1.")

    debt_service_ceiling = int(
        (Decimal(income) * ratio).to_integral_value(
            rounding=ROUND_FLOOR
        )
    )

    disposable_income = income - expenses - existing_repayments

    capacity_after_existing_debt = max(
        0,
        debt_service_ceiling - existing_repayments,
    )

    capacity_after_living_expenses = max(0, disposable_income)

    recommended_instalment = min(
        capacity_after_existing_debt,
        capacity_after_living_expenses,
    )

    return {
        "monthly_income_minor": income,
        "monthly_expenses_minor": expenses,
        "existing_monthly_repayments_minor": existing_repayments,
        "debt_service_ratio": str(ratio),
        "maximum_total_monthly_debt_service_minor": debt_service_ceiling,
        "disposable_income_minor": disposable_income,
        "recommended_monthly_instalment_minor": recommended_instalment,
    }


def classify_affordability(
    *,
    has_sufficient_records,
    has_material_discrepancy=False,
    has_serious_repayment_concerns=False,
):
    """Return a review status; this function never approves or rejects loans."""

    if has_serious_repayment_concerns:
        return "high_risk"

    if has_material_discrepancy:
        return "needs_review"

    if not has_sufficient_records:
        return "insufficient_records"

    return "verified"


def estimate_affordable_principal(
    *,
    monthly_capacity_minor,
    maximum_principal_minor,
    term_count,
    start_date,
    due_date,
    repayment_frequency,
    interest_method,
    interest_rate_percent,
):
    """Find the largest principal whose actual repayment schedule fits capacity.

    Repayment capacity accumulates between installment due dates. This
    supports weekly, biweekly, monthly, quarterly and one-time schedules.
    """
    from datetime import date
    from decimal import Decimal
    from types import SimpleNamespace

    from .loan_schedules import build_repayment_schedule

    capacity = _amount(monthly_capacity_minor, "monthly_capacity_minor")
    maximum = _amount(maximum_principal_minor, "maximum_principal_minor")

    if capacity == 0 or maximum == 0:
        return {
            "estimated_affordable_principal_minor": 0,
            "maximum_scheduled_instalment_minor": 0,
            "schedule": [],
        }

    if not isinstance(start_date, date) or not isinstance(due_date, date):
        raise ValueError("Valid start and due dates are required.")

    if due_date <= start_date:
        raise ValueError("The final due date must follow the start date.")

    if isinstance(term_count, bool) or int(term_count) <= 0:
        raise ValueError("Term count must be positive.")

    if repayment_frequency not in {
        "weekly", "biweekly", "monthly", "quarterly", "one_time"
    }:
        raise ValueError("Unsupported repayment frequency.")

    if repayment_frequency == "one_time" and int(term_count) != 1:
        raise ValueError("One-time loans must have exactly one installment.")

    def fits(principal):
        loan = SimpleNamespace(
            principal_minor=principal,
            term_count=int(term_count),
            start_date=start_date,
            due_date=due_date,
            repayment_frequency=repayment_frequency,
            interest_method=interest_method,
            interest_rate_percent=Decimal(str(interest_rate_percent)),
        )

        try:
            schedule = build_repayment_schedule(loan)
        except (ValueError, ArithmeticError):
            return False, [], 0

        reserve = Decimal("0")
        previous_date = start_date
        maximum_payment = 0

        for installment in schedule:
            days = (installment["due_date"] - previous_date).days
            reserve += (
                Decimal(capacity) * Decimal(days) / Decimal("30.4375")
            )
            payment = installment["amount_minor"]
            maximum_payment = max(maximum_payment, payment)

            if Decimal(payment) > reserve:
                return False, [], maximum_payment

            reserve -= Decimal(payment)
            previous_date = installment["due_date"]

        return True, schedule, maximum_payment

    low = 0
    high = maximum
    best_schedule = []
    best_payment = 0

    while low < high:
        candidate = (low + high + 1) // 2
        affordable, schedule, maximum_payment = fits(candidate)

        if affordable:
            low = candidate
            best_schedule = schedule
            best_payment = maximum_payment
        else:
            high = candidate - 1

    if low and not best_schedule:
        affordable, best_schedule, best_payment = fits(low)
        if not affordable:
            low = 0
            best_schedule = []
            best_payment = 0

    return {
        "estimated_affordable_principal_minor": low,
        "maximum_scheduled_instalment_minor": best_payment,
        "schedule": best_schedule,
    }
