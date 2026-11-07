import asyncio
import json

import pytest

from tech_art_core.bus import Bus
from tech_art_core.registry import ClientError_, ClientRegistry


class FakeSocket:
    def __init__(self):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.sent: list[dict] = []

    async def send_text(self, data):
        self.sent.append(json.loads(data))

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    def say(self, **message):
        self.inbox.put_nowait(json.dumps(message))

    def close(self):
        self.inbox.put_nowait(None)


async def settle():
    for _ in range(8):
        await asyncio.sleep(0)


async def join(registry, host, tools=None):
    socket = FakeSocket()
    task = asyncio.create_task(registry.serve(socket))
    socket.say(t="hello", host=host, name=f"{host}-1", tools=tools or [{"name": "ping"}])
    await settle()
    return socket, task, socket.sent[0]["clientId"]


async def test_hello_registers_and_announces():
    bus = Bus()
    seen = []
    bus.subscribe("core.clients", lambda t, d, s: seen.append(d))
    registry = ClientRegistry(bus)
    socket, task, cid = await join(registry, "blender")
    assert [c.host for c in registry.list()] == ["blender"]
    assert registry.find("blender").client_id == cid
    assert seen[0]["event"] == "joined"
    socket.close()
    await task
    assert registry.list() == [] and seen[-1]["event"] == "left"


async def test_nothing_before_hello():
    registry = ClientRegistry(Bus())
    socket = FakeSocket()
    task = asyncio.create_task(registry.serve(socket))
    socket.say(t="pub", topic="x", data=1)
    await settle()
    assert socket.sent[0]["t"] == "error"
    socket.close()
    await task


async def test_call_resolves_errors_and_rejects_unknown_tools():
    registry = ClientRegistry(Bus())
    socket, task, cid = await join(registry, "houdini")
    pending = asyncio.create_task(registry.call(cid, "ping", {"n": 1}))
    await settle()
    call = next(m for m in socket.sent if m["t"] == "call")
    assert call["args"] == {"n": 1}
    socket.say(t="result", id=call["id"], ok=True, value="pong")
    assert await pending == "pong"

    failing = asyncio.create_task(registry.call(cid, "ping"))
    await settle()
    second = [m for m in socket.sent if m["t"] == "call"][-1]
    socket.say(t="result", id=second["id"], ok=False, error="boom")
    with pytest.raises(ClientError_, match="boom"):
        await failing
    with pytest.raises(ClientError_, match="offers no tool"):
        await registry.call(cid, "nope")
    socket.close()
    await task


async def test_disconnect_fails_inflight_calls_and_timeouts_name_the_app():
    registry = ClientRegistry(Bus())
    socket, task, cid = await join(registry, "game")
    inflight = asyncio.create_task(registry.call(cid, "ping"))
    await settle()
    socket.close()
    with pytest.raises(ClientError_, match="disconnected"):
        await inflight
    await task
    socket2, task2, cid2 = await join(registry, "blender")
    with pytest.raises(ClientError_, match="did not answer"):
        await registry.call(cid2, "ping", timeout=0.05)
    socket2.close()
    await task2


async def test_pubsub_between_apps_not_back_to_publisher():
    registry = ClientRegistry(Bus())
    a, ta, aid = await join(registry, "blender")
    b, tb, bid = await join(registry, "houdini")
    a.say(t="sub", topic="asset.*")
    b.say(t="sub", topic="asset.*")
    await settle()
    b.say(t="pub", topic="asset.exported", data={"path": "/x.usd"})
    await settle()
    got = [m for m in a.sent if m["t"] == "msg"]
    assert got == [{"t": "msg", "topic": "asset.exported", "from": bid, "data": {"path": "/x.usd"}}]
    assert not [m for m in b.sent if m["t"] == "msg"]
    a.say(t="unsub", topic="asset.*")
    await settle()
    b.say(t="pub", topic="asset.exported", data=2)
    await settle()
    assert len([m for m in a.sent if m["t"] == "msg"]) == 1
    a.close(); b.close()
    await asyncio.gather(ta, tb)


async def test_rename_keeps_the_connection_and_announces_it():
    bus = Bus()
    seen = []
    bus.subscribe("core.clients", lambda t, d, s: seen.append(d["event"]))
    registry = ClientRegistry(bus)
    socket, task, cid = await join(registry, "blender")
    socket.say(t="rename", name="car-v2.blend")
    await settle()
    assert registry.get(cid).name == "car-v2.blend" and seen[-1] == "renamed"
    socket.close()
    await task


async def test_an_app_is_found_by_its_process_id():
    registry = ClientRegistry(Bus())
    socket = FakeSocket()
    task = asyncio.create_task(registry.serve(socket))
    socket.say(t="hello", host="houdini", name="a.hip", pid=4242, tools=[])
    await settle()
    assert registry.find("pid:4242").host == "houdini" and registry.find("pid:1") is None
    socket.close()
    await task
