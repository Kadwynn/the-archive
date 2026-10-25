from __future__ import annotations

import json
import io
import sqlite3
import tempfile
import unittest
from pathlib import Path

from chat_export_viewer.database import init_db
from chat_export_viewer.ingest import ingest_export, iter_export_conversations
from chat_export_viewer.server import create_app


def conversation_fixture(*, title: str = "A directly extracted chat", extra_node: bool = False) -> dict:
    mapping = {
        "root": {"id": "root", "parent": None, "children": ["message"], "message": None},
        "message": {
            "id": "message",
            "parent": "root",
            "children": ["future"] if extra_node else [],
            "message": {
                "id": "message-id",
                "author": {"role": "assistant", "name": None, "metadata": {}},
                "create_time": 1_700_000_000.0,
                "update_time": None,
                "content": {
                    "content_type": "reasoning_recap",
                    "content": "A visible recap from the web conversation.",
                    "future_detail": {"kept": True},
                },
                "status": "finished_successfully",
                "recipient": "all",
                "metadata": {"model_slug": "test-model", "unfamiliar_metadata": [1, 2, 3]},
            },
        },
    }
    if extra_node:
        mapping["future"] = {
            "id": "future",
            "parent": "message",
            "children": [],
            "message": {
                "id": "future-id",
                "author": {"role": "tool", "name": "future_tool", "metadata": {}},
                "create_time": 1_700_000_001.0,
                "content": {
                    "content_type": "mysterious_glitter_payload_v7",
                    "payload": {"still": "preserved"},
                },
                "status": "finished_successfully",
                "recipient": "all",
                "metadata": {},
            },
        }

    return {
        "title": title,
        "create_time": 1_700_000_000.0,
        "update_time": 1_700_000_001.0 if extra_node else 1_700_000_000.0,
        "conversation_id": "single-conversation-id",
        "default_model_slug": "test-model",
        "mapping": mapping,
        "current_node": "future" if extra_node else "message",
        "unknown_root_field": {"also": "preserved"},
    }


class SingleConversationIngestTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "archive.db"
        self.db = sqlite3.connect(self.database_path)
        self.db.row_factory = sqlite3.Row
        init_db(self.db)

    def tearDown(self) -> None:
        self.db.close()
        self.temp_directory.cleanup()

    def write_json(self, name: str, payload: object) -> Path:
        path = self.root / name
        path.write_text(json.dumps(payload), encoding="utf-8")
        return path

    def test_iterator_accepts_a_single_conversation_object(self) -> None:
        path = self.write_json("single.json", conversation_fixture())
        items = list(iter_export_conversations(path))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["conversation_id"], "single-conversation-id")

    def test_iterator_still_accepts_a_full_export_array(self) -> None:
        path = self.write_json("conversations.json", [conversation_fixture()])
        items = list(iter_export_conversations(path))
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0]["conversation_id"], "single-conversation-id")

    def test_single_conversation_is_searchable_and_preserves_unknown_data(self) -> None:
        path = self.write_json("single.json", conversation_fixture(extra_node=True))
        result = ingest_export(self.db, path)

        self.assertEqual(result["conversation_count"], 1)
        self.assertEqual(result["new_conversation_count"], 1)
        row = self.db.execute(
            "SELECT raw_json, message_count, tool_message_count FROM conversations WHERE id = ?",
            ("single-conversation-id",),
        ).fetchone()
        self.assertEqual(row["message_count"], 2)
        self.assertEqual(row["tool_message_count"], 1)
        self.assertEqual(json.loads(row["raw_json"])["unknown_root_field"], {"also": "preserved"})

        message = self.db.execute(
            "SELECT text_content, raw_json FROM messages WHERE node_id = 'future'"
        ).fetchone()
        self.assertIn("mysterious_glitter_payload_v7", message["text_content"])
        self.assertEqual(json.loads(message["raw_json"])["content"]["payload"], {"still": "preserved"})

        search_hit = self.db.execute(
            "SELECT conversation_id FROM message_fts WHERE message_fts MATCH 'glitter'"
        ).fetchone()
        self.assertEqual(search_hit["conversation_id"], "single-conversation-id")

    def test_reextracting_the_same_chat_updates_instead_of_duplicating(self) -> None:
        first_path = self.write_json("first.json", conversation_fixture())
        second_path = self.write_json(
            "second.json",
            conversation_fixture(title="The title changed", extra_node=True),
        )

        ingest_export(self.db, first_path)
        result = ingest_export(self.db, second_path)

        self.assertEqual(result["new_conversation_count"], 0)
        self.assertEqual(result["updated_conversation_count"], 1)
        count = self.db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0]
        row = self.db.execute(
            "SELECT title, message_count FROM conversations WHERE id = ?",
            ("single-conversation-id",),
        ).fetchone()
        self.assertEqual(count, 1)
        self.assertEqual(row["title"], "The title changed")
        self.assertEqual(row["message_count"], 2)

    def test_upload_rejects_json_that_is_not_a_chat_or_export(self) -> None:
        client = create_app(self.database_path).test_client()
        response = client.post(
            "/api/import-file",
            data={"file": (io.BytesIO(b"{}"), "not-a-chat.json")},
            content_type="multipart/form-data",
        )
        self.assertEqual(response.status_code, 400)
        self.assertIn("conversation id", response.get_json()["error"])


if __name__ == "__main__":
    unittest.main()
