"""What the artist sees when the model server can't be reached — local or hosted — and that nothing needs the internet."""

import json

import httpx
import pytest
from pydantic_ai import exceptions as ai

from tech_art_core import wire
from tech_art_core.app import Core
from tech_art_core.errors import describe
from tech_art_core.hosts import BLENDER, HOUDINI
from tech_art_core.models import load_catalog, local_models
from tech_art_core.providers import OptionalToolset, build_model, mcp_toolsets
from tech_art_core.session import AppLink, Session
from tech_art_core.settings import Provider, Settings
from tech_art_core.store import SqliteConversationStore

LOCAL = "http://localhost:11434/v1"
HOSTED = "https://api.example.com/v1"


def chunk(delta, finish=None):
    body = {"id": "c", "object": "chat.completion.chunk", "created": 1, "model": "m", "choices": [{"index": 0, "delta": delta, "finish_reason": finish}]}
    return f"data: {json.dumps(body)}\n\n"


class Server:
    """A model server that can be switched off and on, speaking OpenAI's streaming chat API."""

    def __init__(self):
        self.up = True
        self.requests = 0
        self.error = httpx.ConnectError

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests += 1
        if not self.up:
            raise self.error("connection refused", request=request)
        sse = chunk({"role": "assistant", "content": "all good"}) + chunk({}, "stop") + "data: [DONE]\n\n"
        return httpx.Response(200, headers={"content-type": "text/event-stream"}, content=sse.encode())


async def make(connected, base_url, server, profile=BLENDER):
    connect, registry, tmp = connected
    app, info = await connect(host=profile.kind)
    settings = Settings(providers={"srv": Provider(baseUrl=base_url)})
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    events = []
    session = Session(link=AppLink(registry, info.client_id, profile), settings=settings, store=SqliteConversationStore(tmp / "c.sqlite"), assets_dir=tmp / "a",
                      emit=events.append, model_factory=lambda key: build_model(key, settings, http_client=client), model_key="srv/model")
    return app, session, events


def errors_of(events):
    return [e for e in events if isinstance(e, wire.ErrorOut)]


async def test_a_local_server_that_is_not_running_says_so_and_how_to_start_it(connected):
    server = Server()
    server.up = False
    app, session, events = await make(connected, LOCAL, server)
    session.prompt("add a cube")
    await session.wait()
    (error,) = errors_of(events)
    assert error.action == "unreachable"
    assert "local model server" in error.message and "localhost:11434" in error.message and "ollama serve" in error.message
    assert "internet" not in error.message  # a local server doesn't need it, so don't send them looking for it


async def test_a_hosted_provider_that_is_unreachable_blames_the_connection_and_offers_a_local_model(connected):
    server = Server()
    server.up = False
    app, session, events = await make(connected, HOSTED, server)
    session.prompt("add a cube")
    await session.wait()
    (error,) = errors_of(events)
    assert error.action == "unreachable"
    assert "api.example.com" in error.message and "internet connection" in error.message and "local model" in error.message


async def test_a_timeout_counts_as_unreachable_too(connected):
    server = Server()
    server.up, server.error = False, httpx.ConnectTimeout
    app, session, events = await make(connected, LOCAL, server)
    session.prompt("hi")
    await session.wait()
    assert errors_of(events)[0].action == "unreachable"


async def test_the_turn_still_closes_cleanly_and_the_next_message_works_once_the_server_is_back(connected):
    server = Server()
    server.up = False
    app, session, events = await make(connected, LOCAL, server)
    session.prompt("first")
    await session.wait()
    assert not session.is_streaming
    assert app.names().count("begin_turn") == 1 and app.names().count("end_turn") == 1  # the undo group was closed
    assert [e for e in events if isinstance(e, wire.SettledOut)] and not [e for e in events if isinstance(e, wire.SettledOut)][0].aborted
    assert "backup_scene" not in app.names()  # nothing was touched, so nothing needed saving

    server.up = True
    events.clear()
    session.prompt("second")
    await session.wait()
    assert not errors_of(events)
    assert any(isinstance(e, wire.DeltaOut) and "all good" in e.delta for e in events)


