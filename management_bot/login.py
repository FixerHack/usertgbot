"""Telethon login state machine for the /connect flow.

Telegram blocks logging a code in that's been *sent inside Telegram*, which
breaks the naive "paste the code" UX for userbots. Instead we drive the login
from an inline number-pad: the user taps digits, we feed them to Telethon's
`sign_in`, then handle 2FA if needed.

A live `TelegramClient` can't be serialized into aiogram's FSM storage, so the
in-flight clients live in an in-process registry keyed by Telegram user id
(one login at a time per user). If the process restarts mid-login, the user
simply runs /connect again.

Telethon is reached only through a small duck-typed surface (connect,
send_code_request, sign_in, get_me, disconnect, session.save), so tests inject
a fake client via `client_factory` and never touch the network.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from telethon import TelegramClient, errors
from telethon.sessions import StringSession

from management_bot.config import settings
from management_bot.keyboards import CODE_LENGTH

logger = logging.getLogger(__name__)


class LoginError(Exception):
    """Base for recoverable login problems surfaced to the user."""


class CodeTooShort(LoginError):
    """Submit pressed before a full code was entered."""


class InvalidCode(LoginError):
    """Telegram rejected the entered code (wrong or expired)."""


class WrongPassword(LoginError):
    """Telegram rejected the 2FA password."""


class NoActiveLogin(LoginError):
    """A step was attempted with no login in progress for this user."""


@dataclass
class AccountInfo:
    user_id: int
    username: str | None
    first_name: str | None
    last_name: str | None

    @property
    def display_name(self) -> str:
        parts = [p for p in (self.first_name, self.last_name) if p]
        return " ".join(parts) or (f"@{self.username}" if self.username else str(self.user_id))


@dataclass
class LoginResult:
    status: str  # "success" | "needs_password"
    phone: str | None = None
    session_string: str | None = None
    account: AccountInfo | None = None


@dataclass
class _LoginState:
    client: object
    phone: str
    phone_code_hash: str
    code: str = field(default="")


def _default_client_factory() -> TelegramClient:
    return TelegramClient(StringSession(), settings.telegram_api_id, settings.telegram_api_hash)


class LoginManager:
    """Drives per-user Telethon logins for the /connect handler."""

    def __init__(self, client_factory=_default_client_factory) -> None:
        self._client_factory = client_factory
        self._states: dict[int, _LoginState] = {}

    # --- lifecycle ---------------------------------------------------------

    def has_login(self, user_id: int) -> bool:
        return user_id in self._states

    async def start(self, user_id: int, phone: str) -> None:
        """Create a client, request a login code for `phone`."""
        await self.cancel(user_id)  # drop any stale attempt first
        client = self._client_factory()
        await client.connect()
        try:
            sent = await client.send_code_request(phone)
        except Exception:
            await self._safe_disconnect(client)
            raise
        self._states[user_id] = _LoginState(client=client, phone=phone, phone_code_hash=sent.phone_code_hash)

    async def cancel(self, user_id: int) -> None:
        state = self._states.pop(user_id, None)
        if state is not None:
            await self._safe_disconnect(state.client)

    # --- code entry --------------------------------------------------------

    def push_digit(self, user_id: int, digit: str) -> str:
        state = self._require(user_id)
        if len(state.code) < CODE_LENGTH:
            state.code += digit
        return state.code

    def backspace(self, user_id: int) -> str:
        state = self._require(user_id)
        state.code = state.code[:-1]
        return state.code

    def current_code(self, user_id: int) -> str:
        return self._require(user_id).code

    # --- sign in -----------------------------------------------------------

    async def submit_code(self, user_id: int) -> LoginResult:
        state = self._require(user_id)
        if len(state.code) < CODE_LENGTH:
            raise CodeTooShort
        try:
            await state.client.sign_in(state.phone, state.code, phone_code_hash=state.phone_code_hash)
        except errors.SessionPasswordNeededError:
            return LoginResult(status="needs_password", phone=state.phone)
        except (errors.PhoneCodeInvalidError, errors.PhoneCodeExpiredError):
            state.code = ""
            raise InvalidCode
        except Exception:
            logger.exception("sign_in failed for user_id=%s", user_id)
            await self.cancel(user_id)
            raise LoginError
        return await self._finish(user_id, state)

    async def submit_password(self, user_id: int, password: str) -> LoginResult:
        state = self._require(user_id)
        try:
            await state.client.sign_in(password=password)
        except errors.PasswordHashInvalidError:
            raise WrongPassword
        except Exception:
            logger.exception("2FA sign_in failed for user_id=%s", user_id)
            await self.cancel(user_id)
            raise LoginError
        return await self._finish(user_id, state)

    # --- internals ---------------------------------------------------------

    async def _finish(self, user_id: int, state: _LoginState) -> LoginResult:
        me = await state.client.get_me()
        session_string = state.client.session.save()
        account = AccountInfo(
            user_id=me.id,
            username=getattr(me, "username", None),
            first_name=getattr(me, "first_name", None),
            last_name=getattr(me, "last_name", None),
        )
        await self._safe_disconnect(state.client)
        self._states.pop(user_id, None)
        return LoginResult(
            status="success",
            phone=state.phone,
            session_string=session_string,
            account=account,
        )

    def _require(self, user_id: int) -> _LoginState:
        state = self._states.get(user_id)
        if state is None:
            raise NoActiveLogin
        return state

    @staticmethod
    async def _safe_disconnect(client: object) -> None:
        try:
            await client.disconnect()
        except Exception:
            logger.debug("client disconnect failed", exc_info=True)


# App-wide singleton used by the handler; tests build their own.
login_manager = LoginManager()
