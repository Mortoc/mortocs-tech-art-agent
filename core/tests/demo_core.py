"""A real core + the built pane UI + a fake *local* model server the browser tests can switch off and on.

    uv run python tests/demo_core.py --port 9123 --token e2e

Control (for Playwright): POST http://127.0.0.1:<port+1>/llm/down /llm/up /core/down /core/up   — stops/starts the fake model server's
listening socket, so "down" is a genuine connection refused. A stand-in app (a stdlib connector with canned
helpers) registers as `pid:<this process>`, so the page URL is /?token=e2e&client=pid:<pid>.
"""

import argparse
import asyncio
import json
import os
import tempfile
from pathlib import Path

import uvicorn
from fastapi import FastAPI
from fastapi.responses import JSONResponse, StreamingResponse

from tech_art_agent.connector import Connector, Tool, core_url
from tech_art_core.app import Core, create_app
from tech_art_core.settings import Provider, ReviewSettings, Settings

ROOT = Path(__file__).resolve().parents[2]


CANNED_REVIEW = {
    "verdict": "check",
    "summary": "One request was ambiguous.",
    "findings": [{
        "kind": "ambiguity", "severity": "high", "turn": 1, "user_said": "add a cube",
        "agent_did": "Added a default 2 m cube at the origin.", "risk": "They may have wanted a different size or position.",
        "ask_instead": "How big should the cube be, and where should it go?",
    }],
}


def sse(delta, finish=None):
    body = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body)}\n\n"


def fake_llm() -> FastAPI:
    app = FastAPI()

    @app.get("/v1/models")
    async def models():
        return {"data": [{"id": "fake-vision"}]}

    @app.post("/v1/chat/completions")
    async def chat(request: dict):
        if "MISCOMMUNICATION REVIEW" in json.dumps(request.get("messages", [])):
            content = json.dumps(CANNED_REVIEW)  # the second-opinion brief: answer it with a fixed review
        else:
            content = "all good"
        if not request.get("stream"):
            return {"id": "c", "object": "chat.completion", "created": 1, "model": "m",
                    "choices": [{"index": 0, "message": {"role": "assistant", "content": content}, "finish_reason": "stop"}],
                    "usage": {"prompt_tokens": 10, "completion_tokens": 5, "total_tokens": 15}}

        async def stream():
            yield sse({"role": "assistant", "content": "all "})
            await asyncio.sleep(0.05)
            yield sse({"content": "good"})
            yield sse({}, "stop")
            yield "data: [DONE]\n\n"

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


HELPERS = {
    "scene_context": lambda: {"document": "/shots/car.blend", "current": "Scene", "selected": [], "extra": {}},
    "begin_turn": lambda label="": {}, "end_turn": lambda: {}, "undo_turn": lambda: {},
    "backup_scene": lambda: {"skipped": "demo"},
    "memory_read": lambda: {"text": "", "prev": None}, "memory_write": lambda text, prev: {},
    "project_memory_read": lambda: {"available": False, "text": "", "prev": None}, "project_memory_write": lambda text, prev: {},
    "sessions_read": lambda: {"sessions": []}, "session_tag": lambda path: {},
    "follow": lambda paths: {"categories": {}}, "validate_paths": lambda paths: {"kinds": {p: "node" for p in paths}}, "do_action": lambda kind, value: {},
}


async def main(port: int, token: str) -> None:
    llm_port, control_port = port + 2, port + 1
    home = Path(tempfile.mkdtemp(prefix="tech-art-demo-"))
    (home / "core.token").write_text(token)
    os.environ["TECH_ART_AGENT_HOME"] = str(home)
    settings = Settings(providers={"local": Provider(baseUrl=f"http://127.0.0.1:{llm_port}/v1")}, default_model=["local/fake-vision"], idle_timeout=0,
                        review=ReviewSettings(model="local/fake-vision"))
    static = ROOT / "ui" / "dist"
    running: dict[str, object] = {}
    state: dict[str, object] = {}

    async def core_up():
        """A fresh Core over the same state directory: what a restart is. Conversations come back from disk."""
        if "server" in running:
            return
        core = state["core"] = Core(home, settings, toolsets=lambda client_id: [])  # no real tool servers: hermetic  # the real model path: an OpenAI-compatible client talking real HTTP to the fake server
        server = uvicorn.Server(uvicorn.Config(create_app(core, static), host="127.0.0.1", port=port, log_level="warning"))
        running["server"], running["task"] = server, asyncio.create_task(server.serve())
        while not server.started:
            await asyncio.sleep(0.02)

    async def core_down():
        if "server" in running:
            running["server"].should_exit = True
            await running["task"]
            running.clear()

    llm: dict[str, object] = {}

    async def llm_up():
        if "server" in llm:
            return
        s = uvicorn.Server(uvicorn.Config(fake_llm(), host="127.0.0.1", port=llm_port, log_level="warning"))
        llm["server"], llm["task"] = s, asyncio.create_task(s.serve())
        while not s.started:
            await asyncio.sleep(0.02)

    async def llm_down():
        if "server" in llm:
            llm["server"].should_exit = True
            await llm["task"]
            llm.clear()

    control = FastAPI()
    control.post("/llm/up")(lambda: llm_up() or JSONResponse({"up": True}))
    control.post("/llm/down")(lambda: llm_down() or JSONResponse({"up": False}))

    async def up():
        await llm_up()
        return JSONResponse({"up": True})

    async def down():
        await llm_down()
        return JSONResponse({"up": False})

    async def core_start():
        await core_up()
        return JSONResponse({"up": True})

    async def core_stop():
        await core_down()
        return JSONResponse({"up": False})

    async def session_reset():
        """A fresh conversation for the next test, as if the artist had pressed 'new'."""
        core = state.get("core")
        if core is not None:
            core.sessions.clear()
            core.current.file.unlink(missing_ok=True)
        return JSONResponse({"reset": True})

    control.router.routes.clear()
    control.add_api_route("/session/reset", session_reset, methods=["POST"])
    control.add_api_route("/llm/up", up, methods=["POST"])
    control.add_api_route("/llm/down", down, methods=["POST"])
    control.add_api_route("/core/up", core_start, methods=["POST"])
    control.add_api_route("/core/down", core_stop, methods=["POST"])
    control_server = uvicorn.Server(uvicorn.Config(control, host="127.0.0.1", port=control_port, log_level="warning"))

    await llm_up()
    await core_up()
    connector = Connector(
        host="blender", name="car.blend", tools=[Tool(n, f, internal=True) for n, f in HELPERS.items()],
        url=lambda: core_url({"TECH_ART_AGENT_PORT": str(port)}, home),
    )
    connector.start()
    print(f"demo core on http://127.0.0.1:{port}/?token={token}&client=pid:{os.getpid()}", flush=True)
    await control_server.serve()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=9123)
    parser.add_argument("--token", default="e2e")
    args = parser.parse_args()
    asyncio.run(main(args.port, args.token))
