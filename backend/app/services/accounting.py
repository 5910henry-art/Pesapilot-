
"""Core double-entry accounting operations for PesaPilot."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select

from app.extensions import db
from app.models import (
    AuditLog,
    Currency,
    JournalEntry,
    JournalLine,
    LedgerAccount,
    Workspace,
)

MAX_MINOR_UNITS = 9_223_372_036_854_775_807
RATE_QUANTUM = Decimal("0.000000000001")


class AccountingError(ValueError):
    """An accounting validation error."""


def _positive_minor_units(value, field_name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise AccountingError(
            f"{field_name} must be an integer number of minor units."
        )

    if value <= 0 or value > MAX_MINOR_UNITS:
        raise AccountingError(f"{field_name} is outside the supported range.")

    return value


def _exchange_rate(value, same_currency):
    if same_currency and value is None:
        return Decimal("1")

    if isinstance(value, (bool, float)) or value is None:
        raise AccountingError("A valid decimal exchange_rate is required.")

    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise AccountingError("Invalid exchange rate.")

    if (
        not rate.is_finite()
        or rate <= 0
        or rate >= Decimal("1000000000000")
    ):
        raise AccountingError("Exchange rate is outside the supported range.")

    if rate != rate.quantize(RATE_QUANTUM):
        raise AccountingError("Exchange rate supports at most 12 decimal places.")

    if same_currency and rate != Decimal("1"):
        raise AccountingError("Exchange rate must be 1 for identical currencies.")

    return rate


def _functional_minor_units(
    amount_minor,
    rate,
    source_minor_unit,
    functional_minor_unit,
):
    converted = (
        Decimal(amount_minor)
        * rate
        * (Decimal(10) ** (functional_minor_unit - source_minor_unit))
    )

    result = int(
        converted.quantize(Decimal("1"), rounding=ROUND_HALF_UP)
    )

    if result <= 0 or result > MAX_MINOR_UNITS:
        raise AccountingError("Converted amount is outside the supported range.")

    return result


def create_draft_entry(
    *,
    workspace_id: UUID,
    created_by: UUID,
    reference: str,
    description: str,
    transaction_date: datetime,
    lines: list,
):
    """Create a draft entry. The caller controls commit and rollback."""

    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 100
    ):
        raise AccountingError("A reference of 1 to 100 characters is required.")

    if not isinstance(description, str) or not description.strip():
        raise AccountingError("A transaction description is required.")

    if (
        not isinstance(transaction_date, datetime)
        or transaction_date.tzinfo is None
    ):
        raise AccountingError("A timezone-aware transaction date is required.")

    if not isinstance(lines, list) or len(lines) < 2:
        raise AccountingError("At least two journal lines are required.")

    workspace = db.session.execute(
        select(Workspace).where(Workspace.id == workspace_id)
    ).scalar_one_or_none()

    if workspace is None:
        raise AccountingError("Workspace not found.")

    functional_currency = db.session.get(
        Currency, workspace.functional_currency
    )

    if functional_currency is None or not functional_currency.is_active:
        raise AccountingError("Workspace functional currency is unavailable.")

    parsed_lines = []
    account_ids = set()

    for index, item in enumerate(lines, start=1):
        if not isinstance(item, dict):
            raise AccountingError(f"Journal line {index} must be an object.")

        try:
            account_id = UUID(str(item.get("ledger_account_id")))
        except (ValueError, TypeError, AttributeError):
            raise AccountingError(
                f"Journal line {index} has an invalid ledger account ID."
            )

        account_ids.add(account_id)

        debit = item.get("debit_minor", 0)
        credit = item.get("credit_minor", 0)

        if (
            isinstance(debit, bool)
            or not isinstance(debit, int)
            or debit < 0
        ):
            raise AccountingError(f"Journal line {index} has an invalid debit.")

        if (
            isinstance(credit, bool)
            or not isinstance(credit, int)
            or credit < 0
        ):
            raise AccountingError(f"Journal line {index} has an invalid credit.")

        if (debit > 0) == (credit > 0):
            raise AccountingError(
                f"Journal line {index} must have exactly one positive side."
            )

        amount = _positive_minor_units(
            debit or credit,
            f"Journal line {index} amount",
        )

        parsed_lines.append({
            "account_id": account_id,
            "debit": debit,
            "credit": credit,
            "amount": amount,
            "exchange_rate": item.get("exchange_rate"),
            "memo": item.get("memo"),
        })

    accounts = db.session.execute(
        select(LedgerAccount).where(
            LedgerAccount.id.in_(account_ids),
            LedgerAccount.workspace_id == workspace_id,
        ).with_for_update(read=True)
    ).scalars().all()

    accounts_by_id = {account.id: account for account in accounts}

    if len(accounts_by_id) != len(account_ids):
        raise AccountingError(
            "One or more ledger accounts do not belong to this workspace."
        )

    currencies = {}

    for account in accounts:
        if not account.is_active:
            raise AccountingError(
                f"Ledger account {account.code} is inactive."
            )

        currency = db.session.get(Currency, account.currency_code)

        if currency is None or not currency.is_active:
            raise AccountingError(
                f"Currency {account.currency_code} is unavailable."
            )

        currencies[account.currency_code] = currency

    entry = JournalEntry(
        workspace_id=workspace_id,
        reference=reference.strip(),
        description=description.strip(),
        status="draft",
        transaction_date=transaction_date.astimezone(timezone.utc),
        created_by=created_by,
    )

    db.session.add(entry)
    db.session.flush()

    journal_lines = []

    for index, item in enumerate(parsed_lines, start=1):
        account = accounts_by_id[item["account_id"]]
        source_currency = currencies[account.currency_code]

        same_currency = (
            account.currency_code == workspace.functional_currency
        )

        rate = _exchange_rate(
            item["exchange_rate"],
            same_currency,
        )

        functional_amount = _functional_minor_units(
            item["amount"],
            rate,
            source_currency.minor_unit,
            functional_currency.minor_unit,
        )

        memo = item["memo"]

        if memo is not None and (
            not isinstance(memo, str) or len(memo) > 255
        ):
            raise AccountingError(
                f"Journal line {index} memo must not exceed 255 characters."
            )

        journal_lines.append(JournalLine(
            journal_entry_id=entry.id,
            ledger_account_id=account.id,
            currency_code=account.currency_code,
            debit_minor=item["debit"],
            credit_minor=item["credit"],
            functional_debit_minor=(
                functional_amount if item["debit"] else 0
            ),
            functional_credit_minor=(
                functional_amount if item["credit"] else 0
            ),
            exchange_rate=rate,
            memo=memo.strip() if memo else None,
        ))

    db.session.add_all(journal_lines)

    db.session.add(AuditLog(
        workspace_id=workspace_id,
        user_id=created_by,
        action="journal_entry_created",
        resource_type="journal_entry",
        resource_id=str(entry.id),
        details={
            "reference": entry.reference,
            "line_count": len(journal_lines),
        },
    ))

    db.session.flush()
    return entry


def post_entry(*, entry_id: UUID, workspace_id: UUID, posted_by: UUID):
    """Validate and post a draft entry. Caller must commit."""

    entry = db.session.execute(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.workspace_id == workspace_id,
        ).with_for_update()
    ).scalar_one_or_none()

    if entry is None:
        raise AccountingError("Journal entry not found.")

    if entry.status != "draft":
        raise AccountingError("Only draft entries can be posted.")

    lines = db.session.execute(
        select(JournalLine).where(
            JournalLine.journal_entry_id == entry.id
        ).with_for_update()
    ).scalars().all()

    if len(lines) < 2:
        raise AccountingError(
            "At least two journal lines are required before posting."
        )

    total_debits = sum(
        line.functional_debit_minor for line in lines
    )

    total_credits = sum(
        line.functional_credit_minor for line in lines
    )

    if total_debits <= 0 or total_debits != total_credits:
        raise AccountingError(
            f"Entry is not balanced: debits={total_debits}, "
            f"credits={total_credits}."
        )

    entry.status = "posted"
    entry.posted_at = datetime.now(timezone.utc)

    db.session.add(AuditLog(
        workspace_id=workspace_id,
        user_id=posted_by,
        action="journal_entry_posted",
        resource_type="journal_entry",
        resource_id=str(entry.id),
        details={
            "reference": entry.reference,
            "functional_debits_minor": total_debits,
            "functional_credits_minor": total_credits,
        },
    ))

    db.session.flush()
    return entry



def reverse_entry(
    *,
    entry_id: UUID,
    workspace_id: UUID,
    reversed_by: UUID,
    reference: str,
    description: str | None = None,
    transaction_date: datetime | None = None,
):
    """
    Reverse a posted journal entry.

    The original and reversal are updated in the same transaction.
    The caller controls commit and rollback.
    """

    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 100
    ):
        raise AccountingError(
            "A reversal reference of 1 to 100 characters is required."
        )

    if transaction_date is None:
        transaction_date = datetime.now(timezone.utc)

    if (
        not isinstance(transaction_date, datetime)
        or transaction_date.tzinfo is None
    ):
        raise AccountingError(
            "A timezone-aware reversal date is required."
        )

    original = db.session.execute(
        select(JournalEntry).where(
            JournalEntry.id == entry_id,
            JournalEntry.workspace_id == workspace_id,
        ).with_for_update()
    ).scalar_one_or_none()

    if original is None:
        raise AccountingError("Original journal entry not found.")

    if original.status != "posted":
        raise AccountingError(
            "Only posted transactions can be reversed."
        )

    if original.reversal_of_id is not None:
        raise AccountingError(
            "A reversal entry cannot itself be reversed."
        )

    original_lines = db.session.execute(
        select(JournalLine).where(
            JournalLine.journal_entry_id == original.id
        ).order_by(JournalLine.id.asc()).with_for_update()
    ).scalars().all()

    if len(original_lines) < 2:
        raise AccountingError(
            "Original transaction has insufficient journal lines."
        )

    if description is None:
        reversal_description = (
            f"Reversal of {original.reference}: {original.description}"
        )
    elif isinstance(description, str) and description.strip():
        reversal_description = description.strip()
    else:
        raise AccountingError("A valid reversal description is required.")

    now = datetime.now(timezone.utc)

    reversal = JournalEntry(
        workspace_id=workspace_id,
        reference=reference.strip(),
        description=reversal_description,
        status="posted",
        transaction_date=transaction_date.astimezone(timezone.utc),
        created_by=reversed_by,
        posted_at=now,
        reversal_of_id=original.id,
    )

    db.session.add(reversal)
    db.session.flush()

    reversal_lines = []

    for line in original_lines:
        reversal_lines.append(JournalLine(
            journal_entry_id=reversal.id,
            ledger_account_id=line.ledger_account_id,
            currency_code=line.currency_code,
            debit_minor=line.credit_minor,
            credit_minor=line.debit_minor,
            functional_debit_minor=line.functional_credit_minor,
            functional_credit_minor=line.functional_debit_minor,
            exchange_rate=line.exchange_rate,
            memo=line.memo,
        ))

    db.session.add_all(reversal_lines)

    original.status = "reversed"

    db.session.add_all([
        AuditLog(
            workspace_id=workspace_id,
            user_id=reversed_by,
            action="journal_entry_reversed",
            resource_type="journal_entry",
            resource_id=str(original.id),
            details={
                "original_reference": original.reference,
                "reversal_reference": reversal.reference,
                "reversal_entry_id": str(reversal.id),
            },
        ),
        AuditLog(
            workspace_id=workspace_id,
            user_id=reversed_by,
            action="journal_reversal_created",
            resource_type="journal_entry",
            resource_id=str(reversal.id),
            details={
                "reversal_of_id": str(original.id),
                "reference": reversal.reference,
                "line_count": len(reversal_lines),
            },
        ),
    ])

    db.session.flush()
    return reversal
