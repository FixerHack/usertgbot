"""What actually reaches the owner when media is captured.

Both bugs here were the same shape: the file arrived and the "who sent this,
in which chat" line did not. A recovered sticker with no attribution is barely
better than no sticker at all — the whole point is knowing who deleted what.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone

import pytest

from db.models import Subscription, SubscriptionStatus
from management_bot import storage
from userbot import formatting
from userbot.context import WorkerContext


class _Notifier:
    """Records what the manager bot was asked to send, in order."""

    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, chat_id, text, **kw):
        self.sent.append({"as": "text", "text": text, **kw})
        return True

    async def send_photo(self, chat_id, photo, *, caption="", **kw):
        self.sent.append({"as": "photo", "caption": caption, "bytes": photo, **kw})
        return True

    async def send_voice(self, chat_id, voice, *, caption="", **kw):
        self.sent.append({"as": "voice", "caption": caption, "bytes": voice, **kw})
        return True

    async def send_video(self, chat_id, video, *, caption="", **kw):
        self.sent.append({"as": "video", "caption": caption, "bytes": video, **kw})
        return True

    async def send_document(self, chat_id, document, *, filename="file", caption="", **kw):
        self.sent.append({"as": "document", "caption": caption, "bytes": document, "filename": filename, **kw})
        return True

    async def send_location(self, chat_id, lat, lon, *, caption="", **kw):
        self.sent.append({"as": "location", "caption": caption, **kw})
        return True


@pytest.fixture
async def owner(db_session):
    """A subscribed owner, since every capture path is gated on that."""
    user = await storage.upsert_user(db_session, 777001, username="owner")
    db_session.add(
        Subscription(
            user_id=user.id,
            tariff="pro",
            status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
    )
    await db_session.commit()
    return user


def _ctx(user_id: int, notifier) -> WorkerContext:
    return WorkerContext(
        session_id=1, owner_user_id=user_id, owner_telegram_id=777001,
        owner_lang="uk", notifier=notifier,
    )


def _patch_lookups(monkeypatch, module, db_session):
    """Names and chat titles come from Telegram; the DB session comes from the
    fixture. Neither is what these tests are about."""

    @contextlib.asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(module, "get_session", fake_session)
    monkeypatch.setattr(module.entities, "resolve", _fake_resolve, raising=False)
    monkeypatch.setattr(module.entities, "ref", _fake_ref, raising=False)
    if hasattr(module.entities, "plain_name"):
        monkeypatch.setattr(module.entities, "plain_name", _fake_plain, raising=False)


async def _fake_resolve(client, entity_id, lang):
    return "@sender", False


async def _fake_ref(client, entity_id, lang):
    return "@somechat"


async def _fake_plain(client, entity_id):
    return "Some chat"


# --- deleted stickers ------------------------------------------------------


async def test_a_deleted_sticker_still_says_who_sent_it(db_session, owner, monkeypatch):
    """Telegram turns a .webp/.tgs/.webm document back into a sticker, and a
    sticker cannot carry a caption — so the attribution line was silently
    dropped and the owner got a bare image."""
    from userbot.handlers import autosave

    _patch_lookups(monkeypatch, autosave, db_session)
    notifier = _Notifier()

    await autosave._handle(
        client=None, ctx=_ctx(owner.id, notifier), chat_id=-100, message_id=5, sender_id=42,
        event_type="deleted", text="", previous_text=None,
        media=b"webp-bytes", media_kind="sticker", media_filename="pack.webp",
    )

    kinds = [m["as"] for m in notifier.sent]
    assert kinds == ["text", "document"], "the notice goes on its own, the file follows"

    notice = notifier.sent[0]["text"]
    assert "@sender" in notice and "@somechat" in notice
    assert notifier.sent[0]["reply_markup"] is not None, "the ignore button rides with the notice"
    assert notifier.sent[1]["bytes"] == b"webp-bytes"


async def test_a_deleted_sticker_with_no_text_is_called_a_sticker(db_session, owner, monkeypatch):
    """It used to fall through to the generic "медіа"."""
    from userbot.handlers import autosave

    _patch_lookups(monkeypatch, autosave, db_session)
    notifier = _Notifier()

    await autosave._handle(
        client=None, ctx=_ctx(owner.id, notifier), chat_id=-100, message_id=6, sender_id=42,
        event_type="deleted", text="", previous_text=None,
        media=b"x", media_kind="sticker",
    )

    assert formatting.kind_label("sticker", "uk") in notifier.sent[0]["text"]


async def test_captioned_media_still_arrives_as_one_message(db_session, owner, monkeypatch):
    """The two-message split is for stickers only — everything else keeps its
    caption and its button on the file itself."""
    from userbot.handlers import autosave

    _patch_lookups(monkeypatch, autosave, db_session)
    notifier = _Notifier()

    await autosave._handle(
        client=None, ctx=_ctx(owner.id, notifier), chat_id=-100, message_id=7, sender_id=42,
        event_type="deleted", text="", previous_text=None,
        media=b"jpeg", media_kind="photo",
    )

    assert [m["as"] for m in notifier.sent] == ["photo"]
    assert "@sender" in notifier.sent[0]["caption"]


# --- one-time videos -------------------------------------------------------


async def test_a_one_time_video_arrives_with_the_video(db_session, owner, monkeypatch):
    """The bytes were downloaded and then thrown away: the owner got a bare
    "Одноразове відео — Від: @x" header and nothing to watch."""
    from userbot.handlers import viewonce

    monkeypatch.setattr(viewonce.entities, "ref", _fake_ref, raising=False)
    notifier = _Notifier()

    class _Client:
        async def download_media(self, message, file=None):
            return b"mp4-bytes"

    class _Msg:
        pass

    class _Event:
        message = _Msg()
        sender_id = 42

    await viewonce._forward(_Client(), _Event(), _ctx(owner.id, notifier), "video")

    assert [m["as"] for m in notifier.sent] == ["video"]
    assert notifier.sent[0]["bytes"] == b"mp4-bytes"
    assert "@sender" in notifier.sent[0]["caption"] or "@somechat" in notifier.sent[0]["caption"]


async def test_a_one_time_video_we_could_not_download_still_warns(db_session, owner, monkeypatch):
    """Losing the file is bad; losing the fact that it existed is worse."""
    from userbot.handlers import viewonce

    monkeypatch.setattr(viewonce.entities, "ref", _fake_ref, raising=False)
    notifier = _Notifier()

    class _Client:
        async def download_media(self, message, file=None):
            return None

    class _Event:
        message = object()
        sender_id = 42

    await viewonce._forward(_Client(), _Event(), _ctx(owner.id, notifier), "video")

    assert [m["as"] for m in notifier.sent] == ["text"]
