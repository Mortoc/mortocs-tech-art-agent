"""Pydantic AI Gateway: model strings, the menu, credentials, error wording, and a full turn through a fake gateway."""

import asyncio
import json
import socket
from decimal import Decimal

import pytest
import uvicorn
from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse
from pydantic_ai import exceptions as ai
from pydantic_ai.messages import ModelResponse
from pydantic_ai.usage import RequestUsage

from tech_art_core import wire
from tech_art_core.app import Core
from tech_art_core.errors import describe, key_env
from tech_art_core.hosts import BLENDER
from tech_art_core.models import gateway_models, has_gateway_key
from tech_art_core.providers import build_model
from tech_art_core.session import AppLink, Session, _cost_of
from tech_art_core.settings import Settings
from tech_art_core.store import SqliteConversationStore

# Built at runtime: a literal key-shaped string would trip the repo's own secret scanner.
FAKE_KEY = "pylf_v1_us_" + "x" * 24


def test_our_model_keys_map_onto_pydantic_ais_gateway_strings():
    s = Settings()
    assert build_model("gateway/anthropic/claude-sonnet-5-5", s) == "gateway/anthropic:claude-sonnet-5-5"
    assert build_model("gateway/openai/gpt-6.1-sol", s) == "gateway/openai:gpt-6.1-sol"
    assert build_model("gateway/groq/openai/gpt-oss-120b", s) == "gateway/groq:openai/gpt-oss-120b"  # slashes in the model name survive
    assert build_model("openrouter/deepseek/x", s) == "openrouter:deepseek/x"  # others unchanged


def test_the_key_chooses_the_region_and_the_route(monkeypatch):
    from pydantic_ai.models import infer_model

    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", FAKE_KEY)
    model = infer_model(build_model("gateway/anthropic/claude-sonnet-5-5", Settings()))
    assert str(model.client.base_url) == "https://gateway-us.pydantic.dev/proxy/anthropic/"


def test_the_menu_offers_gateway_models_only_when_there_is_a_gateway_key():
    gateway = Settings().gateway
    assert gateway_models(gateway, {}) == []
    assert not has_gateway_key({})
    listed = gateway_models(gateway, {"PYDANTIC_AI_GATEWAY_API_KEY": FAKE_KEY})
    assert [(m.provider, m.id) for m in listed][0] == ("gateway", "anthropic/claude-haiku-5-5")  # Haiku first, for now
    from tech_art_core.review import family_of

    assert {family_of(f"gateway/{m.id}") for m in listed} == {"openai", "anthropic"}  # two families on offer, so the reviewer is always cross-family
    assert all(m.vision for m in listed)


async def test_the_core_lists_only_what_this_machine_can_use(tmp_path, monkeypatch):
    for name in ("PYDANTIC_AI_GATEWAY_API_KEY", "PAIG_API_KEY", "OPENROUTER_API_KEY"):
        monkeypatch.delenv(name, raising=False)
    core = Core(tmp_path, Settings())
    assert await core.all_models() == []  # no keys, no local servers: nothing to offer (and no network call to find out)
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", FAKE_KEY)
    assert {m.provider for m in await core.all_models()} == {"gateway"}
    await core.ensure_default_model()
    assert core.new_session_model == "gateway/anthropic/claude-haiku-5-5"  # everything runs on Haiku for now


def test_missing_and_rejected_keys_name_the_right_variable():
    missing = ai.UserError("Set the `PYDANTIC_AI_GATEWAY_API_KEY` environment variable or pass it via `gateway_provider(..., api_key=...)`")
    message, action = describe(missing, provider="gateway")
    assert action == "login" and "PYDANTIC_AI_GATEWAY_API_KEY" in message and "Logfire" in message
    message, action = describe(ai.UserError("Set the `OPENROUTER_API_KEY` environment variable"), provider="openrouter")
    assert action == "login" and "OPENROUTER_API_KEY" in message and "Logfire" not in message
    rejected, action = describe(ai.ModelHTTPError(401, "m", {}), provider="gateway")
    assert action == "login" and "PYDANTIC_AI_GATEWAY_API_KEY" in rejected
    assert key_env("some-new-provider") == "SOME_NEW_PROVIDER_API_KEY"


def test_a_spend_limit_on_the_gateway_key_is_explained():
    message, action = describe(ai.ModelHTTPError(429, "m", {}), provider="gateway")
    assert action == "retry" and "spend limit" in message and "Logfire" in message
    other, _ = describe(ai.ModelHTTPError(429, "m", {}), provider="openrouter")
    assert "spend limit" not in other


def test_the_cost_the_gateway_reported_beats_the_estimate():
    reported = ModelResponse(parts=[], model_name="claude-sonnet-5-5", usage=RequestUsage(input_tokens=1000, output_tokens=500, cost=Decimal("0.0038")))
    assert _cost_of([reported]) == pytest.approx(0.0038)
    cached = ModelResponse(parts=[], model_name="claude-sonnet-5-5", usage=RequestUsage(input_tokens=1000, output_tokens=500, cost=Decimal("0")))
    assert _cost_of([cached]) == 0.0  # a cache hit is a real zero, not "unknown"
    unknown = ModelResponse(parts=[], model_name="nobody-has-heard-of-this", usage=RequestUsage(input_tokens=5))
    assert _cost_of([unknown]) == 0.0  # unpriced: counted as nothing rather than guessed


def sse(event, data):
    return f"event: {event}\ndata: {json.dumps(data)}\n\n"


