"""One conversation with the agent, attached to one connected app.

A session owns the pydantic-ai agent run for each turn and turns its event stream into the pane's messages.
Everything app-specific arrives through `HostProfile` (what the model sees) and `AppLink` (calls into the app).
"""

from __future__ import annotations

import asyncio
import json
import os
import re
import time
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pydantic import BaseModel
from pydantic_ai import (ModelRetry, Agent, AgentRunResultEvent, BinaryContent, FunctionToolCallEvent, FunctionToolResultEvent, PartDeltaEvent,
                         PartEndEvent, PartStartEvent, RetryPromptPart, TextPart, TextPartDelta, ThinkingPart, ThinkingPartDelta,
                         Tool, ToolReturn)
from pydantic_ai.capabilities import Hooks
from pydantic_ai.conversation import Conversation
from pydantic_ai.conversation_store import ConversationStore
from pydantic_ai.messages import ModelRequest, ModelResponse, ToolCallPart, ToolReturnPart, UserPromptPart

from . import wire
from .errors import describe, endpoint_for
from .hosts import HostProfile
from .memory import MemoryStore, maybe_compact
from .registry import ClientError_, ClientRegistry
from .settings import Settings

Emit = Callable[[BaseModel], None]
ModelFactory = Callable[[str], Any]

COMMON_NUDGE_LIMIT = 2
QUESTION_OPENERS = re.compile(r"^(how|what|why|where|which|when|who|can|could|should|is|are|does|do|did|explain|describe|tell me|help me understand)\b", re.I)
ANSWER_NUDGE = "The artist asked a question. Answer it with the present_answer tool: clean Markdown, steps as a numbered list, and {scheme}:// links on the things each step points at. Do not answer in plain text."
LOOK_NUDGE = "You changed the scene this turn but have not looked at the result. Call {tool} now, fix what the image shows is wrong, then finish. Do not summarise."
LINK = re.compile(r"([a-z]+)://([a-z]+)\?([a-z]+)=([^)\s\"'<>]+)")
PARAM_FOR = {"select": "path", "parm": "path", "network": "path", "pane": "type", "help": "page"}


def is_question(prompt: str) -> bool:
    text = prompt.strip()
    return "?" in text[:200] or bool(QUESTION_OPENERS.match(text))


def action_links(markdown: str, scheme: str) -> list[dict[str, str]]:
    """Every well-formed action link in the Markdown, in order, de-duplicated by href."""
    from urllib.parse import unquote

    found: dict[str, dict[str, str]] = {}
    for m in LINK.finditer(markdown):
        link_scheme, kind, param, raw = m.groups()
        if link_scheme == scheme and PARAM_FOR.get(kind) == param and m.group(0) not in found:
            found[m.group(0)] = {"kind": kind, "value": unquote(raw), "href": m.group(0)}
    return list(found.values())


def to_model_id(key: str) -> str:
    """`provider/model-id` (settings, pane) to pydantic-ai's `provider:model-id`."""
    provider, _, rest = key.partition("/")
    return f"{provider}:{rest}" if rest else key


MAX_IMAGE_BYTES = 20 * 1024 * 1024
PNG_MAGIC = b"\x89PNG\r\n\x1a\n"


def temp_roots() -> list[Path]:
    import tempfile

    roots = [Path(tempfile.gettempdir()), Path("/tmp"), Path("/var/tmp")]
    roots += [Path(os.environ[v]) for v in ("HOUDINI_TEMP_DIR", "TECH_ART_AGENT_IMAGE_DIR") if os.environ.get(v)]
    return [r.resolve() for r in roots if r.exists()]


def read_app_image(path: str) -> bytes:
    file = Path(path).resolve()
    if not any(file.is_relative_to(root) for root in temp_roots()):
        raise ValueError(f"refusing to read {path}: images must come from a temp directory")
    if not file.is_file() or file.stat().st_size > MAX_IMAGE_BYTES:
        raise ValueError(f"{path} is not an image file of a sane size")
    data = file.read_bytes()
    if not data.startswith(PNG_MAGIC):
        raise ValueError(f"{path} is not a PNG")
    return data


