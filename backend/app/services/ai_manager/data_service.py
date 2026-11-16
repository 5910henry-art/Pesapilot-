from datetime import date, datetime, time, timedelta, timezone
from uuid import UUID
from zoneinfo import ZoneInfo

from sqlalchemy import func

from app.extensions import db
from app.models import (
    Workspace,
    WorkspaceMember,
    UserProfile,
    Currency,
    FinancialAccount,
    LedgerAccount,
    JournalEntry,
    JournalLine,
    ExpenseCategory,
    Budget,
    SavingsGoal,
    SavingsGoalMovement,
    Bill,
    BillPayment,
    PersonalLoan,
    LoanInstallment,
    LoanPayment,
    LoanPaymentAllocation,
    LoanDisbursement,
)


POSTED_STATUSES = ("posted", "reversed")


def _safe_timezone(timezone_name):
    try:
        return ZoneInfo(timezone_name), timezone_name
    except Exception:
        return ZoneInfo("UTC"), "UTC"


def _period_bounds(local_date, user_timezone):
    start_local = datetime.combine(
        local_date.replace(day=1),
        time.min,
        tzinfo=user_timezone,
    )

    if local_date.month == 12:
        next_month = date(local_date.year + 1, 1, 1)
    else:
        next_month = date(local_date.year, local_date.month + 1, 1)

    end_local = datetime.combine(
        next_month,
        time.min,
        tzinfo=user_timezone,
    )

    return (
        start_local.astimezone(timezone.utc),
        end_local.astimezone(timezone.utc),
        next_month,
    )


