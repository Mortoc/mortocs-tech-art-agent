"""The real connector (stdlib client, in a thread) talking to a real core over a real socket."""

import asyncio
import socket

import uvicorn

from tech_art_agent.connector import Connector, Tool, core_url
from tech_art_core.app import Core, create_app
from tech_art_core.settings import Settings


def free_port():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


async def until(predicate, seconds=5.0):
    end = asyncio.get_running_loop().time() + seconds
    while not predicate():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.02)


async def test_connector_registers_answers_calls_and_pubsubs(tmp_path):
    port = free_port()
    core = Core(tmp_path, Settings())
    server = uvicorn.Server(uvicorn.Config(create_app(core), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    await until(lambda: server.started)

    seen = []
    ran_on = []
    connector = Connector(
        host="blender", name="car.blend", meta={"bridgePort": 9876},
        tools=[
            Tool("scene_context", lambda: {"document": "car.blend"}, internal=True),
            Tool("add_cube", lambda name="Cube": f"made {name}", description="add", modifies=True),
            Tool("explode", lambda: 1 / 0),
        ],
        run_on_main=lambda fn: (ran_on.append("main"), fn())[1],
        url=lambda: core_url({"TECH_ART_AGENT_PORT": str(port)}, tmp_path),
    )
    connector.start()
    try:
        await until(lambda: core.registry.list())
        info = core.registry.list()[0]
        assert (info.host, info.name, info.meta) == ("blender", "car.blend", {"bridgePort": 9876})
        assert {t.name for t in info.tools if not t.internal} == {"add_cube", "explode"}

        assert await core.registry.call(info.client_id, "add_cube", {"name": "Wheel"}) == "made Wheel"
        assert ran_on == ["main"]
        try:
            await core.registry.call(info.client_id, "explode")
            raise AssertionError("should have raised")
        except Exception as error:
            assert "ZeroDivisionError" in str(error)

        connector.subscribe("asset.*", lambda topic, sender, data: seen.append((topic, data)))
        await asyncio.sleep(0.2)
        core.bus.publish("asset.exported", {"path": "/x.usd"}, "houdini-id")
        await until(lambda: seen)
        assert seen == [("asset.exported", {"path": "/x.usd"})]
        connector.publish("scene.changed", {"n": 1})
        got = []
        core.bus.subscribe("scene.*", lambda t, d, s: got.append((t, d)))
        connector.publish("scene.changed", {"n": 2})
        await until(lambda: got)
        assert got[-1] == ("scene.changed", {"n": 2})
    finally:
        connector.stop()
        server.should_exit = True
        await serving


async def test_a_wrong_token_is_refused(tmp_path):
    port = free_port()
    core = Core(tmp_path, Settings())
    server = uvicorn.Server(uvicorn.Config(create_app(core), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    await until(lambda: server.started)
    errors = []
    connector = Connector(host="blender", name="x", tools=[], url=lambda: f"ws://127.0.0.1:{port}/clients?token=nope", log=errors.append)
    connector.start()
    try:
        await until(lambda: errors)
        assert "refused" in errors[0] or "closed" in errors[0]
        assert core.registry.list() == []
    finally:
        connector.stop()
        server.should_exit = True
        await serving
