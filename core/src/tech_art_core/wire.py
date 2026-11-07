"""Every message that crosses the core's two WebSocket endpoints, as pydantic models.

`/ws` speaks to the chat pane (`PaneIn` / `PaneOut`); `/clients` speaks to
apps (`ToCore` / `FromCore`). These models are the single source of truth:
`scripts/gen_ts_types.py` writes the TypeScript the React UI compiles against
from their JSON Schema, so the two sides cannot drift.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from pydantic import BaseModel, ConfigDict, Field, TypeAdapter
from pydantic.alias_generators import to_camel


class Camel(BaseModel):
    """camelCase on the wire (the pane is JavaScript), snake_case in Python."""

    model_config = ConfigDict(alias_generator=to_camel, populate_by_name=True)


# --- apps <-> core: /clients ------------------------------------------------


class ClientTool(BaseModel):
    name: str
    description: str = ""
    # JSON Schema for the arguments object.
    schema_: dict[str, Any] = Field(default_factory=lambda: {"type": "object", "properties": {}}, alias="schema")
    # Calling it can change the app's scene: the core takes a backup first.
    modifies: bool = False
    # A lifecycle helper the core calls itself (scene_context, begin_turn, ...); never shown to the model.
    internal: bool = False

    model_config = ConfigDict(populate_by_name=True)


class ClientInfo(Camel):
    client_id: str
    host: str
    name: str
    pid: int | None = None
    tools: list[ClientTool] = Field(default_factory=list)
    # What the connector reports about itself: bridgePort (where the app's MCP bridge listens), version, ...
    meta: dict[str, Any] = Field(default_factory=dict)


class Hello(BaseModel):
    t: Literal["hello"] = "hello"
    host: str
    name: str
    pid: int | None = None
    tools: list[ClientTool] = Field(default_factory=list)
    meta: dict[str, Any] = Field(default_factory=dict)


class Result(BaseModel):
    t: Literal["result"] = "result"
    id: str
    ok: bool
    value: Any = None
    error: str | None = None


class Sub(BaseModel):
    t: Literal["sub"] = "sub"
    topic: str


class Unsub(BaseModel):
    t: Literal["unsub"] = "unsub"
    topic: str


class Pub(BaseModel):
    t: Literal["pub"] = "pub"
    topic: str
    data: Any = None


class Rename(BaseModel):
    """The app's document changed (Save As, open): same connection, same conversation, new name."""

    t: Literal["rename"] = "rename"
    name: str


ToCore = Annotated[Hello | Result | Sub | Unsub | Pub | Rename, Field(discriminator="t")]
to_core = TypeAdapter(ToCore)


class Welcome(BaseModel):
    t: Literal["welcome"] = "welcome"
    client_id: str = Field(serialization_alias="clientId")


class Call(BaseModel):
    t: Literal["call"] = "call"
    id: str
    tool: str
    args: dict[str, Any] = Field(default_factory=dict)


class Msg(BaseModel):
    t: Literal["msg"] = "msg"
    topic: str
    from_: str = Field(serialization_alias="from")
    data: Any = None


class ClientError(BaseModel):
    t: Literal["error"] = "error"
    message: str


FromCore = Welcome | Call | Msg | ClientError


def topic_matches(pattern: str, topic: str) -> bool:
    """`pattern` selects `topic`: exact, `*` for all, or `prefix.*` for a subtree."""
    if pattern in ("*", topic):
        return True
    return pattern.endswith(".*") and topic.startswith(pattern[:-1])


# --- pane <-> core: /ws -----------------------------------------------------

ThinkingLevel = Literal["off", "minimal", "low", "medium", "high", "xhigh", "max"]
ActionKind = Literal["select", "parm", "network", "pane", "help"]


class ModelCost(Camel):
    input: float
    output: float


class ModelKey(Camel):
    provider: str
    id: str


class ModelRef(Camel):
    provider: str
    id: str
    name: str
    reasoning: bool = False
    # Accepts images. The pane only offers these: the agent has to look at renders.
    vision: bool = False
    cost: ModelCost | None = None


class SessionRef(Camel):
    id: str
    path: str
    name: str | None = None
    first_message: str = ""
    modified: float = 0


class Answer(Camel):
    markdown: str
    invalid: dict[str, str] = Field(default_factory=dict)


class ImageAttachment(Camel):
    mime_type: str
    data: str


class HistoryTool(Camel):
    tool_call_id: str
    tool_name: str
    args: Any = None
    result: str | None = None
    is_error: bool | None = None
    image_url: str | None = None
    answer: Answer | None = None


class HistoryMessage(Camel):
    role: Literal["user", "assistant"]
    text: str
    tools: list[HistoryTool] | None = None


class _In(Camel):
    pass


class PromptIn(_In):
    type: Literal["prompt"] = "prompt"
    text: str
    images: list[ImageAttachment] | None = None


class TextIn(_In):
    type: Literal["steer", "follow_up", "rename_session", "memory_edit", "project_memory_edit"]
    text: str | None = None
    name: str | None = None


