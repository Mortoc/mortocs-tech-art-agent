from __future__ import annotations

import logging
from collections.abc import Callable
from typing import Any

from .wire import topic_matches

log = logging.getLogger(__name__)
Handler = Callable[[str, Any, str], None]


class Bus:
    """In-process topic bus. Apps (through the registry), agent sessions and the panes all meet here."""

    def __init__(self) -> None:
        self._subs: dict[Handler, set[str]] = {}

    def subscribe(self, pattern: str, handler: Handler) -> Callable[[], None]:
        patterns = self._subs.setdefault(handler, set())
        patterns.add(pattern)

        def off() -> None:
            patterns.discard(pattern)
            if not patterns:
                self._subs.pop(handler, None)

        return off

    def publish(self, topic: str, data: Any = None, sender: str = "core") -> int:
        """Deliver to every handler with a matching pattern, once each. One failing handler does not stop the rest."""
        delivered = 0
        for handler, patterns in list(self._subs.items()):
            if not any(topic_matches(p, topic) for p in patterns):
                continue
            delivered += 1
            try:
                handler(topic, data, sender)
            except Exception:
                log.exception("bus handler failed on %s", topic)
        return delivered
