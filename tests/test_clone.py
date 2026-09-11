"""`.clone` — echoing someone back at themselves.

The behaviour is simple; the collisions are not. Cloning a muted person, or
cloning our own echo, are both ways to turn a joke into a loop.
"""

from __future__ import annotations

import contextlib
from datetime import datetime, timedelta, timezone
from types import SimpleNamespace

import pytest
from telethon import errors

from db.models import Subscription, SubscriptionStatus
from management_bot import storage
from userbot.context import WorkerContext
from userbot.handlers import clone


class _Client:
    def __init__(self, raises: Exception | None = None):
        self.handlers: dict[str, object] = {}
        self.sent: list[object] = []
        self._raises = raises
        self._first = True

    def on(self, _event):
        def decorator(func):
            self.handlers[func.__name__] = func
            return func

        return decorator

    async def send_message(self, chat_id, message):
        if self._raises is not None and self._first:
            self._first = False
            raise self._raises
        self.sent.append(message)
        return SimpleNamespace(delete=_noop)


async def _noop(*a, **kw):
    return None


@pytest.fixture
async def owner(db_session):
    user = await storage.upsert_user(db_session, 991001, username="owner")
    db_session.add(
        Subscription(
            user_id=user.id, tariff="premium", status=SubscriptionStatus.ACTIVE,
            payment_provider="stub",
            expires_at=datetime.now(timezone.utc).replace(tzinfo=None) + timedelta(days=30),
        )
    )
    await db_session.commit()
    return user


def _ctx(user_id: int) -> WorkerContext:
    return WorkerContext(
        session_id=1, owner_user_id=user_id, owner_telegram_id=991001, owner_lang="uk"
    )


async def _ref(client, entity_id, lang):
    return "@target"


def _setup(monkeypatch, db_session):
    from userbot.handlers import commands

    @contextlib.asynccontextmanager
    async def fake_session():
        yield db_session

    monkeypatch.setattr(clone, "get_session", fake_session)
    monkeypatch.setattr(commands, "get_session", fake_session)
    monkeypatch.setattr(commands.asyncio, "sleep", _noop)
    monkeypatch.setattr(clone.entities, "ref", _ref, raising=False)


def _command_event(*, chat_id: int = 555, private: bool = True, reply_to: int | None = None):
    deleted: list[int] = []
    responses: list[str] = []

    async def delete():
        deleted.append(1)

    async def respond(text, **kw):
        responses.append(text)

    async def get_reply_message():
        return SimpleNamespace(sender_id=reply_to)

    event = SimpleNamespace(
        chat_id=chat_id,
        is_private=private,
        is_reply=reply_to is not None,
        get_reply_message=get_reply_message,
        delete=delete,
        respond=respond,
        reply=_noop,
    )
    event.deleted = deleted
    event.responses = responses
    return event


def _incoming(text: str = "привіт", *, chat_id: int = 555, sender_id: int = 555):
    return SimpleNamespace(chat_id=chat_id, sender_id=sender_id, message=text)


async def _run(db_session, owner, monkeypatch, name, event, ctx=None, client=None):
    _setup(monkeypatch, db_session)
    ctx = ctx or _ctx(owner.id)
    client = client or _Client()
    clone.register(client, ctx)
    await client.handlers[name](event)
    _run.last_client = client
    return ctx


# --- starting and stopping -------------------------------------------------


async def test_clone_starts_and_clears_its_command(db_session, owner, monkeypatch):
    event = _command_event()
    ctx = await _run(db_session, owner, monkeypatch, "handle_clone", event)

    assert ctx.cloned == {(555, 555)}
    assert event.deleted == [1]
    assert event.responses


async def test_stopc_stops_it(db_session, owner, monkeypatch):
    ctx = await _run(db_session, owner, monkeypatch, "handle_clone", _command_event())
    await _run(db_session, owner, monkeypatch, "handle_stopc", _command_event(), ctx=ctx)

    assert ctx.cloned == set()


async def test_a_group_clone_needs_a_reply(db_session, owner, monkeypatch):
    ctx = await _run(db_session, owner, monkeypatch, "handle_clone", _command_event(chat_id=-100, private=False))
    assert ctx.cloned == set()


async def test_a_group_clone_targets_the_person_replied_to(db_session, owner, monkeypatch):
    ctx = await _run(
        db_session, owner, monkeypatch, "handle_clone",
        _command_event(chat_id=-100, private=False, reply_to=4242),
    )
    assert ctx.cloned == {(-100, 4242)}


async def test_a_clone_survives_a_restart(db_session, owner, monkeypatch):
    """`.clone` says "until .stopc", not "until the next deploy"."""
    await _run(db_session, owner, monkeypatch, "handle_clone", _command_event())

    fresh = _ctx(owner.id)
    _setup(monkeypatch, db_session)
    await clone.load_clones(fresh)

    assert fresh.cloned == {(555, 555)}


# --- the echo --------------------------------------------------------------


async def test_a_cloned_message_is_sent_back(db_session, owner, monkeypatch):
    await _run(db_session, owner, monkeypatch, "handle_clone", _command_event())
    client = _run.last_client

    await client.handlers["echo"](_incoming("привіт"))
    assert client.sent == ["привіт"]


async def test_nobody_else_is_echoed(db_session, owner, monkeypatch):
    await _run(db_session, owner, monkeypatch, "handle_clone", _command_event())
    client = _run.last_client

    await client.handlers["echo"](_incoming("привіт", sender_id=999))
    assert client.sent == []


async def test_a_muted_person_is_not_echoed(db_session, owner, monkeypatch):
    """Muting says "I do not want to see this"; cloning says "say it again".
    Repeating a message we are about to delete is nonsense."""
    ctx = await _run(db_session, owner, monkeypatch, "handle_clone", _command_event())
    client = _run.last_client
    ctx.muted[(555, 555)] = None

    await client.handlers["echo"](_incoming("привіт"))
    assert client.sent == []


async def test_a_rate_limit_stops_the_clone_instead_of_queueing(db_session, owner, monkeypatch):
    """Echoing doubles a chat's traffic, so this is the first thing to hit a
    limit — and a clone that keeps retrying into a flood wait makes it worse."""
    client = _Client(raises=errors.FloodWaitError(request=None))
    ctx = await _run(db_session, owner, monkeypatch, "handle_clone", _command_event(), client=client)

    await client.handlers["echo"](_incoming("привіт"))

    assert ctx.cloned == set(), "stopped, not retried"
    assert client.sent, "and the owner is told why"
