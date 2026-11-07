"""The agent's reach into the disk: only through tools, only to fixed places, only what an app hands over."""

import json
from pathlib import Path

import pytest
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tech_art_core.hosts import BLENDER
from tech_art_core.memory import FileMemoryStore
from tech_art_core.session import PNG_MAGIC, AppLink, Session, read_app_image
from tech_art_core.settings import Settings
from tech_art_core.store import SqliteConversationStore


def test_an_app_cannot_make_the_core_read_arbitrary_files(tmp_path, monkeypatch):
    monkeypatch.setenv("TECH_ART_AGENT_IMAGE_DIR", str(tmp_path))
    good = tmp_path / "a.png"
    good.write_bytes(PNG_MAGIC + b"data")
    assert read_app_image(str(good)).startswith(PNG_MAGIC)
    secret = Path.home() / ".ssh" / "id_ed25519"
    with pytest.raises(ValueError, match="temp directory"):
        read_app_image(str(secret))
    with pytest.raises(ValueError, match="temp directory"):
        read_app_image("/etc/passwd")
    with pytest.raises(ValueError, match="temp directory"):
        read_app_image(str(tmp_path / ".." / ".." / ".." / ".." / ".." / "etc" / "passwd"))
    fake = tmp_path / "b.png"
    fake.write_text("not a png")
    with pytest.raises(ValueError, match="not a PNG"):
        read_app_image(str(fake))
    assert good.exists()  # read-only: the core never deletes an app's file


async def test_user_memory_lives_in_one_fixed_file_and_keeps_one_rollback(tmp_path):
    memory = FileMemoryStore(tmp_path / "memory.md")
    await memory.append("Preferences", "- metric units")
    await memory.replace("replaced")
    assert (tmp_path / "memory.md").read_text() == "replaced"
    await memory.rollback()
    assert "metric units" in (tmp_path / "memory.md").read_text()
    assert sorted(p.name for p in tmp_path.iterdir()) == ["memory.md"]


async def test_the_model_can_store_a_user_memory_without_naming_a_path(connected, tmp_path):
    connect, registry, tmp = connected
    app, info = await connect(tools=[])
    steps = [("tool", "memory_append_user", {"section": "Habits", "text": "- prefers quads"}), ("text", "ok")]
    n = {"i": 0}

    async def stream(messages, i):
        step = steps[min(n["i"], 1)]
        n["i"] += 1
        yield {0: DeltaToolCall(name=step[1], json_args=json.dumps(step[2]))} if step[0] == "tool" else step[1]

    user = FileMemoryStore(tmp / "state" / "memory.md")
    session = Session(link=AppLink(registry, info.client_id, BLENDER), settings=Settings(), store=SqliteConversationStore(":memory:"), assets_dir=tmp / "a",
                      emit=lambda m: None, model_factory=lambda k: FunctionModel(stream_function=stream), user_memory=user)
    session.prompt("remember I like quads")
    await session.wait()
    assert "prefers quads" in (tmp / "state" / "memory.md").read_text()
    tool_names = {t.name for t in session._memory_tools()}
    assert {"memory_append_user", "memory_read_user"} <= tool_names
    # No file or shell tool exists anywhere in the agent's toolbox.
    toolbox = {t.name for t in [*session._app_tools(), *session._memory_tools(), session._answer_tool(), *session._bus_tools(), *[session._inspector(i) for i in BLENDER.inspectors]]}
    assert not {"bash", "shell", "read_file", "write_file", "edit", "write"} & toolbox