def text_of(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(p if isinstance(p, str) else getattr(p, "text", "") for p in content if isinstance(p, str) or hasattr(p, "text"))
    return json.dumps(content, default=str) if content is not None else ""


class AppLink:
    """Calls into one connected app."""

    def __init__(self, registry: ClientRegistry, client_id: str, profile: HostProfile) -> None:
        self.registry, self.client_id, self.profile = registry, client_id, profile

    @property
    def info(self) -> wire.ClientInfo:
        info = self.registry.get(self.client_id)
        if info is None:
            raise RuntimeError(f"{self.profile.title} has disconnected")
        return info

    async def helper(self, name: str, **kwargs: Any) -> Any:
        return await self.registry.call(self.client_id, name, kwargs)

    def others(self) -> list[str]:
        return [f"{c.host}: {c.name}" for c in self.registry.list() if c.client_id != self.client_id]


@dataclass
class TurnState:
    backed_up: bool = False
    modified: bool = False
    looked: set[str] = field(default_factory=set)
    categories: set[str] = field(default_factory=set)  # categories of scene things the agent changed this turn
    answered: bool = False
    question: bool = False
    nudges: int = 0
    started: float = field(default_factory=time.monotonic)


class Session:
    def __init__(
        self,
        *,
        link: AppLink,
        settings: Settings,
        store: ConversationStore,
        assets_dir: Path,
        emit: Emit,
        model_factory: ModelFactory = lambda key: to_model_id(key),
        toolsets: Callable[[Callable[[str], None]], list[Any]] = lambda notify: [],
        conversation_id: str | None = None,
        model_key: str | None = None,
        user_memory: Any = None,
        after_turn: Callable[["Session", bool], Awaitable[None]] | None = None,
    ) -> None:
        self.after_turn = after_turn
        self.link, self.profile, self.settings, self.store = link, link.profile, settings, store
        self.assets_dir, self.emit, self.model_factory, self.toolsets = assets_dir, emit, model_factory, toolsets
        self.conversation = Conversation(conversation_id=conversation_id) if conversation_id else Conversation()
        self.model_key = model_key or (settings.default_model[0] if settings.default_model else None)
        self.thinking: wire.ThinkingLevel = "off"
        self.name: str | None = None
        self.cost = 0.0
        self.is_streaming = False
        self.details: dict[str, dict[str, Any]] = {}  # tool call id -> {asset, answer}
        self.turn = TurnState()
        self._task: asyncio.Task[None] | None = None
        self.pending_review: asyncio.Task[None] | None = None
        self._follow_tasks: list[asyncio.Task[None]] = []
        self._user_messages = 0
        self.memory = MemoryStore(link.helper, "memory_read", "memory_write", "memory")
        self.project_memory = MemoryStore(link.helper, "project_memory_read", "project_memory_write", "project memory")
        self.user_memory = user_memory

    @property
    def id(self) -> str:
        return self.conversation.conversation_id

    @property
    def _details_file(self) -> Path:
        return self.assets_dir / self.id / "details.json"

    async def restore(self) -> bool:
        """Load this conversation (and the images and answers its tool cards show) from disk. False: it is not stored."""
        loaded = await self.store.load(self.id)
        if loaded is None:
            return False
        self.conversation = loaded
        self.cost = _cost_of(loaded.messages)
        try:
            self.details = json.loads(self._details_file.read_text())
        except (OSError, ValueError):
            self.details = {}
        return True

    def _save_details(self) -> None:
        self._details_file.parent.mkdir(parents=True, exist_ok=True)
        self._details_file.write_text(json.dumps(self.details))

    # --- tools -------------------------------------------------------------------------------

    def _app_tools(self) -> list[Tool]:
        tools = []
        for spec in self.link.info.tools:
            if spec.internal:
                continue

            async def call(_name: str = spec.name, **kwargs: Any) -> Any:
                try:
                    return await self.link.registry.call(self.link.client_id, _name, kwargs)
                except ClientError_ as error:
                    raise ModelRetry(str(error)) from error

            tools.append(Tool.from_schema(call, name=spec.name, description=spec.description, json_schema=spec.schema_))
        return tools

    def _keep_image(self, path: str, call_id: str) -> tuple[str, bytes]:
        """Copy an image an app produced next to the conversation; return its pane-relative asset name and bytes.

        The path comes from the app, not the model, but the core still treats it as untrusted: it must be a
        PNG under a temp root, of sane size. The core only reads it; the app removes its own temp file.
        """
        data = read_app_image(path)
        target = self.assets_dir / self.id / (re.sub(r"[^A-Za-z0-9_.-]", "_", call_id) + ".png")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
        return f"{self.id}/{target.name}", data

    def _inspector(self, spec: Any) -> Tool:
        async def look(**kwargs: Any) -> ToolReturn:
            call_id = f"{spec.name}-{time.monotonic_ns()}"
            try:
                shot = await self.link.helper(spec.helper, **kwargs)
            except ClientError_ as error:
                raise ModelRetry(str(error)) from error
            asset, data = self._keep_image(shot["path"], call_id)
            self.turn.looked.add(spec.name)
            self._last_asset = asset
            return ToolReturn(
                return_value=f"{spec.name}: the image is in your context; judge it, do not re-read it.",
                content=[BinaryContent(data=data, media_type="image/png")],
                metadata={"asset": asset},
            )

        schema = {"type": "object", "properties": spec.params}
        return Tool.from_schema(look, name=spec.name, description=spec.description, json_schema=schema)

    def _memory_tools(self) -> list[Tool]:
        doc, proj = self.memory, self.project_memory
        noun = self.profile.document_noun

        async def memory_read() -> str:
            """Read the curated document memory stored in this scene file."""
            return await doc.read() or "(empty)"

        async def memory_append(section: str, text: str) -> str:
            """Append a note under a section of the document memory. Decisions, naming conventions, what things are for; never narration."""
            return f"{noun} memory is now {len(await doc.append(section, text))} chars"

        async def memory_replace(text: str) -> str:
            """Replace the whole document memory. The previous text stays recoverable once."""
            await doc.replace(text)
            return f"{noun} memory replaced"

        async def memory_read_project() -> str:
            """Read the project memory shared across every scene in the project."""
            snap = await proj.snapshot()
            return (snap.text or "(empty)") if snap.available else "Project memory is unavailable (no project set)."

        async def memory_append_project(section: str, text: str) -> str:
            """Append a note to the shared project memory: team conventions and pipeline rules only, never per-scene decisions."""
            if not (await proj.snapshot()).available:
                raise RuntimeError("Project memory is unavailable (no project set).")
            return f"project memory is now {len(await proj.append(section, text))} chars"

        async def memory_replace_project(text: str) -> str:
            """Replace the whole project memory. The previous text stays recoverable once."""
            if not (await proj.snapshot()).available:
                raise RuntimeError("Project memory is unavailable (no project set).")
            await proj.replace(text)
            return "project memory replaced"

        tools = [Tool(f) for f in (memory_read, memory_append, memory_replace, memory_read_project, memory_append_project, memory_replace_project)]
        user = self.user_memory
        if user is None:
            return tools

        async def memory_read_user() -> str:
            """Read the artist's own memory, shared across every app and project: personal preferences and habits."""
            return await user.read() or "(empty)"

        async def memory_append_user(section: str, text: str) -> str:
            """Remember something about the artist across all apps and projects (preferences, habits, how they like to work). Never scene or project facts."""
            return f"user memory is now {len(await user.append(section, text))} chars"

        async def memory_replace_user(text: str) -> str:
            """Replace the whole user memory. The previous text stays recoverable once."""
            await user.replace(text)
            return "user memory replaced"

        return tools + [Tool(f) for f in (memory_read_user, memory_append_user, memory_replace_user)]

    def _answer_tool(self) -> Tool:
        scheme = self.profile.link_scheme

        async def present_answer(markdown: str) -> ToolReturn:
            """Show the artist an answer or tutorial. Markdown only; steps as a numbered list. This is the only way prose reaches the artist."""
            links = action_links(markdown, scheme)
            paths = [lk["value"] for lk in links if lk["kind"] in ("select", "parm", "network")]
            invalid: dict[str, str] = {}
            if paths:
                kinds = (await self.link.helper("validate_paths", paths=paths))["kinds"]
                for lk in links:
                    kind = kinds.get(lk["value"])
                    wants_node = lk["kind"] in ("select", "network")
                    if kind is None:
                        invalid[lk["href"]] = f"nothing at {lk['value']}"
                    elif wants_node and kind != "node":
                        invalid[lk["href"]] = f"{lk['value']} is a parameter, not a node"
                    elif lk["kind"] == "parm" and kind != "parm":
                        invalid[lk["href"]] = f"{lk['value']} is a node, not a parameter"
            self.turn.answered = True
            text = "Answer shown to the artist." if not invalid else f"Answer shown. {len(invalid)} link(s) point at nothing and were disabled: {'; '.join(invalid.values())}"
            return ToolReturn(return_value=text, metadata={"answer": {"markdown": markdown, "invalid": invalid}})

        return Tool(present_answer)

    def _bus_tools(self) -> list[Tool]:
        async def app_call(app: str, tool: str, arguments: dict[str, Any] | None = None) -> Any:
            """Call a tool on another connected app (see 'Other connected apps'). Only when the request spans apps."""
            target = self.link.registry.find(app)
            if target is None or target.client_id == self.link.client_id:
                raise RuntimeError(f"no other connected app matches {app!r}")
            return await self.link.registry.call(target.client_id, tool, arguments or {})

        async def bus_publish(topic: str, data: Any = None) -> str:
            """Publish a message on the bus for other connected apps subscribed to the topic."""
            return f"delivered to {self.link.registry.bus.publish(topic, data, self.link.client_id)} subscriber(s)"

        return [Tool(app_call), Tool(bus_publish)]

    def _hooks(self) -> Hooks:
        hooks = Hooks()
        profile = self.profile

        @hooks.on.before_tool_execute
        async def before(ctx: Any, *, call: ToolCallPart, tool_def: Any, args: Any) -> Any:
            declared = next((t.modifies for t in self.link.info.tools if t.name == call.tool_name and not t.internal), None)
            if profile.can_modify(call.tool_name.split("__")[-1], declared) and not call.tool_name.startswith(("memory_", "present_", "inspect_")):
                self.turn.modified = True
                if not self.turn.backed_up:
                    self.turn.backed_up = True
                    try:
                        report = await self.link.helper("backup_scene")
                        if "skipped" in report:
                            self.emit(wire.ErrorOut(message=f"no backup this turn: {report['skipped']}"))
                    except Exception as error:
                        self.emit(wire.ErrorOut(message=f"backup before the turn failed: {error}"))
            return args

        @hooks.on.after_tool_execute
        async def after(ctx: Any, *, call: ToolCallPart, tool_def: Any, args: Any, result: Any) -> Any:
            if self.settings.follow_nodes and not call.tool_name.startswith(("memory_", "present_")):
                paths = profile.paths_from(call.args_as_dict(), result if isinstance(result, (dict, list, str)) else None)
                if paths:
                    modifies = profile.can_modify(call.tool_name, next((t.modifies for t in self.link.info.tools if t.name == call.tool_name and not t.internal), None))
                    self._follow_tasks.append(asyncio.get_running_loop().create_task(self._follow(paths, modifies)))
            return result

        return hooks

    async def _follow(self, paths: list[str], modifies: bool) -> None:
        try:
            report = await self.link.helper("follow", paths=paths)
            if modifies:
                self.turn.categories.update((report or {}).get("categories", {}).values())
        except Exception as error:  # a convenience lost, never a turn lost
            self.emit(wire.ErrorOut(message=f"follow failed: {error}"))

    # --- turn --------------------------------------------------------------------------------

    def _agent(self) -> Agent:
        tools = [*self._app_tools(), *[self._inspector(i) for i in self.profile.inspectors], *self._memory_tools(), self._answer_tool(), *self._bus_tools()]
        return Agent(
            self.model_factory(self.model_key or ""),
            instructions=self.profile.system_prompt(),
            tools=tools,
            toolsets=self.toolsets(self._notify),
            model_settings={"thinking": self.thinking} if self.thinking != "off" else None,
            capabilities=[self._hooks()],
            deps_type=type(None),
            retries=2,
        )

    def prompt(self, text: str, images: list[wire.ImageAttachment] | None = None) -> None:
        """Start a turn in the background; the pane hears about it through `emit`."""
        if self.is_streaming:
            self.emit(wire.ErrorOut(message="a turn is already running; wait for it or stop it first"))
            return
        self.is_streaming = True
        self._task = asyncio.get_running_loop().create_task(self._run_turn(text, images or []))

    async def abort(self) -> None:
        if self._task and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)

    async def wait(self) -> None:
        if self._task:
            await asyncio.gather(self._task, return_exceptions=True)

    async def _run_turn(self, text: str, images: list[wire.ImageAttachment]) -> None:
        aborted = False
        self.turn = TurnState(question=is_question(text))
        self._user_messages += 1
        self.emit(wire.UserMessageOut(text=text))
        self.emit(wire.StateOut(model=self._model_ref(), thinking_level=self.thinking, is_streaming=True))
        turn_open = False
        try:
            scene, doc_memory, project = await asyncio.gather(self.link.helper("scene_context"), self.memory.read(), self.project_memory.snapshot())
            await self.link.helper("begin_turn", label=f"agent: {text[:60]}")
            turn_open = True
            user_text = await self.user_memory.read() if self.user_memory else ""
            context = self.profile.context_block(scene, doc_memory, project.text if project.available else None, self.link.others(), user_text)
            prompt: list[Any] = [text, *[BinaryContent(data=__import__("base64").b64decode(i.data), media_type=i.mime_type) for i in images]]
            agent = self._agent()
            while True:
                await self._stream(agent, prompt if len(prompt) > 1 else prompt[0], context)
                nudge = await self._nudge()
                if nudge is None:
                    break
                self.turn.nudges += 1
                prompt = [nudge]
        except asyncio.CancelledError:
            aborted = True
        except Exception as error:
            self.emit(self._error(error))
        finally:
            if turn_open:
                try:
                    await asyncio.shield(self.link.helper("end_turn"))
                except Exception as error:
                    self.emit(wire.ErrorOut(message=f"could not close the undo group: {error}"))
            self.is_streaming = False
            if self.conversation.messages:
                try:  # recorded in the document, so Save As carries the conversation list along
                    await self.link.helper("session_tag", path=self.id)
                except Exception as error:
                    self.emit(wire.ErrorOut(message=f"could not record this conversation in the document: {error}"))
            self.emit(wire.SettledOut(aborted=aborted, cost=self.cost, duration_ms=int((time.monotonic() - self.turn.started) * 1000)))
            self.emit(wire.StateOut(model=self._model_ref(), thinking_level=self.thinking, is_streaming=False))
            if self.after_turn is not None and not aborted:
                try:
                    await self.after_turn(self, aborted)
                except Exception:  # background housekeeping never interrupts the artist
                    pass

    async def _nudge(self) -> str | None:
        t = self.turn
        # The follow calls decide which categories the turn changed; let them land before judging.
        pending, self._follow_tasks = self._follow_tasks, []
        await asyncio.gather(*pending, return_exceptions=True)
        if t.nudges >= COMMON_NUDGE_LIMIT:
            return None
        if not t.question:
            for inspector in self.profile.inspectors:
                changed = inspector.required_category in t.categories if inspector.required_category else (inspector.required_after_modify and t.modified)
                if changed and inspector.name not in t.looked:
                    return LOOK_NUDGE.format(tool=inspector.name)
        if t.question and not t.answered:
            return ANSWER_NUDGE.format(scheme=self.profile.link_scheme)
        return None

    def _notify(self, text: str) -> None:
        self.emit(wire.ErrorOut(message=text))

    def error_for(self, error: BaseException, model_key: str | None = None) -> wire.ErrorOut:
        """The pane's plain-language error for a failure, worded for `model_key`'s provider (default: this session's model)."""
        key = model_key or self.model_key
        message, action = describe(error, endpoint_for(key, self.settings.providers), (key or "").partition("/")[0] or None, key)
        return wire.ErrorOut(message=message, action=action)

    def _error(self, error: BaseException) -> wire.ErrorOut:
        return self.error_for(error)

    def _model_ref(self) -> wire.ModelRef | None:
        if not self.model_key:
            return None
        provider, _, model_id = self.model_key.partition("/")
        return wire.ModelRef(provider=provider, id=model_id, name=model_id)

    async def _stream(self, agent: Agent, prompt: Any, context: str) -> None:
        started: dict[str, float] = {}
        async with agent.run_stream_events(prompt, conversation=self.conversation, instructions=context) as events:
            async for event in events:
                if isinstance(event, PartStartEvent):
                    if isinstance(event.part, TextPart) and event.part.content:
                        self.emit(wire.DeltaOut(type="text_delta", delta=event.part.content))
                    elif isinstance(event.part, ThinkingPart) and event.part.content:
                        self.emit(wire.DeltaOut(type="thinking_delta", delta=event.part.content))
                elif isinstance(event, PartDeltaEvent):
                    if isinstance(event.delta, TextPartDelta):
                        self.emit(wire.DeltaOut(type="text_delta", delta=event.delta.content_delta))
                    elif isinstance(event.delta, ThinkingPartDelta) and event.delta.content_delta:
                        self.emit(wire.DeltaOut(type="thinking_delta", delta=event.delta.content_delta))
                elif isinstance(event, PartEndEvent) and isinstance(event.part, TextPart) and event.part.content:
                    self.emit(wire.MessageEndOut(text=event.part.content))
                elif isinstance(event, FunctionToolCallEvent):
                    started[event.part.tool_call_id] = time.monotonic()
                    self.emit(wire.ToolStartOut(tool_call_id=event.part.tool_call_id, tool_name=event.part.tool_name, args=event.part.args_as_dict()))
                elif isinstance(event, FunctionToolResultEvent):
                    self.emit(self._tool_end(event, started))
                elif isinstance(event, AgentRunResultEvent):
                    self.conversation = event.result.conversation
                    self.cost += _cost_of(event.result.new_messages())
                    await self.store.save(self.conversation, scope=self._scope())
                    self.emit(wire.TurnEndOut(turn_index=0))

    def _scope(self) -> str:
        return self.profile.kind

    def _tool_end(self, event: FunctionToolResultEvent, started: dict[str, float]) -> wire.ToolEndOut:
        part = event.part
        is_error = getattr(part, "outcome", "success") != "success" or isinstance(part, RetryPromptPart)
        meta = getattr(part, "metadata", None) or {}
        asset = meta.get("asset") if isinstance(meta, dict) else None
        answer = meta.get("answer") if isinstance(meta, dict) else None
        self.details[part.tool_call_id] = {k: v for k, v in (("asset", asset), ("answer", answer)) if v}
        if self.details[part.tool_call_id]:
            self._save_details()
        content = part.model_response() if isinstance(part, RetryPromptPart) else part.content
        return wire.ToolEndOut(
            tool_call_id=part.tool_call_id,
            tool_name=part.tool_name or "",
            result=text_of(content),
            is_error=is_error,
            duration_ms=int((time.monotonic() - started.pop(part.tool_call_id, time.monotonic())) * 1000),
            image_url=f"/session-assets/{'/'.join(asset.split('/'))}" if asset else None,
            answer=wire.Answer(**answer) if answer else None,
        )

    # --- pane --------------------------------------------------------------------------------

    def history(self) -> list[wire.HistoryMessage]:
        return history_from(self.conversation.messages, self.details)

    async def compact_memory(self, condense: Callable[[str], Awaitable[str]]) -> dict[str, int] | None:
        return await maybe_compact(self.memory, condense)