def fake_gateway(seen: list) -> FastAPI:
    """Speaks Anthropic's streaming Messages API at /proxy/anthropic/v1/messages, and reports a cost like the real one."""
    app = FastAPI()

    @app.post("/proxy/anthropic/v1/messages")
    async def messages(request: Request):
        seen.append({"authorization": request.headers.get("authorization"), "body": await request.json()})

        async def stream():
            yield sse("message_start", {"type": "message_start", "message": {"id": "msg_1", "type": "message", "role": "assistant", "model": "claude-sonnet-5-5", "content": [], "stop_reason": None, "stop_sequence": None, "usage": {"input_tokens": 25, "output_tokens": 1}}})
            yield sse("content_block_start", {"type": "content_block_start", "index": 0, "content_block": {"type": "text", "text": ""}})
            yield sse("content_block_delta", {"type": "content_block_delta", "index": 0, "delta": {"type": "text_delta", "text": "all good"}})
            yield sse("content_block_stop", {"type": "content_block_stop", "index": 0})
            yield sse("message_delta", {"type": "message_delta", "delta": {"stop_reason": "end_turn", "stop_sequence": None}, "usage": {"output_tokens": 5, "logfire_ai_gateway": {"cost": "0.0038"}}})
            yield sse("message_stop", {"type": "message_stop"})

        return StreamingResponse(stream(), media_type="text/event-stream")

    return app


async def test_a_whole_turn_through_a_fake_gateway(connected, monkeypatch):
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
    seen: list = []
    server = uvicorn.Server(uvicorn.Config(fake_gateway(seen), host="127.0.0.1", port=port, log_level="warning"))
    serving = asyncio.create_task(server.serve())
    while not server.started:
        await asyncio.sleep(0.02)
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", FAKE_KEY)
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_BASE_URL", f"http://127.0.0.1:{port}/proxy")
    try:
        connect, registry, tmp = connected
        app, info = await connect()
        events = []
        settings = Settings()
        session = Session(link=AppLink(registry, info.client_id, BLENDER), settings=settings, store=SqliteConversationStore(tmp / "c.sqlite"), assets_dir=tmp / "a",
                          emit=events.append, model_factory=lambda key: build_model(key, settings), model_key="gateway/anthropic/claude-sonnet-5-5")
        session.prompt("add a cube")
        await session.wait()
    finally:
        server.should_exit = True
        await serving

    assert not [e for e in events if isinstance(e, wire.ErrorOut)], [e.message for e in events if isinstance(e, wire.ErrorOut)]
    assert any(isinstance(e, wire.DeltaOut) and "all good" in e.delta for e in events)
    assert seen and seen[0]["authorization"] == f"Bearer {FAKE_KEY}"          # the gateway key, as a bearer token
    assert seen[0]["body"]["model"] == "claude-sonnet-5-5" and seen[0]["body"]["stream"] is True
    assert session.cost == pytest.approx(0.0038)                              # what the gateway said it cost
    settled = next(e for e in events if isinstance(e, wire.SettledOut))
    assert settled.cost == pytest.approx(0.0038)


def test_the_cost_openrouter_reported_is_used_when_no_estimate_exists():
    charged = ModelResponse(parts=[], model_name="deepseek/deepseek-v4.1-flash-1017", usage=RequestUsage(input_tokens=1000, output_tokens=500), provider_details={"cost": 0.00042})
    assert _cost_of([charged]) == pytest.approx(0.00042)
    # ...and the gateway's figure still wins if both are present.
    both = ModelResponse(parts=[], model_name="m", usage=RequestUsage(cost=Decimal("0.01")), provider_details={"cost": 0.5})
    assert _cost_of([both]) == pytest.approx(0.01)


def test_a_custom_gateway_endpoint_is_addressed_by_its_slug(monkeypatch):
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", FAKE_KEY)
    model = build_model("gateway/chat@my-deepseek/deepseek-v4.1-flash", Settings())
    assert type(model).__name__ == "OpenAIChatModel" and model.model_name == "deepseek-v4.1-flash"
    assert str(model.client.base_url) == "https://gateway-us.pydantic.dev/proxy/my-deepseek/"
    anthropic = build_model("gateway/anthropic@failover/claude-sonnet-5-5", Settings())
    assert str(anthropic.client.base_url).endswith("/proxy/failover/")
    with pytest.raises(ValueError, match="don't know how to speak"):
        build_model("gateway/carrier-pigeon@x/m", Settings())


def test_a_model_behind_a_custom_endpoint_is_judged_by_its_own_name_not_the_endpoints():
    from tech_art_core.review import family_of

    assert family_of("gateway/chat@my-deepseek/deepseek-v4.1-flash") == "deepseek"
    assert family_of("gateway/openai@team-route/gpt-6.1-sol") == "openai"
    assert family_of("gateway/chat/gemini-3.8-flash") == "google"


def test_a_refused_model_behind_a_custom_endpoint_points_at_the_endpoint_and_the_pricing_toggle():
    key = "gateway/chat@openrouter/deepseek/deepseek-v4.1-flash-1017"
    message, action = describe(ai.ModelHTTPError(400, "m", {"error": "pricing data required"}), provider="gateway", model_key=key)
    assert action is None and "'openrouter'" in message and "Require pricing data" in message and "spend limits" in message
    plain, _ = describe(ai.ModelHTTPError(404, "m", {}), provider="gateway", model_key="gateway/openai/gpt-9")
    assert "pricing data" in plain and "custom endpoint" not in plain


def test_bedrock_models_are_judged_by_their_maker_not_by_bedrock():
    from tech_art_core.review import family_of

    assert family_of("gateway/bedrock/us.deepseek.r1-v1:0") == "deepseek"
    assert family_of("gateway/bedrock/eu.anthropic.claude-haiku-4-5-20251001-v1:0") == "anthropic"
    assert family_of("gateway/bedrock/amazon.nova-micro-v1:0") == "amazon"
    assert family_of("gateway/bedrock/meta.llama3-70b-instruct-v1:0") == "meta"
