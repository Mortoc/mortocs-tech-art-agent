"""Starting the core when nothing else will.

On NixOS the socket unit wakes the core the moment an app connects, so this is never needed. Elsewhere,
`./install` records how to start it in `install.json` (`{"core": ["/path/to/tech-art-core"]}`); a connector that
cannot connect runs that, detached, and tries again.
"""

from __future__ import annotations

import os
import subprocess
from pathlib import Path

from .paths import install_record, state_dir


def core_command(env: dict[str, str] | None = None) -> list[str] | None:
    env = os.environ if env is None else env
    if env.get("TECH_ART_AGENT_CORE_CMD"):
        return env["TECH_ART_AGENT_CORE_CMD"].split()
    command = install_record(env).get("core")
    return list(command) if isinstance(command, list) and command else None


def wake_core(env: dict[str, str] | None = None, popen=subprocess.Popen) -> bool:
    """Start the core detached. False: no way to start it is configured (or it is the socket unit's job)."""
    command = core_command(env)
    if command is None:
        return False
    directory = state_dir(env)
    directory.mkdir(parents=True, exist_ok=True)
    log = open(directory / "core.log", "ab")
    # The app's own environment points its loader and Python at the app's libraries; the core wants its own.
    clean = {k: v for k, v in (os.environ if env is None else env).items() if k not in ("LD_LIBRARY_PATH", "LD_PRELOAD", "PYTHONPATH", "PYTHONHOME", "QT_PLUGIN_PATH")}
    popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, env=clean)
    return True
