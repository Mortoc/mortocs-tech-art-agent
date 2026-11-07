"""The FastAPI app: `/ws` for chat panes, `/clients` for apps, `/health`, the pane's static files and tool images.

Binds loopback only and requires the shared token on every socket. Heavy imports (pydantic-ai models,
Logfire exporters) are deferred to the first turn, so a woken-but-idle core stays small.
"""

from __future__ import annotations

import asyncio
import json
import logging
import os
import secrets
import time
import webbrowser
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from fastapi.responses import FileResponse, JSONResponse
from pydantic import BaseModel, ValidationError

from . import wire
from .bus import Bus
from .current import CurrentConversations, NameIndex
from .hosts import PROFILES, HostProfile
from .memory import FileMemoryStore, maybe_compact
from .errors import is_unreachable
from .models import cheapest, gateway_models, load_catalog, local_models, recommended
from .registry import CLIENTS_TOPIC, ClientError_, ClientRegistry
from .session import AppLink, Session, history_from
from .settings import Settings, load_settings, state_dir
from .store import SqliteConversationStore

log = logging.getLogger("tech_art_core")


def ensure_token(directory: Path) -> str:
    """The long-lived token every socket must present. Created 0600 on first use; apps read the same file."""
    file = directory / "core.token"
    try:
        return file.read_text().strip()
    except FileNotFoundError:
        directory.mkdir(parents=True, exist_ok=True)
        token = secrets.token_urlsafe(24)
        fd = os.open(file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(token)
        return token


class Core:
    """Everything the endpoints share. Built once; tests build their own with a scripted `model_factory`."""

    def __init__(self, directory: Path, settings: Settings | None = None, model_factory: Any = None, toolsets: Any = None, catalog: Any = None) -> None:
        self._catalog = catalog  # async () -> list[ModelRef]; None: OpenRouter's, cached on disk
        self.dir = directory
        self.settings = settings or load_settings(directory / "settings.json")
        if "TECH_ART_AGENT_IDLE_TIMEOUT" in os.environ:  # the NixOS module's option wins over the file
            self.settings = self.settings.model_copy(update={"idle_timeout": int(os.environ["TECH_ART_AGENT_IDLE_TIMEOUT"])})
        self.token = ensure_token(directory)
        self.bus = Bus()
        self.registry = ClientRegistry(self.bus)
        self.store = SqliteConversationStore(directory / "conversations.sqlite")
        self.assets = directory / "assets"
        self.user_memory = FileMemoryStore(directory / "memory.md")
        self.current = CurrentConversations(directory / "current.json")
        self.names = NameIndex(directory / "names.json")
        self._default_model: str | None = None  # remembered once a configured default is actually available
        self._fallback_model: str | None = None
        self.model_factory = model_factory
        self.toolsets = toolsets
        self.sessions: dict[str, Session] = {}  # client id -> its current conversation
        self.panes: dict[str, set[WebSocket]] = {}  # session id -> attached panes
        self.last_activity = time.monotonic()
        self.bus.subscribe(CLIENTS_TOPIC, self._client_event)

    # --- liveness --------------------------------------------------------------------------

    def touch(self) -> None:
        self.last_activity = time.monotonic()

    def busy(self) -> bool:
        """Anything that should keep the daemon awake: a connected app, an attached pane, or a running turn."""
        return bool(self.registry.list()) or any(self.panes.values()) or any(s.is_streaming for s in self.sessions.values())

    def idle_for(self) -> float:
        return 0.0 if self.busy() else time.monotonic() - self.last_activity

    def _client_event(self, _topic: str, data: Any, _sender: str) -> None:
        self.touch()
        if data.get("event") == "left":
            gone = self.sessions.pop(data["client"]["clientId"], None)
            if gone and not self.panes.get(gone.id):
                self.panes.pop(gone.id, None)

    # --- sessions --------------------------------------------------------------------------

    def profile_of(self, client_id: str) -> HostProfile:
        info = self.registry.get(client_id)
        if info is None:
            raise HTTPException(404, "that app is not connected")
        if info.host not in PROFILES:
            raise HTTPException(400, f"the core has no profile for host {info.host!r}")
        return PROFILES[info.host]

    def session_for(self, client_id: str, *, fresh: bool = False, conversation_id: str | None = None, model_key: str | None = None) -> Session:
        current = self.sessions.get(client_id)
        if current and not fresh and conversation_id is None:
            return current
        model_key = model_key or (current.model_key if current else None)
        session = Session(
            link=AppLink(self.registry, client_id, self.profile_of(client_id)), settings=self.settings, store=self.store,
            assets_dir=self.assets, emit=lambda message: None, model_factory=self.model_for, after_turn=self.after_turn,
            toolsets=self._toolsets_for(client_id), conversation_id=conversation_id, model_key=model_key or self.new_session_model, user_memory=self.user_memory,
        )
        session.emit = lambda message, s=session: self.broadcast(s, message)
        self.sessions[client_id] = session
        self._remember(client_id, session)
        return session

    def _key(self, client_id: str) -> str | None:
        info = self.registry.get(client_id)
        return f"pid:{info.pid}" if info and info.pid else None

    def _remember(self, client_id: str, session: Session) -> None:
        if (key := self._key(client_id)) is not None:
            self.current.set(key, session.id)

    async def resume_or_new(self, client_id: str) -> Session:
        """The app's current session; after a core restart, the conversation it was on, restored from disk."""
        await self.ensure_default_model()
        if client_id in self.sessions:
            return self.sessions[client_id]
        key = self._key(client_id)
        previous = self.current.get(key) if key else None
        if previous:
            session = self.session_for(client_id, conversation_id=previous)
            if await session.restore():
                return session
        return self.session_for(client_id, fresh=True)

    def model_for(self, key: str) -> Any:
        if self.model_factory:
            return self.model_factory(key)
        from .providers import build_model  # deferred: pulls in the provider SDKs

        return build_model(key, self.settings)

    def _toolsets_for(self, client_id: str) -> Any:
        if self.toolsets is not None:
            return lambda notify: self.toolsets(client_id)

        def build(notify: Any) -> list[Any]:
            from .providers import mcp_toolsets  # deferred

            return mcp_toolsets(self.profile_of(client_id), self.registry.get(client_id), self.settings, self.dir, notify=notify)

        return build

    def broadcast(self, session: Session, message: BaseModel) -> None:
        self.touch()
        raw = wire.dump(message)
        for socket in list(self.panes.get(session.id, ())):
            asyncio.get_running_loop().create_task(_safe_send(socket, raw))

    async def hello(self, session: Session) -> wire.HelloOut:
        info = session.link.info
        return wire.HelloOut(
            session_id=session.id, session_path=session.id, model=session._model_ref(), thinking_level=session.thinking,
            host=info.host, document=info.name, link_scheme=session.profile.link_scheme, title=f"{session.profile.title} Agent",
            session_name=session.name, is_streaming=session.is_streaming, cost=session.cost,
        )

    async def all_models(self) -> list[wire.ModelRef]:
        """Models this machine can use now: local servers that are running, the gateway's if there is a gateway key, and
        OpenRouter's catalog (cached; empty if never fetched and unreachable) if there is an OpenRouter key."""
        if self._catalog:
            hosted = await self._catalog()  # tests and demos inject their own list
        elif os.environ.get("OPENROUTER_API_KEY"):
            hosted = await load_catalog(self.dir / "cache" / "models.json")
        else:
            hosted = []
        local = await local_models(self.settings.providers) if self.settings.providers else []
        return [m for m in [*local, *gateway_models(self.settings.gateway), *hosted] if m.vision]

    async def models_message(self) -> wire.ModelsOut:
        models = await self.all_models()
        rec = recommended(models, self.settings.default_model)
        return wire.ModelsOut(models=models, recommended=rec, default_model=rec[0] if rec else None)

    async def ensure_default_model(self) -> None:
        """What a new conversation starts on: the first configured default that is available right now.

        If none is (say its server isn't running), keep the first *configured* one anyway, so the artist is told
        that server is unreachable, instead of quietly landing on some hosted model that needs a key they haven't set.
        That fallback is not remembered: once the server is back the choice is made again.
        """
        if self._default_model:
            return
        wanted = self.settings.default_model
        models = await self.all_models()
        rec = recommended(models, wanted)
        if rec:
            self._default_model = f"{rec[0].provider}/{rec[0].id}"
        elif wanted:
            self._fallback_model = wanted[0]
        elif models:
            self._default_model = f"{models[0].provider}/{models[0].id}"

    @property
    def new_session_model(self) -> str | None:
        return self._default_model or self._fallback_model

    async def review_now(self, session: Session) -> None:
        """Ask a model from another family whether the artist and the agent understood each other; show the pane the answer."""
        from .review import family_of, pick_reviewer, run_review

        reviewer = None
        try:
            if not self.settings.review.enabled:
                raise ValueError("Second opinions are turned off (review.enabled in settings).")
            if not any(m.role == "user" for m in session.history()):
                raise ValueError("Nothing to review yet: send the agent a message first.")
            available = {f"{m.provider}/{m.id}" for m in await self.all_models()}
            reviewer, different = pick_reviewer(session.model_key, self.settings.review, available)
            review, cost, turns = await run_review(self.model_for(reviewer), session.conversation.messages, session.profile.title, session.link.info.name)
            session.cost += cost
            session.emit(wire.ReviewOut(
                verdict=review.verdict, summary=review.summary, findings=[wire.FindingOut(**f.model_dump()) for f in review.findings],
                reviewer=reviewer, reviewer_family=family_of(reviewer), agent_family=family_of(session.model_key), different_family=different, turns=turns, cost=cost,
            ))
        except Exception as error:
            session.emit(session.error_for(error, reviewer))

    async def condense(self, prompt: str, session: Session, model_key: str | None = None) -> str:
        from pydantic_ai import Agent

        return (await Agent(self.model_for(model_key or session.model_key or "")).run(prompt)).output

    async def after_turn(self, session: Session, aborted: bool) -> None:
        """Housekeeping once a turn is over: compact a long memory, name the conversation. Never interrupts the artist."""
        try:
            report = await maybe_compact(session.memory, lambda p: self.condense(p, session))
            if report:
                session.emit(wire.MemoryCompactedOut(**report))
        except Exception as error:
            if not is_unreachable(error):
                session.emit(wire.ErrorOut(message=f"memory compaction failed: {error}"))
        asked = [m.text for m in session.history() if m.role == "user"]
        auto = self.settings.review
        if auto.enabled and auto.auto and asked and len(asked) % max(1, auto.every) == 0:
            await self.review_now(session)
        if len(asked) in self.settings.naming.rename_at_user_messages:
            try:
                model_key = self.settings.naming.model
                if model_key == "cheapest":
                    best = cheapest(await self.all_models())
                    model_key = f"{best.provider}/{best.id}" if best else session.model_key
                prompt = "Give this conversation a title of at most five words, no quotes, no trailing period. Answer with the title only.\n\n" + "\n".join(f"- {t[:200]}" for t in asked[:5])
                name = (await self.condense(prompt, session, model_key)).strip().strip('"')[:60]
                if name:
                    session.name = name
                    self.names.set(session.id, name)
                    session.emit(wire.SessionNameOut(name=name))
            except Exception:
                pass  # a nice-to-have; an unreachable or rate-limited naming model must not be noisy

async def _safe_send(socket: WebSocket, raw: str) -> None:
    try:
        await socket.send_text(raw)
    except Exception:
        pass


def create_app(core: Core, static_dir: Path | None = None) -> FastAPI:
    @asynccontextmanager
    async def lifespan(app: FastAPI):
        yield
        for session in list(core.sessions.values()):
            await session.abort()
        core.store.close()

    app = FastAPI(title="tech-art-core", lifespan=lifespan, docs_url=None, redoc_url=None)
    app.state.core = core

    def authorised(token: str | None) -> bool:
        return token is not None and secrets.compare_digest(token, core.token)

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {"ok": True, "pid": os.getpid(), "apps": [{"host": c.host, "name": c.name} for c in core.registry.list()], "idleFor": round(core.idle_for())}

    @app.get("/apps")
    async def apps(token: str = Query("")) -> Any:
        if not authorised(token):
            raise HTTPException(401, "token")
        return [c.model_dump(by_alias=True, exclude={"tools"}) for c in core.registry.list()]

    @app.get("/session-assets/{path:path}")
    async def session_assets(path: str, token: str = Query("")) -> Any:
        if not authorised(token):
            raise HTTPException(401, "token")
        file = (core.assets / path).resolve()
        if not file.is_relative_to(core.assets.resolve()) or not file.is_file():
            raise HTTPException(404, "not found")
        return FileResponse(file, headers={"cache-control": "no-store"})

    @app.websocket("/clients")
    async def clients(socket: WebSocket, token: str = Query("")) -> None:
        if not authorised(token):
            await socket.close(code=4401)
            return
        await socket.accept()
        core.touch()
        try:
            await core.registry.serve(socket)
        except WebSocketDisconnect:
            pass

    @app.websocket("/ws")
    async def pane(socket: WebSocket, token: str = Query(""), client: str = Query("")) -> None:
        if not authorised(token):
            await socket.close(code=4401)
            return
        await socket.accept()
        core.touch()
        info = core.registry.find(client) if client else (core.registry.list() or [None])[0]
        if info is None:
            await socket.send_text(wire.dump(wire.ErrorOut(message="No app is connected to the core yet. Open Houdini or Blender with the agent installed.", action="reconnect")))
            await socket.close()
            return
        session = await core.resume_or_new(info.client_id)
        core.panes.setdefault(session.id, set()).add(socket)
        try:
            await socket.send_text(wire.dump(await core.hello(session)))
            await socket.send_text(wire.dump(wire.HistoryOut(messages=session.history())))
            while True:
                raw = await socket.receive_text()
                core.touch()
                try:
                    message = wire.pane_in.validate_python(json.loads(raw))
                except (ValueError, ValidationError) as error:
                    await socket.send_text(wire.dump(wire.ErrorOut(message=f"bad message: {str(error)[:200]}")))
                    continue
                try:
                    new_session = await handle_pane_message(core, session, message, socket)
                except Exception as error:  # a failing request is reported, never fatal to the pane
                    log.exception("pane request failed")
                    await socket.send_text(wire.dump(wire.ErrorOut(message=str(error))))
                    continue
                if new_session is not None:
                    core.panes.get(session.id, set()).discard(socket)
                    session = new_session
                    core.panes.setdefault(session.id, set()).add(socket)
                    await socket.send_text(wire.dump(await core.hello(session)))
                    await socket.send_text(wire.dump(wire.HistoryOut(messages=session.history())))
        except WebSocketDisconnect:
            pass
        finally:
            core.panes.get(session.id, set()).discard(socket)
            core.touch()

    if static_dir is not None and static_dir.exists():
        @app.get("/{path:path}")
        async def static(path: str) -> Any:
            file = (static_dir / (path or "index.html")).resolve()
            if not file.is_relative_to(static_dir.resolve()) or not file.is_file():
                file = static_dir / "index.html"
            return FileResponse(file, headers={"cache-control": "no-store"})

    return app


async def handle_pane_message(core: Core, session: Session, message: Any, socket: WebSocket) -> Session | None:
    """Act on one pane request. Returns a different session when the pane should switch to it."""
    send = lambda m: socket.send_text(wire.dump(m))  # noqa: E731
    client_id = session.link.client_id
    t = message.type
    if t == "prompt":
        session.prompt(message.text, message.images)
    elif t in ("steer", "follow_up"):
        session.prompt(message.text or "") if not session.is_streaming else await send(wire.ErrorOut(message="wait for the turn to finish, or stop it first"))
    elif t == "abort":
        await session.abort()
    elif t == "new_session":
        await core.ensure_default_model()
        return core.session_for(client_id, fresh=True)
    elif t == "switch_session":
        target = core.session_for(client_id, conversation_id=message.session_path)
        if not await target.restore():
            core.sessions[client_id] = session
            raise ValueError("that conversation is no longer stored")
        return target
    elif t == "fork":
        forked = await core.store.fork(session.id, from_entry=message.entry_id)
        target = core.session_for(client_id, conversation_id=forked.conversation_id)
        await target.restore()
        return target
    elif t == "set_model":
        session.model_key = f"{message.provider}/{message.model_id}"
        session.emit(wire.StateOut(model=session._model_ref(), thinking_level=session.thinking, is_streaming=session.is_streaming))
    elif t == "set_thinking":
        session.thinking = message.level
        session.emit(wire.StateOut(model=session._model_ref(), thinking_level=session.thinking, is_streaming=session.is_streaming))
    elif t == "list_models":
        await send(await core.models_message())
    elif t == "list_sessions":
        recorded = (await session.link.helper("sessions_read"))["sessions"]
        records = [r for r in [await core.store.read_record(i) for i in recorded] if r is not None]
        records.sort(key=lambda r: r.updated_at, reverse=True)
        refs = []
        for r in records:
            loaded = await core.store.load(r.conversation_id)
            first = next((m.text for m in history_from(loaded.messages, {}) if m.role == "user"), "") if loaded else ""
            refs.append(wire.SessionRef(id=r.conversation_id, path=r.conversation_id, name=core.names.get(r.conversation_id), first_message=first[:120], modified=r.updated_at.timestamp() * 1000))
        await send(wire.SessionsOut(sessions=refs))
    elif t == "memory_read":
        snap = await session.memory.snapshot()
        await send(wire.MemoryOut(text=snap.text, prev_available=snap.prev is not None))
    elif t == "memory_edit":
        await session.memory.replace(message.text or "")
        snap = await session.memory.snapshot()
        await send(wire.MemoryOut(text=snap.text, prev_available=snap.prev is not None))
    elif t == "undo_compaction":
        await session.memory.rollback()
        snap = await session.memory.snapshot()
        await send(wire.MemoryOut(text=snap.text, prev_available=snap.prev is not None))
    elif t == "project_memory_read":
        snap = await session.project_memory.snapshot()
        await send(wire.ProjectMemoryOut(available=snap.available, text=snap.text, prev_available=snap.prev is not None))
    elif t == "project_memory_edit":
        await session.project_memory.replace(message.text or "")
        snap = await session.project_memory.snapshot()
        await send(wire.ProjectMemoryOut(available=snap.available, text=snap.text, prev_available=snap.prev is not None))
    elif t == "compact":
        report = await maybe_compact(session.memory, lambda p: core.condense(p, session), threshold=0)
        if report:
            await send(wire.MemoryCompactedOut(**report))
    elif t == "rename_session":
        session.name = message.name or message.text
        core.names.set(session.id, session.name or "")
        await send(wire.SessionNameOut(name=session.name))
    elif t == "host_action":
        await session.link.helper("do_action", kind=message.kind, value=message.value)
    elif t == "open_external":
        if message.url.startswith(("http://", "https://")):
            webbrowser.open(message.url)
    elif t == "review":
        session.pending_review = asyncio.get_running_loop().create_task(core.review_now(session))  # seconds long: never block the socket
    elif t == "undo_turn":
        await session.link.helper("undo_turn")
    return None
