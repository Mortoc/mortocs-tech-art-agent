import asyncio
import json
from pathlib import Path

import pytest

from tech_art_core.bus import Bus
from tech_art_core.registry import ClientRegistry


class FakeApp:
    """A connector: a socket that answers the core's calls from a table of handlers and records them."""

    def __init__(self, handlers=None):
        self.inbox: asyncio.Queue = asyncio.Queue()
        self.calls: list[tuple[str, dict]] = []
        self.handlers = {
            "scene_context": lambda **kw: {"document": "/s/car.blend", "current": "Scene", "selected": [], "extra": {}},
            "begin_turn": lambda **kw: {}, "end_turn": lambda **kw: {},
            "backup_scene": lambda **kw: {"path": "/tmp/backup.blend"},
            "memory_read": lambda **kw: {"text": "", "prev": None},
            "project_memory_read": lambda **kw: {"available": True, "text": "", "prev": None},
            "sessions_read": lambda **kw: {"sessions": []}, "session_tag": lambda **kw: {},
            "follow": lambda **kw: {}, "validate_paths": lambda paths, **kw: {"kinds": {p: "node" for p in paths}},
            **(handlers or {}),
        }

    async def send_text(self, data):
        message = json.loads(data)
        if message["t"] != "call":
            return
        self.calls.append((message["tool"], message["args"]))
        try:
            value = self.handlers[message["tool"]](**message["args"])
            self.say(t="result", id=message["id"], ok=True, value=value)
        except Exception as error:
            self.say(t="result", id=message["id"], ok=False, error=str(error))

    async def receive_text(self):
        item = await self.inbox.get()
        if item is None:
            raise ConnectionError("closed")
        return item

    def say(self, **message):
        self.inbox.put_nowait(json.dumps(message))

    def names(self):
        return [n for n, _ in self.calls]


@pytest.fixture
async def connected(tmp_path):
    registry = ClientRegistry(Bus())
    apps = []

    async def connect(host="blender", tools=None, handlers=None):
        app = FakeApp(handlers)
        task = asyncio.create_task(registry.serve(app))
        own = {t["name"] for t in tools or []}
        helpers = [{"name": n, "internal": True} for n in app.handlers if n not in own]
        app.say(t="hello", host=host, name="car.blend", tools=helpers + (tools or []))
        for _ in range(10):
            await asyncio.sleep(0)
        apps.append((app, task))
        return app, registry.find(host)

    yield connect, registry, tmp_path
    for app, task in apps:
        app.say(t="x") if False else app.inbox.put_nowait(None)
        await task
