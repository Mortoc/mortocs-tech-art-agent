"""The private-name -> alias table behind scripts/rename and scripts/check-secrets. Standard library only.

`.renames` is a LOCAL, gitignored file (never committed; copy it between machines by hand). One pair per line:

    <private name> TAB <alias>          # blank lines and #-comments are ignored

A pair is matched case-sensitively as written, and its lower-case, Capitalised and UPPER-CASE variants are added
automatically unless a line spells them out (explicit lines win). Replacement respects word boundaries, so "ana"
inside "banana" is left alone; detection is stricter (case-insensitive, also inside identifiers) because a leak
hiding inside `fooSomeNameBar` is still a leak.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NAME = ".renames"
HEADER = (
    "# private name<TAB>alias   (local only: this file is gitignored; copy it between machines by hand)\n"
    "# Every private name that shows up in a public repo gets a line here, and then only the alias is written down.\n"
)


def default_path() -> Path:
    return Path(os.environ.get("RENAMES_FILE") or ROOT / NAME)


def _variants(word: str) -> list[str]:
    cap = word[:1].upper() + word[1:]
    return list(dict.fromkeys([word, word.lower(), cap, word.upper()]))


def _bounded(term: str, flags: int = 0) -> re.Pattern[str]:
    return re.compile(r"(?<![A-Za-z0-9])" + re.escape(term) + r"(?![A-Za-z0-9])", flags)


@dataclass(frozen=True)
class Pair:
    private: str
    alias: str


class Renames:
    def __init__(self, pairs: list[Pair]) -> None:
        self.pairs = pairs
        explicit = {p.private for p in pairs}
        forward: dict[str, str] = {}
        for pair in pairs:
            forward[pair.private] = pair.alias
        for pair in pairs:
            for private, alias in zip(_variants(pair.private), _variants(pair.alias)):
                if private not in explicit:
                    forward.setdefault(private, alias)
        self.forward = forward
        self.reverse: dict[str, str] = {}
        for private, alias in forward.items():
            self.reverse.setdefault(alias, private)
        # longest first, so "mortoc-thing" is replaced before "thing" can nibble at it
        self._fwd = [(_bounded(k), v) for k, v in sorted(forward.items(), key=lambda kv: -len(kv[0]))]
        self._rev = [(_bounded(k), v) for k, v in sorted(self.reverse.items(), key=lambda kv: -len(kv[0]))]
        self._loose = [(re.compile(re.escape(k), re.I), v) for k, v in sorted({p.private.lower(): p.alias for p in pairs}.items(), key=lambda kv: -len(kv[0]))]
        self._detect = [(re.compile(re.escape(p.private), re.I), p.alias) for p in sorted(pairs, key=lambda p: -len(p.private))]

    # --- loading / editing --------------------------------------------------------------------

    @classmethod
    def parse(cls, text: str) -> Renames:
        pairs = []
        for number, line in enumerate(text.splitlines(), 1):
            if not line.strip() or line.lstrip().startswith("#"):
                continue
            parts = line.rstrip("\n").split("\t")
            if len(parts) != 2 or not all(p.strip() for p in parts):
                raise ValueError(f"{NAME} line {number}: expected `private<TAB>alias`")
            pairs.append(Pair(parts[0].strip(), parts[1].strip()))
        return cls(pairs)

    @classmethod
    def load(cls, path: Path | None = None) -> Renames | None:
        """The table, or None when this machine has no .renames (CI never does)."""
        path = path or default_path()
        try:
            return cls.parse(path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return None

    def problem_with(self, private: str, alias: str) -> str | None:
        if not private.strip() or not alias.strip():
            return "both the private name and the alias are required"
        if "\t" in private or "\t" in alias or "\n" in private + alias:
            return "names cannot contain tabs or newlines"
        if private.lower() in alias.lower():
            return "the alias contains the private name"
        if alias.lower() in {p.alias.lower() for p in self.pairs}:
            return f"alias {alias!r} is already used for another name (aliases must be unique)"
        if private.lower() in {p.private.lower() for p in self.pairs}:
            return f"{private!r} is already in the table"
        if any(p.private.lower() in alias.lower() for p in self.pairs):
            return "the alias contains another private name"
        return None

    # --- using the table ----------------------------------------------------------------------

    def sanitize(self, text: str, loose: bool = False) -> tuple[str, int]:
        """Replace private names with aliases. Returns (new text, number of replacements)."""
        total = 0
        for pattern, alias in self._loose if loose else self._fwd:
            text, n = pattern.subn(alias, text)
            total += n
        return text, total

    def reveal(self, text: str) -> str:
        """Aliases back to the private names. For reading only: never write this into a file that gets published."""
        for pattern, private in self._rev:
            text = pattern.sub(private, text)
        return text

    def find(self, text: str) -> list[tuple[int, str]]:
        """(line number, ALIAS) for every line holding a private name, however it is cased or embedded."""
        hits = []
        for number, line in enumerate(text.splitlines(), 1):
            for pattern, alias in self._detect:
                if pattern.search(line):
                    hits.append((number, alias))
        return hits

    def lookup(self, term: str) -> list[Pair]:
        t = term.lower()
        return [p for p in self.pairs if t in (p.private.lower(), p.alias.lower())]
