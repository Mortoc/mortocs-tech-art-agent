"""Which conversation each running app is on, so a core restart (or a hot reload) resumes the same chat.

Keyed by `pid:<n>` (the app's process): stable across reconnects, unique per running app. Entries older than
`MAX_AGE_S` are ignored, because a pid can be reused after the app is gone.
"""

from __future__ import annotations

import json
import time
from pathlib import Path

MAX_AGE_S = 12 * 3600


class CurrentConversations:
    def __init__(self, file: Path) -> None:
        self.file = file

    def _load(self) -> dict[str, dict]:
        try:
            data = json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, key: str) -> str | None:
        entry = self._load().get(key)
        if not entry or time.time() - entry.get("at", 0) > MAX_AGE_S:
            return None
        return entry.get("conversation")

    def set(self, key: str, conversation_id: str) -> None:
        data = {k: v for k, v in self._load().items() if time.time() - v.get("at", 0) <= MAX_AGE_S}
        data[key] = {"conversation": conversation_id, "at": time.time()}
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data))


class NameIndex:
    """Conversation titles, kept beside the conversations (the store itself has no title field)."""

    def __init__(self, file: Path) -> None:
        self.file = file

    def _load(self) -> dict[str, str]:
        try:
            data = json.loads(self.file.read_text())
        except (OSError, ValueError):
            return {}
        return data if isinstance(data, dict) else {}

    def get(self, conversation_id: str) -> str | None:
        return self._load().get(conversation_id)

    def set(self, conversation_id: str, name: str) -> None:
        data = self._load()
        data[conversation_id] = name
        self.file.parent.mkdir(parents=True, exist_ok=True)
        self.file.write_text(json.dumps(data))
