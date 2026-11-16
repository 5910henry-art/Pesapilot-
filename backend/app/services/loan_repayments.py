from collections import defaultdict
from datetime import datetime, time, timezone

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    AuditLog,
    FinancialAccount,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    LoanInstallment,
    LoanPayment,
    LoanPaymentAllocation,
    PersonalLoan,
    Workspace,
    WorkspaceMember,
)
from app.services.accounting import (
    AccountingError,
    create_draft_entry,
    post_entry,
)


def build_payment_allocations(
    installments,
    previous_allocations,
    payment_amount,
):
    """Allocate oldest installment first, with interest before principal."""

    if (
        isinstance(payment_amount, bool)
        or not isinstance(payment_amount, int)
        or payment_amount <= 0
    ):
        raise ValueError("Payment amount must be a positive integer.")

    allocated_principal = defaultdict(int)
    allocated_interest = defaultdict(int)

    for allocation in previous_allocations:
        allocated_principal[allocation.installment_id] += (
            allocation.principal_minor
        )
        allocated_interest[allocation.installment_id] += (
            allocation.interest_minor
        )

    ordered = sorted(
        (
            item for item in installments
            if item.status not in ("paid", "waived")
        ),
        key=lambda item: (item.due_date, item.installment_number),
    )

    outstanding = 0

    for item in ordered:
        principal_due = (
            item.principal_minor - allocated_principal[item.id]
        )
        interest_due = (
            item.interest_minor - allocated_interest[item.id]
        )

        if principal_due < 0 or interest_due < 0:
            raise ValueError(
                "Existing allocations exceed an installment balance."
            )

        outstanding += principal_due + interest_due

    if payment_amount > outstanding:
        raise ValueError(
            "Payment exceeds the total outstanding scheduled balance."
        )

    remaining = payment_amount
    results = []

    for item in ordered:
        if remaining == 0:
            break

        interest_due = (
            item.interest_minor - allocated_interest[item.id]
        )
        principal_due = (
            item.principal_minor - allocated_principal[item.id]
        )

        interest_paid = min(remaining, interest_due)
        remaining -= interest_paid

        principal_paid = min(remaining, principal_due)
        remaining -= principal_paid

        amount = interest_paid + principal_paid

        if amount:
            results.append({
                "installment_id": item.id,
                "amount_minor": amount,
                "interest_minor": interest_paid,
                "principal_minor": principal_paid,
            })

    if remaining:
        raise ValueError("Payment could not be fully allocated.")

    return results


