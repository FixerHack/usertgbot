"""Builds the /start greeting + metrics block.

Pure/testable: takes a UserStatus snapshot, the current time and a language,
returns the localized HTML text.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from management_bot.storage import UserStatus
from shared.i18n import t
from shared.tariffs import Tariff, get_plan


def _tariff_title(raw: str) -> str:
    try:
        return get_plan(Tariff(raw)).title
    except (ValueError, KeyError):
        return raw


# Everything the user reads is in their wall-clock time, not the server's.
# UTC is three hours behind Kyiv in summer, so between midnight and 03:00 the
# dashboard was cheerfully showing yesterday's date.
KYIV = ZoneInfo("Europe/Kyiv")


def _local(moment: datetime) -> datetime:
    """A stored timestamp with no tzinfo is UTC — that rule holds everywhere
    in this schema (see db.queries.as_aware)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KYIV)


# Only these four exist; anything else means the enum grew without this
# screen being told, and printing the raw value is a louder failure than
# silently showing nothing.
_STATE_KEYS = {
    "active": "sub_state_active",
    "expired": "sub_state_expired",
    "pending": "sub_state_pending",
    "cancelled": "sub_state_cancelled",
}


def _state_label(sub, lang: str) -> str:
    """A finished subscription carries its date inside the state, a running one
    outside it: "Pro (завершилась 03.09)" against "Pro (активна) до 03.11".
    The same date is a fact in one case and a promise in the other, and it
    should not be printed twice to say so."""
    if sub.status == "expired" and sub.expires_at is not None:
        return t(lang, "sub_state_expired_on", date=f"{_local(sub.expires_at):%d.%m.%Y}")
    key = _STATE_KEYS.get(sub.status)
    return t(lang, key) if key else sub.status


def _expiry_phrase(sub, lang: str) -> str:
    # "until <date>" only makes sense while something is still running. A
    # cancelled plan stopped when it stopped; a finished one carries its date
    # inside the state instead.
    if sub.expires_at is None or sub.status in ("expired", "cancelled"):
        return ""
    return t(lang, "dash_expires", date=f"{_local(sub.expires_at):%d.%m.%Y}")


def build_start_text(status: UserStatus, now: datetime, lang: str = "uk") -> str:
    lines = [t(lang, "start_header"), ""]
    lines.append(f"📅 {_local(now):%d.%m.%Y}")

    if status.subscription is not None:
        sub = status.subscription
        lines.append(
            t(
                lang, "dash_sub",
                tariff=_tariff_title(sub.tariff),
                status=_state_label(sub, lang),
                expires=_expiry_phrase(sub, lang),
            )
        )
    else:
        lines.append(t(lang, "dash_sub_none"))

    conn = t(lang, "conn_yes") if status.sessions else t(lang, "conn_no")
    lines.append(t(lang, "dash_account", conn=conn))
    return "\n".join(lines)
