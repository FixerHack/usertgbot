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


def build_start_text(status: UserStatus, now: datetime, lang: str = "uk") -> str:
    lines = [t(lang, "start_header"), ""]
    lines.append(f"📅 {now:%d.%m.%Y}")

    if status.subscription is not None:
        sub = status.subscription
        expires = t(lang, "dash_expires", date=f"{sub.expires_at:%d.%m.%Y}") if sub.expires_at is not None else ""
        lines.append(t(lang, "dash_sub", tariff=_tariff_title(sub.tariff), status=sub.status, expires=expires))
    else:
        lines.append(t(lang, "dash_sub_none"))

    conn = t(lang, "conn_yes") if status.sessions else t(lang, "conn_no")
    lines.append(t(lang, "dash_account", conn=conn))
    return "\n".join(lines)
