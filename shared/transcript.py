"""Turning recorded messages into the .txt a person actually reads.

Shared because a recording can be stopped from two places: `.unsave` in the
chat (userbot) and the button in ⚙️ Налаштування (management_bot). Two copies
of this would drift, and the archive is the whole product of the feature.
"""

from __future__ import annotations

from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from shared.i18n import t

# Timestamps in an archive are read by a person in Kyiv, not by a server.
KYIV = ZoneInfo("Europe/Kyiv")


def to_local(moment: datetime) -> datetime:
    """Stored timestamps are naive UTC — the same rule as everywhere else in
    this schema (see db.queries.as_aware)."""
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment.astimezone(KYIV)


def build_archive(title: str, rows, lang: str) -> str:
    """One line per message, oldest first."""
    lines = [t(lang, "rec_archive_header", chat=title), "=" * 30]
    for row in rows:
        who = t(lang, "rec_me") if row.is_outgoing else row.sender
        lines.append(f"[{to_local(row.sent_at):%d.%m.%Y %H:%M:%S}] {who}: {row.text}")
    return "\n".join(lines) + "\n"
