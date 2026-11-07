"""Helpers the sidecar runs inside Houdini through fxhoudinimcp's execute_python.

Every function returns something json.dumps can take, because the sidecar
reads the result as a JSON string. `hou` is imported lazily so the module can
be tested with a fake injected via `set_hou`.
"""

from __future__ import annotations

import importlib
import json
import os
from pathlib import Path
from typing import Any

MEMORY_KEY = "houdini_pi.memory"
MEMORY_PREV_KEY = "houdini_pi.memory.prev"
SESSIONS_KEY = "houdini_pi.sessions"


def _patch_dispatcher_no_undo_group() -> bool:
    """Add code.execute_python to the dispatcher's no-undo-group set.

    fxhoudinimcp's dispatcher wraps every MCP command in hou.undos.group().
    begin_turn and end_turn arrive through execute_python, so the dispatcher's
    wrapper group nests around the turn's group — and when the wrapper closes
    at the end of the execute_python call, Houdini closes the nested turn group
    too. The turn's undo group dies before any tool call runs, and each MCP
    tool call becomes its own undo step: one Ctrl-Z undoes only the last call.

    Skipping the wrapper for code.execute_python lets begin_turn open the
    turn's group directly, and it stays open across every tool call in the
    turn. Scene modifications made through execute_python are still captured
    by the turn's group. Idempotent; silently degrades when upstream moves.
    """
    try:
        from fxhoudinimcp_server import dispatcher

        if "code.execute_python" not in dispatcher._NO_UNDO_GROUP:
            dispatcher._NO_UNDO_GROUP = dispatcher._NO_UNDO_GROUP | {"code.execute_python"}
        return True
    except Exception:
        return False


# Apply on import. The dispatcher checks _NO_UNDO_GROUP at dispatch time, so
# the patch takes effect on the next execute_python call after this module
# loads — which is fine, because begin_turn runs after scene_context.
_dispatcher_patched = _patch_dispatcher_no_undo_group()


def undo_grouping_status() -> dict[str, Any]:
    """Whether turn-level undo grouping is active (the dispatcher is patched)."""
    try:
        from fxhoudinimcp_server import dispatcher

        return {"patched": "code.execute_python" in dispatcher._NO_UNDO_GROUP}
    except Exception:
        return {"patched": False}

_hou: Any = None
_open_group: Any = None


def set_hou(module: Any):
    """Inject a hou stand-in for tests; production resolves the real module."""
    global _hou
    _hou = module


def _h() -> Any:
    global _hou
    if _hou is None:
        _hou = importlib.import_module("hou")
    return _hou


def scene_context() -> dict[str, Any]:
    hou = _h()
    hip = hou.hipFile.path()
    is_untitled = _is_untitled(hou)
    selected = [{"path": node.path(), "type": node.type().name()} for node in hou.selectedNodes()]
    current_network = None
    display_node = None
    # hou.ui exists only in a GUI session; hython and the tests have none.
    ui = getattr(hou, "ui", None)
    if ui is not None:
        pane = ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if pane is not None:
            network = pane.pwd()
            current_network = network.path()
            display = network.displayNode() if hasattr(network, "displayNode") else None
            display_node = display.path() if display is not None else None
    return {
        "hip": None if is_untitled else hip,
        "current_network": current_network,
        "selected": selected,
        "display_node": display_node,
        "frame": hou.frame(),
    }


def begin_turn(label: str) -> dict[str, Any]:
    """Open an undo group that outlives this request, so a whole agent turn is one Ctrl-Z.

    Houdini nests undo blocks by depth, so the per-command groups fxhoudinimcp
    opens inside this one merge into it rather than closing it.
    """
    global _open_group
    if _open_group is not None:
        end_turn()
    group = _h().undos.group(label)
    group.__enter__()
    _open_group = group
    return {"open": True}


def end_turn() -> dict[str, Any]:
    global _open_group
    if _open_group is None:
        return {"closed": False}
    group, _open_group = _open_group, None
    group.__exit__(None, None, None)
    return {"closed": True}


def turn_is_open() -> bool:
    return _open_group is not None


def memory_read() -> dict[str, Any]:
    """Hip memory: stored on the root node's user data, travels with the .hip file."""
    root = _h().node("/")
    text = root.userData(MEMORY_KEY)
    prev = root.userData(MEMORY_PREV_KEY)
    return {"text": text or "", "prev": prev if prev else None}


