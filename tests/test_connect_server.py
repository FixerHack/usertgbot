"""ConnectWebServer's origin selection — which URL the Mini App button gets.

The failure this guards against is silent and production-only: if the public
URL were ignored and ngrok opened instead, the button would still work in
testing but point at a throwaway tunnel that dies with the process.
"""

from __future__ import annotations

import pytest

from connect_web.server import ConnectWebServer


class _FakeUvicornServer:
    def __init__(self, config):
        self.config = config
        self.started = True

    async def serve(self):
        return None


@pytest.fixture
def server(monkeypatch):
    import connect_web.server as mod

    captured = {}

    class _Config:
        def __init__(self, app, host, port, log_level):
            captured["host"] = host
            captured["port"] = port

    monkeypatch.setattr(mod.uvicorn, "Config", _Config)
    monkeypatch.setattr(mod.uvicorn, "Server", _FakeUvicornServer)
    class _FakeApp:
        """Only needs to accept the /pay sub-app the server mounts on it."""

        def __init__(self):
            self.mounted = []

        def mount(self, path, app):
            self.mounted.append(path)

    monkeypatch.setattr(mod, "create_app", lambda **kw: _FakeApp())

    s = ConnectWebServer()
    s._captured = captured
    return s


async def _start(server, **kw):
    return await server.start(
        port=8081, bot_token="t", webapp_api_id=1, webapp_api_hash="h", **kw
    )


async def test_public_url_wins_and_opens_no_tunnel(server, monkeypatch):
    import connect_web.server as mod

    async def _boom(*a, **k):
        raise AssertionError("must not open a tunnel when a public URL is configured")

    monkeypatch.setattr(mod.ConnectWebServer, "_open_tunnel", _boom)

    url = await _start(server, public_url="https://moozuku.tech", ngrok_authtoken="tok")
    assert url == "https://moozuku.tech"


async def test_trailing_slash_is_trimmed(server):
    """The button URL is built as f"{base}/connect/{token}" — a trailing slash
    would produce a double slash and a 404."""
    url = await _start(server, public_url="https://moozuku.tech/")
    assert url == "https://moozuku.tech"


async def test_host_is_passed_through_to_uvicorn(server):
    """0.0.0.0 in a container, or a proxy on the host can never reach it."""
    await _start(server, public_url="https://x.test", host="0.0.0.0")
    assert server._captured["host"] == "0.0.0.0"


async def test_defaults_to_loopback(server):
    await _start(server, public_url="https://x.test")
    assert server._captured["host"] == "127.0.0.1"
