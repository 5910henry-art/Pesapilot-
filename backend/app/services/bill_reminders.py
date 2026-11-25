from datetime import datetime, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from sqlalchemy import func

from app.extensions import db
from app.models import Bill, BillPayment, JournalEntry, UserProfile
from app.models.notifications import Notification
from app.services.notifications import create_notification


DEFAULT_TIMEZONE = "Africa/Nairobi"

REMINDER_OFFSETS = (
    (3, "three-days", "Bill due in 3 days",
     "You have a bill due in three days."),
    (0, "due-today", "Bill due today",
     "You have a bill due today."),
)


def _recipient_timezone(user_id):
    profile = UserProfile.query.filter_by(user_id=user_id).first()
    timezone_name = profile.timezone if profile else DEFAULT_TIMEZONE

    try:
        return ZoneInfo(timezone_name)
    except (ZoneInfoNotFoundError, TypeError, ValueError):
        return ZoneInfo(DEFAULT_TIMEZONE)


def send_bill_reminders(now=None):
    """Create reminders for outstanding posted bills.

    The caller owns the transaction and must commit or roll it back.
    """
    if now is None:
        now = datetime.now(timezone.utc)
    elif now.tzinfo is None:
        raise ValueError("The reminder reference time must be timezone-aware.")

    now_utc = now.astimezone(timezone.utc)
    utc_date = now_utc.date()

    # Covers local dates from UTC-12 through UTC+14.
    earliest_due_date = utc_date - timedelta(days=1)
    latest_due_date = utc_date + timedelta(days=4)

    posted_payment_totals = (
        db.session.query(
            BillPayment.bill_id.label("bill_id"),
            func.sum(BillPayment.amount_minor).label("paid_minor"),
        )
        .join(
            JournalEntry,
            (JournalEntry.id == BillPayment.journal_entry_id)
            & (JournalEntry.workspace_id == BillPayment.workspace_id),
        )
        .filter(JournalEntry.status == "posted")
        .group_by(BillPayment.bill_id)
        .subquery()
    )

    rows = (
        db.session.query(
            Bill,
            func.coalesce(posted_payment_totals.c.paid_minor, 0),
        )
        .join(
            JournalEntry,
            (JournalEntry.id == Bill.journal_entry_id)
            & (JournalEntry.workspace_id == Bill.workspace_id),
        )
        .outerjoin(
            posted_payment_totals,
            posted_payment_totals.c.bill_id == Bill.id,
        )
        .filter(
            Bill.created_by.is_not(None),
            Bill.due_date >= earliest_due_date,
            Bill.due_date <= latest_due_date,
            JournalEntry.status == "posted",
        )
        .all()
    )

    result = {
        "bills_checked": 0,
        "notifications_created": 0,
        "already_notified": 0,
        "preferences_disabled": 0,
        "fully_paid_or_invalid": 0,
    }

    timezone_cache = {}

    for bill, paid_minor in rows:
        result["bills_checked"] += 1

        remaining_minor = max(
            0, bill.amount_minor - int(paid_minor or 0)
        )
        if remaining_minor == 0:
            result["fully_paid_or_invalid"] += 1
            continue

        zone = timezone_cache.get(bill.created_by)
        if zone is None:
            zone = _recipient_timezone(bill.created_by)
            timezone_cache[bill.created_by] = zone

        local_date = now_utc.astimezone(zone).date()
        days_until_due = (bill.due_date - local_date).days

        reminder = next(
            (item for item in REMINDER_OFFSETS
             if item[0] == days_until_due),
            None,
        )
        if reminder is None:
            continue

        _, reminder_kind, title, body = reminder
        dedupe_key = (
            f"bill-reminder:{bill.id}:{reminder_kind}:"
            f"{bill.due_date.isoformat()}"
        )

        existing = Notification.query.filter_by(
            user_id=bill.created_by,
            dedupe_key=dedupe_key,
        ).first()

        if existing is not None:
            result["already_notified"] += 1
            continue

        notification = create_notification(
            user_id=bill.created_by,
            category="bill_reminders",
            title=title,
            body=body,
            target_url="/bills",
            dedupe_key=dedupe_key,
        )

        if notification is None:
            result["preferences_disabled"] += 1
        else:
            result["notifications_created"] += 1

    return result
