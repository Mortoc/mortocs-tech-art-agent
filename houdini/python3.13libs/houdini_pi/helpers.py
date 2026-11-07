"""The helper tools the core calls inside Houdini, in the contract of `tech_art_core.hosts.HELPERS`.

`hooks_target` holds the Houdini work (it predates the core and is tested against a fake `hou`); this module
only reshapes it into the host-independent contract the Blender extension also implements.
"""

from __future__ import annotations

from typing import Any

from . import hooks_target as ht


def scene_context() -> dict[str, Any]:
    c = ht.scene_context()
    return {
        "document": c["hip"],
        "current": c["current_network"],
        "selected": c["selected"],
        "extra": {"display node": c["display_node"] or "(none)", "frame": c["frame"]},
    }


def follow(paths: list[str]) -> dict[str, Any]:
    return ht.follow_nodes(paths)


def undo_turn() -> dict[str, Any]:
    ht._h().undos.performUndo()
    return {}


# name -> function. Everything the core may call; the first group is HELPERS, the rest are the inspectors' images.
FUNCTIONS = {
    "scene_context": scene_context,
    "begin_turn": ht.begin_turn,
    "end_turn": ht.end_turn,
    "undo_turn": undo_turn,
    "backup_scene": ht.backup_scene,
    "memory_read": ht.memory_read,
    "memory_write": ht.memory_write,
    "project_memory_read": ht.project_memory_read,
    "project_memory_write": ht.project_memory_write,
    "sessions_read": ht.sessions_read,
    "session_tag": ht.session_tag,
    "follow": follow,
    "validate_paths": ht.validate_paths,
    "do_action": ht.do_action,
    "render_cop": ht.render_cop,
    "capture_viewport": ht.capture_viewport,
}
