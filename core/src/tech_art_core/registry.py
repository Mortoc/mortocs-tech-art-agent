from __future__ import annotations

import asyncio
import json
import uuid
from dataclasses import dataclass, field
from typing import Any, Protocol

from pydantic import ValidationError

from .bus import Bus
from .wire import (Call, ClientError, ClientInfo, FromCore, Hello, Msg, Pub, Rename, Result, Sub, Unsub, Welcome, dump,
                   to_core)

CLIENTS_TOPIC = "core.clients"
DEFAULT_CALL_TIMEOUT_S = 120.0


class ClientError_(RuntimeError):
    """A call into an app failed: the app said so, went away, or did not answer."""


class Socket(Protocol):
    async def send_text(self, data: str) -> None: ...
    async def receive_text(self) -> str: ...


@dataclass
class Connected:
    info: ClientInfo
    socket: Socket
    pending: dict[str, asyncio.Future[Any]] = field(default_factory=dict)


class ClientRegistry:
    """The apps connected right now: who, what tools, calls into them, and their pub/sub traffic."""

    def __init__(self, bus: Bus) -> None:
        self.bus = bus
        self._clients: dict[str, Connected] = {}

    def list(self) -> list[ClientInfo]:
        return [c.info for c in self._clients.values()]

    def get(self, client_id: str) -> ClientInfo | None:
        found = self._clients.get(client_id)
        return found.info if found else None

    def find(self, selector: str) -> ClientInfo | None:
        """By id, or `pid:<n>` (stable across a reconnect or a hot reload), else the first app whose host kind or name matches."""
        if selector.startswith("pid:"):
            return next((c for c in self.list() if f"pid:{c.pid}" == selector), None)
        return self.get(selector) or next((c for c in self.list() if selector in (c.host, c.name)), None)

    async def serve(self, socket: Socket) -> None:
        """Run one app's connection until it closes. The first message must be `hello`."""
        me: Connected | None = None
        subs: dict[str, Any] = {}
        outbox: asyncio.Queue[str] = asyncio.Queue()  # bus handlers are sync; the pump sends from the loop

        async def pump() -> None:
            while True:
                await socket.send_text(await outbox.get())

        pump_task = asyncio.get_running_loop().create_task(pump())

        def send(message: FromCore) -> None:
            outbox.put_nowait(dump(message))

        try:
            while True:
                try:
                    raw = await socket.receive_text()
                except Exception:
                    break
                try:
                    message = to_core.validate_python(json.loads(raw))
                except (ValueError, ValidationError) as error:
                    send(ClientError(message=f"bad message: {str(error)[:300]}"))
                    continue
                if isinstance(message, Hello):
                    if me:
                        send(ClientError(message="already said hello"))
                        continue
                    info = ClientInfo(client_id=str(uuid.uuid4()), host=message.host, name=message.name, pid=message.pid, tools=message.tools, meta=message.meta)
                    me = Connected(info, socket)
                    self._clients[info.client_id] = me
                    send(Welcome(client_id=info.client_id))
                    self.bus.publish(CLIENTS_TOPIC, {"event": "joined", "client": info.model_dump(by_alias=True)}, info.client_id)
                    continue
                if me is None:
                    send(ClientError(message="say hello first"))
                    continue
                cid = me.info.client_id
                if isinstance(message, Result):
                    future = me.pending.pop(message.id, None)
                    if future and not future.done():
                        if message.ok:
                            future.set_result(message.value)
                        else:
                            future.set_exception(ClientError_(message.error or "tool failed"))
                elif isinstance(message, Sub) and message.topic not in subs:
                    subs[message.topic] = self.bus.subscribe(
                        message.topic,
                        lambda topic, data, sender, cid=cid: sender != cid and send(Msg(topic=topic, from_=sender, data=data)),
                    )
                elif isinstance(message, Unsub):
                    off = subs.pop(message.topic, None)
                    if off:
                        off()
                elif isinstance(message, Rename):
                    me.info.name = message.name
                    self.bus.publish(CLIENTS_TOPIC, {"event": "renamed", "client": me.info.model_dump(by_alias=True)}, cid)
                elif isinstance(message, Pub):
                    self.bus.publish(message.topic, message.data, cid)
        finally:
            pump_task.cancel()
            for off in subs.values():
                off()
            if me:
                self._clients.pop(me.info.client_id, None)
                for future in me.pending.values():
                    if not future.done():
                        future.set_exception(ClientError_(f'{me.info.host} "{me.info.name}" disconnected during the call'))
                self.bus.publish(CLIENTS_TOPIC, {"event": "left", "client": me.info.model_dump(by_alias=True)}, me.info.client_id)

    async def call(self, client_id: str, tool: str, args: dict[str, Any] | None = None, timeout: float = DEFAULT_CALL_TIMEOUT_S) -> Any:
        target = self._clients.get(client_id)
        if target is None:
            raise ClientError_(f"no connected client {client_id}")
        if not any(t.name == tool for t in target.info.tools):
            raise ClientError_(f'{target.info.host} offers no tool "{tool}"')
        call_id = str(uuid.uuid4())
        future: asyncio.Future[Any] = asyncio.get_running_loop().create_future()
        target.pending[call_id] = future
        await target.socket.send_text(dump(Call(id=call_id, tool=tool, args=args or {})))
        try:
            return await asyncio.wait_for(future, timeout)
        except TimeoutError:
            raise ClientError_(f'{target.info.host} did not answer "{tool}" within {timeout:g} s; it may be busy (a modal dialog, a long operation).') from None
        finally:
            target.pending.pop(call_id, None)