class BareIn(_In):
    type: Literal[
        "abort", "new_session", "compact", "list_models", "list_sessions", "memory_read",
        "undo_compaction", "project_memory_read", "undo_turn", "review",
    ]


class SwitchSessionIn(_In):
    type: Literal["switch_session"] = "switch_session"
    session_path: str


class ForkIn(_In):
    type: Literal["fork"] = "fork"
    entry_id: str


class SetModelIn(_In):
    type: Literal["set_model"] = "set_model"
    provider: str
    model_id: str


class SetThinkingIn(_In):
    type: Literal["set_thinking"] = "set_thinking"
    level: ThinkingLevel


class HostActionIn(_In):
    type: Literal["host_action"] = "host_action"
    kind: ActionKind
    value: str


class OpenExternalIn(_In):
    type: Literal["open_external"] = "open_external"
    url: str


PaneIn = PromptIn | TextIn | BareIn | SwitchSessionIn | ForkIn | SetModelIn | SetThinkingIn | HostActionIn | OpenExternalIn
pane_in = TypeAdapter(Annotated[PaneIn, Field(discriminator="type")])


class _Out(Camel):
    pass


class HelloOut(_Out):
    type: Literal["hello"] = "hello"
    session_id: str
    session_path: str | None = None
    model: ModelRef | None = None
    thinking_level: ThinkingLevel = "off"
    # What this pane is attached to: the host app and its open document.
    host: str
    document: str | None = None
    # The link scheme answers use for buttons (houdini://, blender://).
    link_scheme: str
    title: str
    session_name: str | None = None
    is_streaming: bool = False
    cost: float = 0


class StateOut(_Out):
    type: Literal["state"] = "state"
    model: ModelRef | None = None
    thinking_level: ThinkingLevel = "off"
    is_streaming: bool = False


class SessionNameOut(_Out):
    type: Literal["session_name"] = "session_name"
    name: str | None = None


class UserMessageOut(_Out):
    type: Literal["user_message"] = "user_message"
    text: str


class DeltaOut(_Out):
    type: Literal["text_delta", "thinking_delta"]
    delta: str


class MessageEndOut(_Out):
    type: Literal["message_end"] = "message_end"
    role: Literal["assistant"] = "assistant"
    text: str


class ToolStartOut(_Out):
    type: Literal["tool_start"] = "tool_start"
    tool_call_id: str
    tool_name: str
    args: Any = None


class ToolEndOut(_Out):
    type: Literal["tool_end"] = "tool_end"
    tool_call_id: str
    tool_name: str
    result: str
    is_error: bool
    duration_ms: int
    image_url: str | None = None
    answer: Answer | None = None


class TurnEndOut(_Out):
    type: Literal["turn_end"] = "turn_end"
    turn_index: int


class SettledOut(_Out):
    type: Literal["settled"] = "settled"
    aborted: bool
    cost: float
    duration_ms: int


class ModelsOut(_Out):
    type: Literal["models"] = "models"
    models: list[ModelRef]
    recommended: list[ModelKey]
    default_model: ModelKey | None = None


class SessionsOut(_Out):
    type: Literal["sessions"] = "sessions"
    sessions: list[SessionRef]


class MemoryOut(_Out):
    type: Literal["memory"] = "memory"
    text: str
    prev_available: bool


class MemoryCompactedOut(_Out):
    type: Literal["memory_compacted"] = "memory_compacted"
    before: int
    after: int


class ProjectMemoryOut(_Out):
    type: Literal["project_memory"] = "project_memory"
    available: bool
    text: str
    prev_available: bool


class FindingOut(Camel):
    kind: str
    severity: Literal["low", "medium", "high"]
    turn: int
    user_said: str
    agent_did: str
    risk: str
    ask_instead: str


class ReviewOut(_Out):
    """A second opinion on whether the agent and the artist understood each other."""

    type: Literal["review"] = "review"
    verdict: Literal["aligned", "check", "misaligned"]
    summary: str
    findings: list[FindingOut] = Field(default_factory=list)
    reviewer: str
    reviewer_family: str
    agent_family: str
    different_family: bool
    turns: int
    cost: float = 0


class ErrorOut(_Out):
    type: Literal["error"] = "error"
    message: str
    action: Literal["reconnect", "login", "unreachable", "retry"] | None = None


class HistoryOut(_Out):
    type: Literal["history"] = "history"
    messages: list[HistoryMessage]


PaneOut = (
    HelloOut | StateOut | SessionNameOut | UserMessageOut | DeltaOut | MessageEndOut | ToolStartOut | ToolEndOut
    | TurnEndOut | SettledOut | ModelsOut | SessionsOut | MemoryOut | MemoryCompactedOut | ProjectMemoryOut
    | ErrorOut | HistoryOut | ReviewOut
)


def dump(message: BaseModel) -> str:
    """JSON for the wire: camelCase, absent optionals omitted (as the TS side expects)."""
    return message.model_dump_json(by_alias=True, exclude_none=True)
