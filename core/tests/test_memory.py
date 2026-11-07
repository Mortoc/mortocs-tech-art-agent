import pytest

from tech_art_core.memory import MemoryStore, append_to_section, maybe_compact


def test_append_creates_and_extends_sections():
    assert append_to_section("", "Conventions", "- a") == "## Conventions\n\n- a\n"
    one = append_to_section("## A\n\n- x\n\n## B\n\n- y\n", "A", "- z")
    assert one == "## A\n\n- x\n- z\n\n## B\n\n- y\n"
    assert append_to_section("## A\n\n- x\n", "B", "- y") == "## A\n\n- x\n\n## B\n\n- y\n"


class FakeApp:
    def __init__(self):
        self.text, self.prev = "", None

    async def helper(self, name, **kw):
        if name == "memory_read":
            return {"text": self.text, "prev": self.prev}
        self.text, self.prev = kw["text"], kw["prev"]


async def test_replace_keeps_one_step_of_rollback():
    app = FakeApp()
    store = MemoryStore(app.helper, "memory_read", "memory_write", "memory")
    await store.append("A", "- one")
    await store.replace("new")
    assert app.prev.startswith("## A")
    await store.rollback()
    assert app.text.startswith("## A") and app.prev is None
    with pytest.raises(ValueError, match="no previous"):
        await store.rollback()


async def test_compaction_only_when_over_threshold_and_never_grows_memory():
    app = FakeApp()
    app.text = "x" * 400
    store = MemoryStore(app.helper, "memory_read", "memory_write", "memory")
    assert await maybe_compact(store, lambda p: None, threshold=1000) is None

    async def shorter(prompt):
        return "x" * 40

    report = await maybe_compact(store, shorter, threshold=10)
    assert report["after"] < report["before"] and app.text == "x" * 40 + "\n"
    app.text = "y" * 400

    async def longer(prompt):
        return "y" * 500

    with pytest.raises(ValueError, match="left untouched"):
        await maybe_compact(store, longer, threshold=10)
    assert app.text == "y" * 400
