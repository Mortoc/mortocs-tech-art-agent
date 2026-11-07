"""The lifecycle helpers the core calls inside Blender. Names and shapes are the contract in
`tech_art_core.hosts.HELPERS`; the Houdini connector implements the same set.

Every function runs on Blender's main thread (the connector marshals it there) and returns plain JSON.
`bpy` is imported lazily so the module can be exercised under `blender --background` and in tests with a fake.
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import webbrowser
from pathlib import Path
from typing import Any

from .tech_art_agent import paths, project_memory  # vendored at build time; see scripts/build_blender.py

MEMORY_TEXT = "tech_art_agent.memory"
MEMORY_PREV_TEXT = "tech_art_agent.memory.prev"
SESSIONS_TEXT = "tech_art_agent.sessions"
HELP_ROOT = "https://docs.blender.org/manual/en/latest/"

_bpy: Any = None


def set_bpy(module: Any) -> None:
    global _bpy
    _bpy = module


def bpy() -> Any:
    global _bpy
    if _bpy is None:
        import bpy as real

        _bpy = real
    return _bpy


# --- document-embedded text (travels with the .blend, survives Save As) ---------------------


def _text(name: str, create: bool = False) -> Any:
    texts = bpy().data.texts
    block = texts.get(name)
    if block is None and create:
        block = texts.new(name)
        block.use_fake_user = True  # keep it through save/reload even though nothing references it
    return block


def _read(name: str) -> str | None:
    block = _text(name)
    return None if block is None else block.as_string()


def _write(name: str, value: str | None) -> None:
    if value is None:
        block = _text(name)
        if block is not None:
            bpy().data.texts.remove(block)
        return
    _text(name, create=True).from_string(value)


# --- scene ---------------------------------------------------------------------------------


def scene_context() -> dict[str, Any]:
    b = bpy()
    scene = b.context.scene
    selected = [{"path": o.name, "type": o.type} for o in b.context.selected_objects]
    active = b.context.view_layer.objects.active
    return {
        "document": b.data.filepath or None,
        "current": scene.name,
        "selected": selected,
        "extra": {
            "active object": active.name if active else "(none)",
            "frame": scene.frame_current,
            "mode": b.context.mode,
            "units": f"{scene.unit_settings.system} ({scene.unit_settings.length_unit})",
        },
    }


def begin_turn(label: str = "agent turn") -> dict[str, Any]:
    bpy().ops.ed.undo_push(message=f"Before: {label}"[:60])
    return {}


def end_turn() -> dict[str, Any]:
    bpy().ops.ed.undo_push(message="Agent turn")
    return {}


def undo_turn() -> dict[str, Any]:
    # Blender exposes no API to find where a turn began in the undo stack, so we do not guess:
    # a wrong count would undo the artist's own work.
    raise RuntimeError("Undo turn is not supported in Blender yet; use Edit > Undo History to step back to 'Before: ...'.")


def backup_scene() -> dict[str, Any]:
    b = bpy()
    folder = paths.state_dir() / "backups"
    folder.mkdir(parents=True, exist_ok=True)
    stem = Path(b.data.filepath).stem or "untitled"
    target = folder / f"{stem}-{time.strftime('%Y%m%d-%H%M%S')}.blend"
    b.ops.wm.save_as_mainfile(filepath=str(target), copy=True)
    return {"path": str(target)}


# --- memory --------------------------------------------------------------------------------


def memory_read() -> dict[str, Any]:
    return {"text": _read(MEMORY_TEXT) or "", "prev": _read(MEMORY_PREV_TEXT)}


def memory_write(text: str, prev: str | None) -> dict[str, Any]:
    _write(MEMORY_TEXT, text)
    _write(MEMORY_PREV_TEXT, prev)
    return {}


def project_memory_read() -> dict[str, Any]:
    return project_memory.read(os.environ.get("JOB"))


def project_memory_write(text: str, prev: str | None) -> dict[str, Any]:
    return project_memory.write(os.environ.get("JOB"), text, prev)


# --- sessions recorded in the document ------------------------------------------------------


def sessions_read() -> dict[str, Any]:
    raw = _read(SESSIONS_TEXT)
    return {"sessions": json.loads(raw) if raw else []}


def session_tag(path: str) -> dict[str, Any]:
    sessions = sessions_read()["sessions"]
    if path not in sessions:
        sessions.append(path)
        _write(SESSIONS_TEXT, json.dumps(sessions))
    return {}


# --- following the agent, and answer buttons -------------------------------------------------


def follow(paths: list[str]) -> dict[str, Any]:
    b = bpy()
    objects = [b.data.objects[p] for p in paths if p in b.data.objects]
    if not objects:
        return {"followed": None, "categories": {}}
    for o in b.context.view_layer.objects:
        o.select_set(False)
    for o in objects:
        if o.name in b.context.view_layer.objects:
            o.select_set(True)
    b.context.view_layer.objects.active = objects[-1]
    return {"followed": objects[-1].name, "categories": {o.name: o.type for o in objects}}


def validate_paths(paths: list[str]) -> dict[str, Any]:
    d = bpy().data
    kinds = {}
    for p in paths:
        found = p in d.objects or p in d.node_groups or p in d.materials or p in d.collections
        kinds[p] = "node" if found else None
    return {"kinds": kinds}


def _area(kind: str) -> tuple[Any, Any] | None:
    for window in bpy().context.window_manager.windows:
        for area in window.screen.areas:
            if area.type == kind:
                return window, area
    return None


def do_action(kind: str, value: str) -> dict[str, Any]:
    b = bpy()
    if kind == "select":
        follow([value])
    elif kind == "network":
        if value in b.data.objects:
            follow([value])
        # Opening a node tree in an editor needs a UI context; selecting the owner is the honest minimum.
    elif kind == "pane":
        window_area = _area(value) or _area("VIEW_3D")
        if window_area is None:
            raise RuntimeError(f"no {value} editor is open")
        window_area[1].tag_redraw()
    elif kind == "help":
        webbrowser.open(HELP_ROOT + value.lstrip("/"))
    else:
        raise RuntimeError(f"unsupported action {kind!r} in Blender")
    return {}


def capture_viewport() -> dict[str, Any]:
    """A PNG of the 3D viewport. Needs a window with a 3D View, so it works in the UI, not under --background."""
    b = bpy()
    found = None if b.app.background else _area("VIEW_3D")
    if found is None:
        raise RuntimeError("no 3D Viewport is open to capture" + (" (Blender is running without a UI)" if b.app.background else ""))
    window, area = found
    region = next(r for r in area.regions if r.type == "WINDOW")
    path = Path(tempfile.mkdtemp(prefix="tech-art-agent-")) / "viewport.png"
    with b.context.temp_override(window=window, area=area, region=region):
        b.ops.screen.screenshot_area(filepath=str(path))
    return {"path": str(path), "frame": b.context.scene.frame_current}
