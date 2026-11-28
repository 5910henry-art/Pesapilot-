import calendar
from datetime import date, timedelta
from decimal import Decimal, ROUND_HALF_UP, localcontext


DAYS_PER_MONTH = Decimal("30")
ZERO = Decimal("0")
ONE = Decimal("1")


def _round_minor(value):
    return int(
        value.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )


def _add_months(anchor, months):
    month_index = anchor.month - 1 + months
    year = anchor.year + month_index // 12
    month = month_index % 12 + 1
    day = min(anchor.day, calendar.monthrange(year, month)[1])
    return date(year, month, day)


def _installment_dates(loan):
    frequency = loan.repayment_frequency
    count = loan.term_count

    if frequency == "one_time":
        if count != 1:
            raise ValueError("One-time loans must have exactly one installment.")
        return [loan.due_date]

    if frequency not in {"weekly", "biweekly", "monthly", "quarterly"}:
        raise ValueError("Unsupported repayment frequency.")

    dates = []

    for number in range(1, count + 1):
        if frequency == "weekly":
            due = loan.start_date + timedelta(days=7 * number)
        elif frequency == "biweekly":
            due = loan.start_date + timedelta(days=14 * number)
        elif frequency == "monthly":
            due = _add_months(loan.start_date, number)
        else:
            due = _add_months(loan.start_date, 3 * number)

        dates.append(due)

    if dates[-1] != loan.due_date:
        raise ValueError(
            "Final due date does not match the selected repayment "
            "frequency and term."
        )

    return dates


def _period_interest(principal, monthly_rate, days):
    return _round_minor(
        principal * monthly_rate * Decimal(days) / DAYS_PER_MONTH
    )


def _flat_schedule(loan, dates, monthly_rate):
    principal = loan.principal_minor
    previous_date = loan.start_date
    interests = []

    for due_date in dates:
        days = (due_date - previous_date).days

        if days <= 0:
            raise ValueError("Installment dates must be increasing.")

        interests.append(
            _period_interest(
                Decimal(principal),
                monthly_rate,
                days,
            )
        )
        previous_date = due_date

    total_interest = sum(interests)
    regular_payment = _round_minor(
        Decimal(principal + total_interest) / Decimal(len(dates))
    )

    remaining_principal = principal
    schedule = []

    for index, (due_date, interest) in enumerate(zip(dates, interests)):
        if index == len(dates) - 1:
            principal_part = remaining_principal
            amount = principal_part + interest
        else:
            amount = regular_payment
            principal_part = amount - interest

        if principal_part <= 0 or principal_part > remaining_principal:
            raise ValueError(
                "The selected rate and term cannot amortize this loan "
                "with equal installments."
            )

        remaining_principal -= principal_part

        schedule.append({
            "installment_number": index + 1,
            "due_date": due_date,
            "amount_minor": amount,
            "principal_minor": principal_part,
            "interest_minor": interest,
        })

    if remaining_principal != 0:
        raise ValueError("Flat schedule did not repay the full principal.")

    return schedule


def _reducing_schedule(loan, dates, monthly_rate):
    principal = loan.principal_minor
    period_rates = []
    previous_date = loan.start_date

    for due_date in dates:
        days = (due_date - previous_date).days

        if days <= 0:
            raise ValueError("Installment dates must be increasing.")

        period_rates.append(monthly_rate * Decimal(days) / DAYS_PER_MONTH)
        previous_date = due_date

    # Present-value factors for equal payments over unequal periods.
    discount = ONE
    payment_factor = ZERO

    for rate in period_rates:
        discount *= ONE + rate
        payment_factor += ONE / discount

    if payment_factor <= ZERO:
        raise ValueError("Unable to calculate repayment installments.")

    regular_payment = _round_minor(
        Decimal(principal) / payment_factor
    )

    remaining_principal = principal
    schedule = []

    for index, (due_date, rate) in enumerate(zip(dates, period_rates)):
        interest = _round_minor(
            Decimal(remaining_principal) * rate
        )

        if index == len(dates) - 1:
            principal_part = remaining_principal
            amount = principal_part + interest
        else:
            amount = regular_payment
            principal_part = amount - interest

        if principal_part <= 0 or principal_part > remaining_principal:
            raise ValueError(
                "The selected rate and term cause negative amortization. "
                "Increase the installment amount or extend the term."
            )

        remaining_principal -= principal_part

        schedule.append({
            "installment_number": index + 1,
            "due_date": due_date,
            "amount_minor": amount,
            "principal_minor": principal_part,
            "interest_minor": interest,
        })

    if remaining_principal != 0:
        raise ValueError("Reducing schedule did not repay the full principal.")

    return schedule


def build_repayment_schedule(loan):
    """
    Build a repayment schedule without writing to the database.

    Interest rates may be monthly or nominal annual percentages. Annual rates are converted to monthly rates by dividing by 12. Interest is prorated using actual period days divided by 30. All amounts are integer minor units. Interest is prorated using
    actual period days divided by 30. All amounts are integer minor units.
    """

    if isinstance(loan.principal_minor, bool) or loan.principal_minor <= 0:
        raise ValueError("Loan principal must be positive.")

    if loan.term_count <= 0:
        raise ValueError("Loan term must be positive.")

    if loan.due_date < loan.start_date:
        raise ValueError("Final due date cannot precede the start date.")

    rate_period = getattr(loan, "interest_rate_period", "monthly")
    if rate_period not in ("monthly", "annual"):
        raise ValueError("Interest rate period must be monthly or annual.")

    monthly_rate_percent = Decimal(str(loan.interest_rate_percent))
    if rate_period == "annual":
        monthly_rate_percent /= Decimal("12")

    monthly_rate = monthly_rate_percent / Decimal("100")

    if not monthly_rate.is_finite() or monthly_rate < ZERO:
        raise ValueError("Monthly interest rate must be valid and non-negative.")

    dates = _installment_dates(loan)

    with localcontext() as context:
        context.prec = 40

        if loan.interest_method == "none":
            schedule = _flat_schedule(loan, dates, ZERO)
        elif loan.interest_method == "flat":
            schedule = _flat_schedule(loan, dates, monthly_rate)
        elif loan.interest_method == "reducing":
            schedule = _reducing_schedule(loan, dates, monthly_rate)
        else:
            raise ValueError("Unsupported interest method.")

    if sum(item["principal_minor"] for item in schedule) != loan.principal_minor:
        raise ValueError("Schedule principal does not equal the loan principal.")

    return schedule
