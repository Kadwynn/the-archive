from __future__ import annotations

import sqlite3
from pathlib import Path


SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS exports (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_path TEXT NOT NULL,
    source_name TEXT NOT NULL,
    file_sha256 TEXT NOT NULL UNIQUE,
    imported_at TEXT NOT NULL,
    conversation_count INTEGER NOT NULL DEFAULT 0,
    merged_conversation_count INTEGER NOT NULL DEFAULT 0,
    new_conversation_count INTEGER NOT NULL DEFAULT 0,
    updated_conversation_count INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS conversations (
    id TEXT PRIMARY KEY,
    title TEXT,
    create_time REAL,
    update_time REAL,
    default_model_slug TEXT,
    current_node TEXT,
    is_archived INTEGER NOT NULL DEFAULT 0,
    raw_json TEXT NOT NULL,
    search_text TEXT NOT NULL,
    message_count INTEGER NOT NULL DEFAULT 0,
    user_message_count INTEGER NOT NULL DEFAULT 0,
    assistant_message_count INTEGER NOT NULL DEFAULT 0,
    system_message_count INTEGER NOT NULL DEFAULT 0,
    tool_message_count INTEGER NOT NULL DEFAULT 0,
    first_seen_export_id INTEGER NOT NULL,
    last_seen_export_id INTEGER NOT NULL,
    first_seen_at TEXT NOT NULL,
    last_seen_at TEXT NOT NULL,
    merged_at TEXT NOT NULL,
    FOREIGN KEY(first_seen_export_id) REFERENCES exports(id),
    FOREIGN KEY(last_seen_export_id) REFERENCES exports(id)
);

CREATE TABLE IF NOT EXISTS conversation_exports (
    conversation_id TEXT NOT NULL,
    export_id INTEGER NOT NULL,
    seen_at TEXT NOT NULL,
    PRIMARY KEY(conversation_id, export_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(export_id) REFERENCES exports(id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS messages (
    message_pk TEXT PRIMARY KEY,
    conversation_id TEXT NOT NULL,
    message_id TEXT NOT NULL,
    node_id TEXT NOT NULL,
    parent_id TEXT,
    role TEXT,
    author_name TEXT,
    recipient TEXT,
    status TEXT,
    create_time REAL,
    update_time REAL,
    model_slug TEXT,
    content_type TEXT,
    text_content TEXT,
    is_current_path INTEGER NOT NULL DEFAULT 0,
    sort_index INTEGER NOT NULL,
    raw_json TEXT NOT NULL,
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversations_update_time ON conversations(update_time DESC);
CREATE INDEX IF NOT EXISTS idx_conversations_last_seen ON conversations(last_seen_at DESC);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_time ON messages(conversation_id, create_time, sort_index);
CREATE INDEX IF NOT EXISTS idx_messages_conversation_role ON messages(conversation_id, role);
CREATE INDEX IF NOT EXISTS idx_messages_create_time ON messages(create_time, conversation_id);
CREATE INDEX IF NOT EXISTS idx_messages_update_time ON messages(update_time, conversation_id);

CREATE TABLE IF NOT EXISTS tags (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    name TEXT NOT NULL,
    normalized_name TEXT NOT NULL UNIQUE,
    created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS conversation_tags (
    conversation_id TEXT NOT NULL,
    tag_id INTEGER NOT NULL,
    display_order INTEGER NOT NULL DEFAULT 0,
    added_at TEXT NOT NULL,
    PRIMARY KEY(conversation_id, tag_id),
    FOREIGN KEY(conversation_id) REFERENCES conversations(id) ON DELETE CASCADE,
    FOREIGN KEY(tag_id) REFERENCES tags(id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_conversation_tags_tag
ON conversation_tags(tag_id, conversation_id);

CREATE VIRTUAL TABLE IF NOT EXISTS message_fts USING fts5 (
    message_pk UNINDEXED,
    conversation_id UNINDEXED,
    conversation_title,
    role,
    text_content,
    tokenize = 'unicode61 remove_diacritics 2'
);
"""


def connect_db(db_path: str | Path) -> sqlite3.Connection:
    db_file = Path(db_path).expanduser().resolve()
    db_file.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(db_file)
    connection.row_factory = sqlite3.Row
    connection.execute("PRAGMA foreign_keys = ON;")
    connection.execute("PRAGMA journal_mode = WAL;")
    connection.execute("PRAGMA synchronous = NORMAL;")
    return connection


def init_db(connection: sqlite3.Connection) -> None:
    connection.executescript(SCHEMA_SQL)
    connection.commit()
