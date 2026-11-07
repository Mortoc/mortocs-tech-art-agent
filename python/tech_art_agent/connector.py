"""The app side of the core connection: register tools, answer calls, pub/sub.

Host-independent. A host (Houdini, Blender) supplies `run_on_main`, which runs a callable on the app's UI
thread and returns its result, because scene APIs are not thread-safe. Everything else lives here.
"""

from __future__ import annotations

import json
import os
import threading
import time
import traceback
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .paths import state_dir
from .wsclient import WebSocket, WebSocketError

DEFAULT_PORT = 9100
RECONNECT_MIN, RECONNECT_MAX = 1.0, 15.0


@dataclass
class Tool:
    name: str
    fn: Callable[..., Any]
    description: str = ""
    schema: dict[str, Any] = field(default_factory=lambda: {"type": "object", "properties": {}})
    # Calling it can change the scene (the core takes a backup first).
    modifies: bool = False
    # A lifecycle helper the core calls itself; never shown to the model.
    internal: bool = False

    def spec(self) -> dict[str, Any]:
        return {"name": self.name, "description": self.description, "schema": self.schema, "modifies": self.modifies, "internal": self.internal}


def core_url(env: dict[str, str] | None = None, directory: Path | None = None) -> str:
    """ws://127.0.0.1:<port>/clients?token=<token>. The token file is created by the core (or by `ensure_token`)."""
    env = {**os.environ, **(env or {})}  # overrides sit on top of the real environment, never replace it
    port = int(env.get("TECH_ART_AGENT_PORT", DEFAULT_PORT))
    return f"ws://127.0.0.1:{port}/clients?token={ensure_token(directory or state_dir(env))}"


def ensure_token(directory: Path) -> str:
    """Read the shared token, creating it (0600) if the core has never run. Same file the core uses."""
    file = directory / "core.token"
    try:
        return file.read_text().strip()
    except FileNotFoundError:
        import secrets

        directory.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
            with os.fdopen(fd, "w") as handle:
                handle.write(secrets.token_urlsafe(24))
        except FileExistsError:
            pass
        return file.read_text().strip()


class Connector:
    """Keeps this app connected to the core: reconnects with backoff, re-registers, and runs calls on the UI thread."""

    def __init__(
        self,
        *,
        host: str,
        name: Callable[[], str] | str,
        tools: list[Tool],
        meta: Callable[[], dict[str, Any]] | dict[str, Any] | None = None,
        run_on_main: Callable[[Callable[[], Any]], Any] = lambda fn: fn(),
        url: Callable[[], str] = core_url,
        wake: Callable[[], None] | None = None,
        ready: Callable[[], bool] = lambda: True,
        log: Callable[[str], None] = lambda message: None,
    ) -> None:
        self.host, self._name, self.tools = host, name, {t.name: t for t in tools}
        self._meta, self._run_on_main, self._url, self._wake, self._log = meta, run_on_main, url, wake, log
        self._ready = ready
        self.client_id: str | None = None
        self._ws: WebSocket | None = None
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._subs: dict[str, list[Callable[[str, str, Any], None]]] = {}

    # --- public --------------------------------------------------------------------------------

    @property
    def connected(self) -> bool:
        return self._ws is not None and self.client_id is not None

    def start(self) -> None:
        if self._thread and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(target=self._loop, name=f"tech-art-agent-{self.host}", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._ws:
            self._ws.close()

    def rename(self, name: str) -> None:
        """The document changed (Save As, open). Keeps the connection and the conversation."""
        self._send({"t": "rename", "name": name})

    def publish(self, topic: str, data: Any = None) -> None:
        self._send({"t": "pub", "topic": topic, "data": data})

    def subscribe(self, pattern: str, handler: Callable[[str, str, Any], None]) -> None:
        """`handler(topic, sender, data)` runs on the connector thread."""
        self._subs.setdefault(pattern, []).append(handler)
        self._send({"t": "sub", "topic": pattern})

    # --- internals -----------------------------------------------------------------------------

    def _send(self, message: dict[str, Any]) -> None:
        ws = self._ws
        if ws is None:
            return
        try:
            ws.send(json.dumps(message))
        except (OSError, WebSocketError):
            pass

    def _loop(self) -> None:
        delay = RECONNECT_MIN
        while not self._stop.is_set():
            try:
                self._session()
                delay = RECONNECT_MIN
            except (OSError, WebSocketError) as error:
                self._log(f"core connection: {error}")
                if self._wake:
                    try:
                        self._wake()
                    except Exception as wake_error:
                        self._log(f"could not start the core: {wake_error}")
            finally:
                self._ws, self.client_id = None, None
            self._stop.wait(delay)
            delay = min(delay * 2, RECONNECT_MAX)

    def _session(self) -> None:
        # Say hello only once the app can describe itself (Houdini: its MCP bridge has picked a port).
        waited = 0.0
        while not self._ready() and not self._stop.is_set() and waited < 90:
            self._stop.wait(0.5)
            waited += 0.5
        ws = WebSocket.connect(self._url())
        self._ws = ws
        name = self._name() if callable(self._name) else self._name
        meta = self._meta() if callable(self._meta) else (self._meta or {})
        ws.send(json.dumps({"t": "hello", "host": self.host, "name": name, "pid": os.getpid(), "tools": [t.spec() for t in self.tools.values()], "meta": meta}))
        while not self._stop.is_set():
            message = json.loads(ws.recv())
            kind = message.get("t")
            if kind == "welcome":
                self.client_id = message["clientId"]
                for pattern in self._subs:
                    self._send({"t": "sub", "topic": pattern})
            elif kind == "call":
                threading.Thread(target=self._answer, args=(message,), daemon=True).start()
            elif kind == "msg":
                for pattern, handlers in self._subs.items():
                    if _matches(pattern, message["topic"]):
                        for handler in handlers:
                            handler(message["topic"], message["from"], message.get("data"))
            elif kind == "error":
                self._log(f"core: {message.get('message')}")

    def _answer(self, call: dict[str, Any]) -> None:
        tool = self.tools.get(call["tool"])
        if tool is None:
            self._send({"t": "result", "id": call["id"], "ok": False, "error": f"no tool {call['tool']!r}"})
            return
        try:
            value = self._run_on_main(lambda: tool.fn(**call.get("args", {})))
            self._send({"t": "result", "id": call["id"], "ok": True, "value": value})
        except Exception as error:
            self._log(traceback.format_exc())
            self._send({"t": "result", "id": call["id"], "ok": False, "error": f"{type(error).__name__}: {error}"})


def _matches(pattern: str, topic: str) -> bool:
    return pattern in ("*", topic) or (pattern.endswith(".*") and topic.startswith(pattern[:-1]))
