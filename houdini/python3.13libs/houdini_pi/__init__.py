"""Houdini side of the tech-art agent: a connector to the agent core, the pane, and the scene helpers.

The shared client library (python/tech_art_agent, a sibling of houdini/ in this repo and in the Nix package)
is put on sys.path here, so the package file only needs to add houdini/ to the path.
"""

import sys
from pathlib import Path

PACKAGES = ("houdini_pi", "tech_art_agent")


def _expose_shared_library() -> None:
    shared = Path(__file__).resolve().parents[3] / "python"
    if shared.is_dir() and str(shared) not in sys.path:
        sys.path.insert(0, str(shared))


_expose_shared_library()


def reload():
    """Pick up edits to this package without restarting Houdini.

    Stops the connector, drops every module of this package and the shared library from `sys.modules`,
    imports them fresh and starts again. Open Agent panes are re-created so they run the new code. The core
    sees the app reconnect under the same process id, so the pane keeps its conversation.
    """
    import importlib

    from houdini_pi import runtime

    runtime.stop()
    for name in [n for n in sys.modules if n.split(".")[0] in PACKAGES]:
        del sys.modules[name]
    importlib.invalidate_caches()
    _expose_shared_library()
    fresh = importlib.import_module("houdini_pi.runtime")
    fresh.start()
    fresh.refresh_panes()
    return fresh
