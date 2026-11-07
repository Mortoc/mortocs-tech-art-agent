"""Tech Art Agent for Blender: connects this Blender to the agent core and gives it a sidebar panel.

The model's Blender tools come from the mcp-for-blender server the core runs (install its add-on too:
`uvx mcp-for-blender install-addon`). This extension is the other half: the lifecycle helpers (scene context,
memory in the .blend, undo grouping, backups, following the agent) and the connection to the core.
"""

import os
import queue
import threading
import webbrowser

import bpy
from bpy.app.handlers import persistent

from . import helpers
from .tech_art_agent import paths
from .tech_art_agent.connector import Connector, Tool, core_url
from .tech_art_agent.core_launch import wake_core

HELPER_NAMES = (
    "scene_context", "begin_turn", "end_turn", "undo_turn", "backup_scene", "memory_read", "memory_write",
    "project_memory_read", "project_memory_write", "sessions_read", "session_tag", "follow", "validate_paths",
    "do_action", "capture_viewport",
)

_connector = None
_calls: "queue.Queue" = queue.Queue()
_status = {"text": "Not connected", "log": []}


def _log(message):
    _status["log"] = (_status["log"] + [message])[-20:]


def run_on_main(fn, timeout=120.0):
    """Run `fn` on Blender's main thread (bpy is not thread-safe) and return its result, from a worker thread."""
    done = threading.Event()
    box = {}
    _calls.put((fn, box, done))
    if not done.wait(timeout):
        raise TimeoutError("Blender did not get to the call in time; it may be busy (a modal operator or dialog)")
    if "error" in box:
        raise box["error"]
    return box["value"]


def _drain():
    """bpy.app.timers callback, on the main thread: run queued calls."""
    while True:
        try:
            fn, box, done = _calls.get_nowait()
        except queue.Empty:
            break
        try:
            box["value"] = fn()
        except Exception as error:
            box["error"] = error
        finally:
            done.set()
    _refresh_status()
    return 0.05


def _document_name():
    return bpy.path.basename(bpy.data.filepath) or "untitled.blend"


def _meta():
    prefs = _prefs()
    return {"bridgePort": int(prefs.mcp_port) if prefs else 9876, "host": "localhost", "blender": bpy.app.version_string}


def _prefs():
    addon = bpy.context.preferences.addons.get(__package__)
    return addon.preferences if addon else None


def _refresh_status():
    if _connector is None:
        _status["text"] = "Stopped"
    else:
        _status["text"] = f"Connected to the core ({_document_name()})" if _connector.connected else "Waiting for the core…"


def start():
    global _connector
    if _connector is not None:
        return
    tools = [Tool(name, getattr(helpers, name), internal=True) for name in HELPER_NAMES]
    port = _prefs().core_port if _prefs() else int(os.environ.get("TECH_ART_AGENT_PORT", 9100))
    _connector = Connector(
        host="blender", name=_document_name, tools=tools, meta=_meta, run_on_main=run_on_main,
        url=lambda: core_url({"TECH_ART_AGENT_PORT": str(port)}), wake=wake_core, log=_log,
    )
    _connector.start()


def stop():
    global _connector
    if _connector is not None:
        _connector.stop()
        _connector = None


@persistent
def _on_document_changed(*_args):
    """Save As / Open: same connection, the new name; the conversation list travels in the document."""
    if _connector is not None and _connector.connected:
        _connector.rename(_document_name())


def pane_url():
    prefs = _prefs()
    port = prefs.core_port if prefs else int(os.environ.get("TECH_ART_AGENT_PORT", 9100))
    token = paths.state_dir() / "core.token"
    client = _connector.client_id if _connector and _connector.client_id else ""
    return f"http://127.0.0.1:{port}/?token={token.read_text().strip() if token.exists() else ''}&client={client}"


class TECHART_OT_open_agent(bpy.types.Operator):
    bl_idname = "techart.open_agent"
    bl_label = "Open Agent"
    bl_description = "Open the chat for this Blender in your browser"

    def execute(self, context):
        if _connector is None or not _connector.connected:
            self.report({"WARNING"}, "Not connected to the agent core yet")
            return {"CANCELLED"}
        webbrowser.open(pane_url())
        return {"FINISHED"}


class TECHART_OT_reconnect(bpy.types.Operator):
    bl_idname = "techart.reconnect"
    bl_label = "Reconnect"
    bl_description = "Drop the connection to the agent core and connect again"

    def execute(self, context):
        stop()
        start()
        return {"FINISHED"}


class TECHART_PT_panel(bpy.types.Panel):
    bl_label = "Agent"
    bl_idname = "TECHART_PT_panel"
    bl_space_type = "VIEW_3D"
    bl_region_type = "UI"
    bl_category = "Agent"

    def draw(self, context):
        layout = self.layout
        layout.label(text=_status["text"], icon="CHECKMARK" if _connector and _connector.connected else "TIME")
        layout.operator("techart.open_agent", icon="URL")
        layout.operator("techart.reconnect", icon="FILE_REFRESH")
        if _status["log"] and not (_connector and _connector.connected):
            box = layout.box()
            for line in _status["log"][-3:]:
                box.label(text=line[:60])


class TechArtPreferences(bpy.types.AddonPreferences):
    bl_idname = __package__

    core_port: bpy.props.IntProperty(name="Core port", default=9100, min=1, max=65535, description="Where the agent core listens on this machine")
    mcp_port: bpy.props.IntProperty(name="Blender MCP add-on port", default=9876, min=1, max=65535, description="The port the mcp-for-blender add-on listens on")

    def draw(self, context):
        col = self.layout.column()
        col.prop(self, "core_port")
        col.prop(self, "mcp_port")
        col.label(text="Settings shared with Houdini live in ~/.tech-art-agent/settings.json")


CLASSES = (TECHART_OT_open_agent, TECHART_OT_reconnect, TECHART_PT_panel, TechArtPreferences)


def register():
    for cls in CLASSES:
        bpy.utils.register_class(cls)
    bpy.app.timers.register(_drain, persistent=True)
    bpy.app.handlers.save_post.append(_on_document_changed)
    bpy.app.handlers.load_post.append(_on_document_changed)
    # Preferences are not readable inside register(); start on the first timer tick.
    bpy.app.timers.register(lambda: (start(), None)[1], first_interval=0.5)


def unregister():
    stop()
    for handler in (bpy.app.handlers.save_post, bpy.app.handlers.load_post):
        if _on_document_changed in handler:
            handler.remove(_on_document_changed)
    if bpy.app.timers.is_registered(_drain):
        bpy.app.timers.unregister(_drain)
    for cls in reversed(CLASSES):
        bpy.utils.unregister_class(cls)
