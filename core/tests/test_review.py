"""The second opinion: a model from another family audits the conversation for miscommunication."""

import json

import pytest
from pydantic_ai.messages import ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from tech_art_core import wire
from tech_art_core.app import Core
from tech_art_core.review import (MAX_RESULT_CHARS, REVIEW_MARKER, SYSTEM, Review, family_of, parse_review, pick_reviewer,
                                  run_review, transcript)
from tech_art_core.settings import ReviewSettings, Settings

from test_session import script
from test_unreachable import Server  # noqa: F401  (fixtures below reuse the OpenAI-style server)

FAKE_KEY = "pylf_v1_us_" + "x" * 24


def fn_model(respond):
    """A scripted model that answers both streamed and plain calls with respond(messages, info) -> text."""

    async def stream(messages, info):
        yield respond(messages, info)

    def function(messages, info):
        return ModelResponse(parts=[TextPart(content=respond(messages, info))])

    return FunctionModel(function=function, stream_function=stream)

FINDING = {
    "kind": "ambiguity", "severity": "high", "turn": 1, "user_said": "make it bigger",
    "agent_did": "Scaled the selected object by 2 on every axis.", "risk": "They may have meant only its height, or a different object.",
    "ask_instead": "Which object, and bigger in which direction?",
}
GOOD = {"verdict": "check", "summary": "One request was ambiguous.", "findings": [FINDING]}


def conversation():
    return [
        ModelRequest(parts=[UserPromptPart(content="make it bigger")]),
        ModelResponse(parts=[ToolCallPart(tool_name="set_parameter", args={"node_path": "/obj/geo1/xform1", "value": 2.0}, tool_call_id="c1")]),
        ModelRequest(parts=[ToolReturnPart(tool_name="set_parameter", content="ok " + "x" * 5000, tool_call_id="c1")]),
        ModelResponse(parts=[TextPart(content="Done: scaled it up.")]),
        ModelRequest(parts=[UserPromptPart(content="You changed the scene this turn but have not looked at the result. Call inspect_viewport now.")]),
        ModelRequest(parts=[UserPromptPart(content="thanks, and the other one too")]),
    ]


def test_the_transcript_is_readable_clipped_and_leaves_out_the_cores_own_nudges():
    text, turns = transcript(conversation(), "Blender Agent", "/shots/car.blend")
    assert turns == 2  # the nudge is the core talking, not the artist
    assert "ARTIST (message 1): make it bigger" in text and "ARTIST (message 2): thanks, and the other one too" in text
    assert "assistant called set_parameter(" in text and "ASSISTANT: Done: scaled it up." in text
    assert "have not looked at the result" not in text
    assert "x" * (MAX_RESULT_CHARS + 50) not in text and "…" in text  # long tool output is shortened
    assert "Application: Blender Agent. Document: /shots/car.blend." in text


def test_a_long_conversation_keeps_its_beginning_and_its_end():
    many = [ModelRequest(parts=[UserPromptPart(content=f"message number {i} " + "word " * 400)]) for i in range(100)]
    text, turns = transcript(many, "Houdini Agent", None)
    assert turns == 100 and "message number 0 " in text and "message number 99 " in text and "middle of the conversation omitted" in text


def test_families_see_through_gateway_and_openrouter_prefixes():
    assert family_of("gateway/openai/gpt-6.1-sol") == "openai"
    assert family_of("gateway/google/gemini-3.8-flash") == "google"
    assert family_of("gateway/anthropic/claude-sonnet-5-5") == "anthropic"
    assert family_of("openrouter/deepseek/deepseek-v4") == "deepseek"
    assert family_of("openrouter/openai/gpt-6") == "openai"
    assert family_of("local/llava") == "local"
    assert family_of(None) == "unknown"


def test_the_reviewer_is_from_another_family_when_one_is_available():
    settings = ReviewSettings(model=None)  # unpinned: the cross-family rule applies
    every = {"gateway/google/gemini-3.8-flash", "gateway/openai/gpt-6.1-terra", "gateway/anthropic/claude-haiku-5-5"}
    assert pick_reviewer("gateway/openai/gpt-6.1-sol", settings, every) == ("gateway/google/gemini-3.8-flash", True)
    assert pick_reviewer("gateway/google/gemini-3.8-flash", settings, every) == ("gateway/openai/gpt-6.1-terra", True)
    assert pick_reviewer("gateway/anthropic/claude-sonnet-5-5", settings, every - {"gateway/google/gemini-3.8-flash"}) == ("gateway/openai/gpt-6.1-terra", True)


