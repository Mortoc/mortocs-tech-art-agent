import json

from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tech_art_core import wire
from tech_art_core.hosts import BLENDER
from tech_art_core.session import AppLink, Session
from tech_art_core.settings import Settings
from tech_art_core.store import SqliteConversationStore

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 16


def script(*steps):
    """A model that plays `steps` in order: ('tool', name, args) or ('text', str); one step per model request."""
    state = {"n": 0}

    async def stream(messages, info):
        step = steps[min(state["n"], len(steps) - 1)]
        state["n"] += 1
        if step[0] == "tool":
            yield {0: DeltaToolCall(name=step[1], json_args=json.dumps(step[2]))}
        else:
            yield step[1]

    return FunctionModel(stream_function=stream)


async def make(connected, model, tools, handlers=None):
    connect, registry, tmp = connected
    app, info = await connect(tools=tools, handlers=handlers)
    events = []
    session = Session(
        link=AppLink(registry, info.client_id, BLENDER), settings=Settings(), store=SqliteConversationStore(tmp / "c.sqlite"),
        assets_dir=tmp / "assets", emit=events.append, model_factory=lambda key: model,
    )
    return app, session, events


async def test_a_modifying_turn_backs_up_once_looks_and_one_undo_group(connected, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    tools = [{"name": "add_cube", "description": "add", "modifies": True}]
    app, session, events = await make(
        connected,
        script(("tool", "add_cube", {"name": "Cube"}), ("tool", "inspect_viewport", {}), ("text", "ok")),
        tools,
        {"add_cube": lambda **kw: {"created": "Cube"}, "capture_viewport": lambda **kw: {"path": str(shot)}},
    )
    session.prompt("add a cube")
    await session.wait()

    names = app.names()
    assert names[:3] == ["scene_context", "memory_read", "project_memory_read"] or "scene_context" in names
    assert names.index("begin_turn") < names.index("backup_scene") < names.index("add_cube") < names.index("capture_viewport") < names.index("end_turn")
    assert names.count("backup_scene") == 1
    types = [e.type for e in events]
    assert types[0] == "user_message" and "tool_start" in types and types.count("tool_end") == 2
    look = next(e for e in events if e.type == "tool_end" and e.tool_name == "inspect_viewport")
    assert look.image_url and look.image_url.startswith("/session-assets/")
    assert any(isinstance(e, wire.SettledOut) and not e.aborted for e in events)
    assert (tmp_path / "assets" / session.id).exists()
    assert (await session.store.load(session.id)) is not None
    assert [m.role for m in session.history()][:2] == ["user", "assistant"]


async def test_a_visible_change_with_no_look_is_nudged_to_look(connected, tmp_path):
    shot = tmp_path / "shot.png"
    shot.write_bytes(PNG)
    app, session, events = await make(
        connected,
        script(("tool", "add_cube", {}), ("text", "done"), ("tool", "inspect_viewport", {}), ("text", "ok")),
        [{"name": "add_cube", "modifies": True}],
        {"add_cube": lambda **kw: "ok", "capture_viewport": lambda **kw: {"path": str(shot)}},
    )
    session.prompt("add a cube")
    await session.wait()
    assert "capture_viewport" in app.names()


async def test_a_question_must_end_in_present_answer(connected):
    app, session, events = await make(
        connected,
        script(("text", "Use the bevel modifier."), ("tool", "present_answer", {"markdown": "1. Add [Cube](blender://select?path=Cube)"}), ("text", "")),
        [],
    )
    session.prompt("how do I bevel a cube?")
    await session.wait()
    ended = next(e for e in events if e.type == "tool_end" and e.tool_name == "present_answer")
    assert ended.answer and ended.answer.markdown.startswith("1. Add") and not ended.answer.invalid
    assert ("validate_paths", {"paths": ["Cube"]}) in app.calls
    assert "begin_turn" in app.names()  # and the turn was still closed
    assert app.names()[-2:] == ["end_turn", "session_tag"]  # closed, then recorded in the document


async def test_a_failing_tool_and_a_disconnect_are_reported_not_fatal(connected):
    app, session, events = await make(connected, script(("tool", "boom", {}), ("text", "sorry")), [{"name": "boom", "modifies": False}], {"boom": lambda **kw: 1 / 0})
    session.prompt("do it")
    await session.wait()
    assert next(e for e in events if e.type == "tool_end" and e.tool_name == "boom").is_error
    assert "end_turn" in app.names()


async def test_stopping_a_turn_still_closes_the_undo_group(connected):
    import asyncio

    async def stream(messages, info):
        await asyncio.sleep(30)
        yield "never"

    app, session, events = await make(connected, FunctionModel(stream_function=stream), [])
    session.prompt("slow")
    for _ in range(40):
        await asyncio.sleep(0.01)
    await session.abort()
    settled = [e for e in events if e.type == "settled"]
    assert settled and settled[0].aborted
    assert app.names()[-1] == "end_turn"
