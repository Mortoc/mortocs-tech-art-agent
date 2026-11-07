import pytest
from pydantic_ai import Agent
from pydantic_ai.conversation import Conversation
from pydantic_ai.conversation_store import ConversationConflict
from pydantic_ai.models.test import TestModel

from tech_art_core.store import SqliteConversationStore


async def test_a_conversation_survives_a_new_connection_and_only_appends(tmp_path):
    agent = Agent(TestModel(custom_output_text="ok"))
    path = tmp_path / "c.sqlite"
    store = SqliteConversationStore(path)
    convo = Conversation(conversation_id="c1")
    result = await agent.run("hello", conversation=convo)
    await store.save(result, scope="houdini:/s/a.hip")
    store.close()

    reopened = SqliteConversationStore(path)
    loaded = await reopened.load("c1")
    assert [type(m).__name__ for m in loaded.messages] == ["ModelRequest", "ModelResponse"]
    second = await agent.run("again", conversation=loaded)
    entries = await reopened.save(second)
    assert len(entries) == 2
    assert len((await reopened.load("c1")).messages) == 4
    assert [r.conversation_id for r in await reopened.read_records(scope="houdini:/s/a.hip")] == ["c1"]


async def test_stale_saves_conflict_and_forks_share_history(tmp_path):
    agent = Agent(TestModel(custom_output_text="ok"))
    store = SqliteConversationStore(tmp_path / "c.sqlite")
    first = await agent.run("hi", conversation=Conversation(conversation_id="c1"))
    await store.save(first)
    a, b = await store.load("c1"), await store.load("c1")
    await store.save(await agent.run("a", conversation=a))
    with pytest.raises(ConversationConflict):
        await store.save(await agent.run("b", conversation=b))
    forked = await store.fork("c1", new_conversation_id="c2")
    assert len(forked.messages) == len((await store.load("c1")).messages)
