"""Real Houdini (hython) running the real houdini_pi runtime, connected to a real core."""

import asyncio
import os
import shutil
import signal
import socket
import subprocess
from pathlib import Path

import pytest
import uvicorn

from tech_art_core.app import Core, create_app
from tech_art_core.hosts import HELPERS
from tech_art_core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("hython") is None, reason="hython is not installed")


async def until(predicate, seconds=60.0):
    end = asyncio.get_running_loop().time() + seconds
    while not predicate():
        assert asyncio.get_running_loop().time() < end, "timed out"
        await asyncio.sleep(0.05)


async def test_houdini_registers_its_helpers_work_and_it_survives_a_hot_reload(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    home = tmp_path / "home"
    core = Core(home, Settings())
    server = uvicorn.Server(uvicorn.Config(create_app(core), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    await until(lambda: server.started)

    env = {**os.environ, "TECH_ART_AGENT_HOME": str(home), "TECH_ART_AGENT_PORT": str(port), "HOUDINI_PI_BRIDGE_PORT": "8123", "HOUDINI_PI_SOFTWARE_RENDER": "0"}
    libs = str(ROOT / "houdini" / "python3.13libs")
    proc = subprocess.Popen(["hython", str(Path(__file__).with_name("houdini_driver.py")), libs, "60", "12"], env=env, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, start_new_session=True)
    try:
        await until(lambda: core.registry.list())
        info = core.registry.list()[0]
        assert info.host == "houdini" and info.name == "untitled.hip" and info.meta["bridgePort"] == 8123
        registered = {t.name for t in info.tools if t.internal}
        assert set(HELPERS) <= registered and {"render_cop", "capture_viewport"} <= registered
        pid = info.pid
        call = lambda tool, **kw: core.registry.call(info.client_id, tool, kw, timeout=40)  # noqa: E731

        ctx = await call("scene_context")
        assert ctx["document"] is None and [s["path"] for s in ctx["selected"]] == ["/obj/geo1/box1"] and "frame" in ctx["extra"]

        await call("memory_write", text="## Conventions\n\n- metres\n", prev=None)
        assert (await call("memory_read"))["text"].startswith("## Conventions")
        await call("session_tag", path="conv-1")
        assert (await call("sessions_read"))["sessions"] == ["conv-1"]
        assert (await call("validate_paths", paths=["/obj/geo1/box1", "/obj/geo1/box1/sizex", "/nope"]))["kinds"] == {"/obj/geo1/box1": "node", "/obj/geo1/box1/sizex": "parm", "/nope": None}
        followed = await call("follow", paths=["/obj/geo1/box1"])
        assert followed["followed"] == "/obj/geo1/box1" and followed["categories"] == {"/obj/geo1/box1": "Sop"}
        assert "skipped" in await call("backup_scene")  # an unsaved scene has nowhere to back up to

        await call("begin_turn", label="agent: test")
        await call("end_turn")
        await call("undo_turn")

        # Hot reload: the connector drops and comes back as the same process, so a pane keeps its conversation.
        first_id = info.client_id
        await until(lambda: core.registry.list() and core.registry.list()[0].client_id != first_id, seconds=60)
        again = core.registry.list()[0]
        assert again.pid == pid and core.registry.find(f"pid:{pid}").client_id == again.client_id
        assert (await core.registry.call(again.client_id, "memory_read", {}, timeout=40))["text"].startswith("## Conventions")
    finally:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)  # hython is a launcher script; stop the whole group
        output = proc.communicate(timeout=30)[0]
        server.should_exit = True
        await serving
    assert "Traceback" not in output, output[-1500:]
