"""Launch the Mini App backend (uvicorn) + an on-demand ngrok tunnel.

Same pattern as admin_web/server.py — uvicorn as a task inside management_bot's
event loop, ngrok for a public HTTPS origin (Telegram refuses to open a Mini
App over plain http or a self-signed cert). This is the local-testing answer:
no VPS, no domain, no docker-compose changes — just the tool this project
already trusts for exactly this problem.

Idempotent: `start()` reuses the running instance if one already is, so every
handler that needs a Mini App link can just call it without tracking state
itself.
"""

from __future__ import annotations

import asyncio
import logging

import uvicorn

from connect_web.app import create_app
from landing.app import LegalDetails
from landing.app import create_app as create_landing_app
from management_bot.payment.wayforpay import WayForPayProvider
from pay_web.app import create_app as create_pay_app
from shared.notify import ManagerNotifier

logger = logging.getLogger(__name__)


class ConnectWebServer:
    def __init__(self) -> None:
        self._server: uvicorn.Server | None = None
        self._task: asyncio.Task | None = None
        self._listener = None  # ngrok.Listener, kept to close() on stop
        self.base_url: str | None = None

    def is_running(self) -> bool:
        return self._task is not None and not self._task.done()

    async def start(
        self, *, port: int, bot_token: str,
        webapp_api_id: int | None = None, webapp_api_hash: str | None = None,
        ngrok_authtoken: str | None = None, ngrok_domain: str | None = None,
        manager_bot_username: str | None = None,
        wayforpay: WayForPayProvider | None = None,
        bot_username: str | None = None,
        support_contact: str | None = None,
        legal: LegalDetails | None = None,
        public_url: str | None = None, host: str = "127.0.0.1",
    ) -> str:
        """`public_url` is the production path: a real domain already terminating
        TLS in front of us (Caddy), so no tunnel is opened at all — ngrok exists
        only to give local testing an HTTPS origin Telegram will accept.

        `host` must be 0.0.0.0 in a container: 127.0.0.1 there binds inside the
        container's own network namespace, where a reverse proxy on the host
        can never reach it. Locally it stays loopback so the dev machine isn't
        serving this to its whole network.
        """
        if self.is_running() and self.base_url:
            return self.base_url

        app = create_app(
            bot_token=bot_token, webapp_api_id=webapp_api_id, webapp_api_hash=webapp_api_hash,
            manager_bot_username=manager_bot_username,
        )
        # Card payments share this origin rather than opening a second port:
        # one domain to register with the acquirer, one thing for Caddy to
        # proxy. `base_url` is read lazily because it is only known once the
        # tunnel (or the configured public URL) below has resolved.
        app.mount(
            "/pay",
            create_pay_app(
                provider=wayforpay,
                base_url=lambda: self.base_url,
                # Only built when there is a gateway to report on: the
                # notifier opens a Bot API session, and an unconfigured
                # deployment has nothing to send through it.
                notifier=ManagerNotifier(bot_token) if wayforpay is not None else None,
                bot_username=bot_username,
            ),
        )
        # Mounted LAST and at the root, so it only ever sees paths no more
        # specific route claimed. Registration order is what keeps /connect
        # and /pay from being swallowed here.
        app.mount(
            "/",
            create_landing_app(
                bot_username=bot_username,
                support_contact=support_contact,
                legal=legal,
            ),
        )
        config = uvicorn.Config(app, host=host, port=port, log_level="warning")
        self._server = uvicorn.Server(config)
        self._server.install_signal_handlers = lambda: None  # not the main thread's job here
        self._task = asyncio.create_task(self._server.serve())

        for _ in range(50):
            if self._server.started:
                break
            await asyncio.sleep(0.1)

        base = f"http://{host}:{port}"
        if public_url:
            base = public_url.rstrip("/")
        elif ngrok_authtoken:
            base = await self._open_tunnel(port, ngrok_authtoken, ngrok_domain) or base
        else:
            logger.warning(
                "connect_web: neither CONNECT_PUBLIC_URL nor NGROK_AUTHTOKEN configured — "
                "serving %s, which Telegram will refuse to open (needs HTTPS)", base
            )

        self.base_url = base
        logger.info("connect_web started at %s", base)
        return base

    async def _open_tunnel(self, port: int, authtoken: str, domain: str | None) -> str | None:
        try:
            import ngrok

            # Without a reserved domain, ngrok's free tier hands out a random
            # subdomain on every call — a bot restart mid-login always strands
            # whatever URL the user's chat button or open tab still points at
            # (confirmed live: ERR_NGROK_3200 on the old link). A free static
            # domain, reserved once at dashboard.ngrok.com, keeps the same URL
            # across restarts and makes local testing behave like the eventual
            # stable prod domain will.
            kwargs = {"authtoken": authtoken, "proto": "http"}
            if domain:
                kwargs["domain"] = domain
            self._listener = await ngrok.forward(port, **kwargs)
            return self._listener.url()
        except Exception:
            logger.exception("connect_web: ngrok tunnel failed; falling back to localhost")
            self._listener = None
            return None

    async def stop(self) -> None:
        if self._listener is not None:
            try:
                await self._listener.close()
            except Exception:
                logger.debug("connect_web: ngrok listener close failed", exc_info=True)
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
        self.base_url = None


connect_server = ConnectWebServer()
