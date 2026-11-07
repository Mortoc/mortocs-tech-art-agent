"""Starts and stops this Houdini's connection to the agent core. Qt-free; the pane lives in `panel`.

Everything here is restartable: `stop()` then `start()` is a hot reload (see `houdini_pi.reload`).
"""

from __future__ import annotations

import os
import sys
import traceback
from typing import Any, Callable

from tech_art_agent.connector import Connector, Tool, core_url
from tech_art_agent.core_launch import wake_core
from tech_art_agent.paths import state_dir

from . import helpers
from .settings import auto_layout_wanted, bridge_port

_connector: Connector | None = None
_hip_callback: Callable | None = None
_main_runner: Callable[[Callable[[], Any]], Any] | None = None
LOG: list[str] = []


def _log(message: str) -> None:
    LOG.append(message)
    del LOG[:-50]
    print(f"[houdini-pi] {message}", file=sys.stderr)


def set_main_runner(runner: Callable[[Callable[[], Any]], Any] | None) -> None:
    """Override how calls reach Houdini's main thread (tests and hython pump their own queue)."""
    global _main_runner
    _main_runner = runner


def run_on_main(fn: Callable[[], Any]) -> Any:
    """Run `fn` on Houdini's main thread. `hou` is not thread-safe, and the connector calls from its own thread."""
    if _main_runner is not None:
        return _main_runner(fn)
    import hou

    if not hou.isUIAvailable():
        return fn()  # hython has no event loop to defer to
    import hdefereval

    return hdefereval.executeInMainThreadWithResult(fn)


def document_name() -> str:
    import hou

    return hou.hipFile.basename()


def meta() -> dict[str, Any]:
    import hou

    return {"bridgePort": bridge_port(), "houdini": hou.applicationVersionString()}


def _on_hip_event(event_type: Any) -> None:
    """Open / Save As / New: same connection, the new name. The conversation list travels in the hip."""
    import hou

    renamed = (hou.hipFileEventType.AfterLoad, hou.hipFileEventType.AfterSave, hou.hipFileEventType.AfterClear)
    if _connector is not None and event_type in renamed:
        _connector.rename(document_name())


def start() -> Connector:
    """Connect to the core (waking it if need be). Idempotent."""
    global _connector, _hip_callback
    if _connector is not None:
        return _connector
    import hou

    wanted = auto_layout_wanted()
    if wanted is not None:
        hou.putenv("FXHOUDINIMCP_AUTO_LAYOUT", "1" if wanted else "0")
    tools = [Tool(name, fn, internal=True) for name, fn in helpers.FUNCTIONS.items()]
    _connector = Connector(
        host="houdini", name=document_name, tools=tools, meta=meta, run_on_main=run_on_main,
        url=core_url, wake=wake_core, ready=lambda: bridge_port() is not None, log=_log,
    )
    _hip_callback = _on_hip_event
    hou.hipFile.addEventCallback(_hip_callback)
    _connector.start()
    return _connector


def stop() -> None:
    global _connector, _hip_callback
    if _hip_callback is not None:
        try:
            import hou

            hou.hipFile.removeEventCallback(_hip_callback)
        except Exception:
            traceback.print_exc()
        _hip_callback = None
    if _connector is not None:
        _connector.stop()
        _connector = None


def connected() -> bool:
    return _connector is not None and _connector.connected


def pane_url() -> str:
    """The pane's address. `client=pid:<pid>` names this Houdini and survives a reconnect or hot reload."""
    port = int(os.environ.get("TECH_ART_AGENT_PORT", 9100))
    directory = state_dir()
    token = core_url({}, directory).split("token=")[1]
    return f"http://127.0.0.1:{port}/?token={token}&client=pid:{os.getpid()}"


def refresh_panes() -> None:
    """Re-create open Agent panes so they run the freshly imported panel code."""
    try:
        import hou

        if not hou.isUIAvailable():
            return
        interface = hou.pypanel.interfaceByName("houdini_pi")
        for tab in hou.ui.paneTabs():
            active = tab.type() == hou.paneTabType.PythonPanel and tab.activeInterface()
            if active and active.name() == "houdini_pi":
                tab.setActiveInterface(interface)
    except Exception:
        traceback.print_exc()
