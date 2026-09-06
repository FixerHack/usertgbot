"""`.mute` — who gets silenced, for how long, and what anti-delete does about it.

The interesting part is not the deleting. It is that muting someone and then
receiving every one of their messages back through anti-delete would be worse
than not muting them at all.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from db.models import Subscription, SubscriptionStatus
from management_bot import storage
from userbot.context import WorkerContext
from userbot.handlers import mute


class _Notifier:
    def __init__(self):
        self.sent: list[dict] = []

    async def send_text(self, chat_id, text, **kw):
        self.sent.append({"as": "text", "text": text, **kw})
        return True

    async def send_document(self, chat_id, document, **kw):
        self.sent.append({"as": "document", **kw})
        return True

    async def send_photo(self, chat_id, photo, *, caption="", **kw):
        self.sent.append({"as": "photo", "caption": caption, **kw})
        return True


class _Client:
    def __init__(self):
        self.handlers: dict[str, object] = {}
        self.notices: list[str] = []
        self.notice_deleted: list[int] = []

    def on(self, _event):
        def decorator(func):
            self.handlers[func.__name__] = func
            return func

        return decorator

    async def send_message(self, chat_id, text):
        self.notices.append(text)
        parent = self

        class _Sent:
            async def delete(self_inner):
                parent.notice_deleted.append(1)

        return _Sent()


@pytest.fixture
async def owner(db_session):
    user = await storage.upsert_user(db_session, 880001, username="owner")
    db_session.add(
        Subscription(
            user_id=user.id, tariff="pro", status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
    )
    await db_session.commit()
    return user


def _ctx(user_id: int, notifier=None) -> WorkerContext:
    return WorkerContext(
        session_id=1, owner_user_id=user_id, owner_telegram_id=880001,
        owner_lang="uk", notifier=notifier,
    )


def _patch(monkeypatch, module, db_session):
    @contextlib.asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(module, "get_session", fake_session)


async def _ref(client, entity_id, lang):
    return "@target"


def _command_event(*, minutes: str | None = None, chat_id: int = -100, private: bool = True, reply_to: int | None = None):
    responses: list[str] = []
    replies: list[str] = []

    async def respond(text, **kw):
        responses.append(text)

    async def reply(text, **kw):
        replies.append(text)

    async def get_reply_message():
        return SimpleNamespace(sender_id=reply_to)

    deleted: list[int] = []

    async def delete():
        deleted.append(1)

    event = SimpleNamespace(
        pattern_match=SimpleNamespace(group=lambda n: minutes),
        chat_id=chat_id,
        is_private=private,
        is_reply=reply_to is not None,
        get_reply_message=get_reply_message,
        respond=respond,
        reply=reply,
        delete=delete,
    )
    event.responses = responses
    event.replies = replies
    event.deleted = deleted
    return event


async def _noop_sleep(_seconds):
    return None


async def _run_command(db_session, owner, monkeypatch, name: str, event, notifier=None):
    from userbot.handlers import commands

    _patch(monkeypatch, mute, db_session)
    _patch(monkeypatch, commands, db_session)
    monkeypatch.setattr(mute.entities, "ref", _ref, raising=False)
    monkeypatch.setattr(commands.asyncio, "sleep", _noop_sleep)

    ctx = _ctx(owner.id, notifier)
    client = _Client()
    mute.register(client, ctx)
    await client.handlers[name](event)
    _run_command.last_client = client
    return ctx


# --- the commands ----------------------------------------------------------


async def test_mute_without_minutes_lasts_until_unmute(db_session, owner, monkeypatch):
    event = _command_event(chat_id=555)
    ctx = await _run_command(db_session, owner, monkeypatch, "handle_mute", event)

    assert ctx.muted == {(555, 555): None}
    assert ".unmute" in event.responses[0]


async def test_mute_with_minutes_expires_on_its_own(db_session, owner, monkeypatch):
    event = _command_event(minutes="180", chat_id=555)
    ctx = await _run_command(db_session, owner, monkeypatch, "handle_mute", event)

    until = ctx.muted[(555, 555)]
    assert until is not None
    assert timedelta(minutes=179) < (until - datetime.now(timezone.utc)) <= timedelta(minutes=180)
    assert "3 год" in event.responses[0]


async def test_a_group_mute_needs_a_reply_to_know_who(db_session, owner, monkeypatch):
    """Guessing in a group would silence whoever happened to be there."""
    event = _command_event(chat_id=-100, private=False)
    ctx = await _run_command(db_session, owner, monkeypatch, "handle_mute", event)

    assert ctx.muted == {}
    assert _run_command.last_client.notices, "the owner has to be told why nothing happened"


async def test_a_group_mute_targets_the_person_replied_to(db_session, owner, monkeypatch):
    event = _command_event(chat_id=-100, private=False, reply_to=4242)
    ctx = await _run_command(db_session, owner, monkeypatch, "handle_mute", event)

    assert ctx.muted == {(-100, 4242): None}


async def test_unmute_clears_it(db_session, owner, monkeypatch):
    await _run_command(db_session, owner, monkeypatch, "handle_mute", _command_event(chat_id=555))
    event = _command_event(chat_id=555)
    ctx = await _run_command(db_session, owner, monkeypatch, "handle_unmute", event)

    assert ctx.muted == {}
    assert event.responses


async def test_re_muting_replaces_the_deadline_instead_of_stacking(db_session, owner, monkeypatch):
    from db import queries

    await _run_command(db_session, owner, monkeypatch, "handle_mute", _command_event(minutes="60", chat_id=555))
    await _run_command(db_session, owner, monkeypatch, "handle_mute", _command_event(chat_id=555))

    rows = await queries.get_mutes(db_session, owner.id)
    assert len(rows) == 1 and rows[0].until is None


# --- the cache -------------------------------------------------------------


def test_an_expired_mute_stops_applying():
    ctx = _ctx(1)
    ctx.muted[(1, 2)] = datetime.now(timezone.utc) - timedelta(minutes=1)
    assert mute.is_muted(ctx, 1, 2) is False
    assert (1, 2) not in ctx.muted, "and is dropped so the clock is not re-read every message"


def test_a_forever_mute_has_no_clock():
    ctx = _ctx(1)
    ctx.muted[(1, 2)] = None
    assert mute.is_muted(ctx, 1, 2) is True


async def test_mutes_survive_a_restart(db_session, owner, monkeypatch):
    """The rows outlive the process; the cache does not. Without loading them
    back, a mute would quietly lapse on the next deploy."""
    await _run_command(db_session, owner, monkeypatch, "handle_mute", _command_event(chat_id=555))

    fresh = _ctx(owner.id)
    _patch(monkeypatch, mute, db_session)
    await mute.load_mutes(fresh)

    assert fresh.muted == {(555, 555): None}


# --- the part that matters -------------------------------------------------


async def test_a_muted_persons_message_is_deleted(db_session, owner, monkeypatch):
    deleted: list[int] = []

    async def _delete():
        deleted.append(1)

    ctx = _ctx(owner.id)
    ctx.muted[(555, 999)] = None
    client = _Client()
    mute.register(client, ctx)

    await client.handlers["drop_muted"](
        SimpleNamespace(sender_id=999, chat_id=555, delete=_delete)
    )
    assert deleted == [1]


async def test_anti_delete_does_not_hand_a_muted_person_back(db_session, owner, monkeypatch):
    """Muting someone and then receiving everything they write, because we
    deleted it ourselves, is worse than not muting them."""
    from userbot.handlers import autosave

    remembered: list[tuple] = []

    class _Cache:
        async def remember(self, **kw):
            remembered.append((kw.get("chat_id"), kw.get("sender_id")))

    ctx = _ctx(owner.id)
    ctx.cache = _Cache()
    ctx.muted[(555, 999)] = None

    client = _Client()
    autosave.register(client, ctx)

    await client.handlers["remember"](
        SimpleNamespace(
            sender_id=999, chat_id=555, id=1, raw_text="hi",
            photo=None, voice=None, video=None, video_note=None, sticker=None, file=None,
            geo=None, message=None,
        )
    )
    assert remembered == [], "nothing cached means nothing to restore"


async def test_the_command_takes_itself_off_the_screen(db_session, owner, monkeypatch):
    """Every other dot-command does. Leaving ".mute 2" in the chat clutters it
    and tells the other side exactly what was just done."""
    event = _command_event(minutes="2", chat_id=555)
    await _run_command(db_session, owner, monkeypatch, "handle_mute", event)
    assert event.deleted == [1]


async def test_unmute_clears_its_command_too(db_session, owner, monkeypatch):
    await _run_command(db_session, owner, monkeypatch, "handle_mute", _command_event(chat_id=555))
    event = _command_event(chat_id=555)
    await _run_command(db_session, owner, monkeypatch, "handle_unmute", event)
    assert event.deleted == [1]


async def test_a_command_that_could_not_run_still_clears_itself(db_session, owner, monkeypatch):
    """A failed .mute left on screen is the same leak of intent as a working
    one."""
    event = _command_event(chat_id=-100, private=False)
    await _run_command(db_session, owner, monkeypatch, "handle_mute", event)
    assert event.deleted == [1]


async def test_a_switched_off_mute_still_clears_its_command(db_session, owner, monkeypatch):
    """Doing nothing is not the same as leaving no trace: a bare `.mute` in a
    client's chat tells them what was attempted."""
    from db import queries
    from shared.settings_schema import Features

    features = Features(mute=False)
    await queries.set_features(db_session, owner.id, features.to_dict())
    await db_session.commit()

    event = _command_event(chat_id=555)
    ctx = await _run_command(db_session, owner, monkeypatch, "handle_mute", event)

    assert ctx.muted == {}, "switched off means it does not run"
    assert event.responses == [], "and says nothing"
    assert event.deleted == [1], "but still takes the command off the screen"


async def test_a_correction_takes_itself_off_the_screen(db_session, owner, monkeypatch):
    """It is a note to the owner, not something the other side needs left in
    the chat — the same treatment .send's limit notices get."""
    event = _command_event(chat_id=-100, private=False)
    await _run_command(db_session, owner, monkeypatch, "handle_mute", event)

    client = _run_command.last_client
    assert client.notices, "it is said"
    assert client.notice_deleted == [1], "and then cleared"
    assert event.deleted == [1], "as is the command that prompted it"
