"""Ask the models we use whether they actually work, with a tiny request each: text, an image, a tool call.

The gateway has no model-list endpoint, so "which models does this key reach?" can only be answered by trying. This runs
three cheap requests per model (a few hundred tokens in all) and reports, in plain words, what worked and what did not,
including why (an endpoint that does not exist, a model without pricing data, a key that was rejected).
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from .errors import describe, endpoint_for

def make_png(width: int = 32, height: int = 32) -> bytes:
    """A small, genuinely valid PNG: red on the left half, blue on the right. Built, not typed, because providers decode
    the image and refuse one with a bad checksum (a hand-typed 1x1 PNG did exactly that)."""
    import struct
    import zlib

    def chunk(kind: bytes, body: bytes) -> bytes:
        return struct.pack(">I", len(body)) + kind + body + struct.pack(">I", zlib.crc32(kind + body))

    row = b"\x00" + (b"\xff\x00\x00" * (width // 2)) + (b"\x00\x00\xff" * (width - width // 2))  # filter 0, then RGB pixels
    header = struct.pack(">IIBBBBB", width, height, 8, 2, 0, 0, 0)  # 8-bit RGB
    return b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", header) + chunk(b"IDAT", zlib.compress(row * height)) + chunk(b"IEND", b"")


TEST_PNG = make_png()


@dataclass
class Check:
    ok: bool
    ms: int = 0
    detail: str = ""


@dataclass
class ModelProbe:
    key: str
    text: Check = field(default_factory=lambda: Check(False, detail="not run"))
    image: Check | None = None
    tools: Check | None = None
    cost: float = 0.0

    @property
    def usable(self) -> bool:
        """Good enough to be the working agent: it answers, sees images and calls tools."""
        return self.text.ok and bool(self.image and self.image.ok) and bool(self.tools and self.tools.ok)


def first_sentence(message: str, limit: int = 420) -> str:
    """The whole explanation on one line (the actionable part is usually in the second sentence), capped for a terminal."""
    one_line = " ".join(message.split())
    return one_line if len(one_line) <= limit else one_line[: limit - 1] + "…"


async def _timed(label_ok: Callable[[Any], bool], run: Callable[[], Any], key: str, settings: Any) -> tuple[Check, Any]:
    started = time.monotonic()
    try:
        result = await run()
    except Exception as error:
        message, _ = describe(error, endpoint_for(key, settings.providers), key.partition("/")[0] or None, key)
        return Check(False, int((time.monotonic() - started) * 1000), first_sentence(message)), None
    ok = label_ok(result)
    return Check(ok, int((time.monotonic() - started) * 1000), "" if ok else f"unexpected answer: {str(result.output)[:60]!r}"), result


async def probe_model(key: str, settings: Any, build: Callable[[str], Any], quick: bool = False) -> ModelProbe:
    from pydantic_ai import Agent, BinaryContent

    from .session import _cost_of

    probe = ModelProbe(key)
    model = None
    try:
        model = build(key)
    except Exception as error:  # a missing key, an unknown route format...
        message, _ = describe(error, endpoint_for(key, settings.providers), key.partition("/")[0] or None, key)
        probe.text = Check(False, 0, first_sentence(message))
        return probe

    async def text():
        return await Agent(model).run("Reply with exactly one word: pong")

    probe.text, result = await _timed(lambda r: "pong" in str(r.output).lower(), text, key, settings)
    if result is not None:
        probe.cost += _cost_of(result.new_messages())
    if quick or not probe.text.ok:
        return probe  # no point asking a model that cannot answer to look at an image

    async def image():
        content = ["This image is made of two solid colours. Name both.", BinaryContent(data=TEST_PNG, media_type="image/png")]
        return await Agent(model).run(content)

    calls: list[tuple[int, int]] = []

    async def tools():
        agent = Agent(model, instructions="Use the tool for any arithmetic. Do not compute it yourself.")

        @agent.tool_plain
        def add_numbers(a: int, b: int) -> int:
            """Add two numbers."""
            calls.append((a, b))
            return a + b

        return await agent.run("What is 417 plus 268? Use the tool.")

    # The two checks are independent, so they run side by side: the slower one (a tool call is two model round trips) sets the pace.
    (probe.image, image_result), (probe.tools, tools_result) = await asyncio.gather(
        _timed(lambda r: all(c in str(r.output).lower() for c in ("red", "blue")), image, key, settings),
        _timed(lambda r: calls == [(417, 268)] and "685" in str(r.output), tools, key, settings),
    )
    for result in (image_result, tools_result):
        if result is not None:
            probe.cost += _cost_of(result.new_messages())
    return probe


async def probe_models(keys: list[str], settings: Any, build: Callable[[str], Any], quick: bool = False, concurrency: int | None = None,
                       on_done: Callable[[ModelProbe], None] | None = None) -> list[ModelProbe]:
    """Probe every model at once (a few tiny requests each), calling `on_done` the moment each finishes so a slow one
    cannot make the rest look hung. Results come back in the order of `keys`."""
    gate = asyncio.Semaphore(concurrency or max(1, min(len(keys), 16)))

    async def one(key: str) -> ModelProbe:
        async with gate:
            probe = await probe_model(key, settings, build, quick)
        if on_done is not None:
            on_done(probe)
        return probe

    return list(await asyncio.gather(*(one(k) for k in keys)))


def _cell(check: Check | None) -> str:
    return "-" if check is None else (f"ok {check.ms}ms" if check.ok else "FAIL")


def row(p: ModelProbe, width: int = 58) -> str:
    return f"{p.key:<{width}}  {_cell(p.text):<11}{_cell(p.image):<11}{_cell(p.tools):<11}{'$' + format(p.cost, '.5f'):>9}"


def table(probes: list[ModelProbe]) -> str:
    """A plain-text table: one row per model, then the reason for every failure."""
    width = max([len(p.key) for p in probes] + [5])
    lines = [f"{'model':<{width}}  {'text':<11}{'image':<11}{'tools':<11}{'cost':>9}"]
    lines += [row(p, width) for p in probes]
    notes = [f"  {p.key}: {name} - {check.detail}" for p in probes for name, check in (("text", p.text), ("image", p.image), ("tools", p.tools)) if check is not None and not check.ok]
    if notes:
        lines += ["", "What failed, and why:", *notes]
    usable = [p.key for p in probes if p.usable]
    lines += ["", f"usable as the working agent (text + image + tools): {', '.join(usable) if usable else 'none'}", f"total spent probing: ${sum(p.cost for p in probes):.5f}"]
    return "\n".join(lines)
