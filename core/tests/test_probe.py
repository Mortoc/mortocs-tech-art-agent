"""`tech-art-core probe`: what works, in plain words. Scripted models stand in for the real ones."""

import httpx
from pydantic_ai import BinaryContent
from pydantic_ai import exceptions as ai
from pydantic_ai.messages import ModelResponse, TextPart, ToolCallPart, ToolReturnPart, UserPromptPart
from pydantic_ai.models.function import FunctionModel

from tech_art_core.probe import probe_model, probe_models, table
from tech_art_core.settings import Settings


def scripted(sees_images=True, calls_tools=True, error=None):
    def function(messages, info):
        parts = [p for m in messages for p in m.parts]
        if error is not None:
            raise error
        contents = [c for p in parts if isinstance(p, UserPromptPart) for c in (p.content if isinstance(p.content, list) else [p.content])]
        if any(isinstance(c, BinaryContent) for c in contents):
            if not sees_images:
                raise ai.ModelHTTPError(400, "m", {"error": "image input is not supported by this model"})
            return ModelResponse(parts=[TextPart(content="Red")])
        if any(isinstance(p, ToolReturnPart) for p in parts):
            return ModelResponse(parts=[TextPart(content="The answer is 685")])
        if "417" in " ".join(str(c) for c in contents):
            if not calls_tools:
                return ModelResponse(parts=[TextPart(content="It is 685.")])  # answered itself instead of calling the tool
            return ModelResponse(parts=[ToolCallPart(tool_name="add_numbers", args={"a": 417, "b": 268})])
        return ModelResponse(parts=[TextPart(content="pong")])

    return FunctionModel(function=function)


async def test_a_capable_model_passes_all_three_checks():
    probe = await probe_model("gateway/openai/gpt-6.1-sol", Settings(), lambda key: scripted())
    assert probe.text.ok and probe.image.ok and probe.tools.ok and probe.usable


async def test_a_model_that_cannot_see_images_is_not_usable_and_says_why():
    probe = await probe_model("gateway/chat@openrouter/some/text-only", Settings(), lambda key: scripted(sees_images=False))
    assert probe.text.ok and not probe.image.ok and probe.tools.ok and not probe.usable
    assert "custom endpoint 'openrouter'" in probe.image.detail or "openrouter" in probe.image.detail  # the gateway-route hint, not a raw 400


async def test_a_model_that_answers_instead_of_calling_the_tool_fails_the_tool_check():
    probe = await probe_model("local/x", Settings(), lambda key: scripted(calls_tools=False))
    assert probe.text.ok and probe.image.ok and not probe.tools.ok and "unexpected answer" in probe.tools.detail


async def test_an_unreachable_model_stops_after_the_first_check_and_blames_the_server():
    refused = httpx.ConnectError("refused", request=httpx.Request("POST", "http://localhost:11434/v1/chat/completions"))
    probe = await probe_model("local/llava", Settings(), lambda key: scripted(error=refused))
    assert not probe.text.ok and probe.image is None and probe.tools is None  # no point asking it to look at an image
    assert "local model server" in probe.text.detail


async def test_a_missing_key_is_reported_as_a_missing_key_not_a_stack_trace():
    def build(key):
        raise ai.UserError("Set the `PYDANTIC_AI_GATEWAY_API_KEY` environment variable or pass it via `gateway_provider(..., api_key=...)`")

    probe = await probe_model("gateway/openai/gpt-6.1-sol", Settings(), build)
    assert not probe.text.ok and "PYDANTIC_AI_GATEWAY_API_KEY" in probe.text.detail


async def test_quick_mode_only_asks_for_a_word():
    probe = await probe_model("local/x", Settings(), lambda key: scripted(), quick=True)
    assert probe.text.ok and probe.image is None and probe.tools is None


async def test_the_table_names_the_usable_models_and_explains_every_failure():
    models = {"gateway/good": scripted(), "gateway/blind": scripted(sees_images=False)}
    probes = await probe_models(list(models), Settings(), lambda key: models[key])
    text = table(probes)
    assert "gateway/good" in text and "gateway/blind" in text
    assert "What failed, and why:" in text and "gateway/blind: image" in text
    assert "usable as the working agent (text + image + tools): gateway/good" in text
    assert "ok" in text and "FAIL" in text and "total spent probing" in text


def test_the_test_image_is_a_genuinely_valid_png():
    """The earlier hand-typed PNG had a bad checksum and every provider refused it. Check the real structure."""
    import struct
    import zlib

    from tech_art_core.probe import TEST_PNG

    assert TEST_PNG[:8] == b"\x89PNG\r\n\x1a\n"
    pos, kinds = 8, []
    while pos < len(TEST_PNG):
        length = struct.unpack(">I", TEST_PNG[pos : pos + 4])[0]
        kind, body = TEST_PNG[pos + 4 : pos + 8], TEST_PNG[pos + 8 : pos + 8 + length]
        crc = struct.unpack(">I", TEST_PNG[pos + 8 + length : pos + 12 + length])[0]
        assert zlib.crc32(kind + body) == crc, f"bad CRC in {kind!r}"
        if kind == b"IDAT":
            raw = zlib.decompress(body)  # raises on a corrupt stream
            assert len(raw) == 32 * (1 + 32 * 3)
        kinds.append(kind)
        pos += 12 + length
    assert kinds == [b"IHDR", b"IDAT", b"IEND"] and pos == len(TEST_PNG)


async def test_models_are_probed_together_and_each_row_is_reported_as_it_finishes():
    import asyncio
    import time

    async def slow_function(messages, info):
        await asyncio.sleep(0.3)
        return ModelResponse(parts=[TextPart(content="pong")])

    done = []
    keys = [f"local/m{i}" for i in range(6)]
    started = time.monotonic()
    probes = await probe_models(keys, Settings(), lambda key: FunctionModel(function=slow_function), quick=True, on_done=lambda p: done.append(p.key))
    assert time.monotonic() - started < 1.0, "six 0.3 s models should overlap, not queue"
    assert sorted(done) == sorted(keys) and [p.key for p in probes] == keys  # reported as they finish; returned in order