def test_when_every_candidate_shares_the_agents_family_it_says_so():
    unpinned = ReviewSettings(model=None)
    only_openai = {"gateway/openai/gpt-6.1-terra"}
    assert pick_reviewer("gateway/openai/gpt-6.1-sol", unpinned, only_openai) == ("gateway/openai/gpt-6.1-terra", False)
    assert pick_reviewer("local/llava", unpinned, set()) == ("local/llava", False)  # nothing else: the agent's own model
    with pytest.raises(ValueError, match="no model"):
        pick_reviewer(None, unpinned, set())


def test_for_now_everything_including_the_reviewer_is_haiku_and_the_card_says_it_is_the_weaker_check():
    assert pick_reviewer("gateway/anthropic/claude-haiku-5-5", ReviewSettings(), set()) == ("gateway/anthropic/claude-haiku-5-5", False)


def test_a_pinned_reviewer_wins_and_is_still_judged_on_family():
    pinned = ReviewSettings(model="gateway/openai/gpt-6.1-sol")
    assert pick_reviewer("gateway/openai/gpt-6.1-terra", pinned, set()) == ("gateway/openai/gpt-6.1-sol", False)
    assert pick_reviewer("gateway/google/gemini-3.8-flash", pinned, set()) == ("gateway/openai/gpt-6.1-sol", True)


def test_parsing_tolerates_prose_and_code_fences():
    wrapped = "Sure, here is my review:\n```json\n" + json.dumps(GOOD) + "\n```\nHope that helps."
    review = parse_review(wrapped)
    assert review.verdict == "check" and review.findings[0].ask_instead.startswith("Which object")
    with pytest.raises(ValueError, match="did not return JSON"):
        parse_review("I could not find any problems.")


async def test_the_reviewer_gets_the_brief_and_the_transcript_and_returns_structured_findings():
    seen = {}

    def respond(messages, info):
        seen["instructions"] = info.instructions or ""
        seen["user"] = "\n".join(str(p.content) for m in messages if isinstance(m, ModelRequest) for p in m.parts if isinstance(p, UserPromptPart))
        return json.dumps(GOOD)

    review, cost, turns = await run_review(fn_model(respond), conversation(), "Blender Agent", "car.blend")
    assert isinstance(review, Review) and review.findings[0].kind == "ambiguity" and turns == 2
    assert REVIEW_MARKER in seen["instructions"] and "different AI" in seen["instructions"]
    assert "ARTIST (message 1): make it bigger" in seen["user"]


async def test_a_malformed_first_answer_is_retried():
    calls = []

    def respond(messages, info):
        calls.append(1)
        return "not json at all" if len(calls) == 1 else json.dumps(GOOD)

    review, _, _ = await run_review(fn_model(respond), conversation(), "Houdini Agent", None)
    assert len(calls) == 2 and review.verdict == "check"


async def test_there_is_nothing_to_review_before_the_artist_has_spoken():
    with pytest.raises(ValueError, match="Nothing to review yet"):
        await run_review(fn_model(lambda m, i: "{}"), [], "Houdini Agent", None)


async def make_core(connected, tmp_path, monkeypatch, settings=None, models=None):
    """A Core whose agent and reviewer are different scripted models, keyed by the model key the core asks for."""
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", FAKE_KEY)
    connect, registry, _ = connected
    app, info = await connect(host="blender")
    models = models or {}

    async def no_catalog():
        return []

    core = Core(tmp_path / "state", settings or Settings(), model_factory=lambda key: models[key], toolsets=lambda cid: [], catalog=no_catalog)
    core.registry = registry
    session = core.session_for(info.client_id, model_key="gateway/openai/gpt-6.1-sol")
    events = []
    session.emit = events.append
    return core, session, events, app


