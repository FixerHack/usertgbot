"""Builds the /start greeting + metrics block.

Pure/testable: takes a UserStatus snapshot, the current time and a language,
returns the localized HTML text.
"""

from __future__ import annotations

from datetime import datetime

from management_bot.storage import UserStatus
from shared.i18n import t
from shared.tariffs import Tariff, get_plan


def _tariff_title(raw: str) -> str:
    try:
        return get_plan(Tariff(raw)).title
    except (ValueError, KeyError):
        return raw


# Only these four exist; anything else means the enum grew without this
# screen being told, and printing the raw value is a louder failure than
# silently showing nothing.
_STATE_KEYS = {
    "active": "sub_state_active",
    "expired": "sub_state_expired",
    "pending": "sub_state_pending",
    "cancelled": "sub_state_cancelled",
}


def _state_label(state: str, lang: str) -> str:
    key = _STATE_KEYS.get(state)
    return t(lang, key) if key else state


def _expiry_phrase(sub, lang: str) -> str:
    """"until <date>" while it runs, "ended <date>" once it has — the same
    date reads as a promise or as a fact depending on which side of it we
    are on."""
    if sub.expires_at is None:
        return ""
    date = f"{sub.expires_at:%d.%m.%Y}"
    key = "dash_expired_on" if sub.status == "expired" else "dash_expires"
    return t(lang, key, date=date)


def build_start_text(status: UserStatus, now: datetime, lang: str = "uk") -> str:
    lines = [t(lang, "start_header"), ""]
    lines.append(f"📅 {now:%d.%m.%Y}")

    if status.subscription is not None:
        sub = status.subscription
        lines.append(
            t(
                lang, "dash_sub",
                tariff=_tariff_title(sub.tariff),
                status=_state_label(sub.status, lang),
                expires=_expiry_phrase(sub, lang),
            )
        )
    else:
        lines.append(t(lang, "dash_sub_none"))

    conn = t(lang, "conn_yes") if status.sessions else t(lang, "conn_no")
    lines.append(t(lang, "dash_account", conn=conn))
    return "\n".join(lines)