def confirm_loan_payment(
    *,
    payment_id,
    workspace_id,
    confirmed_by,
    financial_account_id,
):
    """
    Confirm a pending repayment and post its accounting entry.

    The caller controls commit and rollback.
    """

    member = db.session.execute(
        select(WorkspaceMember).where(
            WorkspaceMember.workspace_id == workspace_id,
            WorkspaceMember.user_id == confirmed_by,
        )
    ).scalar_one_or_none()

    if member is None:
        raise ValueError("You are not a member of this workspace.")

    if member.role == "viewer":
        raise ValueError("Viewers cannot confirm loan repayments.")

    payment = db.session.execute(
        select(LoanPayment)
        .where(
            LoanPayment.id == payment_id,
            LoanPayment.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if payment is None:
        raise ValueError("Loan payment not found.")

    if payment.status != "pending":
        raise ValueError("Only pending payments can be confirmed.")

    loan = db.session.execute(
        select(PersonalLoan)
        .where(
            PersonalLoan.id == payment.personal_loan_id,
            PersonalLoan.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if loan is None:
        raise ValueError("Personal loan not found.")

    if loan.status not in ("active", "overdue", "restructured"):
        raise ValueError("This loan cannot receive repayments.")

    workspace = db.session.execute(
        select(Workspace).where(
            Workspace.id == workspace_id
        )
    ).scalar_one_or_none()

    if workspace is None:
        raise ValueError("Workspace not found.")

    account = db.session.execute(
        select(FinancialAccount)
        .where(
            FinancialAccount.id == financial_account_id,
            FinancialAccount.workspace_id == workspace_id,
        )
        .with_for_update()
    ).scalar_one_or_none()

    if account is None or not account.is_active:
        raise ValueError("An active financial account is required.")

    if account.account_type not in ("cash", "bank", "mobile_money"):
        raise ValueError(
            "Repayments require a cash, bank, or mobile-money account."
        )

    if (
        payment.payment_method != "other"
        and payment.payment_method != account.account_type
    ):
        raise ValueError(
            "Payment method does not match the selected financial account."
        )

    if not (
        loan.currency_code
        == account.currency_code
        == workspace.functional_currency
    ):
        raise ValueError(
            "Loan, payment account, and workspace currencies must match."
        )

    cash_ledger = db.session.execute(
        select(LedgerAccount)
        .where(
            LedgerAccount.workspace_id == workspace_id,
            LedgerAccount.financial_account_id == account.id,
            LedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalar_one_or_none()

    if cash_ledger is None:
        raise ValueError(
            "The selected financial account has no active ledger account."
        )

    if cash_ledger.currency_code != loan.currency_code:
        raise ValueError("Financial account ledger currency mismatch.")

    # A borrowed-loan repayment is money leaving this account.
    # Check its real posted ledger balance before creating the journal.
    if loan.direction == "borrowed":
        if (
            cash_ledger.account_class != "asset"
            or cash_ledger.normal_balance != "debit"
        ):
            raise ValueError(
                "The selected account does not have a valid cash ledger."
            )

        available_minor = db.session.execute(
            select(
                func.coalesce(
                    func.sum(
                        JournalLine.debit_minor
                        - JournalLine.credit_minor
                    ),
                    0,
                )
            )
            .join(
                JournalEntry,
                JournalEntry.id == JournalLine.journal_entry_id,
            )
            .where(
                JournalLine.ledger_account_id == cash_ledger.id,
                JournalEntry.workspace_id == workspace_id,
                JournalEntry.status.in_(("posted", "reversed")),
            )
        ).scalar_one()

        available_minor = int(available_minor or 0)

        if payment.amount_minor > available_minor:
            raise ValueError(
                "Insufficient funds in the selected account. "
                f"Available: {available_minor} minor units; "
                f"required: {payment.amount_minor} minor units."
            )

    installments = db.session.execute(
        select(LoanInstallment)
        .where(
            LoanInstallment.workspace_id == workspace_id,
            LoanInstallment.personal_loan_id == loan.id,
        )
        .order_by(
            LoanInstallment.due_date,
            LoanInstallment.installment_number,
        )
        .with_for_update()
    ).scalars().all()

    if not installments:
        raise ValueError(
            "This loan has no repayment schedule. Create installments first."
        )

    previous_allocations = db.session.execute(
        select(LoanPaymentAllocation)
        .where(
            LoanPaymentAllocation.workspace_id == workspace_id,
            LoanPaymentAllocation.personal_loan_id == loan.id,
        )
    ).scalars().all()

    # Ensure installment balances agree with confirmed allocations.
    allocated_by_installment = {
        item.id: 0 for item in installments
    }
    for previous in previous_allocations:
        allocated_by_installment[previous.installment_id] += (
            previous.amount_minor
        )

    for installment in installments:
        if allocated_by_installment[installment.id] != installment.paid_minor:
            raise ValueError(
                "Installment balance does not match its payment allocations."
            )

    allocations = build_payment_allocations(
        installments,
        previous_allocations,
        payment.amount_minor,
    )

    principal_total = sum(
        item["principal_minor"] for item in allocations
    )
    interest_total = sum(
        item["interest_minor"] for item in allocations
    )

    if principal_total + interest_total != payment.amount_minor:
        raise ValueError("Payment allocation does not balance.")

    if loan.direction == "borrowed":
        system_accounts = {
            "2000": ("liability", "credit"),
            "5000": ("expense", "debit"),
        }
    else:
        system_accounts = {
            "1200": ("asset", "debit"),
            "4000": ("income", "credit"),
        }

    ledger_accounts = db.session.execute(
        select(LedgerAccount)
        .where(
            LedgerAccount.workspace_id == workspace_id,
            LedgerAccount.code.in_(list(system_accounts)),
            LedgerAccount.is_system.is_(True),
            LedgerAccount.is_active.is_(True),
        )
        .with_for_update()
    ).scalars().all()

    accounts_by_code = {
        item.code: item for item in ledger_accounts
    }

    if len(accounts_by_code) != len(system_accounts):
        raise ValueError(
            "Required system ledger accounts are missing or inactive."
        )

    for code, expected in system_accounts.items():
        ledger = accounts_by_code[code]

        if (
            ledger.account_class != expected[0]
            or ledger.normal_balance != expected[1]
            or ledger.currency_code != loan.currency_code
        ):
            raise ValueError(
                f"System ledger account {code} has invalid configuration."
            )

    lines = []

    def add_line(ledger, debit=0, credit=0):
        if debit or credit:
            lines.append({
                "ledger_account_id": str(ledger.id),
                "debit_minor": debit,
                "credit_minor": credit,
                "memo": "Personal loan repayment",
            })

    if loan.direction == "borrowed":
        add_line(
            accounts_by_code["2000"],
            debit=principal_total,
        )
        add_line(
            accounts_by_code["5000"],
            debit=interest_total,
        )
        add_line(
            cash_ledger,
            credit=payment.amount_minor,
        )
    else:
        add_line(
            cash_ledger,
            debit=payment.amount_minor,
        )
        add_line(
            accounts_by_code["1200"],
            credit=principal_total,
        )
        add_line(
            accounts_by_code["4000"],
            credit=interest_total,
        )

    reference = f"LOAN-PAYMENT-{payment.id}"

    journal = create_draft_entry(
        workspace_id=workspace_id,
        created_by=confirmed_by,
        reference=reference,
        description=f"Repayment for loan with {loan.counterparty_name}",
        transaction_date=datetime.combine(
            payment.payment_date,
            time.min,
            tzinfo=timezone.utc,
        ),
        lines=lines,
    )

    post_entry(
        entry_id=journal.id,
        workspace_id=workspace_id,
        posted_by=confirmed_by,
    )

    for allocation in allocations:
        installment = next(
            item for item in installments
            if item.id == allocation["installment_id"]
        )

        db.session.add(LoanPaymentAllocation(
            workspace_id=workspace_id,
            personal_loan_id=loan.id,
            payment_id=payment.id,
            installment_id=installment.id,
            amount_minor=allocation["amount_minor"],
            principal_minor=allocation["principal_minor"],
            interest_minor=allocation["interest_minor"],
        ))

        installment.paid_minor += allocation["amount_minor"]

    # Recalculate every installment, including overdue installments
    # that did not receive any part of this payment.
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
    payment.financial_account_id = account.id
    payment.journal_entry_id = journal.id
    payment.status = "confirmed"

    if all(
        item.status in ("paid", "waived")
        for item in installments
    ):
        loan.status = "paid"
    elif loan.status != "restructured":
        loan.status = (
            "overdue"
            if any(
                item.status == "overdue"
                for item in installments
            )
            else "active"
        )

    db.session.add(AuditLog(
        workspace_id=workspace_id,
        user_id=confirmed_by,
        action="loan_payment_confirmed",
        resource_type="loan_payment",
        resource_id=str(payment.id),
        details={
            "loan_id": str(loan.id),
            "amount_minor": payment.amount_minor,
            "principal_minor": principal_total,
            "interest_minor": interest_total,
            "journal_entry_id": str(journal.id),
        },
    ))

    db.session.flush()

    return payment
