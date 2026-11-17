"""Independent double-entry accounting operations for PesaPilot."""

from datetime import datetime, timezone
from decimal import Decimal, InvalidOperation, ROUND_HALF_UP
from uuid import UUID

from sqlalchemy import select

from app.extensions import db
from app.models import (
    AuditLog,
    Currency,
    PlatformAccountingSettings,
    PlatformJournalEntry,
    PlatformJournalLine,
    PlatformLedgerAccount,
)

MAX_MINOR_UNITS = 9_223_372_036_854_775_807
RATE_QUANTUM = Decimal("0.000000000001")


class PlatformAccountingError(ValueError):
    """A platform accounting validation error."""


def _positive_minor_units(value, field_name):
    if isinstance(value, bool) or not isinstance(value, int):
        raise PlatformAccountingError(
            f"{field_name} must be an integer number of minor units."
        )

    if value <= 0 or value > MAX_MINOR_UNITS:
        raise PlatformAccountingError(
            f"{field_name} is outside the supported range."
        )

    return value


def _exchange_rate(value, same_currency):
    if same_currency and value is None:
        return Decimal("1")

    if isinstance(value, (bool, float)) or value is None:
        raise PlatformAccountingError(
            "A valid decimal exchange_rate is required."
        )

    try:
        rate = Decimal(str(value))
    except (InvalidOperation, TypeError, ValueError):
        raise PlatformAccountingError("Invalid exchange rate.")

    if (
        not rate.is_finite()
        or rate <= 0
        or rate >= Decimal("1000000000000")
    ):
        raise PlatformAccountingError(
            "Exchange rate is outside the supported range."
        )

    if rate != rate.quantize(RATE_QUANTUM):
        raise PlatformAccountingError(
            "Exchange rate supports at most 12 decimal places."
        )

    if same_currency and rate != Decimal("1"):
        raise PlatformAccountingError(
            "Exchange rate must be 1 for identical currencies."
        )

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
        raise PlatformAccountingError(
            "Converted amount is outside the supported range."
        )

    return result


def _get_functional_currency():
    settings = db.session.execute(
        select(PlatformAccountingSettings)
        .where(PlatformAccountingSettings.id == 1)
        .with_for_update(read=True)
    ).scalar_one_or_none()

    if settings is None:
        raise PlatformAccountingError(
            "Platform accounting settings have not been configured."
        )

    currency = db.session.get(
        Currency,
        settings.functional_currency_code,
    )

    if currency is None or not currency.is_active:
        raise PlatformAccountingError(
            "Platform functional currency is unavailable."
        )

    return settings.functional_currency_code, currency


