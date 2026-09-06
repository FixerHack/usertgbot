"""Schemas for the user-configurable `.me` card and Pro autoresponder.

Shared: management_bot writes these (settings UI), userbot reads them (renders
the card / decides whether to auto-reply). Kept as plain dataclasses with
dict (de)serialization so they map straight onto the JSON columns.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import datetime, time

MAX_BUTTONS = 5
MAX_TEXT_LEN = 1024

_HHMM_RE = re.compile(r"^([01]?\d|2[0-3]):([0-5]?\d)$")


@dataclass
class LinkButton:
    text: str
    url: str

    def to_dict(self) -> dict:
        return {"text": self.text, "url": self.url}

    @classmethod
    def from_dict(cls, d: dict) -> "LinkButton":
        return cls(text=d["text"], url=d["url"])


# Telegram only renders these schemes as real links; anything else becomes a
# text entity with a junk URL that the API rejects, and the send fails.
ALLOWED_URL_SCHEMES = ("http://", "https://", "tg://")


def is_valid_url(value: str | None) -> bool:
    return bool(value) and value.strip().startswith(ALLOWED_URL_SCHEMES)


def parse_buttons(raw: str) -> list[LinkButton]:
    """Parse one `Label | https://url` per line; ignores blanks/malformed."""
    buttons: list[LinkButton] = []
    for line in (raw or "").splitlines():
        if "|" not in line:
            continue
        label, _, url = line.partition("|")
        label, url = label.strip(), url.strip()
        if label and is_valid_url(url):
            buttons.append(LinkButton(text=label[:64], url=url))
        if len(buttons) >= MAX_BUTTONS:
            break
    return buttons


def parse_hhmm(value: str | None) -> str | None:
    """Normalize `"9:5"`-ish input to `"09:05"`, or None if empty/invalid."""
    if not value:
        return None
    m = _HHMM_RE.match(value.strip())
    if not m:
        return None
    return f"{int(m.group(1)):02d}:{int(m.group(2)):02d}"


def _buttons_to_dicts(buttons: list[LinkButton]) -> list[dict]:
    return [b.to_dict() for b in buttons[:MAX_BUTTONS]]


def _buttons_from_dicts(raw: list | None) -> list[LinkButton]:
    return [LinkButton.from_dict(b) for b in (raw or [])]


@dataclass
class MeCard:
    text: str = ""
    link: str | None = None
    has_photo: bool = False
    buttons: list[LinkButton] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {
            "text": self.text,
            "link": self.link,
            "has_photo": self.has_photo,
            "buttons": _buttons_to_dicts(self.buttons),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "MeCard":
        d = d or {}
        return cls(
            text=d.get("text", ""),
            link=d.get("link"),
            has_photo=bool(d.get("has_photo", False)),
            buttons=_buttons_from_dicts(d.get("buttons")),
        )

    def is_empty(self) -> bool:
        return not (self.text or self.link or self.has_photo or self.buttons)


@dataclass
class Autoresponder:
    enabled: bool = False
    message: str = ""
    has_photo: bool = False
    buttons: list[LinkButton] = field(default_factory=list)
    active_from: str | None = None  # "HH:MM"
    active_to: str | None = None    # "HH:MM"
    exceptions: list[int] = field(default_factory=list)  # telegram ids to skip

    def to_dict(self) -> dict:
        return {
            "enabled": self.enabled,
            "message": self.message,
            "has_photo": self.has_photo,
            "buttons": _buttons_to_dicts(self.buttons),
            "active_from": self.active_from,
            "active_to": self.active_to,
            "exceptions": list(self.exceptions),
        }

    @classmethod
    def from_dict(cls, d: dict | None) -> "Autoresponder":
        d = d or {}
        return cls(
            enabled=bool(d.get("enabled", False)),
            message=d.get("message", ""),
            has_photo=bool(d.get("has_photo", False)),
            buttons=_buttons_from_dicts(d.get("buttons")),
            active_from=parse_hhmm(d.get("active_from")),
            active_to=parse_hhmm(d.get("active_to")),
            exceptions=[int(x) for x in d.get("exceptions", [])],
        )


@dataclass
class Features:
    """Per-owner on/off switches for the userbot's automatic behaviors and
    typed commands. All default to True so existing users keep today's
    behavior unchanged."""

    deleted: bool = True        # capture deleted messages
    edited: bool = True         # capture edited messages
    info: bool = True           # .info
    me: bool = True             # .me
    ban: bool = True            # .ban
    check: bool = True          # .check
    viewonce_photo: bool = True     # capture one-time photos
    viewonce_voice: bool = True     # capture one-time voice messages
    viewonce_video: bool = True     # capture one-time videos and video notes
    send: bool = True               # .send — bulk-post N copies (Pro/Premium)
    mute: bool = True               # .mute / .unmute (Pro/Premium)
    record: bool = True             # .save / .unsave (Premium)

    # New fields default to True and `from_dict` fills anything absent, so a
    # settings row written before a switch existed keeps today's behaviour
    # rather than silently turning the feature off.
    _FIELDS = (
        "deleted", "edited", "info", "me", "ban", "check",
        "viewonce_photo", "viewonce_voice", "viewonce_video", "send", "mute", "record",
    )

    def to_dict(self) -> dict:
        return {name: getattr(self, name) for name in self._FIELDS}

    @classmethod
    def from_dict(cls, d: dict | None) -> "Features":
        d = d or {}
        return cls(**{name: bool(d.get(name, True)) for name in cls._FIELDS})


def _within_window(now_t: time, start: str, end: str) -> bool:
    s_h, s_m = map(int, start.split(":"))
    e_h, e_m = map(int, end.split(":"))
    start_t, end_t = time(s_h, s_m), time(e_h, e_m)
    if start_t == end_t:
        return True  # full-day window
    if start_t < end_t:
        return start_t <= now_t < end_t
    # window wraps past midnight, e.g. 22:00 -> 07:00
    return now_t >= start_t or now_t < end_t


def autoresponder_should_fire(
    config: Autoresponder | dict | None,
    *,
    now: datetime,
    sender_id: int,
    is_private: bool,
) -> bool:
    """Decide whether the autoresponder replies to this incoming message."""
    ar = config if isinstance(config, Autoresponder) else Autoresponder.from_dict(config)
    if not ar.enabled or not ar.message:
        return False
    if not is_private:  # autoresponder only replies in private chats
        return False
    if sender_id in ar.exceptions:
        return False
    if ar.active_from and ar.active_to:
        return _within_window(now.time(), ar.active_from, ar.active_to)
    return True
