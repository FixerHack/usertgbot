"""Plan-aware /help and its all-functionality toggle.

Two failure modes are specific to this feature and invisible until a user in
that exact situation hits them: a view whose {placeholder} never got filled
(renders as literal `{manager}` to a paying client), and a view promising a
command the tariff doesn't actually grant.
"""

from __future__ import annotations

from contextlib import asynccontextmanager
from types import SimpleNamespace

import pytest

from db.models import Subscription, SubscriptionStatus
from management_bot.handlers import help as help_handlers
from management_bot.storage import upsert_user
from shared.i18n import t
from shared.tariffs import Tariff, get_plan, tariff_grants_command

LANGS = ("uk", "ru", "en")
VIEWS = ("help_all", "help_standard", "help_pro", "help_premium")


class _FakeMessage:
    def __init__(self, telegram_id: int) -> None:
        self.chat = SimpleNamespace(id=telegram_id)
        self.from_user = SimpleNamespace(id=telegram_id, language_code="uk")
        self.answered: list[dict] = []
        self.edits: list[dict] = []

    async def answer(self, text, **kwargs):
        self.answered.append({"text": text, **kwargs})

    async def edit_text(self, text, **kwargs):
        self.edits.append({"text": text, **kwargs})


class _FakeCallback:
    def __init__(self, message: _FakeMessage) -> None:
        self.message = message
        self.from_user = message.from_user
        self.answered = False

    async def answer(self, *a, **kw):
        self.answered = True


def _patch_db(monkeypatch, db_session):
    @asynccontextmanager
    async def fake_get_session():
        yield db_session

    monkeypatch.setattr(help_handlers, "get_session", fake_get_session)


async def _subscribed(db_session, telegram_id: int, tariff: str | None):
    user = await upsert_user(db_session, telegram_id, language_code="uk")
    if tariff:
        db_session.add(
            Subscription(
                user_id=user.id, tariff=tariff,
                status=SubscriptionStatus.ACTIVE, payment_provider="stub",
            )
        )
    await db_session.commit()


# --- rendering ---------------------------------------------------------------


@pytest.mark.parametrize("lang", LANGS)
@pytest.mark.parametrize("view", VIEWS)
def test_no_view_leaves_an_unfilled_placeholder(lang, view):
    rendered = help_handlers._render(lang, view)
    assert "{" not in rendered and "}" not in rendered, f"{view}/{lang} kept a placeholder"
    assert rendered.strip()


@pytest.mark.parametrize("view", VIEWS)
def test_every_view_is_actually_translated(view):
    uk, ru, en = (help_handlers._render(lang, view) for lang in LANGS)
    assert uk != en and ru != en, f"{view} looks untranslated"


def test_plan_views_do_not_promise_commands_the_plan_lacks():
    """Standard has no .send and no autoresponder — saying otherwise in its own
    help is worse than saying nothing, because the user will go try it."""
    standard = help_handlers._render("uk", "help_standard")
    assert not tariff_grants_command(Tariff.STANDARD, "send")
    body, _, not_included = standard.partition("Не входить у Standard")
    assert not_included, "the Standard view must spell out what it excludes"
    assert ".send" not in body, ".send is listed as if Standard had it"


def test_pro_view_states_the_real_send_limits():
    pro_plan = get_plan(Tariff.PRO)
    rendered = help_handlers._render("uk", "help_pro")
    assert str(pro_plan.send_max_count) in rendered
    assert str(pro_plan.send_cooldown_seconds // 60) in rendered
    assert str(pro_plan.check_quota) in rendered


def test_premium_view_does_not_promise_what_it_cannot_do():
    """Premium is grantable from the admin panel but `available=False` gates
    every command off, so its help must not read like a working plan."""
    assert not tariff_grants_command(Tariff.PREMIUM, "info")
    rendered = help_handlers._render("uk", "help_premium")
    assert "🚧" in rendered


# --- plan selection ----------------------------------------------------------


async def test_no_subscription_gets_the_full_view_and_no_toggle(db_session, monkeypatch):
    await _subscribed(db_session, 4101, None)
    _patch_db(monkeypatch, db_session)

    msg = _FakeMessage(4101)
    await help_handlers.cmd_help(msg)

    assert len(msg.answered) == 1
    assert msg.answered[0].get("reply_markup") is None, "nothing to toggle to"
    assert msg.answered[0]["text"] == help_handlers._render("uk", "help_all")


@pytest.mark.parametrize("tariff,view", [("standard", "help_standard"), ("pro", "help_pro")])
async def test_subscriber_gets_their_own_plan_view(db_session, monkeypatch, tariff, view):
    telegram_id = 4200 + len(tariff)
    await _subscribed(db_session, telegram_id, tariff)
    _patch_db(monkeypatch, db_session)

    msg = _FakeMessage(telegram_id)
    await help_handlers.cmd_help(msg)

    assert msg.answered[0]["text"] == help_handlers._render("uk", view)
    kb = msg.answered[0]["reply_markup"]
    assert kb.inline_keyboard[0][0].callback_data == help_handlers.CB_HELP_ALL


# --- the toggle --------------------------------------------------------------


async def test_toggle_swaps_the_two_views_in_place(db_session, monkeypatch):
    await _subscribed(db_session, 4301, "pro")
    _patch_db(monkeypatch, db_session)

    msg = _FakeMessage(4301)
    await help_handlers.on_show_all(_FakeCallback(msg))

    assert msg.edits[-1]["text"] == help_handlers._render("uk", "help_all")
    assert msg.edits[-1]["reply_markup"].inline_keyboard[0][0].callback_data == help_handlers.CB_HELP_MINE

    await help_handlers.on_show_mine(_FakeCallback(msg))

    assert msg.edits[-1]["text"] == help_handlers._render("uk", "help_pro")
    assert msg.edits[-1]["reply_markup"].inline_keyboard[0][0].callback_data == help_handlers.CB_HELP_ALL
    assert msg.answered == [], "the toggle edits in place, it must not post new messages"


async def test_toggle_back_survives_a_lapsed_subscription(db_session, monkeypatch):
    """The message can sit open for days; by then there may be no plan to go
    back to, and rendering an empty 'your plan' view would be nonsense."""
    await _subscribed(db_session, 4302, None)
    _patch_db(monkeypatch, db_session)

    msg = _FakeMessage(4302)
    await help_handlers.on_show_mine(_FakeCallback(msg))

    assert msg.edits[-1]["text"] == help_handlers._render("uk", "help_all")
    assert msg.edits[-1].get("reply_markup") is None


def test_toggle_labels_are_localized():
    for lang in LANGS:
        assert help_handlers._toggle_keyboard(lang, showing_all=False).inline_keyboard[0][0].text == t(
            lang, "help_btn_all"
        )
        assert help_handlers._toggle_keyboard(lang, showing_all=True).inline_keyboard[0][0].text == t(
            lang, "help_btn_mine"
        )
