import asyncio
import json

from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tech_art_core.app import Core
from tech_art_core.hosts import HELPERS, HOUDINI
from tech_art_core.session import AppLink, Session
from tech_art_core.settings import Settings
from tech_art_core.store import SqliteConversationStore

from test_session import script


async def _no_catalog():
    return []


async def houdini(connected, model, follow_category):
    connect, registry, tmp = connected
    app, info = await connect(
        host="houdini",
        tools=[{"name": "create_node", "modifies": True}, {"name": "set_parameter", "modifies": True}],
        handlers={
            "create_node": lambda **kw: {"created": "/obj/geo1/box1"},
            "set_parameter": lambda **kw: {"node_path": "/obj/copnet1/noise1"},
            "follow": lambda **kw: {"followed": "x", "categories": {p: follow_category for p in kw["paths"]}},
            "render_cop": lambda **kw: (_ for _ in ()).throw(RuntimeError("no gpu")),
        },
    )
    events = []
    session = Session(link=AppLink(registry, info.client_id, HOUDINI), settings=Settings(), store=SqliteConversationStore(tmp / "c.sqlite"),
                      assets_dir=tmp / "a", emit=events.append, model_factory=lambda k: model)
    return app, session, events


async def test_editing_sops_does_not_demand_a_cop_render(connected):
    app, session, events = await houdini(connected, script(("tool", "create_node", {"parent": "/obj/geo1"}), ("text", "done")), "Sop")
    session.prompt("add a box")
    await session.wait()
    assert "render_cop" not in app.names() and not [e for e in events if e.type == "tool_start" and e.tool_name == "inspect_cop"]


async def test_changing_a_cop_network_must_be_looked_at_before_finishing(connected):
    app, session, events = await houdini(
        connected, script(("tool", "set_parameter", {"node_path": "/obj/copnet1/noise1"}), ("text", "done"), ("tool", "inspect_cop", {"node_path": "/obj/copnet1/out"}), ("text", "ok")), "Cop")
    session.prompt("make the noise bigger")
    await session.wait()
    started = [e.tool_name for e in events if e.type == "tool_start"]
    assert started == ["set_parameter", "inspect_cop"]  # the nudge sent it back to look


async def test_a_restarted_core_resumes_the_conversation_the_app_was_on(connected, tmp_path):
    connect, registry, tmp = connected
    app, info = await connect(host="houdini")
    # registry.get(...).pid is what keys the conversation; the fixture's hello has none, so give it one.
    registry._clients[info.client_id].info.pid = 777
    first = Core(tmp_path / "state", Settings(), model_factory=lambda k: script(("text", "hello artist")), catalog=_no_catalog)
    first.registry = registry
    session = await first.resume_or_new(info.client_id)
    session.prompt("hi")
    await session.wait()
    original = session.id

    second = Core(tmp_path / "state", Settings(), model_factory=lambda k: script(("text", "again")), catalog=_no_catalog)
    second.registry = registry  # the app reconnected to the restarted core
    resumed = await second.resume_or_new(info.client_id)
    assert resumed.id == original
    assert [m.text for m in resumed.history() if m.role == "user"] == ["hi"]