def memory_write(text: str, prev: str | None) -> dict[str, Any]:
    root = _h().node("/")
    root.setUserData(MEMORY_KEY, text)
    if prev:
        root.setUserData(MEMORY_PREV_KEY, prev)
    elif root.userData(MEMORY_PREV_KEY) is not None:
        root.destroyUserData(MEMORY_PREV_KEY)
    return {"chars": len(text), "prev": bool(prev)}


def _sessions_of(root: Any) -> list[str]:
    try:
        parsed = json.loads(root.userData(SESSIONS_KEY) or "[]")
    except ValueError:
        return []
    return [p for p in parsed if isinstance(p, str)] if isinstance(parsed, list) else []


def sessions_read() -> dict[str, Any]:
    """The conversations this hip has had, as pi session file paths.

    Kept on the root node's user data like hip memory, so Save As carries the
    list into the new file and the sessions tab there shows the same history.
    """
    return {"sessions": _sessions_of(_h().node("/"))}


def session_tag(path: str) -> dict[str, Any]:
    """Record a conversation on this hip. Outside undo: Ctrl-Z after a turn must undo the turn, not this."""
    hou = _h()
    root = hou.node("/")
    sessions = _sessions_of(root)
    if path in sessions:
        return {"sessions": sessions}
    sessions.append(path)
    with hou.undos.disabler():
        root.setUserData(SESSIONS_KEY, json.dumps(sessions))
    return {"sessions": sessions}


def _job() -> str | None:
    """$JOB is the Houdini convention for the project root; unset (a loose hip) means no project memory."""
    hou = _h()
    job = ""
    if hasattr(hou, "text") and hasattr(hou.text, "expandString"):
        job = hou.text.expandString("$JOB")
    if not job or job == "$JOB":
        job = os.environ.get("JOB", "")
    return job or None


def project_memory_read() -> dict[str, Any]:
    """Project memory, shared across all hips (and with Blender): see tech_art_agent.project_memory."""
    from tech_art_agent import project_memory

    return project_memory.read(_job())


def project_memory_write(text: str, prev: str | None) -> dict[str, Any]:
    from tech_art_agent import project_memory

    return project_memory.write(_job(), text, prev)


def _is_untitled(hou: Any) -> bool:
    """A scene that has never been saved. Houdini reports it as untitled.hip whether or not it has edits."""
    return hou.hipFile.basename() == "untitled.hip"


def backup_scene() -> dict[str, Any]:
    """Write a Houdini backup (hou.hipFile.saveAsBackup) so a turn can be recovered beyond undo.

    A never-saved scene has nowhere to back up to; that is reported as skipped,
    not raised, because the artist asking for work before saving is normal.
    """
    hou = _h()
    if _is_untitled(hou):
        return {"skipped": "the scene has never been saved; save it to get backups before agent edits"}
    try:
        return {"path": hou.hipFile.saveAsBackup()}
    except Exception as exc:  # hou.OperationFailed when $HOUDINI_BACKUP_DIR is unwritable, for one
        return {"skipped": f"Houdini refused the backup: {exc}"}


PANE_INTERFACE = "houdini_pi"


def _pi_tabs(hou: Any) -> list[Any]:
    ui = getattr(hou, "ui", None)
    if ui is None:
        return []
    return [
        tab
        for tab in ui.paneTabs()
        if tab.type() == hou.paneTabType.PythonPanel and tab.activeInterface() is not None and tab.activeInterface().name() == PANE_INTERFACE
    ]


def _pi_link_group(hou: Any):
    """The link group of the Pi pane tab, or None when no pane is open."""
    tabs = _pi_tabs(hou)
    return tabs[0].linkGroup() if tabs else None


def follow_nodes(paths: list[str]) -> dict[str, Any]:
    """Make the scene follow the agent: select the last node it touched and, when the Pi pane
    is in a link group, point every pane tab in that group at it (network editors to its
    parent network, parameter views to the node)."""
    hou = _h()
    nodes = [node for node in (hou.node(path) for path in paths) if node is not None]
    if not nodes:
        return {"followed": None, "panes": 0}
    target = nodes[-1]
    target.setSelected(True, clear_all_selected=True)

    group = _pi_link_group(hou)
    linked = group is not None and group not in (hou.paneLinkType.Pinned, hou.paneLinkType.FollowSelection)
    panes = 0
    if linked:
        parent = target.parent() or target
        for tab in hou.ui.paneTabs():
            if tab.linkGroup() != group or tab.type() == hou.paneTabType.PythonPanel:
                continue
            if tab.type() == hou.paneTabType.NetworkEditor:
                tab.setPwd(parent)
                tab.setCurrentNode(target)
                panes += 1
            elif hasattr(tab, "setCurrentNode"):
                tab.setCurrentNode(target)
                panes += 1
    categories = {node.path(): node.type().category().name() for node in nodes}
    return {"followed": target.path(), "panes": panes, "categories": categories}


