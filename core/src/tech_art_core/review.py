"""A second opinion on whether the agent and the artist understood each other.

A different model family reads the conversation (what the artist asked, what the agent said, which tools it called and
what came back) and reports where the two may have meant different things: an ambiguity resolved silently, an
assumption nobody confirmed, scope that drifted, a term read in the wrong app's sense, an action that contradicts the
agent's own summary. Models of different architectures misread differently, which is the point of asking one.
"""

from __future__ import annotations

import re
from typing import Any, Literal

from pydantic import BaseModel, Field

from .settings import ReviewSettings

REVIEW_MARKER = "MISCOMMUNICATION REVIEW"
MAX_RESULT_CHARS = 300
MAX_ARGS_CHARS = 400
MAX_TRANSCRIPT_CHARS = 60_000

Kind = Literal["ambiguity", "assumption", "scope", "omission", "contradiction", "terminology", "other"]


class Finding(BaseModel):
    kind: Kind = Field(description="ambiguity: the request could mean several things and one was picked silently. assumption: something unstated was taken as fact (units, which object, which scene). scope: the agent did more or less than was asked. omission: something asked for was not done or not mentioned. contradiction: two turns disagree, or the agent's words disagree with its actions. terminology: a word was read in another app's or field's sense.")
    severity: Literal["low", "medium", "high"] = Field(description="high: the artist will probably be surprised or something is wrong. low: worth knowing.")
    turn: int = Field(ge=1, description="Which artist message this is about, counting from 1.")
    user_said: str = Field(description="A short exact quote from the artist.")
    agent_did: str = Field(description="What the agent understood or did instead, in a sentence.")
    risk: str = Field(description="Why the difference matters, in a sentence.")
    ask_instead: str = Field(description="The one question to put to the artist that would settle it, phrased for the artist.")


class Review(BaseModel):
    verdict: Literal["aligned", "check", "misaligned"] = Field(description="aligned: nothing worth raising. check: a few things deserve a quick confirmation. misaligned: the agent is likely working on the wrong thing.")
    summary: str = Field(description="One or two sentences for the artist.")
    findings: list[Finding] = Field(default_factory=list, max_length=6)


SYSTEM = f"""{REVIEW_MARKER}

You are an independent reviewer, deliberately a different AI than the one working with the artist. You will be shown a
conversation between an artist and an assistant that edits their live scene in a 3D application. Do NOT do the task.
Judge only whether the two of them understood each other.

Look for:
- ambiguity: a request with several readings where the assistant picked one without saying so.
- assumption: something unstated treated as settled (units, which object or node, which scene, what "it" refers to).
- scope: the assistant did more than was asked (extra controls, restructuring), or less.
- omission: something the artist asked for that was neither done nor mentioned.
- contradiction: the artist's turns disagree with each other, or the assistant's summary disagrees with the tools it called.
- terminology: a word read in the sense of another application or field (artists move between Houdini and Blender, and
  the same word often means different things in each).

Rules: quote the artist exactly. Be specific and sparing: if nothing is wrong, say "aligned" and return no findings. At most
six findings, most important first. Never critique code style or tool choice, only understanding. For each finding give the
single question that would settle it, phrased as something to ask the artist. Answer with the JSON object only.
"""


def _clip(text: str, limit: int) -> str:
    text = re.sub(r"\s+", " ", text).strip()
    return text if len(text) <= limit else text[: limit - 1] + "…"


