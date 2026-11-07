import json

from fastapi.testclient import TestClient
from pydantic_ai.models.function import DeltaToolCall, FunctionModel

from tech_art_core.__main__ import inherited_fd
from tech_art_core.app import Core, create_app
from tech_art_core.hosts import HELPERS
from tech_art_core.settings import Settings


async def _no_catalog():
    return []


def model(*texts):
    state = {"n": 0}

    async def stream(messages, info):
        yield texts[min(state["n"], len(texts) - 1)]
        state["n"] += 1

    return FunctionModel(stream_function=stream)


def make_core(tmp_path, *texts):
    return Core(tmp_path, Settings(), model_factory=lambda key: model(*texts), toolsets=lambda cid: [], catalog=_no_catalog)


def connect_app(client, token, host="blender"):
    ws = client.websocket_connect(f"/clients?token={token}")
    sock = ws.__enter__()
    sock.send_text(json.dumps({"t": "hello", "host": host, "name": "car.blend", "tools": [{"name": n, "internal": True} for n in HELPERS]}))
    return ws, sock, sock.receive_json()["clientId"]


def serve_calls(sock, answers, stop="session_tag"):
    """Answer the core's calls until it has nothing more to ask (the test client is synchronous)."""
    while True:
        message = sock.receive_json()
        if message["t"] != "call":
            continue
        sock.send_text(json.dumps({"t": "result", "id": message["id"], "ok": True, "value": answers.get(message["tool"], {})}))
        if message["tool"] == stop:
            return


ANSWERS = {
    "scene_context": {"document": "/s/car.blend", "current": "Scene", "selected": [], "extra": {}},
    "sessions_read": {"sessions": []}, "memory_read": {"text": "", "prev": None}, "project_memory_read": {"available": False, "text": "", "prev": None},
}


def test_health_is_open_but_sockets_and_assets_need_the_token(tmp_path):
    core = make_core(tmp_path, "hi")
    client = TestClient(create_app(core))
    assert client.get("/health").json()["ok"] is True
    assert client.get("/apps").status_code == 401
    assert client.get("/session-assets/x/y.png").status_code == 401
    from starlette.websockets import WebSocketDisconnect
    import pytest
    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect("/clients?token=wrong"):
            pass
    assert (tmp_path / "core.token").stat().st_mode & 0o077 == 0


def test_a_pane_with_no_app_is_told_so(tmp_path):
    client = TestClient(create_app(make_core(tmp_path, "hi")))
    with client.websocket_connect(f"/ws?token={client.app.state.core.token}") as pane:
        assert "No app is connected" in pane.receive_json()["message"]


def test_a_prompt_from_the_pane_reaches_the_model_and_the_app_and_streams_back(tmp_path):
    core = make_core(tmp_path, "hello artist")
    with TestClient(create_app(core)) as client:
        _prompt_flow(client, core)


def _prompt_flow(client, core):
    ws, app_sock, cid = connect_app(client, core.token)
    with client.websocket_connect(f"/ws?token={core.token}") as pane:
        hello = pane.receive_json()
        assert hello["type"] == "hello" and hello["host"] == "blender" and hello["linkScheme"] == "blender" and hello["title"] == "Blender Agent"
        assert pane.receive_json()["type"] == "history"
        pane.send_text(json.dumps({"type": "prompt", "text": "hi"}))
        serve_calls(app_sock, ANSWERS)
        seen = []
        while not seen or seen[-1]["type"] != "settled":
            seen.append(pane.receive_json())
        assert [m["type"] for m in seen][:1] == ["user_message"]
        assert any(m["type"] == "text_delta" and "hello artist" in m["delta"] for m in seen)
        pane.send_text(json.dumps({"type": "list_sessions"}))
        serve_calls(app_sock, {"sessions_read": {"sessions": [hello["sessionId"]]}}, stop="sessions_read")
        sessions = next(m for m in iter(pane.receive_json, None) if m["type"] == "sessions")
        assert len(sessions["sessions"]) == 1
    ws.__exit__(None, None, None)


def test_systemd_hands_over_fd_3_only_to_the_process_it_was_meant_for():
    assert inherited_fd({"LISTEN_PID": "42", "LISTEN_FDS": "1"}, pid=42) == 3
    assert inherited_fd({"LISTEN_PID": "7", "LISTEN_FDS": "1"}, pid=42) is None
    assert inherited_fd({}, pid=42) is None


def test_the_core_is_idle_only_when_nothing_is_connected_or_running(tmp_path):
    core = make_core(tmp_path, "hi")
    with TestClient(create_app(core)) as client:
        assert core.idle_for() >= 0 and not core.busy()
        ws, sock, cid = connect_app(client, core.token)
        assert core.busy() and core.idle_for() == 0
        ws.__exit__(None, None, None)
