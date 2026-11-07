"""Houdini-specific readings of the shared settings file and environment. No Qt, no `hou` at import time."""

from __future__ import annotations

import os
from typing import Any

from tech_art_agent.paths import read_settings

BRIDGE_PORT_ENV = "HOUDINI_PI_BRIDGE_PORT"
_ASK_HOUDINI = object()
ZOOM_MIN, ZOOM_MAX = 0.25, 5.0  # QWebEngineView's accepted zoom range


def software_render_wanted(env: dict[str, str] | None = None, settings: dict | None = None) -> bool:
    """Off by default: the pane renders on the GPU like the rest of Houdini.

    `HOUDINI_PI_SOFTWARE_RENDER=1`, or `{"pane": {"softwareRender": true}}` in the settings file. It cannot change
    for a view that already exists, because Qt fixes the scene graph API at the first window.
    """
    env = os.environ if env is None else env
    if env.get("HOUDINI_PI_SOFTWARE_RENDER") == "1":
        return True
    settings = read_settings() if settings is None else settings
    return bool(settings.get("pane", {}).get("softwareRender", False))


def auto_layout_wanted(env: dict[str, str] | None = None, settings: dict | None = None) -> bool | None:
    """Whether to set FXHOUDINIMCP_AUTO_LAYOUT, and to what. None: leave the upstream default (off) alone."""
    env = os.environ if env is None else env
    raw = env.get("HOUDINI_PI_AUTO_LAYOUT")
    if raw is not None:
        return raw.strip() == "1"
    settings = read_settings() if settings is None else settings
    value = settings.get("layout", {}).get("autoLayout")
    return None if value is None else bool(value)


def _bridge_startup() -> Any:
    try:
        from fxhoudinimcp_server import startup

        return startup
    except Exception:  # not inside Houdini, or the plugin is not loaded
        return None


def bridge_port(env: dict[str, str] | None = None, startup: Any = _ASK_HOUDINI) -> int | None:
    """The port of *this* Houdini's fxhoudinimcp bridge. None: it is still starting, ask again.

    The bridge takes the first free port from FXHOUDINIMCP_PORT (8100), so a second Houdini serves on 8101. The
    core runs the MCP server against whatever this reports. `HOUDINI_PI_BRIDGE_PORT` overrides.
    """
    env = os.environ if env is None else env
    if env.get(BRIDGE_PORT_ENV):
        return int(env[BRIDGE_PORT_ENV])
    if startup is _ASK_HOUDINI:
        startup = _bridge_startup()
    if startup is not None:
        try:
            if startup.is_running():
                return int(startup.get_port())
            if startup.is_starting():
                return None
        except AttributeError:  # an fxhoudinimcp without these
            pass
    return int(env.get("FXHOUDINIMCP_PORT") or 8100)


def pane_zoom(scale: float | None) -> float:
    """The web view's zoom factor for Houdini's Global UI Size (`hou.ui.globalScaleFactor()`)."""
    try:
        scale = float(scale)
    except (TypeError, ValueError):
        return 1.0
    if not scale > 0:
        return 1.0
    return min(max(scale, ZOOM_MIN), ZOOM_MAX)
