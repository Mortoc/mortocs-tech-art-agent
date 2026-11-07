"""Conversations on disk: pydantic-ai's `ConversationStore` over one SQLite file.

The base class does the hard parts (append-only log, branching on rewrites, forks, revision checks);
this only stores records and entries. SQLite is in the standard library and costs nothing while idle,
which matters for a daemon that sleeps until woken.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Sequence
from pathlib import Path

from pydantic import TypeAdapter
from pydantic_ai.conversation_store import ConversationConflict, ConversationEntry, ConversationRecord, ConversationStore

_record = TypeAdapter(ConversationRecord)
_entry = TypeAdapter(ConversationEntry)

SCHEMA = """
create table if not exists records (
  conversation_id text primary key, revision text not null, scope text, updated_at text not null, body text not null);
create index if not exists records_scope on records (scope, updated_at desc);
create table if not exists entries (id text primary key, parent_id text, body text not null);
"""


class SqliteConversationStore(ConversationStore):
    def __init__(self, path: Path | str) -> None:
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self._db = sqlite3.connect(str(path), isolation_level=None, check_same_thread=False)
        self._db.executescript("pragma journal_mode=wal;" + SCHEMA)

    def close(self) -> None:
        self._db.close()

    async def read_record(self, conversation_id: str) -> ConversationRecord | None:
        row = self._db.execute("select body from records where conversation_id=?", (conversation_id,)).fetchone()
        return _record.validate_json(row[0]) if row else None

    async def read_records(self, *, scope: str | None = None, limit: int | None = None) -> Sequence[ConversationRecord]:
        sql, args = "select body from records", []
        if scope is not None:
            sql, args = sql + " where scope=?", [scope]
        sql += " order by updated_at desc" + (" limit ?" if limit is not None else "")
        if limit is not None:
            args.append(limit)
        return [_record.validate_json(r[0]) for r in self._db.execute(sql, args)]

    async def write_record(self, record: ConversationRecord, *, new_entries: Sequence[ConversationEntry] = (), expected_revision: str | None) -> None:
        db = self._db
        db.execute("begin immediate")
        try:
            row = db.execute("select revision from records where conversation_id=?", (record.conversation_id,)).fetchone()
            if (row[0] if row else None) != expected_revision:
                raise ConversationConflict(record.conversation_id)
            if record.head_id is not None and record.head_id not in {e.id for e in new_entries}:
                if not db.execute("select 1 from entries where id=?", (record.head_id,)).fetchone():
                    raise ConversationConflict(record.conversation_id)
            db.executemany("insert or replace into entries values (?,?,?)", [(e.id, e.parent_id, _entry.dump_json(e).decode()) for e in new_entries])
            db.execute(
                "insert or replace into records values (?,?,?,?,?)",
                (record.conversation_id, record.revision, record.scope, record.updated_at.isoformat(), _record.dump_json(record).decode()),
            )
            db.execute("commit")
        except BaseException:
            db.execute("rollback")
            raise

    async def delete_record(self, conversation_id: str, *, expected_revision: str) -> None:
        row = self._db.execute("select revision from records where conversation_id=?", (conversation_id,)).fetchone()
        if row is None or row[0] != expected_revision:
            raise ConversationConflict(conversation_id)
        self._db.execute("delete from records where conversation_id=?", (conversation_id,))
        # Entries stay until a vacuum pass: a concurrent save might be about to build on them.

    async def read_entry(self, entry_id: str) -> ConversationEntry:
        row = self._db.execute("select body from entries where id=?", (entry_id,)).fetchone()
        if row is None:
            raise KeyError(entry_id)
        return _entry.validate_json(row[0])
