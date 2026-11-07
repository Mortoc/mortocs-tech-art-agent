"""The Houdini pane as the real Qt widget (offscreen, in hython) against a real core: panel interface, menu, QtWebEngine."""

import asyncio
import os
import shutil
import socket
import subprocess
from pathlib import Path

import pytest
import uvicorn

from tech_art_core.app import Core, create_app
from tech_art_core.settings import Settings

ROOT = Path(__file__).resolve().parents[2]
pytestmark = pytest.mark.skipif(shutil.which("hython") is None, reason="hython is not installed")


async def test_the_houdini_pane_renders_the_chat_page_and_connects(tmp_path):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    home = tmp_path / "home"
    core = Core(home, Settings(), catalog=lambda: asyncio.sleep(0, []))
    server = uvicorn.Server(uvicorn.Config(create_app(core, ROOT / "ui" / "dist"), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    env = {**os.environ, "TECH_ART_AGENT_HOME": str(home), "TECH_ART_AGENT_PORT": str(port), "HOUDINI_PI_BRIDGE_PORT": "8123"}
    try:
        proc = await asyncio.to_thread(
            subprocess.run, ["hython", str(Path(__file__).with_name("houdini_pane_driver.py")), str(ROOT)],
            env=env, capture_output=True, text=True, timeout=150,
        )
    finally:
        server.should_exit = True
        await serving
    lines = [line for line in proc.stdout.splitlines() if line.startswith(("PASS", "FAIL"))]
    failed = [line for line in lines if line.startswith("FAIL")]
    assert lines and not failed, "\n".join(lines) + "\n" + proc.stderr[-1500:]
    assert len(lines) >= 7, lines
