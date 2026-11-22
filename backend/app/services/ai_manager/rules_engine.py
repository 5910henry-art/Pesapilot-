from datetime import date, timedelta


def _parse_date(value):
    if not value:
        return None

    try:
        return date.fromisoformat(str(value))
    except (TypeError, ValueError):
        return None


def _insight(
    rule,
    severity,
    title,
    message,
    recommendation,
    entity_type=None,
    entity_id=None,
    amount_minor=None,
    currency=None,
):
    return {
        "rule": rule,
        "severity": severity,
        "title": title,
        "message": message,
        "recommendation": recommendation,
        "entity_type": entity_type,
        "entity_id": entity_id,
        "amount_minor": amount_minor,
        "currency": currency,
    }


def generate_insights(snapshot):
    """
    Generate deterministic, read-only financial insights.

    This function does not access the database or modify financial records.
    All monetary amounts remain integer minor units.
    """

    today = _parse_date(snapshot.get("as_of")) or date.today()
    currency = snapshot.get("currency")
    summary = snapshot.get("summary") or {}

    insights = []

    # ---------------------------------------------------------
    # Monthly cash flow
    # ---------------------------------------------------------
    monthly_income = int(summary.get("monthly_income_minor") or 0)
    monthly_expenses = int(summary.get("monthly_expenses_minor") or 0)
    monthly_net = monthly_income - monthly_expenses

    if monthly_net < 0:
        deficit = abs(monthly_net)

        insights.append(_insight(
            rule="negative_monthly_cash_flow",
            severity="high",
            title="Monthly cash flow is negative",
            message=(
                "Your recorded expenses exceed your income this month "
                "by {} minor units.".format(deficit)
            ),
            recommendation=(
                "Review non-essential spending and upcoming obligations "
                "before committing to additional expenses."
            ),
            entity_type="cash_flow",
            amount_minor=deficit,
            currency=currency,
        ))

    # ---------------------------------------------------------
    # Income and spending intelligence
    # ---------------------------------------------------------
    savings_rate = summary.get("savings_rate_percentage")

    if (
        monthly_income > 0
        and savings_rate is not None
        and 0 <= float(savings_rate) < 10
    ):
        insights.append(_insight(
            rule="low_savings_rate",
            severity="medium",
            title="Your savings rate is low",
            message=(
                "You are currently retaining {:.1f}% of your income "
                "after recorded expenses."
            ).format(float(savings_rate)),
            recommendation=(
                "Review recurring and discretionary expenses and consider "
                "setting aside a realistic amount when income is received."
            ),
            entity_type="cash_flow",
            amount_minor=max(0, monthly_net),
            currency=currency,
        ))

    trends = snapshot.get("monthly_trends", [])
    if isinstance(trends, list):
        valid_trends = [
            item for item in trends[-6:]
            if isinstance(item, dict)
            and item.get("net_minor") is not None
        ]

        negative_months = sum(
            1 for item in valid_trends
            if int(item.get("net_minor") or 0) < 0
        )

        if len(valid_trends) >= 3 and negative_months >= 3:
            insights.append(_insight(
                rule="recurring_negative_cash_flow",
                severity="high",
                title="Cash flow has been negative repeatedly",
                message=(
                    "Your recorded cash flow was negative in {} of the "
                    "last {} months."
                ).format(negative_months, len(valid_trends)),
                recommendation=(
                    "Review the recurring expenses and obligations driving "
                    "the deficits, then build a monthly spending plan that "
                    "fits your typical income."
                ),
                entity_type="cash_flow",
                currency=currency,
            ))

    spending_categories = snapshot.get("spending_by_category", [])
    if isinstance(spending_categories, list) and monthly_expenses > 0:
        valid_categories = [
            item for item in spending_categories
            if isinstance(item, dict)
            and int(item.get("amount_minor") or 0) > 0
        ]

        largest_category = max(
            valid_categories,
            key=lambda item: int(item.get("amount_minor") or 0),
            default=None,
        )

        if largest_category:
            category_amount = int(largest_category.get("amount_minor") or 0)
            concentration = category_amount / monthly_expenses * 100

            if concentration >= 50:
                category_name = (
                    largest_category.get("category") or "One category"
                )
                insights.append(_insight(
                    rule="spending_concentration",
                    severity="medium",
                    title="Spending is concentrated in one category",
                    message=(
                        "{} accounts for {:.1f}% of your recorded "
                        "spending this month."
                    ).format(category_name, concentration),
                    recommendation=(
                        "Review the transactions in this category to "
                        "identify essential costs and any opportunities "
                        "to reduce discretionary spending."
                    ),
                    entity_type="spending_category",
                    amount_minor=category_amount,
                    currency=currency,
                ))

    # ---------------------------------------------------------
    # Budgets
    # ---------------------------------------------------------
    for budget in snapshot.get("budgets", []):
        categories = budget.get("categories") or [{
            "category": budget.get("category") or "Uncategorized",
            "amount_minor": budget.get("amount_minor"),
            "actual_minor": budget.get("actual_minor"),
            "remaining_minor": budget.get("remaining_minor"),
            "usage_percentage": budget.get("usage_percentage"),
        }]

        budget_currency = budget.get("currency") or currency

        for item in categories:
            limit = int(item.get("amount_minor") or 0)
            actual = int(item.get("actual_minor") or 0)

            if limit <= 0 or actual < 0:
                continue

            usage = actual / limit * 100
            category = item.get("category") or "Uncategorized"
            remaining = limit - actual

            if usage >= 100:
                insights.append(_insight(
                    rule="budget_exceeded",
                    severity="high",
                    title="Budget exceeded: {}".format(category),
                    message=(
                        "{} spending has reached {:.1f}% of its budget, "
                        "exceeding the limit by {} minor units."
                    ).format(category, usage, abs(remaining)),
                    recommendation=(
                        "Review further spending in this category and "
                        "check whether the budget needs adjustment."
                    ),
                    entity_type="budget",
                    entity_id=budget.get("id"),
                    amount_minor=abs(remaining),
                    currency=budget_currency,
                ))

            elif usage >= 80:
                insights.append(_insight(
                    rule="budget_near_limit",
                    severity="medium",
                    title="Budget approaching its limit: {}".format(category),
                    message=(
                        "{} spending has used {:.1f}% of its budget. "
                        "{} minor units remain."
                    ).format(category, usage, remaining),
                    recommendation=(
                        "Monitor additional spending in this category "
                        "for the rest of the budget period."
                    ),
                    entity_type="budget",
                    entity_id=budget.get("id"),
                    amount_minor=remaining,
                    currency=budget_currency,
                ))

    # ---------------------------------------------------------
    # Bills
    # ---------------------------------------------------------
    for bill in snapshot.get("bills", []):
        remaining = int(bill.get("remaining_minor") or 0)

        if remaining <= 0:
            continue

        due_date = _parse_date(bill.get("due_date"))
        if due_date is None:
            continue

        title = bill.get("title") or bill.get("payee") or "Bill"
        bill_currency = bill.get("currency") or currency

        if due_date < today:
            days_overdue = (today - due_date).days

            insights.append(_insight(
                rule="bill_overdue",
                severity="high",
                title="Overdue bill: {}".format(title),
                message=(
                    "{} is {} day(s) overdue with {} minor units "
                    "remaining."
                ).format(title, days_overdue, remaining),
                recommendation=(
                    "Review this outstanding bill and plan its payment "
                    "against your available account balances."
                ),
                entity_type="bill",
                entity_id=bill.get("id"),
                amount_minor=remaining,
                currency=bill_currency,
            ))

        elif due_date == today:
            insights.append(_insight(
                rule="bill_due_today",
                severity="high",
                title="Bill due today: {}".format(title),
                message=(
                    "{} is due today with {} minor units remaining."
                ).format(title, remaining),
                recommendation=(
                    "Check that funds are available for this bill "
                    "before its due date passes."
                ),
                entity_type="bill",
                entity_id=bill.get("id"),
                amount_minor=remaining,
                currency=bill_currency,
            ))

        elif due_date <= today + timedelta(days=7):
            days_until_due = (due_date - today).days

            insights.append(_insight(
                rule="bill_due_soon",
                severity="medium",
                title="Upcoming bill: {}".format(title),
                message=(
                    "{} is due in {} day(s), with {} minor units "
                    "remaining."
                ).format(title, days_until_due, remaining),
                recommendation=(
                    "Include this bill in your short-term cash-flow plan."
                ),
                entity_type="bill",
                entity_id=bill.get("id"),
                amount_minor=remaining,
                currency=bill_currency,
            ))

    # ---------------------------------------------------------
    # Savings goals
    # ---------------------------------------------------------
    for goal in snapshot.get("savings_goals", []):
        if goal.get("status") != "active":
            continue

        remaining = int(goal.get("remaining_minor") or 0)
        target_date = _parse_date(goal.get("target_date"))

        if remaining <= 0 or target_date is None:
            continue

        goal_name = goal.get("name") or "Savings goal"
        goal_currency = goal.get("currency") or currency

        if target_date < today:
            insights.append(_insight(
                rule="savings_target_missed",
                severity="medium",
                title="Savings target date passed: {}".format(goal_name),
                message=(
                    "{} still needs {} minor units and its target "
                    "date has passed."
                ).format(goal_name, remaining),
                recommendation=(
                    "Review the target date or create a realistic "
                    "contribution plan."
                ),
                entity_type="savings_goal",
                entity_id=goal.get("id"),
                amount_minor=remaining,
                currency=goal_currency,
            ))

        elif target_date <= today + timedelta(days=30):
            days_remaining = max(1, (target_date - today).days)
            daily_amount = (
                remaining + days_remaining - 1
            ) // days_remaining

            insights.append(_insight(
                rule="savings_target_approaching",
                severity="medium",
                title="Savings target approaching: {}".format(goal_name),
                message=(
                    "{} needs {} more minor units to reach its target "
                    "in {} day(s)."
                ).format(goal_name, remaining, days_remaining),
                recommendation=(
                    "You would need to save approximately {} minor "
                    "units per day to reach this target on time."
                ).format(daily_amount),
                entity_type="savings_goal",
                entity_id=goal.get("id"),
                amount_minor=remaining,
                currency=goal_currency,
            ))

    # ---------------------------------------------------------
    # Loan repayments
    # ---------------------------------------------------------
    for loan in snapshot.get("loans", []):
        overdue = int(
            loan.get("overdue_installments_minor") or 0
        )

        if overdue <= 0:
            continue

        direction = loan.get("direction")
        loan_currency = loan.get("currency") or currency

        if direction == "borrowed":
            title = "Overdue loan repayments"
            message = (
                "You have {} minor units in overdue loan installments."
            ).format(overdue)
            recommendation = (
                "Review the overdue installments and contact the lender "
                "if you need to discuss a revised repayment arrangement."
            )
        else:
            title = "Overdue repayments on money lent"
            message = (
                "Loans you have lent have {} minor units in overdue "
                "installments."
            ).format(overdue)
            recommendation = (
                "Review the affected loan repayment schedules and "
                "follow up on outstanding repayments."
            )

        insights.append(_insight(
            rule="loan_installments_overdue",
            severity="high",
            title=title,
            message=message,
            recommendation=recommendation,
            entity_type="loan",
            entity_id=loan.get("id"),
            amount_minor=overdue,
            currency=loan_currency,
        ))

    # ---------------------------------------------------------
    # Stable severity ordering
    # ---------------------------------------------------------
    severity_order = {
        "high": 0,
        "medium": 1,
        "low": 2,
    }

    insights.sort(
        key=lambda item: (
            severity_order.get(item["severity"], 3),
            item["rule"],
            item.get("entity_id") or "",
        )
    )

    return insights
