"""Inline/reply keyboards for management_bot."""

from aiogram.types import (
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
)

from shared.i18n import t


def main_menu(lang: str = "uk") -> ReplyKeyboardMarkup:
    """Persistent reply keyboard. Account link/unlink lives inside Settings as an
    inline button, so it's intentionally not here."""
    return ReplyKeyboardMarkup(
        keyboard=[
            [KeyboardButton(text=t(lang, "btn_subscribe")), KeyboardButton(text=t(lang, "btn_settings"))],
            [KeyboardButton(text=t(lang, "btn_help")), KeyboardButton(text=t(lang, "btn_support"))],
            [KeyboardButton(text=t(lang, "btn_menu"))],
        ],
        resize_keyboard=True,
    )

CODE_LENGTH = 5

# callback_data scheme for the /connect code pad
CB_DIGIT = "cc:d:"   # + digit, e.g. "cc:d:7"
CB_BACKSPACE = "cc:bs"
CB_SUBMIT = "cc:ok"


def phone_request_keyboard(lang: str = "uk") -> ReplyKeyboardMarkup:
    """One-time reply keyboard with a `request_contact` button."""
    return ReplyKeyboardMarkup(
        keyboard=[[KeyboardButton(text=t(lang, "connect_phone_button"), request_contact=True)]],
        resize_keyboard=True,
        one_time_keyboard=True,
    )


def code_keyboard() -> InlineKeyboardMarkup:
    """Numeric pad (1-9, then ⬅️ / 0 / ✅) for entering the login code."""
    rows: list[list[InlineKeyboardButton]] = []
    for start in range(1, 10, 3):
        rows.append(
            [
                InlineKeyboardButton(text=str(n), callback_data=f"{CB_DIGIT}{n}")
                for n in range(start, start + 3)
            ]
        )
    rows.append(
        [
            InlineKeyboardButton(text="⬅️", callback_data=CB_BACKSPACE),
            InlineKeyboardButton(text="0", callback_data=f"{CB_DIGIT}0"),
            InlineKeyboardButton(text="✅", callback_data=CB_SUBMIT),
        ]
    )
    return InlineKeyboardMarkup(inline_keyboard=rows)


def masked_code(code: str) -> str:
    """`"12"` -> `"12___"` (never reveals length beyond CODE_LENGTH)."""
    filled = code[:CODE_LENGTH]
    return filled + "_" * (CODE_LENGTH - len(filled))