def transcript(messages: list[Any], app_title: str, document: str | None) -> tuple[str, int]:
    """The conversation as plain text for the reviewer, and the number of artist messages in it.

    Tool arguments and results are shortened and images/binary content become a marker: the reviewer judges
    understanding, not pixels, and nothing here should be bigger than it needs to be."""
    from pydantic_ai.messages import (BinaryContent, ModelRequest, ModelResponse, TextPart, ToolCallPart, ToolReturnPart,
                                       UserPromptPart)

    lines = [f"Application: {app_title}. Document: {document or '(untitled)'}.", ""]
    user_turns = 0
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    content = part.content if isinstance(part.content, str) else " ".join(c if isinstance(c, str) else "[image]" for c in part.content if isinstance(c, (str, BinaryContent)))
                    if content.strip().startswith(("You changed the scene this turn", "The artist asked a question. Answer it")):
                        continue  # the core's own nudges are not the artist speaking
                    user_turns += 1
                    lines.append(f"ARTIST (message {user_turns}): {content.strip()}")
                elif isinstance(part, ToolReturnPart):
                    lines.append(f"   -> {part.tool_name} returned: {_clip(str(part.content), MAX_RESULT_CHARS)}")
        elif isinstance(message, ModelResponse):
            for part in message.parts:
                if isinstance(part, TextPart) and part.content.strip():
                    lines.append(f"ASSISTANT: {part.content.strip()}")
                elif isinstance(part, ToolCallPart):
                    lines.append(f"   assistant called {part.tool_name}({_clip(part.args if isinstance(part.args, str) else str(part.args), MAX_ARGS_CHARS)})")
    text = "\n".join(lines)
    if len(text) > MAX_TRANSCRIPT_CHARS:  # keep the start (what was asked first) and the end (what just happened)
        half = MAX_TRANSCRIPT_CHARS // 2
        text = text[:half] + "\n[... middle of the conversation omitted ...]\n" + text[-half:]
    return text, user_turns


def family_of(model_key: str | None) -> str:
    """Which lineage a model belongs to: 'anthropic', 'openai', 'google', 'deepseek', ... (gateway and OpenRouter pass through)."""
    parts = (model_key or "").lower().split("/")
    if parts and parts[0] in ("gateway", "openrouter") and len(parts) > 1:
        parts = parts[1:]
    head = parts[0] if parts else ""
    by_name = {"google-cloud": "google", "gemini": "google", "claude": "anthropic", "gpt": "openai", "bedrock": "amazon"}
    if "@" in head or head in ("chat", "responses", "openai-chat", "openai-responses"):
        # an API format or a custom gateway endpoint says nothing about who made the model: read the model's own name
        name = parts[-1].split("-")[0].split(".")[0]
        return by_name.get(name, name or "unknown")
    return by_name.get(head, head or "unknown")


def pick_reviewer(agent_model: str | None, settings: ReviewSettings, available: set[str] | None = None) -> tuple[str, bool]:
    """(reviewer model key, is it a different family than the agent). A pinned model wins; otherwise the first
    available candidate from another family; otherwise the first available at all (a weaker check, and it says so)."""
    if settings.model:
        return settings.model, family_of(settings.model) != family_of(agent_model)
    candidates = [c for c in settings.candidates if available is None or c in available]
    different = [c for c in candidates if family_of(c) != family_of(agent_model)]
    if different:
        return different[0], True
    if candidates:
        return candidates[0], False
    if agent_model:
        return agent_model, False
    raise ValueError("no model is available to review with")


def parse_review(text: str) -> Review:
    """Tolerant JSON extraction, for a model that wraps its answer in prose or a code fence."""
    start, end = text.find("{"), text.rfind("}")
    if start == -1 or end <= start:
        raise ValueError("the reviewer did not return JSON")
    return Review.model_validate_json(text[start : end + 1])


async def run_review(model: Any, messages: list[Any], app_title: str, document: str | None) -> tuple[Review, float, int]:
    """Ask `model` to review the conversation. Returns (review, what the call cost in USD, artist messages reviewed)."""
    from pydantic_ai import Agent, PromptedOutput

    from .session import _cost_of

    text, turns = transcript(messages, app_title, document)
    if turns == 0:
        raise ValueError("Nothing to review yet: send the agent a message first.")
    agent = Agent(model, instructions=SYSTEM, output_type=PromptedOutput(Review), retries=2)
    result = await agent.run(text)
    return result.output, _cost_of(result.new_messages()), turns
