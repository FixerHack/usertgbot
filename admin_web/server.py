"""Launch/stop the admin panel (uvicorn) + an on-demand ngrok tunnel.

Runs uvicorn as a task inside the management bot's event loop. If an ngrok
authtoken is configured, a public tunnel is opened; otherwise the local URL is
returned (usable on the same machine).

Uses the official `ngrok` Python SDK (PyPI: "ngrok"), not `pyngrok` — that
package shells out to a separately-downloaded ngrok.exe, which Windows
Defender routinely flags/quarantines as an unrecognized binary. The official
SDK embeds the agent as a compiled extension inside the wheel, so nothing is
downloaded or executed as a standalone process at runtime.
"""

from __future__ import annotations

import asyncio
import logging
import secrets

import uvicorn

from admin_web.app import create_app

logger = logging.getLogger(__name__)


class AdminServer:
    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._listener = None  # ngrok.Listener, kept to close() on stop
        self.token: str | None = None
        self.url: str | None = None

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(self, *, port: int, ngrok_authtoken: str | None = None, bot_username: str | None = None) -> tuple[str, str]:
        if self.is_running() and self.url:
            return self.url, self.token or ""

        self.token = secrets.token_urlsafe(16)
        app = create_app(self.token, bot_username=bot_username)
        config = uvicorn.Config(app, host="127.0.0.1", port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # not the main thread's job here
        self._task = asyncio.create_task(self._server.serve())

        for _ in range(50):
            if self._server.started:
                break
            await asyncio.sleep(0.1)

        base = f"http://127.0.0.1:{port}"
        if ngrok_authtoken:
            base = await self._open_tunnel(port, ngrok_authtoken) or base

        self.url = f"{base}/?token={self.token}"
        logger.info("admin panel started at %s", base)
        return self.url, self.token

    async def _open_tunnel(self, port: int, authtoken: str) -> str | None:
        try:
            import ngrok

            self._listener = await ngrok.forward(port, authtoken=authtoken, proto="http")
            return self._listener.url()
        except Exception:
            logger.exception("ngrok tunnel failed; falling back to localhost")
            self._listener = None
            return None

    async def stop(self) -> None:
        if self._listener is not None:
            try:
                await self._listener.close()
            except Exception:
                logger.debug("ngrok listener close failed", exc_info=True)
            self._listener = None

        if self._server is not None:
            self._server.should_exit = True
        if self._task is not None:
            try:
                await asyncio.wait_for(self._task, timeout=5)
            except Exception:
                self._task.cancel()
        self._task = None
        self._server = None
        self.url = None


admin_server = AdminServer()
