"""Document memory and project memory: curated markdown that travels with the scene (or the project).

Both live inside the app, behind its connector's `memory_*` / `project_memory_*` helpers, so they ride along
through save-as and version control. `prev` holds the pre-compaction text for a one-step rollback.
"""

from __future__ import annotations

import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from pathlib import Path
from typing import Any

Helper = Callable[..., Awaitable[Any]]

TOKENS_PER_CHAR = 0.25
COMPACT_THRESHOLD_TOKENS = 16_384
COMPACT_TARGET_TOKENS = 8_192


def estimate_tokens(text: str) -> int:
    return -(-int(len(text) * TOKENS_PER_CHAR * 4) // 4)


def append_to_section(memory: str, section: str, text: str) -> str:
    """Append `text` under `## <section>`, creating the heading at the end when absent."""
    heading = f"## {section.strip()}"
    lines = memory.rstrip().split("\n") if memory else []
    entry = text.strip()
    start = next((i for i, line in enumerate(lines) if line.strip() == heading), -1)
    if start == -1:
        prefix = [*lines, ""] if lines else []
        return "\n".join([*prefix, heading, "", entry, ""])
    end = next((i for i in range(start + 1, len(lines)) if re.match(r"^## ", lines[i])), len(lines))
    while end > start + 1 and lines[end - 1].strip() == "":
        end -= 1
    rest = lines[end:]
    while rest and rest[0].strip() == "":
        rest.pop(0)
    return "\n".join([*lines[:end], entry, *(["", *rest] if rest else [])]) + "\n"


@dataclass
class Snapshot:
    text: str
    prev: str | None
    available: bool = True


class MemoryStore:
    """One memory (document or project) behind a pair of helpers."""

    def __init__(self, helper: Helper, read: str, write: str, label: str) -> None:
        self._helper, self._read, self._write, self.label = helper, read, write, label

    async def snapshot(self) -> Snapshot:
        raw = await self._helper(self._read)
        return Snapshot(text=raw.get("text", ""), prev=raw.get("prev"), available=raw.get("available", True))

    async def read(self) -> str:
        return (await self.snapshot()).text

    async def replace(self, text: str) -> None:
        current = await self.snapshot()
        await self._helper(self._write, text=text, prev=current.text)

    async def append(self, section: str, text: str) -> str:
        current = (await self.snapshot()).text
        merged = append_to_section(current, section, text)
        await self._helper(self._write, text=merged, prev=current)
        return merged

    async def rollback(self) -> None:
        current = await self.snapshot()
        if current.prev is None:
            raise ValueError(f"no previous {self.label} to roll back to")
        await self._helper(self._write, text=current.prev, prev=None)


def condense_prompt(memory: str, target_tokens: int = COMPACT_TARGET_TOKENS) -> str:
    return "\n".join([
        f"Condense the memory below to under {target_tokens} tokens.",
        "Preserve every decision, naming convention and scene path verbatim.",
        "Drop narration, repetition and anything a reader could re-derive from the scene.",
        "Keep the markdown section structure. Answer with the condensed memory only.",
        "", "----", memory,
    ])


async def maybe_compact(store: MemoryStore, condense: Callable[[str], Awaitable[str]], threshold: int = COMPACT_THRESHOLD_TOKENS) -> dict[str, int] | None:
    """Compact when over the threshold; None when nothing needed doing. A bad result leaves memory untouched."""
    current = await store.snapshot()
    before = estimate_tokens(current.text)
    if before <= threshold:
        return None
    condensed = (await condense(condense_prompt(current.text))).strip()
    if not condensed or len(condensed) >= len(current.text):
        raise ValueError(f"compaction produced {len(condensed)} chars from {len(current.text)}; memory left untouched")
    await store.replace(condensed + "\n")
    return {"before": before, "after": estimate_tokens(condensed)}


class FileMemoryStore(MemoryStore):
    """A memory in one fixed file under the core's state directory (the user's own, across all apps).

    The path is fixed at construction: the model can read and append but never names a file. This is the
    only disk the agent can write that is not a scene or project file, and it goes through these tools.
    """

    def __init__(self, path: Path, label: str = "user memory") -> None:
        self.path, self._prev = path, path.with_suffix(".prev.md")
        super().__init__(self._helper_stub, "read", "write", label)

    async def _helper_stub(self, name: str, **kw: Any) -> Any:
        if name == "read":
            return {
                "text": self.path.read_text(encoding="utf-8") if self.path.exists() else "",
                "prev": self._prev.read_text(encoding="utf-8") if self._prev.exists() else None,
            }
        self.path.parent.mkdir(parents=True, exist_ok=True)
        self.path.write_text(kw["text"], encoding="utf-8")
        if kw["prev"] is None:
            self._prev.unlink(missing_ok=True)
        else:
            self._prev.write_text(kw["prev"], encoding="utf-8")
        return {}
