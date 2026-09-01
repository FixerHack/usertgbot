"""Reply-keyboard button handlers (management_bot/handlers/menu.py).

Regression cover for a real bug: `on_menu` called `start.cmd_start(message)`
directly, but `cmd_start` had grown a required `command`/`state` argument
(for the connect-flow deep link) that a direct call never supplies. aiogram
swallows the resulting TypeError per-update, so from the user's side the
"🏠 Меню" button just silently did nothing — no error shown anywhere.
`show_dashboard` exists so this class of bug can't recur: it's the actual
handler body, callable with only a `Message`, no framework-injected args.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

from management_bot.handlers import menu, start
from management_bot.storage import upsert_user


class _FakeMessage:
    """Just enough of aiogram's Message for show_dashboard to run."""

    def __init__(self, telegram_id: int) -> None:
        self.chat = SimpleNamespace(id=telegram_id)
        self.from_user = SimpleNamespace(
            id=telegram_id, full_name="Tester", username="tester", language_code="uk"
        )
        self.answered: list[dict] = []

    async def answer(self, text, **kwargs):
        self.answered.append({"text": text, **kwargs})


async def test_menu_button_does_not_crash(db_session, monkeypatch):
    await upsert_user(db_session, 5001, username="tester")
    await db_session.commit()

    @asynccontextmanager
    async def fake_get_session():
        yield db_session

    monkeypatch.setattr(start, "get_session", fake_get_session)

    message = _FakeMessage(5001)
    await menu.on_menu(message)  # must not raise

    assert len(message.answered) == 1
    assert message.answered[0]["text"]  # dashboard text was actually sent


async def test_show_dashboard_needs_only_a_message(db_session, monkeypatch):
    """The actual signature check: show_dashboard must be callable with
    nothing but a Message — that's the whole point of the split."""
    import inspect

    sig = inspect.signature(start.show_dashboard)
    required = [
        p for p in sig.parameters.values()
        if p.default is inspect.Parameter.empty and p.kind != inspect.Parameter.VAR_KEYWORD
    ]
    assert [p.name for p in required] == ["message"]