def build_financial_snapshot(workspace_id, user_id, as_of=None):
    """
    Build a read-only financial snapshot for one authorized workspace.

    All journal-derived functional amounts are in the workspace currency.
    Draft entries are excluded. Reversed originals and their posted
    reversal entries are both included so they net to zero.
    """

    workspace = Workspace.query.filter_by(id=workspace_id).first()
    if workspace is None:
        raise ValueError("Workspace not found.")

    membership = WorkspaceMember.query.filter_by(
        workspace_id=workspace_id,
        user_id=user_id,
    ).first()

    if membership is None:
        raise PermissionError("You are not a member of this workspace.")

    profile = UserProfile.query.filter_by(user_id=user_id).first()
    timezone_name = (
        profile.timezone
        if profile and profile.timezone
        else "UTC"
    )
    user_timezone, timezone_name = _safe_timezone(timezone_name)

    now = as_of or datetime.now(timezone.utc)
    if now.tzinfo is None:
        now = now.replace(tzinfo=timezone.utc)

    today = now.astimezone(user_timezone).date()
    month_start, next_month_start, next_month = _period_bounds(
        today,
        user_timezone,
    )

    currency_code = workspace.functional_currency
    currency = Currency.query.filter_by(code=currency_code).first()
    minor_unit = currency.minor_unit if currency else 2

    # ---------------------------------------------------------
    # ---------------------------------------------------------
    # Account balances
    # Only posted and reversed journal entries are included.
    # Reversal entries offset their original entries.
    # ---------------------------------------------------------
    valid_balances = (
        db.session.query(
            JournalLine.ledger_account_id.label("ledger_account_id"),
            func.sum(
                JournalLine.functional_debit_minor
            ).label("debits"),
            func.sum(
                JournalLine.functional_credit_minor
            ).label("credits"),
        )
        .join(
            JournalEntry,
            JournalEntry.id == JournalLine.journal_entry_id,
        )
        .filter(
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status.in_(POSTED_STATUSES),
        )
        .group_by(JournalLine.ledger_account_id)
        .subquery()
    )

    balance_rows = (
        db.session.query(
            LedgerAccount.id,
            LedgerAccount.financial_account_id,
            LedgerAccount.account_class,
            LedgerAccount.normal_balance,
            func.coalesce(valid_balances.c.debits, 0).label("debits"),
            func.coalesce(valid_balances.c.credits, 0).label("credits"),
        )
        .outerjoin(
            valid_balances,
            valid_balances.c.ledger_account_id == LedgerAccount.id,
        )
        .filter(LedgerAccount.workspace_id == workspace_id)
        .all()
    )

    ledger_balances = {}
    account_balances = {}
    total_assets = 0
    total_liabilities = 0

    for row in balance_rows:
        debit = int(row.debits or 0)
        credit = int(row.credits or 0)

        balance = (
            debit - credit
            if row.normal_balance == "debit"
            else credit - debit
        )

        ledger_balances[row.id] = balance

        if row.financial_account_id:
            account_balances[row.financial_account_id] = balance

        if row.account_class == "asset":
            total_assets += balance
        elif row.account_class == "liability":
            total_liabilities += balance

    accounts = FinancialAccount.query.filter_by(
        workspace_id=workspace_id,
    ).order_by(FinancialAccount.name.asc()).all()

    account_data = [
        {
            "id": str(account.id),
            "name": account.name,
            "type": account.account_type,
            "currency": account.currency_code,
            "is_active": account.is_active,
            "balance_minor": account_balances.get(account.id, 0),
            "balance_currency": currency_code,
        }
        for account in accounts
    ]

    total_savings_accounts = sum(
        item["balance_minor"]
        for item in account_data
        if item["type"] == "savings"
    )

    # ---------------------------------------------------------
    # Current-month income and expenses
    # ---------------------------------------------------------
    monthly_rows = (
        db.session.query(
            LedgerAccount.account_class,
            func.coalesce(
                func.sum(
                    JournalLine.functional_debit_minor
                    - JournalLine.functional_credit_minor
                ),
                0,
            ).label("net_minor"),
        )
        .join(
            JournalLine,
            JournalLine.ledger_account_id == LedgerAccount.id,
        )
        .join(
            JournalEntry,
            JournalEntry.id == JournalLine.journal_entry_id,
        )
        .filter(
            LedgerAccount.workspace_id == workspace_id,
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status.in_(POSTED_STATUSES),
            JournalEntry.transaction_date >= month_start,
            JournalEntry.transaction_date < next_month_start,
            LedgerAccount.account_class.in_(("income", "expense")),
        )
        .group_by(LedgerAccount.account_class)
        .all()
    )

    monthly_income = 0
    monthly_expenses = 0

    for row in monthly_rows:
        amount = int(row.net_minor or 0)

        if row.account_class == "income":
            monthly_income += -amount
        elif row.account_class == "expense":
            monthly_expenses += amount

    # ---------------------------------------------------------
    # Six-month income and spending trends
    # Includes the current month and five preceding months.
    # ---------------------------------------------------------
    trend_months = []

    for offset in range(5, -1, -1):
        month_index = today.year * 12 + today.month - 1 - offset
        year, month_zero = divmod(month_index, 12)
        trend_months.append(date(year, month_zero + 1, 1))

    trend_start, _, _ = _period_bounds(
        trend_months[0],
        user_timezone,
    )

    trend_totals = {
        month.strftime("%Y-%m"): {
            "income_minor": 0,
            "expenses_minor": 0,
        }
        for month in trend_months
    }

    trend_rows = (
        db.session.query(
            JournalEntry.transaction_date.label("transaction_date"),
            LedgerAccount.account_class.label("account_class"),
            func.coalesce(
                func.sum(
                    JournalLine.functional_debit_minor
                    - JournalLine.functional_credit_minor
                ),
                0,
            ).label("net_minor"),
        )
        .join(
            JournalLine,
            JournalLine.journal_entry_id == JournalEntry.id,
        )
        .join(
            LedgerAccount,
            LedgerAccount.id == JournalLine.ledger_account_id,
        )
        .filter(
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status.in_(POSTED_STATUSES),
            JournalEntry.transaction_date >= trend_start,
            JournalEntry.transaction_date < next_month_start,
            LedgerAccount.workspace_id == workspace_id,
            LedgerAccount.account_class.in_(("income", "expense")),
        )
        .group_by(
            JournalEntry.transaction_date,
            LedgerAccount.account_class,
        )
        .all()
    )

    for row in trend_rows:
        transaction_at = row.transaction_date

        if transaction_at.tzinfo is None:
            transaction_at = transaction_at.replace(
                tzinfo=timezone.utc
            )

        month_key = transaction_at.astimezone(
            user_timezone
        ).strftime("%Y-%m")

        if month_key not in trend_totals:
            continue

        amount = int(row.net_minor or 0)

        if row.account_class == "income":
            trend_totals[month_key]["income_minor"] -= amount
        elif row.account_class == "expense":
            trend_totals[month_key]["expenses_minor"] += amount

    monthly_trends = [
        {
            "month": month_key,
            "income_minor": values["income_minor"],
            "expenses_minor": values["expenses_minor"],
            "net_minor": (
                values["income_minor"] - values["expenses_minor"]
            ),
        }
        for month_key, values in trend_totals.items()
    ]

    # ---------------------------------------------------------
    # Current-month spending by expense category
    # Uses functional amounts and includes posted reversals.
    # ---------------------------------------------------------
    category_rows = (
        db.session.query(
            ExpenseCategory.id.label("category_id"),
            ExpenseCategory.name.label("category_name"),
            func.coalesce(
                func.sum(
                    JournalLine.functional_debit_minor
                    - JournalLine.functional_credit_minor
                ),
                0,
            ).label("actual_minor"),
        )
        .join(
            LedgerAccount,
            LedgerAccount.id == ExpenseCategory.ledger_account_id,
        )
        .join(
            JournalLine,
            JournalLine.ledger_account_id == LedgerAccount.id,
        )
        .join(
            JournalEntry,
            JournalEntry.id == JournalLine.journal_entry_id,
        )
        .filter(
            ExpenseCategory.workspace_id == workspace_id,
            LedgerAccount.workspace_id == workspace_id,
            LedgerAccount.account_class == "expense",
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status.in_(POSTED_STATUSES),
            JournalEntry.transaction_date >= month_start,
            JournalEntry.transaction_date < next_month_start,
        )
        .group_by(
            ExpenseCategory.id,
            ExpenseCategory.name,
        )
        .all()
    )

    spending_by_category = [
        {
            "category": row.category_name,
            "amount_minor": int(row.actual_minor or 0),
        }
        for row in category_rows
        if int(row.actual_minor or 0) != 0
    ]

    spending_by_category.sort(
        key=lambda item: item["amount_minor"],
        reverse=True,
    )

    top_spending_categories = spending_by_category[:5]

    savings_rate_percentage = (
        round(
            (monthly_income - monthly_expenses)
            / monthly_income * 100,
            2,
        )
        if monthly_income > 0
        else None
    )

    # ---------------------------------------------------------
    # Budgets
    # ---------------------------------------------------------
    budgets = Budget.query.filter_by(
        workspace_id=workspace_id,
        is_active=True,
    ).all()

    budget_data = []

    for budget in budgets:
        category = ExpenseCategory.query.filter_by(
            id=budget.category_id,
            workspace_id=workspace_id,
        ).first()

        if category is None:
            continue

        ledger = LedgerAccount.query.filter_by(
            id=category.ledger_account_id,
            workspace_id=workspace_id,
            account_class="expense",
        ).first()

        if ledger is None:
            continue

        start_local = datetime.combine(
            budget.period_start,
            time.min,
            tzinfo=user_timezone,
        )
        end_local = datetime.combine(
            budget.period_end + timedelta(days=1),
            time.min,
            tzinfo=user_timezone,
        )

        actual = (
            db.session.query(
                func.coalesce(
                    func.sum(
                        JournalLine.functional_debit_minor
                        - JournalLine.functional_credit_minor
                    ),
                    0,
                )
            )
            .join(
                JournalEntry,
                JournalEntry.id == JournalLine.journal_entry_id,
            )
            .filter(
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status.in_(POSTED_STATUSES),
                JournalEntry.transaction_date >= (
                    start_local.astimezone(timezone.utc)
                ),
                JournalEntry.transaction_date < (
                    end_local.astimezone(timezone.utc)
                ),
                JournalLine.ledger_account_id == ledger.id,
            )
            .scalar()
        )

        actual_minor = int(actual or 0)

        budget_data.append({
            "id": str(budget.id),
            "category": category.name,
            "amount_minor": budget.amount_minor,
            "actual_minor": actual_minor,
            "remaining_minor": budget.amount_minor - actual_minor,
            "usage_percentage": round(
                actual_minor / budget.amount_minor * 100, 2
            ) if budget.amount_minor else 0,
            "currency": budget.currency_code,
            "period_start": budget.period_start.isoformat(),
            "period_end": budget.period_end.isoformat(),
        })

    # ---------------------------------------------------------
    # Bills: only bills with a posted accrual entry are included.
    # Only payments with posted journal entries reduce balances.
    # ---------------------------------------------------------
    bill_rows = (
        db.session.query(Bill, JournalEntry.status)
        .join(
            JournalEntry,
            JournalEntry.id == Bill.journal_entry_id,
        )
        .filter(
            Bill.workspace_id == workspace_id,
            JournalEntry.workspace_id == workspace_id,
            JournalEntry.status == "posted",
        )
        .all()
    )

    bill_data = []

    for bill, _status in bill_rows:
        paid = (
            db.session.query(
                func.coalesce(func.sum(BillPayment.amount_minor), 0)
            )
            .join(
                JournalEntry,
                JournalEntry.id == BillPayment.journal_entry_id,
            )
            .filter(
                BillPayment.workspace_id == workspace_id,
                BillPayment.bill_id == bill.id,
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status == "posted",
            )
            .scalar()
        )

        paid_minor = int(paid or 0)
        remaining_minor = max(0, bill.amount_minor - paid_minor)

        if remaining_minor == 0:
            status = "paid"
        elif bill.due_date < today:
            status = "overdue"
        elif paid_minor > 0:
            status = "partially_paid"
        else:
            status = "unpaid"

        bill_data.append({
            "id": str(bill.id),
            "title": bill.title,
            "payee": bill.payee_name,
            "amount_minor": bill.amount_minor,
            "paid_minor": paid_minor,
            "remaining_minor": remaining_minor,
            "currency": bill.currency_code,
            "due_date": bill.due_date.isoformat(),
            "status": status,
        })

    # ---------------------------------------------------------
    # Savings goals: only movements linked to posted entries count.
    # ---------------------------------------------------------
    goals = SavingsGoal.query.filter_by(
        workspace_id=workspace_id,
    ).filter(
        SavingsGoal.status.in_(("active", "paused"))
    ).all()

    savings_data = []

    for goal in goals:
        movements = (
            db.session.query(
                SavingsGoalMovement.operation,
                SavingsGoalMovement.amount_minor,
            )
            .join(
                JournalEntry,
                JournalEntry.id == SavingsGoalMovement.journal_entry_id,
            )
            .filter(
                SavingsGoalMovement.workspace_id == workspace_id,
                SavingsGoalMovement.goal_id == goal.id,
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status == "posted",
            )
            .all()
        )

        saved_minor = sum(
            int(amount) if operation == "contribution" else -int(amount)
            for operation, amount in movements
        )
        saved_minor = max(0, saved_minor)

        savings_data.append({
            "id": str(goal.id),
            "name": goal.name,
            "target_minor": goal.target_amount_minor,
            "saved_minor": saved_minor,
            "remaining_minor": max(
                0, goal.target_amount_minor - saved_minor
            ),
            "progress_percentage": round(
                saved_minor / goal.target_amount_minor * 100, 2
            ) if goal.target_amount_minor else 0,
            "currency": goal.currency_code,
            "target_date": (
                goal.target_date.isoformat()
                if goal.target_date else None
            ),
            "status": goal.status,
        })

    # ---------------------------------------------------------
    # Loans: derive paid amounts from confirmed payments whose
    # linked journal entries are still posted.
    # ---------------------------------------------------------
    loans = PersonalLoan.query.filter_by(
        workspace_id=workspace_id,
    ).filter(
        PersonalLoan.status.in_(
            ("active", "overdue", "restructured", "disputed")
        )
    ).all()

    loan_data = []

    for loan in loans:
        valid_payments = (
            db.session.query(
                func.coalesce(func.sum(LoanPayment.amount_minor), 0),
                func.coalesce(func.sum(LoanPayment.principal_minor), 0),
            )
            .join(
                JournalEntry,
                JournalEntry.id == LoanPayment.journal_entry_id,
            )
            .filter(
                LoanPayment.workspace_id == workspace_id,
                LoanPayment.personal_loan_id == loan.id,
                LoanPayment.status == "confirmed",
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status == "posted",
            )
            .first()
        )

        paid_minor = int(valid_payments[0] or 0)
        principal_paid_minor = int(valid_payments[1] or 0)

        installments = LoanInstallment.query.filter_by(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
        ).all()

        outstanding_minor = 0
        overdue_minor = 0

        for installment in installments:
            allocated = (
                db.session.query(
                    func.coalesce(
                        func.sum(LoanPaymentAllocation.amount_minor), 0
                    )
                )
                .join(
                    LoanPayment,
                    LoanPayment.id == LoanPaymentAllocation.payment_id,
                )
                .join(
                    JournalEntry,
                    JournalEntry.id == LoanPayment.journal_entry_id,
                )
                .filter(
                    LoanPaymentAllocation.workspace_id == workspace_id,
                    LoanPaymentAllocation.installment_id == installment.id,
                    LoanPayment.status == "confirmed",
                    JournalEntry.workspace_id == workspace_id,
                    JournalEntry.status == "posted",
                )
                .scalar()
            )

            remaining = max(
                0, installment.amount_minor - int(allocated or 0)
            )
            outstanding_minor += remaining

            if installment.due_date < today:
                overdue_minor += remaining

        loan_data.append({
            "id": str(loan.id),
            "direction": loan.direction,
            "type": loan.loan_type,
            "principal_minor": loan.principal_minor,
            "paid_minor": paid_minor,
            "principal_paid_minor": principal_paid_minor,
            "outstanding_minor": outstanding_minor,
            "overdue_installments_minor": overdue_minor,
            "currency": loan.currency_code,
            "due_date": loan.due_date.isoformat(),
            "status": loan.status,
        })

    return {
        "workspace_id": str(workspace_id),
        "as_of": today.isoformat(),
        "timezone": timezone_name,
        "currency": currency_code,
        "minor_unit": minor_unit,
        "period": {
            "start": today.replace(day=1).isoformat(),
            "end": next_month.isoformat(),
        },
        "summary": {
            "total_assets_minor": total_assets,
            "total_liabilities_minor": total_liabilities,
            "net_worth_minor": total_assets - total_liabilities,
            "monthly_income_minor": monthly_income,
            "monthly_expenses_minor": monthly_expenses,
            "monthly_net_minor": monthly_income - monthly_expenses,
            "savings_rate_percentage": savings_rate_percentage,
            "total_savings_accounts_minor": total_savings_accounts,
        },
        "monthly_trends": monthly_trends,
        "spending_by_category": spending_by_category,
        "top_spending_categories": top_spending_categories,
        "accounts": account_data,
        "budgets": budget_data,
        "bills": bill_data,
        "savings_goals": savings_data,
        "loans": loan_data,
    }
