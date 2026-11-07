"""Where the agent keeps its machine-wide state, shared by every host.

One directory for the whole machine: `settings.json`, sidecar logs and tokens,
tool images, and `install.json` (where the installer built the sidecar).
Houdini and Blender read the same files, so a model choice made once applies
in both.

`TECH_ART_AGENT_HOME` overrides. Otherwise `~/.tech-art-agent`, except that an
existing `~/.houdini-pi` (the directory this project used before it grew a
second host) keeps being used, so nobody's settings and history go missing.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

HOME_ENV = "TECH_ART_AGENT_HOME"
ROOT_ENV = "TECH_ART_AGENT_ROOT"
NODE_ENV = "TECH_ART_AGENT_NODE"
UVX_ENV = "TECH_ART_AGENT_UVX"
# The names the Houdini-only releases used; still honoured.
LEGACY_ROOT_ENV = "HOUDINI_PI_ROOT"
LEGACY_NODE_ENV = "HOUDINI_PI_NODE"
LEGACY_UVX_ENV = "HOUDINI_PI_UVX"

DIR_NAME = ".tech-art-agent"
LEGACY_DIR_NAME = ".houdini-pi"


def state_dir(env: dict[str, str] | None = None, home: Path | None = None) -> Path:
    env = os.environ if env is None else env
    if env.get(HOME_ENV):
        return Path(env[HOME_ENV])
    home = home or Path.home()
    current, legacy = home / DIR_NAME, home / LEGACY_DIR_NAME
    if not current.exists() and legacy.exists():
        return legacy
    return current


def settings_file(env: dict[str, str] | None = None, home: Path | None = None) -> Path:
    return state_dir(env, home) / "settings.json"


def read_settings(file: Path | None = None) -> dict:
    """The settings file as a dict; missing or unreadable is `{}` (the sidecar is the strict reader)."""
    try:
        data = json.loads((file or settings_file()).read_text())
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def env_first(env: dict[str, str], *names: str) -> str | None:
    for name in names:
        if env.get(name):
            return env[name]
    return None


def install_record(env: dict[str, str] | None = None) -> dict:
    """What `./install` recorded: `{"root": ..., "node": ..., "uvx": ...}`. `{}` when it never ran."""
    return read_settings(state_dir(env) / "install.json")