async def test_a_local_model_needs_no_internet_at_all(connected, monkeypatch):
    # Every internet probe says "down"; a turn against a local server must not care.
    monkeypatch.setattr("tech_art_core.errors.network_up", lambda *a, **k: False)
    app, session, events = await make(connected, LOCAL, Server())
    session.prompt("add a cube")
    await session.wait()
    assert not errors_of(events) and any(isinstance(e, wire.DeltaOut) for e in events)


def http_error(status):
    return ai.ModelHTTPError(status, "m", {"error": "x"})


def test_other_failures_are_explained_not_dumped():
    assert describe(http_error(401), provider="openrouter")[1] == "login" and "OPENROUTER_API_KEY" in describe(http_error(401), provider="openrouter")[0]
    assert "PROVIDER_API_KEY" not in describe(http_error(401))[0]  # provider unknown: say so, don't invent a variable name
    assert describe(http_error(429))[1] == "retry" and "rate limiting" in describe(http_error(429))[0]
    assert describe(http_error(503))[1] == "retry"
    message, action = describe(ai.ModelNotFoundError("some-model", "no such model"))
    assert action is None and "model menu" in message
    assert describe(ValueError("boom")) == ("boom", None)


def test_errors_buried_in_exception_groups_are_still_recognised():
    # anyio and the MCP client raise connection failures wrapped in (nested) ExceptionGroups.
    grouped = ExceptionGroup("tg", [ExceptionGroup("inner", [httpx.ConnectError("refused", request=httpx.Request("GET", "http://localhost:8123/mcp"))])])
    message, action = describe(grouped)
    assert action == "unreachable" and "localhost:8123" in message


async def test_a_tool_server_that_cannot_be_reached_is_skipped_with_a_notice_not_a_dead_turn(connected):
    from pydantic_ai import FunctionToolset

    class Broken(FunctionToolset):
        async def __aenter__(self):
            raise ExceptionGroup("connect", [httpx.ConnectError("refused")])

    notices = []
    wrapped = OptionalToolset.wrap(Broken(), "Notion", notices.append)
    connect, registry, tmp = connected
    app, info = await connect()
    events = []
    server = Server()
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    settings = Settings(providers={"srv": Provider(baseUrl=LOCAL)})
    session = Session(link=AppLink(registry, info.client_id, BLENDER), settings=settings, store=SqliteConversationStore(tmp / "c.sqlite"), assets_dir=tmp / "a",
                      emit=events.append, model_factory=lambda key: build_model(key, settings, http_client=client), model_key="srv/model",
                      toolsets=lambda notify: [OptionalToolset.wrap(Broken(), "Notion", notify)])
    session.prompt("hi")
    await session.wait()
    notes = [e.message for e in errors_of(events)]
    assert any("Notion isn't available" in n and "can't be reached" in n for n in notes)
    assert any(isinstance(e, wire.DeltaOut) for e in events)  # and the turn went on without it


def test_tool_servers_update_when_pypi_answers_and_use_their_cache_when_it_does_not():
    notes = []
    online = mcp_toolsets(HOUDINI, None, Settings(), notify=notes.append, online=lambda: True)
    assert notes == [] and online
    offline = mcp_toolsets(HOUDINI, None, Settings(), notify=notes.append, online=lambda: False)
    assert offline and "cached copy" in notes[0] and "PyPI" in notes[0]