async def test_the_review_flows_from_a_different_family_to_the_pane(connected, tmp_path, monkeypatch):
    core, session, events, _ = await make_core(connected, tmp_path, monkeypatch, settings=Settings(review=ReviewSettings(model=None)), models={
        "gateway/openai/gpt-6.1-sol": script(("text", "Done: scaled it up.")),
        "gateway/google/gemini-3.8-flash": fn_model(lambda m, i: json.dumps(GOOD)),
    })
    session.prompt("make it bigger")
    await session.wait()
    events.clear()

    await core.review_now(session)
    (out,) = [e for e in events if isinstance(e, wire.ReviewOut)]
    assert out.verdict == "check" and out.findings[0].user_said == "make it bigger"
    assert out.reviewer == "gateway/google/gemini-3.8-flash" and out.different_family is True
    assert (out.agent_family, out.reviewer_family, out.turns) == ("openai", "google", 1)


async def test_review_failures_are_explained_in_plain_words(connected, tmp_path, monkeypatch):
    core, session, events, _ = await make_core(connected, tmp_path, monkeypatch, models={"gateway/openai/gpt-6.1-sol": script(("text", "ok"))})
    await core.review_now(session)  # nothing said yet
    assert "Nothing to review yet" in events[-1].message

    off = Settings(review=ReviewSettings(enabled=False))
    core2, session2, events2, _ = await make_core(connected, tmp_path / "2", monkeypatch, settings=off, models={"gateway/openai/gpt-6.1-sol": script(("text", "ok"))})
    await core2.review_now(session2)
    assert "turned off" in events2[-1].message


async def test_automatic_review_runs_after_every_nth_message_when_switched_on(connected, tmp_path, monkeypatch):
    quiet = json.dumps({"verdict": "aligned", "summary": "Nothing to raise.", "findings": []})
    settings = Settings(review=ReviewSettings(model=None, auto=True, every=2))
    core, session, events, _ = await make_core(connected, tmp_path, monkeypatch, settings=settings, models={
        "gateway/openai/gpt-6.1-sol": script(("text", "ok")), "gateway/google/gemini-3.8-flash": fn_model(lambda m, i: quiet)})
    session.prompt("first"); await session.wait()
    assert not [e for e in events if isinstance(e, wire.ReviewOut)]  # 1 of every 2
    session.prompt("second"); await session.wait()
    out = [e for e in events if isinstance(e, wire.ReviewOut)]
    assert len(out) == 1 and out[0].verdict == "aligned" and out[0].turns == 2


async def test_by_default_one_gateway_key_runs_everything_on_haiku(tmp_path, monkeypatch):
    """The setup we ship for now: no OpenRouter endpoint needed. The agent, the reviewer and naming are all Haiku."""
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", FAKE_KEY)

    async def no_catalog():
        return []

    core = Core(tmp_path, Settings(), catalog=no_catalog)
    await core.ensure_default_model()
    available = {f"{m.provider}/{m.id}" for m in await core.all_models()}
    assert core.new_session_model == "gateway/anthropic/claude-haiku-5-5"
    assert pick_reviewer(core.new_session_model, core.settings.review, available) == ("gateway/anthropic/claude-haiku-5-5", False)


async def test_the_intended_setup_once_deepseek_is_connected_is_deepseek_reviewed_by_gemini(tmp_path, monkeypatch):
    """What a settings file produces after the gateway has an OpenRouter endpoint: DeepSeek first, a Gemini reviewer."""
    monkeypatch.setenv("PYDANTIC_AI_GATEWAY_API_KEY", FAKE_KEY)
    deepseek = "gateway/chat@openrouter/deepseek/deepseek-v4.1-flash-1017"

    async def no_catalog():
        return []

    settings = Settings(default_model=[deepseek, "gateway/anthropic/claude-haiku-5-5"], review=ReviewSettings(model=None))
    settings.gateway.models.insert(0, "chat@openrouter/deepseek/deepseek-v4.1-flash-1017")
    core = Core(tmp_path, settings, catalog=no_catalog)
    await core.ensure_default_model()
    assert core.new_session_model == deepseek and family_of(deepseek) == "deepseek"
    available = {f"{m.provider}/{m.id}" for m in await core.all_models()}
    assert pick_reviewer(deepseek, settings.review, available) == ("gateway/google/gemini-3.8-flash", True)