PREVIEW_ROP = "houdini_pi_preview"


PREVIEW_MAX_AGE_S = 3600


def preview_dir():
    """Where previews land: the system temp dir. The core only reads them (it may not delete), so this
    prunes the ones nobody will look at again."""
    import tempfile
    import time
    from pathlib import Path

    out = Path(tempfile.gettempdir()) / "houdini-pi"
    out.mkdir(parents=True, exist_ok=True)
    cutoff = time.time() - PREVIEW_MAX_AGE_S
    for old in out.glob("*.png"):
        try:
            if old.stat().st_mtime < cutoff:
                old.unlink()
        except OSError:
            pass
    return out


def render_cop(node_path: str, max_size: int = 256) -> dict[str, Any]:
    """Render a Copernicus node to a PNG through a temporary File Output (rop_image) COP.

    The ROP is created beside the node, pointed at it with `coppath`, executed
    for the current frame and destroyed again, so the network is left as it
    was. Colour management is the ROP's default, which is what the viewer shows.
    """
    hou = _h()
    node = hou.node(node_path)
    if node is None:
        raise ValueError(f"no node at {node_path}")
    is_cop = node.type().category().name() == "Cop"
    if not is_cop:
        raise ValueError(f"{node_path} is a {node.type().category().name()} node, not a Copernicus (Cop) node")
    out = preview_dir() / f"cop_{node.name()}_{int(hou.frame())}.png"
    if out.exists():
        out.unlink()
    parent = node.parent()
    rop = parent.createNode("rop_image", PREVIEW_ROP)
    try:
        rop.parm("coppath").set(node.path())
        rop.parm("copoutput").set(str(out))
        rop.parm("trange").set(0)
        if max_size:
            rop.parm("setres").set(1)
            rop.parmTuple("res").set((max_size, max_size))
        rop.parm("execute").pressButton()
    finally:
        rop.destroy()
    if not out.exists():
        errors = node.errors() if hasattr(node, "errors") else ()
        raise RuntimeError(f"rendering {node_path} produced no file at {out}; node errors: {list(errors)}")
    return {"path": str(out), "node": node.path(), "frame": hou.frame()}


def _displayed_errors(pane: Any) -> dict[str, list[str]]:
    """Errors on the nodes a scene viewer draws, keyed by node path; empty when all is well.

    Reads what the last cook left (`errors()`), it does not cook: forcing a cook here would
    be the slow, blocking thing this check exists to avoid. So it catches a network already
    known to be broken, which is the case that froze Houdini.
    """
    network = pane.pwd()
    display = network.displayNode() if hasattr(network, "displayNode") else None
    if display is not None:
        drawn = [display]
    else:
        # Object level: every displayed object draws its own display SOP.
        drawn = [
            child.displayNode()
            for child in network.children()
            if child.isDisplayFlagSet() and hasattr(child, "displayNode") and child.displayNode() is not None
        ]
    return {node.path(): list(node.errors()) for node in drawn if node.errors()}


