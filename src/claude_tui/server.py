"""aiohttp HTTP server receiving hook events from cc-hook.sh."""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Callable

from aiohttp import web

if TYPE_CHECKING:
    from claude_tui.sessions import SessionStore

PORT_FILE = Path.home() / ".claude" / "state" / "cc.port"


class HookServer:
    def __init__(self, store: SessionStore, on_event: Callable[[], None]) -> None:
        self._store = store
        self._on_event = on_event
        self._app = web.Application()
        self._app.router.add_post("/hook", self._handle_hook)
        self._runner: web.AppRunner | None = None
        self._port: int = 0

    @property
    def port(self) -> int:
        return self._port

    async def _handle_hook(self, request: web.Request) -> web.Response:
        try:
            data = await request.json()
        except Exception:
            return web.Response(status=400, text="invalid json")

        self._store.handle_hook_event(data)
        # Notify the app to refresh UI
        self._on_event()
        return web.Response(status=200, text="ok")

    async def start(self) -> int:
        """Start the server on a random port. Returns the port number."""
        self._runner = web.AppRunner(self._app)
        await self._runner.setup()
        site = web.TCPSite(self._runner, "127.0.0.1", 0)
        await site.start()
        # Extract the actual port
        assert self._runner.addresses
        self._port = self._runner.addresses[0][1]
        self._write_port_file()
        return self._port

    async def stop(self) -> None:
        """Stop the server and clean up the port file."""
        self._remove_port_file()
        if self._runner:
            await self._runner.cleanup()

    def _write_port_file(self) -> None:
        PORT_FILE.parent.mkdir(parents=True, exist_ok=True)
        PORT_FILE.write_text(str(self._port))

    def _remove_port_file(self) -> None:
        try:
            PORT_FILE.unlink(missing_ok=True)
        except OSError:
            pass
