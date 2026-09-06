"""`.save` / `.unsave` — what ends up in the archive, and where it is sent.

A transcript is a record of a private conversation, so the two things worth
pinning down are what goes into it and where it comes out. It must not be
delivered into the chat it transcribes.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest

from db.models import Subscription, SubscriptionStatus
from management_bot import storage
from userbot.context import WorkerContext
from userbot.handlers import record


class _Notifier:
    def __init__(self):
        self.sent: list[dict] = []

    def _record(self, entry):
        self.sent.append(entry)
        return SimpleNamespace(message_id=1)

    async def send_text(self, chat_id, text, **kw):
        return self._record({"as": "text", "text": text, **kw})

    async def send_document(self, chat_id, document, *, filename="file", caption="", **kw):
        return self._record({"as": "document", "bytes": document, "filename": filename, "caption": caption})


class _Client:
    def __init__(self):
        self.handlers: dict[str, object] = {}
        self.notices: list[str] = []

    def on(self, _event):
        def decorator(func):
            self.handlers[func.__name__] = func
            return func

        return decorator

    async def send_message(self, chat_id, text):
        self.notices.append(text)
        return SimpleNamespace(delete=_noop)


async def _noop(*a, **kw):
    return None


@pytest.fixture
async def owner(db_session):
    user = await storage.upsert_user(db_session, 990001, username="owner")
    db_session.add(
        Subscription(
            user_id=user.id, tariff="premium", status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
    )
    await db_session.commit()
    return user


def _ctx(user_id: int, notifier=None) -> WorkerContext:
    return WorkerContext(
        session_id=1, owner_user_id=user_id, owner_telegram_id=990001,
        owner_lang="uk", notifier=notifier,
    )


async def _ref(client, entity_id, lang):
    return "@someone"


async def _plain(client, entity_id):
    return "Some chat"


def _setup(monkeypatch, db_session):
    from userbot.handlers import commands

    @contextlib.asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(record, "get_session", fake_session)
    monkeypatch.setattr(commands, "get_session", fake_session)
    monkeypatch.setattr(commands.asyncio, "sleep", _noop)
    monkeypatch.setattr(record.entities, "ref", _ref, raising=False)
    monkeypatch.setattr(record.entities, "plain_name", _plain, raising=False)


def _command_event(chat_id: int = 555):
    deleted: list[int] = []

    async def delete():
        deleted.append(1)

    event = SimpleNamespace(chat_id=chat_id, delete=delete, reply=_noop, respond=_noop)
    event.deleted = deleted
    return event


def _message_event(text: str, *, chat_id: int = 555, out: bool = False, sender_id: int = 42, when=None):
    return SimpleNamespace(
        chat_id=chat_id,
        raw_text=text,
        out=out,
        sender_id=sender_id,
        sticker=None, photo=None, voice=None, video=None, video_note=None, geo=None, file=None,
        message=SimpleNamespace(date=when or datetime(2026, 9, 6, 20, 0, tzinfo=timezone.utc)),
    )


async def _run(db_session, owner, monkeypatch, name, event, ctx=None, notifier=None):
    _setup(monkeypatch, db_session)
    ctx = ctx or _ctx(owner.id, notifier)
    client = _Client()
    record.register(client, ctx)
    await client.handlers[name](event)
    _run.last_client = client
    return ctx


# --- starting and stopping -------------------------------------------------


async def test_save_starts_a_recording_and_clears_its_command(db_session, owner, monkeypatch):
    event = _command_event()
    ctx = await _run(db_session, owner, monkeypatch, "handle_save", event)

    assert ctx.recording.get(555) is not None
    assert event.deleted == [1]
    assert _run.last_client.notices


async def test_saving_an_already_recording_chat_says_so(db_session, owner, monkeypatch):
    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event())
    before = dict(ctx.recording)
    await _run(db_session, owner, monkeypatch, "handle_save", _command_event(), ctx=ctx)

    assert ctx.recording == before, "no second recording of the same chat"


async def test_unsave_without_a_recording_says_so(db_session, owner, monkeypatch):
    ctx = await _run(db_session, owner, monkeypatch, "handle_unsave", _command_event())
    assert ctx.recording == {}
    assert _run.last_client.notices


# --- what goes in ----------------------------------------------------------


async def test_messages_from_both_sides_are_recorded(db_session, owner, monkeypatch):
    from db import queries

    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event())
    client = _run.last_client

    await client.handlers["collect"](_message_event("привіт"))
    await client.handlers["collect"](_message_event("вітаю", out=True))

    rows = await queries.get_recorded_messages(db_session, ctx.recording[555])
    assert [(r.text, r.is_outgoing) for r in rows] == [("привіт", False), ("вітаю", True)]


async def test_our_own_commands_stay_out_of_the_transcript(db_session, owner, monkeypatch):
    """`.save` is an instruction to the bot, not something anyone said."""
    from db import queries

    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event())
    client = _run.last_client

    await client.handlers["collect"](_message_event(".unsave", out=True))
    await client.handlers["collect"](_message_event("справжнє повідомлення", out=True))

    rows = await queries.get_recorded_messages(db_session, ctx.recording[555])
    assert [r.text for r in rows] == ["справжнє повідомлення"]


async def test_a_chat_that_is_not_recording_collects_nothing(db_session, owner, monkeypatch):
    from db import queries

    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event(chat_id=555))
    client = _run.last_client

    await client.handlers["collect"](_message_event("десь інде", chat_id=999))

    rows = await queries.get_recorded_messages(db_session, ctx.recording[555])
    assert rows == []


async def test_media_is_transcribed_as_a_word_not_stored(db_session, owner, monkeypatch):
    """The output is a .txt; keeping the blobs would make a chat archive an
    unbounded pile of files."""
    from db import queries
    from userbot import formatting

    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event())
    client = _run.last_client

    event = _message_event("")
    event.sticker = object()
    await client.handlers["collect"](event)

    rows = await queries.get_recorded_messages(db_session, ctx.recording[555])
    assert rows[0].text == formatting.kind_label("sticker", "uk")


# --- the file --------------------------------------------------------------


async def test_the_archive_goes_to_the_bot_not_into_the_chat(db_session, owner, monkeypatch):
    """Dropping a transcript of a conversation into that same conversation is
    the one delivery route that must never happen."""
    notifier = _Notifier()
    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event(), notifier=notifier)
    client = _run.last_client
    await client.handlers["collect"](_message_event("привіт"))

    await _run(db_session, owner, monkeypatch, "handle_unsave", _command_event(), ctx=ctx, notifier=notifier)

    assert [m["as"] for m in notifier.sent] == ["document"]
    assert notifier.sent[0]["filename"].endswith(".txt")
    assert ctx.recording == {}


async def test_the_archive_reads_as_a_transcript(db_session, owner, monkeypatch):
    from db import queries

    notifier = _Notifier()
    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event(), notifier=notifier)
    client = _run.last_client
    await client.handlers["collect"](_message_event("привіт"))
    await client.handlers["collect"](_message_event("вітаю", out=True))

    rows = await queries.get_recorded_messages(db_session, ctx.recording[555])
    text = record.build_archive("@someone", rows, "uk")

    lines = text.splitlines()
    assert lines[0].startswith("Архів чату:")
    assert "@someone: привіт" in lines[2]
    assert "Я: вітаю" in lines[3]
    # 20:00 UTC is 23:00 in Kyiv — the person reading this is not on UTC.
    assert "23:00:00" in lines[2]


async def test_an_unfinished_recording_survives_a_restart(db_session, owner, monkeypatch):
    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event())
    expected = dict(ctx.recording)

    fresh = _ctx(owner.id)
    _setup(monkeypatch, db_session)
    await record.load_recordings(fresh)

    assert fresh.recording == expected


async def test_a_stopped_recording_is_not_resumed(db_session, owner, monkeypatch):
    ctx = await _run(db_session, owner, monkeypatch, "handle_save", _command_event())
    await _run(db_session, owner, monkeypatch, "handle_unsave", _command_event(), ctx=ctx, notifier=_Notifier())

    fresh = _ctx(owner.id)
    _setup(monkeypatch, db_session)
    await record.load_recordings(fresh)

    assert fresh.recording == {}
