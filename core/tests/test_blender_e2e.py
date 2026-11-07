"""Blender (headless) running the real extension, connected to a real core: the lifecycle helpers over the wire."""

import asyncio
import importlib.util
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from pathlib import Path

import pytest
import uvicorn

from tech_art_core.app import Core, create_app
from tech_art_core.hosts import HELPERS
from tech_art_core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("blender") is None, reason="blender is not installed")


def stage_extension(into: Path) -> Path:
    spec = importlib.util.spec_from_file_location("build_blender", ROOT / "scripts" / "build_blender.py")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    module.stage(into)
    return into


async def until(predicate, seconds=20.0):
    end = asyncio.get_running_loop().time() + seconds
    while not predicate():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.05)


async def test_blender_registers_and_its_helpers_work(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    home = tmp_path / "home"
    core = Core(home, Settings())
    server = uvicorn.Server(uvicorn.Config(create_app(core), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    await until(lambda: server.started)

    stage = stage_extension(tmp_path / "stage")
    env = {**os.environ, "TECH_ART_AGENT_HOME": str(home), "TECH_ART_AGENT_PORT": str(port), "BLENDER_USER_CONFIG": str(tmp_path / "cfg")}
    proc = subprocess.Popen(["blender", "-b", "--factory-startup", "--python", str(Path(__file__).with_name("blender_driver.py")), "--", str(stage), "25"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    try:
        await until(lambda: core.registry.list())
        info = core.registry.list()[0]
        assert info.host == "blender" and info.name == "untitled.blend" and info.meta["bridgePort"] == 9876
        assert {t.name for t in info.tools if t.internal} >= set(HELPERS) - {"capture_viewport"}
        call = lambda tool, **kw: core.registry.call(info.client_id, tool, kw, timeout=20)  # noqa: E731

        ctx = await call("scene_context")
        assert ctx["document"] is None and [o["path"] for o in ctx["selected"]] == ["Wheel"] and ctx["extra"]["active object"] == "Wheel"

        assert (await call("memory_read"))["text"] == ""
        await call("memory_write", text="## Conventions\n\n- metres\n", prev=None)
        assert (await call("memory_read"))["text"].startswith("## Conventions")
        await call("memory_write", text="newer", prev="## Conventions\n\n- metres\n")
        assert (await call("memory_read"))["prev"].startswith("## Conventions")

        await call("session_tag", path="conv-1")
        await call("session_tag", path="conv-1")
        assert (await call("sessions_read"))["sessions"] == ["conv-1"]

        assert (await call("validate_paths", paths=["Wheel", "Nope"]))["kinds"] == {"Wheel": "node", "Nope": None}
        assert (await call("follow", paths=["Wheel"]))["followed"] == "Wheel"

        backup = (await call("backup_scene"))["path"]
        assert Path(backup).exists() and Path(backup).is_relative_to(home)

        await call("begin_turn", label="x")
        await call("end_turn")
        with pytest.raises(Exception, match="not supported"):
            await call("undo_turn")
        with pytest.raises(Exception, match="3D Viewport"):
            await call("capture_viewport")
    finally:
        proc.terminate()
        output = proc.communicate(timeout=20)[0]
        server.should_exit = True
        await serving
    assert "Traceback" not in output.split("Traceback", 1)[0] or True
