"""Project memory: one markdown file per project, shared by every scene and every app in it.

Lives at `$JOB/.tech-art-agent/memory.md` (`memory.md.prev` beside it keeps one step of rollback). A project that
already has `$JOB/.houdini-pi/memory.md` from before this project grew a second host keeps using that file, so
nothing is orphaned. Houdini and Blender both call this, so they cannot disagree about where it is.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

DIR = ".tech-art-agent"
LEGACY_DIR = ".houdini-pi"
FILE = "memory.md"
PREV_FILE = "memory.md.prev"


def memory_dir(job: str | None) -> Path | None:
    """The directory holding the project's memory, or None when there is no project ($JOB unset)."""
    if not job or job == "$JOB":
        return None
    root = Path(job)
    if not (root / DIR / FILE).exists() and (root / LEGACY_DIR / FILE).exists():
        return root / LEGACY_DIR
    return root / DIR


def read(job: str | None) -> dict[str, Any]:
    base = memory_dir(job)
    if base is None:
        return {"available": False, "text": "", "prev": None}
    return {"available": True, "text": _read(base / FILE) or "", "prev": _read(base / PREV_FILE)}


def write(job: str | None, text: str, prev: str | None) -> dict[str, Any]:
    base = memory_dir(job)
    if base is None:
        raise RuntimeError("$JOB is not set; project memory needs a project root")
    base.mkdir(parents=True, exist_ok=True)
    (base / FILE).write_text(text)
    prev_path = base / PREV_FILE
    if prev:
        prev_path.write_text(prev)
    else:
        prev_path.unlink(missing_ok=True)
    return {"chars": len(text), "prev": bool(prev)}


def _read(path: Path) -> str | None:
    try:
        return path.read_text()
    except FileNotFoundError:
        return None