def capture_viewport(pane_name: str | None = None) -> dict[str, Any]:
    """Screenshot the scene viewer (or the named pane tab) into the temp preview dir.

    Calls fxhoudinimcp's capture_screenshot HANDLER directly, so the capture semantics
    (flipbook for scene viewers) stay upstream's. NOT `dispatcher.dispatch`: this helper
    already runs on Houdini's main thread (it arrives through execute_python, which dispatch
    put there), and dispatch from the main thread deadlocks. It queues the capture for the
    main thread from a worker and then blocks the main thread joining that worker, so
    nothing runs until the join times out (FXHOUDINIMCP_TIMEOUT, 120 s); then the orphaned
    capture runs anyway with nobody waiting for it. Measured on a dev workstation 2026-10-12: the
    viewport froze, the sidecar gave up at 60 s, and a flipbook "Failed" box came after.

    Refuses when the viewer draws nodes that failed to cook. A flipbook re-cooks the display
    node on the main thread, so a broken network costs a long freeze and a modal error box
    instead of an answer; the errors themselves are the answer.
    """
    hou = _h()
    from fxhoudinimcp_server.handlers import viewport_handlers

    # Upstream's own pane resolution, so the pane checked is the pane captured.
    pane = viewport_handlers._find_pane_by_name(pane_name) if pane_name is not None else viewport_handlers._find_scene_viewer()
    if pane.type() == hou.paneTabType.SceneViewer:
        broken = _displayed_errors(pane)
        if broken:
            listed = "\n".join(f"{path}: {' | '.join(errors)}" for path, errors in broken.items())
            raise RuntimeError(f"not capturing: the viewport draws nodes that failed to cook, and a capture would re-cook them. Fix these first:\n{listed}")

    out = preview_dir() / f"viewport_{int(hou.frame())}.png"
    if out.exists():
        out.unlink()
    params: dict[str, Any] = {"output_path": str(out)}
    if pane_name:
        params["pane_name"] = pane_name
    result = viewport_handlers.capture_screenshot(**params)
    # The flipbook may insert the frame number into the name; upstream reports the real one.
    written = result.get("output_path") or str(out)
    if not os.path.isfile(written):
        raise RuntimeError(f"viewport capture reported success but {written} is missing")
    return {"path": written, "pane": pane_name, "frame": hou.frame()}


def load_hip(path: str) -> dict[str, Any]:
    """Open a hip for a conversation that worked on it. Refuses while the current scene has unsaved edits:
    a modal save prompt would block this request, and silently discarding work is worse."""
    hou = _h()
    if hou.hipFile.hasUnsavedChanges():
        raise RuntimeError(f"the current scene has unsaved changes; save or discard them, then open {path}")
    hou.hipFile.load(path, suppress_save_prompt=True, ignore_load_warnings=True)
    return {"hip": hou.hipFile.path()}


def validate_paths(paths: list[str]) -> dict[str, Any]:
    """What each path names in the scene: "node", "parm", or None when nothing is there."""
    hou = _h()
    kinds: dict[str, str | None] = {}
    for path in paths:
        if hou.node(path) is not None:
            kinds[path] = "node"
        elif hou.parm(path) is not None or (hasattr(hou, "parmTuple") and hou.parmTuple(path) is not None):
            kinds[path] = "parm"
        else:
            kinds[path] = None
    return {"kinds": kinds}


def _pane_of_type(hou: Any, name: str):
    kind = getattr(hou.paneTabType, name, None)
    if kind is None:
        raise ValueError(f"no pane tab type named {name!r}")
    tab = hou.ui.paneTabOfType(kind)
    if tab is None:
        tab = hou.ui.curDesktop().createFloatingPaneTab(kind)
    tab.setIsCurrentTab()
    return tab


def do_action(kind: str, value: str) -> dict[str, Any]:
    """Run one answer-button action: select, parm, network, pane or help."""
    hou = _h()
    if kind == "select":
        return follow_nodes([value])
    if kind == "network":
        node = hou.node(value)
        if node is None:
            raise ValueError(f"no node at {value}")
        target = node if node.isNetwork() else node.parent()
        editor = hou.ui.paneTabOfType(hou.paneTabType.NetworkEditor)
        if editor is None:
            raise RuntimeError("no network editor is open")
        editor.setPwd(target)
        editor.setCurrentNode(node)
        node.setSelected(True, clear_all_selected=True)
        return {"network": target.path()}
    if kind == "parm":
        parm = hou.parm(value) or (hou.parmTuple(value) if hasattr(hou, "parmTuple") else None)
        if parm is None:
            raise ValueError(f"no parameter at {value}")
        node = parm.node()
        node.setSelected(True, clear_all_selected=True)
        pane = hou.ui.paneTabOfType(hou.paneTabType.Parm)
        if pane is None:
            pane = hou.ui.curDesktop().createFloatingPaneTab(hou.paneTabType.Parm)
        pane.setCurrentNode(node)
        pane.setIsCurrentTab()
        label = parm.description() if hasattr(parm, "description") else parm.name()
        hou.ui.setStatusMessage(f"houdini-pi: {label} ({parm.name()}) on {node.path()}")
        return {"node": node.path(), "parm": parm.name()}
    if kind == "pane":
        tab = _pane_of_type(hou, value)
        return {"pane": value, "opened": tab is not None}
    if kind == "help":
        browser = _pane_of_type(hou, "HelpBrowser")
        browser.displayHelpPath(value)
        return {"help": value}
    raise ValueError(f"unknown action {kind!r}")
