"""Smoke test: every runtime module imports cleanly.

Catches Telethon/aiogram symbol-path mistakes in handler glue that the unit
tests (which avoid live clients) wouldn't otherwise exercise.
"""

import importlib

import pytest

MODULES = [
    "management_bot.main",
    "management_bot.handlers.start",
    "management_bot.handlers.subscribe",
    "management_bot.handlers.settings",
    "management_bot.handlers.connect",
    "management_bot.handlers.menu",
    "management_bot.handlers.help",
    "management_bot.handlers.admin",
    "management_bot.dashboard",
    "management_bot.subscriptions",
    "admin_web.app",
    "connect_web.app",
    "connect_web.server",
    "pay_web.app",
    "management_bot.payment.wayforpay",
    "admin_web.service",
    "admin_web.server",
    "userbot.main",
    "userbot.worker",
    "userbot.crypto",
    "userbot.notify",
    "userbot.detect",
    "userbot.entities",
    "userbot.handlers.commands",
    "userbot.handlers.autosave",
    "userbot.handlers.autoresponder",
    "userbot.handlers.viewonce",
    "manager_bot.main",
    "manager_bot.handlers",
    "userbot.message_cache",
    "shared.tariffs",
    "shared.settings_schema",
    "shared.notify",
    "shared.logging_conf",
    "shared.pricing",
    "shared.i18n",
    "db.queries",
]


@pytest.mark.parametrize("module", MODULES)
def test_module_imports(module):
    importlib.import_module(module)


def test_management_dispatcher_builds():
    from management_bot.main import build_dispatcher

    dp = build_dispatcher()
    assert {r.name for r in dp.sub_routers} == {
        "start",
        "subscribe",
        "settings",
        "connect",
        "help",
        "admin",
        "menu",
    }
