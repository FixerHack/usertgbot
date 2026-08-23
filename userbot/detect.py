"""Detection helpers for special incoming messages (pure, unit-testable).

View-once ("one-time") photos/voice self-destruct after being opened, so the
userbot must capture them the moment they arrive. Telethon exposes a
`ttl_seconds` on the media for these; we treat any media carrying a ttl as
one-time.
"""

from __future__ import annotations


def is_view_once(has_media: bool, ttl_seconds: int | None) -> bool:
    return has_media and ttl_seconds is not None


def view_once_kind(is_photo: bool, is_voice: bool, is_video: bool) -> str:
    if is_photo:
        return "photo"
    if is_voice:
        return "voice"
    if is_video:
        return "video"
    return "media"
