"""Routes the persistent reply-keyboard buttons to the matching commands.

Reply-keyboard buttons arrive as plain text messages; since the labels are
localized, each handler matches the label in ANY language via i18n.variants().
"""

from aiogram import F, Router
from aiogram.types import Message

from management_bot.handlers import help as help_handlers, settings, start, subscribe
from shared.i18n import variants

router = Router(name="menu")


@router.message(F.text.in_(variants("btn_subscribe")))
async def on_subscribe(message: Message) -> None:
    await subscribe.cmd_subscribe(message)


@router.message(F.text.in_(variants("btn_settings")))
async def on_settings(message: Message) -> None:
    await settings.cmd_settings(message)


@router.message(F.text.in_(variants("btn_help")))
async def on_help(message: Message) -> None:
    await help_handlers.cmd_help(message)


@router.message(F.text.in_(variants("btn_support")))
async def on_support(message: Message) -> None:
    await help_handlers.cmd_support(message)


@router.message(F.text.in_(variants("btn_menu")))
async def on_menu(message: Message) -> None:
    await start.cmd_start(message)