def history_from(messages: list[Any], details: dict[str, dict[str, Any]]) -> list[wire.HistoryMessage]:
    """The transcript as the pane shows it: text plus every tool call with its result."""
    out: list[wire.HistoryMessage] = []
    tools: dict[str, wire.HistoryTool] = {}
    for message in messages:
        if isinstance(message, ModelRequest):
            for part in message.parts:
                if isinstance(part, UserPromptPart):
                    text = text_of(part.content) if isinstance(part.content, str) else " ".join(c for c in part.content if isinstance(c, str))
                    if text:
                        out.append(wire.HistoryMessage(role="user", text=text))
                elif isinstance(part, (ToolReturnPart, RetryPromptPart)) and part.tool_call_id in tools:
                    tool = tools[part.tool_call_id]
                    tool.result = text_of(part.content if isinstance(part, ToolReturnPart) else part.model_response())
                    tool.is_error = getattr(part, "outcome", "success") != "success" or isinstance(part, RetryPromptPart)
                    d = details.get(part.tool_call_id, {})
                    if d.get("asset"):
                        tool.image_url = f"/session-assets/{d['asset']}"
                    if d.get("answer"):
                        tool.answer = wire.Answer(**d["answer"])
        elif isinstance(message, ModelResponse):
            text = "".join(p.content for p in message.parts if isinstance(p, TextPart))
            called = [wire.HistoryTool(tool_call_id=p.tool_call_id, tool_name=p.tool_name, args=p.args_as_dict()) for p in message.parts if isinstance(p, ToolCallPart)]
            tools.update({t.tool_call_id: t for t in called})
            if text or called:
                out.append(wire.HistoryMessage(role="assistant", text=text, tools=called or None))
    return out


def _cost_of(messages: list[Any]) -> float:
    total = 0.0
    for message in messages:
        if isinstance(message, ModelResponse):
            if message.usage.cost is not None:  # what the Pydantic AI Gateway actually charged, not an estimate
                total += float(message.usage.cost)
                continue
            charged = (message.provider_details or {}).get("cost")  # what OpenRouter actually charged
            if isinstance(charged, (int, float)):
                total += float(charged)
                continue
            try:
                total += float(message.cost().total_price)
            except Exception:  # model without published pricing
                continue
    return total
