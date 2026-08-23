"""LoginManager state machine, driven by a fake Telethon client (no network)."""

from types import SimpleNamespace

import pytest
from telethon import errors

from management_bot.login import (
    CodeTooShort,
    InvalidCode,
    LoginError,
    LoginManager,
    NoActiveLogin,
    WrongPassword,
)

USER_ID = 555


class FakeSession:
    def save(self) -> str:
        return "STRINGSESSION"


class FakeClient:
    """Duck-types the slice of TelegramClient that LoginManager touches."""

    def __init__(self, *, needs_2fa=False, bad_code=False, bad_password=False):
        self.session = FakeSession()
        self._needs_2fa = needs_2fa
        self._bad_code = bad_code
        self._bad_password = bad_password
        self._code_signed_in = False
        self.connected = False
        self.disconnected = False

    async def connect(self):
        self.connected = True

    async def send_code_request(self, phone, force_sms=False):
        return SimpleNamespace(phone_code_hash="HASH")

    async def sign_in(self, phone=None, code=None, *, phone_code_hash=None, password=None):
        if password is not None:
            if self._bad_password:
                raise errors.PasswordHashInvalidError(request=None)
            return _me()
        if self._bad_code:
            raise errors.PhoneCodeInvalidError(request=None)
        if self._needs_2fa and not self._code_signed_in:
            self._code_signed_in = True
            raise errors.SessionPasswordNeededError(request=None)
        return _me()

    async def get_me(self):
        return _me()

    async def disconnect(self):
        self.disconnected = True


def _me():
    return SimpleNamespace(id=999, username="bob", first_name="Bob", last_name="Ross", phone="15551234567")


def _manager(**client_kwargs) -> tuple[LoginManager, list[FakeClient]]:
    created: list[FakeClient] = []

    def factory():
        client = FakeClient(**client_kwargs)
        created.append(client)
        return client

    return LoginManager(client_factory=factory), created


async def _enter(manager: LoginManager, code: str) -> None:
    for digit in code:
        manager.push_digit(USER_ID, digit)


async def test_happy_path_no_2fa():
    manager, clients = _manager()
    await manager.start(USER_ID, "+15551234567")
    assert clients[0].connected

    await _enter(manager, "12345")
    result = await manager.submit_code(USER_ID)

    assert result.status == "success"
    assert result.session_string == "STRINGSESSION"
    assert result.phone == "+15551234567"
    assert result.account.username == "bob"
    assert result.account.display_name == "Bob Ross"
    assert clients[0].disconnected
    assert not manager.has_login(USER_ID)


async def test_2fa_flow():
    manager, clients = _manager(needs_2fa=True)
    await manager.start(USER_ID, "+15551234567")
    await _enter(manager, "12345")

    result = await manager.submit_code(USER_ID)
    assert result.status == "needs_password"
    assert manager.has_login(USER_ID)  # still mid-login

    final = await manager.submit_password(USER_ID, "hunter2")
    assert final.status == "success"
    assert final.session_string == "STRINGSESSION"
    assert not manager.has_login(USER_ID)


async def test_code_too_short():
    manager, _ = _manager()
    await manager.start(USER_ID, "+1")
    await _enter(manager, "12")
    with pytest.raises(CodeTooShort):
        await manager.submit_code(USER_ID)
    assert manager.has_login(USER_ID)  # user can keep typing


async def test_backspace_and_cap():
    manager, _ = _manager()
    await manager.start(USER_ID, "+1")
    await _enter(manager, "123456789")  # more than CODE_LENGTH
    assert manager.current_code(USER_ID) == "12345"
    manager.backspace(USER_ID)
    assert manager.current_code(USER_ID) == "1234"


async def test_invalid_code_resets_and_keeps_login():
    manager, _ = _manager(bad_code=True)
    await manager.start(USER_ID, "+1")
    await _enter(manager, "12345")
    with pytest.raises(InvalidCode):
        await manager.submit_code(USER_ID)
    assert manager.current_code(USER_ID) == ""  # cleared for retry
    assert manager.has_login(USER_ID)


async def test_wrong_password():
    manager, _ = _manager(needs_2fa=True, bad_password=True)
    await manager.start(USER_ID, "+1")
    await _enter(manager, "12345")
    await manager.submit_code(USER_ID)
    with pytest.raises(WrongPassword):
        await manager.submit_password(USER_ID, "nope")
    assert manager.has_login(USER_ID)  # retry allowed


async def test_step_without_active_login():
    manager, _ = _manager()
    with pytest.raises(NoActiveLogin):
        await manager.submit_code(USER_ID)


async def test_send_code_failure_cleans_up():
    class Boom(FakeClient):
        async def send_code_request(self, phone, force_sms=False):
            raise RuntimeError("network down")

    manager = LoginManager(client_factory=Boom)
    with pytest.raises(RuntimeError):
        await manager.start(USER_ID, "+1")
    assert not manager.has_login(USER_ID)