def create_draft_entry(
    *,
    created_by: UUID,
    reference: str,
    description: str,
    transaction_date: datetime,
    lines: list,
):
    """Create a platform draft entry. Caller controls commit and rollback."""

    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 100
    ):
        raise PlatformAccountingError(
            "A reference of 1 to 100 characters is required."
        )

    if not isinstance(description, str) or not description.strip():
        raise PlatformAccountingError(
            "A transaction description is required."
        )

    if (
        not isinstance(transaction_date, datetime)
        or transaction_date.tzinfo is None
    ):
        raise PlatformAccountingError(
            "A timezone-aware transaction date is required."
        )

    if not isinstance(lines, list) or len(lines) < 2:
        raise PlatformAccountingError(
            "At least two journal lines are required."
        )

    functional_currency_code, functional_currency = (
        _get_functional_currency()
    )

    parsed_lines = []
    account_ids = set()

    for index, item in enumerate(lines, start=1):
        if not isinstance(item, dict):
            raise PlatformAccountingError(
                f"Journal line {index} must be an object."
            )

        try:
            account_id = UUID(str(item.get("ledger_account_id")))
        except (ValueError, TypeError, AttributeError):
            raise PlatformAccountingError(
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
            raise PlatformAccountingError(
                f"Journal line {index} has an invalid debit."
            )

        if (
            isinstance(credit, bool)
            or not isinstance(credit, int)
            or credit < 0
        ):
            raise PlatformAccountingError(
                f"Journal line {index} has an invalid credit."
            )

        if (debit > 0) == (credit > 0):
            raise PlatformAccountingError(
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
        select(PlatformLedgerAccount)
        .where(PlatformLedgerAccount.id.in_(account_ids))
        .order_by(PlatformLedgerAccount.id.asc())
        .with_for_update(read=True)
    ).scalars().all()

    accounts_by_id = {account.id: account for account in accounts}

    if len(accounts_by_id) != len(account_ids):
        raise PlatformAccountingError(
            "One or more platform ledger accounts are unavailable."
        )

    currencies = {}

    for account in accounts:
        if not account.is_active:
            raise PlatformAccountingError(
                f"Platform ledger account {account.code} is inactive."
            )

        currency = db.session.get(Currency, account.currency_code)

        if currency is None or not currency.is_active:
            raise PlatformAccountingError(
                f"Currency {account.currency_code} is unavailable."
            )

        if account.financial_account_id is not None:
            from app.models import PlatformFinancialAccount

            financial_account = db.session.get(
                PlatformFinancialAccount,
                account.financial_account_id,
            )

            if (
                financial_account is None
                or not financial_account.is_active
                or financial_account.currency_code != account.currency_code
            ):
                raise PlatformAccountingError(
                    f"Financial account linked to {account.code} "
                    "is unavailable or has a currency mismatch."
                )

        currencies[account.currency_code] = currency

    entry = PlatformJournalEntry(
        reference=reference.strip(),
        description=description.strip(),
        status="draft",
        transaction_date=transaction_date.astimezone(timezone.utc),
        functional_currency_code=functional_currency_code,
        created_by=created_by,
    )

    db.session.add(entry)
    db.session.flush()

    journal_lines = []

    for index, item in enumerate(parsed_lines, start=1):
        account = accounts_by_id[item["account_id"]]
        source_currency = currencies[account.currency_code]

        rate = _exchange_rate(
            item["exchange_rate"],
            account.currency_code == functional_currency_code,
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
            raise PlatformAccountingError(
                f"Journal line {index} memo must not exceed 255 characters."
            )

        journal_lines.append(PlatformJournalLine(
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
        workspace_id=None,
        user_id=created_by,
        action="platform_journal_entry_created",
        resource_type="platform_journal_entry",
        resource_id=str(entry.id),
        details={
            "reference": entry.reference,
            "line_count": len(journal_lines),
            "functional_currency_code": functional_currency_code,
        },
    ))

    db.session.flush()
    return entry


def _lock_entry_ledger_accounts(lines):
    """Lock affected platform accounts in a consistent order."""

    ledger_ids = {line.ledger_account_id for line in lines}

    accounts = db.session.execute(
        select(PlatformLedgerAccount)
        .where(PlatformLedgerAccount.id.in_(ledger_ids))
        .order_by(PlatformLedgerAccount.id.asc())
        .with_for_update()
    ).scalars().all()

    if len(accounts) != len(ledger_ids):
        raise PlatformAccountingError(
            "One or more platform ledger accounts are unavailable."
        )

    if any(not account.is_active for account in accounts):
        raise PlatformAccountingError(
            "Journal entries cannot affect inactive platform accounts."
        )

    return accounts


def post_entry(
    *,
    entry_id: UUID,
    posted_by: UUID,
):
    """Validate and post a platform draft entry. Caller must commit."""

    entry = db.session.execute(
        select(PlatformJournalEntry)
        .where(PlatformJournalEntry.id == entry_id)
        .with_for_update()
    ).scalar_one_or_none()

    if entry is None:
        raise PlatformAccountingError("Platform journal entry not found.")

    if entry.status != "draft":
        raise PlatformAccountingError(
            "Only draft platform entries can be posted."
        )

    lines = db.session.execute(
        select(PlatformJournalLine)
        .where(PlatformJournalLine.journal_entry_id == entry.id)
        .order_by(PlatformJournalLine.id.asc())
        .with_for_update()
    ).scalars().all()

    if len(lines) < 2:
        raise PlatformAccountingError(
            "At least two journal lines are required before posting."
        )

    _lock_entry_ledger_accounts(lines)

    total_debits = sum(
        line.functional_debit_minor for line in lines
    )
    total_credits = sum(
        line.functional_credit_minor for line in lines
    )

    if total_debits <= 0 or total_debits != total_credits:
        raise PlatformAccountingError(
            f"Entry is not balanced: debits={total_debits}, "
            f"credits={total_credits}."
        )

    entry.status = "posted"
    entry.posted_by = posted_by
    entry.posted_at = datetime.now(timezone.utc)

    db.session.add(AuditLog(
        workspace_id=None,
        user_id=posted_by,
        action="platform_journal_entry_posted",
        resource_type="platform_journal_entry",
        resource_id=str(entry.id),
        details={
            "reference": entry.reference,
            "functional_currency_code": entry.functional_currency_code,
            "functional_debits_minor": total_debits,
            "functional_credits_minor": total_credits,
        },
    ))

    db.session.flush()
    return entry


def reverse_entry(
    *,
    entry_id: UUID,
    reversed_by: UUID,
    reference: str,
    description: str | None = None,
    transaction_date: datetime | None = None,
):
    """Reverse a posted platform entry. Caller controls commit and rollback."""

    if (
        not isinstance(reference, str)
        or not reference.strip()
        or len(reference.strip()) > 100
    ):
        raise PlatformAccountingError(
            "A reversal reference of 1 to 100 characters is required."
        )

    if transaction_date is None:
        transaction_date = datetime.now(timezone.utc)

    if (
        not isinstance(transaction_date, datetime)
        or transaction_date.tzinfo is None
    ):
        raise PlatformAccountingError(
            "A timezone-aware reversal date is required."
        )

    original = db.session.execute(
        select(PlatformJournalEntry)
        .where(PlatformJournalEntry.id == entry_id)
        .with_for_update()
    ).scalar_one_or_none()

    if original is None:
        raise PlatformAccountingError(
            "Original platform journal entry not found."
        )

    if original.status != "posted":
        raise PlatformAccountingError(
            "Only posted platform transactions can be reversed."
        )

    if original.reversal_of_id is not None:
        raise PlatformAccountingError(
            "A reversal entry cannot itself be reversed."
        )

    original_lines = db.session.execute(
        select(PlatformJournalLine)
        .where(PlatformJournalLine.journal_entry_id == original.id)
        .order_by(PlatformJournalLine.id.asc())
        .with_for_update()
    ).scalars().all()

    if len(original_lines) < 2:
        raise PlatformAccountingError(
            "Original transaction has insufficient journal lines."
        )

    _lock_entry_ledger_accounts(original_lines)

    if description is None:
        reversal_description = (
            f"Reversal of {original.reference}: {original.description}"
        )
    elif isinstance(description, str) and description.strip():
        reversal_description = description.strip()
    else:
        raise PlatformAccountingError(
            "A valid reversal description is required."
        )

    now = datetime.now(timezone.utc)

    reversal = PlatformJournalEntry(
        reference=reference.strip(),
        description=reversal_description,
        status="posted",
        transaction_date=transaction_date.astimezone(timezone.utc),
        functional_currency_code=original.functional_currency_code,
        created_by=reversed_by,
        posted_by=reversed_by,
        posted_at=now,
        reversal_of_id=original.id,
    )

    db.session.add(reversal)
    db.session.flush()

    reversal_lines = []

    for line in original_lines:
        reversal_lines.append(PlatformJournalLine(
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
            workspace_id=None,
            user_id=reversed_by,
            action="platform_journal_entry_reversed",
            resource_type="platform_journal_entry",
            resource_id=str(original.id),
            details={
                "original_reference": original.reference,
                "reversal_reference": reversal.reference,
                "reversal_entry_id": str(reversal.id),
            },
        ),
        AuditLog(
            workspace_id=None,
            user_id=reversed_by,
            action="platform_journal_reversal_created",
            resource_type="platform_journal_entry",
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