async def test_the_model_menu_survives_a_dead_hosted_catalog_and_a_dead_local_server(tmp_path):
    def down(request):
        raise httpx.ConnectError("refused", request=request)

    def models_up(request):
        return httpx.Response(200, json={"data": [{"id": "llava"}, {"id": "gemma3"}]})

    cache = tmp_path / "models.json"
    dead = lambda: httpx.AsyncClient(transport=httpx.MockTransport(down))  # noqa: E731
    assert await load_catalog(cache, dead()) == []  # never fetched, can't fetch: empty, not an exception
    cache.write_text(json.dumps({"data": [{"id": "a/b", "name": "B", "architecture": {"input_modalities": ["image"]}}]}))
    import os, time

    os.utime(cache, (time.time() - 3 * 86400, time.time() - 3 * 86400))  # stale
    assert [m.id for m in await load_catalog(cache, dead())] == ["a/b"]  # the stale copy beats nothing

    providers = {"ollama": Provider(baseUrl="http://localhost:11434/v1"), "off": Provider(baseUrl="http://localhost:9/v1")}
    seen = await local_models(providers, httpx.AsyncClient(transport=httpx.MockTransport(lambda r: models_up(r) if r.url.port == 11434 else down(r))))
    assert [(m.provider, m.id) for m in seen] == [("ollama", "llava"), ("ollama", "gemma3")]  # the server that isn't running is just absent


async def test_background_housekeeping_stays_quiet_when_the_server_is_down(connected, tmp_path):
    from tech_art_core.memory import MemoryStore

    connect, registry, tmp = connected
    app, info = await connect(handlers={"memory_read": lambda **kw: {"text": "x" * 70000, "prev": None}})
    core = Core(tmp_path / "state", Settings(providers={"srv": Provider(baseUrl=LOCAL)}), catalog=lambda: __import__("asyncio").sleep(0, []))
    core.registry = registry
    server = Server()
    server.up = False
    client = httpx.AsyncClient(transport=httpx.MockTransport(server))
    core.model_factory = lambda key: build_model(key, core.settings, http_client=client)
    session = core.session_for(info.client_id, model_key="srv/model")
    emitted = []
    session.emit = emitted.append
    await core.after_turn(session, False)  # memory is long enough to need compacting, but the model is unreachable
    assert not [e for e in emitted if isinstance(e, wire.ErrorOut)]


async def test_a_default_model_whose_server_is_down_is_still_the_default_and_is_not_remembered(tmp_path):
    async def hosted():
        return [wire.ModelRef(provider="openrouter", id="vendor/some-model", name="Some", vision=True)]

    settings = Settings(providers={"local": Provider(baseUrl="http://127.0.0.1:1/v1")}, default_model=["local/llava"])
    core = Core(tmp_path, settings, catalog=hosted)
    await core.ensure_default_model()
    # The local server isn't answering. The artist must hear about *it*, not land on a hosted model that needs a key.
    assert core.new_session_model == "local/llava" and core._default_model is None

    core.settings.providers["local"].models = ["llava"]  # the server is back (listed without asking it)
    await core.ensure_default_model()
    assert core._default_model == "local/llava"


async def test_with_no_configured_default_the_first_available_model_is_used(tmp_path):
    async def hosted():
        return [wire.ModelRef(provider="openrouter", id="vendor/some-model", name="Some", vision=True)]

    core = Core(tmp_path, Settings(default_model=[]), catalog=hosted)
    await core.ensure_default_model()
    assert core.new_session_model == "openrouter/vendor/some-model"


def test_a_400_and_a_404_from_the_gateway_are_told_apart():
    plain400, _ = describe(ai.ModelHTTPError(400, "m", {"error": {"message": "Could not process image"}}), provider="gateway", model_key="gateway/openai/gpt-6.1-sol")
    assert "rejected the request (HTTP 400" in plain400 and "Could not process image" in plain400 and "pricing data" not in plain400
    plain404, _ = describe(ai.ModelHTTPError(404, "m", {}), provider="gateway", model_key="gateway/openai/gpt-9")
    assert "no pricing data" in plain404
    route = "gateway/chat@openrouter/deepseek/deepseek-v4.1-flash-1017"
    missing, _ = describe(ai.ModelHTTPError(404, "m", {}), provider="gateway", model_key=route)
    assert "no endpoint with the slug 'openrouter'" in missing and "pricing" not in missing
    priced, _ = describe(ai.ModelHTTPError(400, "m", {"error": "pricing data required"}), provider="gateway", model_key=route)
    assert "Require pricing data" in priced and "pricing data required" in priced
