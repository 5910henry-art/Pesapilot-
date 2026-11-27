from collections import defaultdict
from datetime import datetime, time, timezone
from decimal import Decimal, ROUND_HALF_UP

from sqlalchemy import func, select

from app.extensions import db
from app.models import (
    AuditLog,
    Currency,
    FinancialAccount,
    ExchangeRate,
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
    MAX_MINOR_UNITS,
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
    financial_account_id=None,
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

    if loan.direction not in ("borrowed", "lent"):
        raise ValueError("Invalid loan direction.")

    if workspace.functional_currency == loan.currency_code:
        fx_rate = None
    else:
        fx_rate = db.session.execute(
            select(ExchangeRate.rate)
            .where(
                ExchangeRate.base_currency == loan.currency_code,
                ExchangeRate.quote_currency == workspace.functional_currency,
                ExchangeRate.effective_at <= datetime.combine(
                    payment.payment_date,
                    time.max,
                    tzinfo=timezone.utc,
                ),
            )
            .order_by(ExchangeRate.effective_at.desc())
            .limit(1)
        ).scalar_one_or_none()

        if fx_rate is None:
            raise ValueError(
                "No exchange rate is configured from "
                f"{loan.currency_code} to {workspace.functional_currency} "
                "on or before the repayment date."
            )

    if not isinstance(payment.reference, str) or not payment.reference.strip():
        raise ValueError("A payment reference is required before confirmation.")

    if payment.payment_method not in ("cash", "bank", "mobile_money", "other"):
        raise ValueError("Invalid repayment method.")

    if payment.payment_source == "registered":
        if financial_account_id is None:
            raise ValueError("Select a registered account for this repayment.")

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

        if account.currency_code != loan.currency_code:
            raise ValueError(
                "The selected account currency must match the loan currency."
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

        if (
            cash_ledger.account_class != "asset"
            or cash_ledger.normal_balance != "debit"
        ):
            raise ValueError(
                "The selected account does not have a valid cash ledger."
            )

        if loan.direction == "borrowed":
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
                    JournalEntry.status == "posted",
                )
            ).scalar_one()

            available_minor = int(available_minor or 0)

            if payment.amount_minor > available_minor:
                raise ValueError(
                    "Insufficient funds in the selected account. "
                    f"Available: {available_minor} minor units; "
                    f"required: {payment.amount_minor} minor units."
                )

    elif payment.payment_source == "external":
        if financial_account_id is not None:
            raise ValueError(
                "External repayments must not use a registered account."
            )

        account = None

        if loan.direction == "borrowed":
            clearing_code = f"LN-EXT-PAY-CLEARING-{loan.currency_code}"
            clearing_name = (
                f"External Loan Repayment Clearing - {loan.currency_code}"
            )
        else:
            clearing_code = f"LN-EXT-REC-CLEARING-{loan.currency_code}"
            clearing_name = (
                f"External Loan Receipt Clearing - {loan.currency_code}"
            )

        clearing_class = "asset"
        clearing_balance = "debit"

        cash_ledger = db.session.execute(
            select(LedgerAccount)
            .where(
                LedgerAccount.workspace_id == workspace_id,
                LedgerAccount.code == clearing_code,
            )
            .with_for_update()
        ).scalar_one_or_none()

        if cash_ledger is None:
            cash_ledger = LedgerAccount(
                workspace_id=workspace_id,
                financial_account_id=None,
                code=clearing_code,
                name=clearing_name,
                account_class=clearing_class,
                normal_balance=clearing_balance,
                currency_code=loan.currency_code,
                is_system=True,
                is_active=True,
            )
            db.session.add(cash_ledger)
            db.session.flush()
        elif (
            cash_ledger.financial_account_id is not None
            or not cash_ledger.is_system
            or not cash_ledger.is_active
            or cash_ledger.account_class != clearing_class
            or cash_ledger.normal_balance != clearing_balance
            or cash_ledger.currency_code != loan.currency_code
        ):
            raise ValueError(
                f"External clearing ledger {clearing_code} has invalid configuration."
            )

    else:
        raise ValueError("Invalid repayment source.")

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
            LoanPaymentAllocation.status == "confirmed",
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

    source_currency = db.session.get(Currency, loan.currency_code)
    functional_currency = db.session.get(
        Currency, workspace.functional_currency
    )

    if source_currency is None or functional_currency is None:
        raise ValueError("Repayment currency configuration is unavailable.")

    conversion_rate = (
        Decimal(str(fx_rate)) if fx_rate is not None else Decimal("1")
    )

    def convert_repayment_component(amount_minor):
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
        result = int(
            converted.quantize(
                Decimal("1"),
                rounding=ROUND_HALF_UP,
            )
        )

        if result < 0 or result > MAX_MINOR_UNITS:
            raise ValueError(
                "Repayment functional amount is outside the supported range."
            )

        return result

    total_functional = convert_repayment_component(payment.amount_minor)

    if total_functional <= 0:
        raise ValueError("Repayment is too small in functional currency.")

    principal_functional = convert_repayment_component(principal_total)
    interest_functional = convert_repayment_component(interest_total)

    rounding_difference = (
        total_functional - principal_functional - interest_functional
    )

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
        or principal_functional > MAX_MINOR_UNITS
        or interest_functional > MAX_MINOR_UNITS
        or principal_functional + interest_functional != total_functional
    ):
        raise ValueError("Repayment FX allocation is invalid.")

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

    def add_line(
        ledger,
        debit=0,
        credit=0,
        functional_amount_minor=None,
    ):
        if debit or credit:
            lines.append({
                "ledger_account_id": str(ledger.id),
                "debit_minor": debit,
                "credit_minor": credit,
                "exchange_rate": fx_rate,
                "functional_amount_minor": functional_amount_minor,
                "memo": "Personal loan repayment",
            })

    if loan.direction == "borrowed":
        add_line(
            accounts_by_code["2000"],
            debit=principal_total,
            functional_amount_minor=principal_functional,
        )
        add_line(
            accounts_by_code["5000"],
            debit=interest_total,
            functional_amount_minor=interest_functional,
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
            functional_amount_minor=principal_functional,
        )
        add_line(
            accounts_by_code["4000"],
            credit=interest_total,
            functional_amount_minor=interest_functional,
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
    payment.financial_account_id = account.id if account is not None else None
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
            "payment_source": payment.payment_source,
            "financial_account_id": (
                str(account.id) if account is not None else None
            ),
            "journal_entry_id": str(journal.id),
        },
    ))

    db.session.flush()

    return payment
