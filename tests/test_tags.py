from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from chat_export_viewer.database import connect_db, init_db
from chat_export_viewer.ingest import ingest_export
from chat_export_viewer.server import _vocabulary_cache, create_app


class TagFeatureTests(unittest.TestCase):
    def setUp(self) -> None:
        _vocabulary_cache.clear()
        self.temp_directory = tempfile.TemporaryDirectory()
        self.root = Path(self.temp_directory.name)
        self.database_path = self.root / "archive.db"

        db = connect_db(self.database_path)
        init_db(db)
        export_id = int(
            db.execute(
                """
                INSERT INTO exports (
                    source_path,
                    source_name,
                    file_sha256,
                    imported_at
                ) VALUES ('seed.json', 'seed.json', 'seed-hash', 'now')
                """
            ).lastrowid
        )
        self.add_conversation(
            db,
            export_id,
            "conversation-a",
            "First tagged chat",
            100.0,
        )
        self.add_conversation(
            db,
            export_id,
            "conversation-b",
            "Second tagged chat",
            200.0,
        )
        self.add_conversation(
            db,
            export_id,
            "conversation-c",
            "Body-only match",
            300.0,
            "An ordinary test prompt appears in this message.",
        )
        db.commit()
        db.close()

        self.client = create_app(self.database_path).test_client()

    def tearDown(self) -> None:
        self.temp_directory.cleanup()

    @staticmethod
    def add_conversation(
        db,
        export_id: int,
        conversation_id: str,
        title: str,
        update_time: float,
        text: str = "",
    ) -> None:
        raw_json = json.dumps(
            {"id": conversation_id, "title": title, "seed_data": True},
            separators=(",", ":"),
        )
        message_count = 1 if text else 0
        db.execute(
            """
            INSERT INTO conversations (
                id,
                title,
                create_time,
                update_time,
                raw_json,
                search_text,
                message_count,
                first_seen_export_id,
                last_seen_export_id,
                first_seen_at,
                last_seen_at,
                merged_at
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 'now', 'now', 'now')
            """,
            (
                conversation_id,
                title,
                update_time,
                update_time,
                raw_json,
                f"{title}\n{text}".strip(),
                message_count,
                export_id,
                export_id,
            ),
        )
        if not text:
            return

        message_pk = f"{conversation_id}:node"
        db.execute(
            """
            INSERT INTO messages (
                message_pk,
                conversation_id,
                message_id,
                node_id,
                role,
                create_time,
                text_content,
                is_current_path,
                sort_index,
                raw_json
            ) VALUES (?, ?, ?, 'node', 'assistant', ?, ?, 1, 0, '{}')
            """,
            (message_pk, conversation_id, message_pk, update_time, text),
        )
        db.execute(
            """
            INSERT INTO message_fts (
                message_pk,
                conversation_id,
                conversation_title,
                role,
                text_content
            ) VALUES (?, ?, ?, 'assistant', ?)
            """,
            (message_pk, conversation_id, title, text),
        )

    def put_tags(self, conversation_id: str, tags: object):
        return self.client.put(
            f"/api/conversations/{conversation_id}/tags",
            json={"tags": tags},
        )

    def test_tags_are_normalized_deduplicated_and_keep_display_order(self) -> None:
        response = self.put_tags(
            "conversation-a",
            [
                "  Test   Prompt  ",
                "model:GPT-5",
                "test prompt",
                "condition:no-memory",
            ],
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            response.get_json()["tags"],
            ["Test Prompt", "model:GPT-5", "condition:no-memory"],
        )

        reordered = self.put_tags(
            "conversation-a",
            ["condition:no-memory", "model:gpt-5", "TEST PROMPT"],
        )
        self.assertEqual(
            reordered.get_json()["tags"],
            ["condition:no-memory", "model:GPT-5", "Test Prompt"],
        )

        detail = self.client.get("/api/conversations/conversation-a").get_json()
        self.assertEqual(detail["conversation"]["tags"], reordered.get_json()["tags"])

        listing = self.client.get("/api/conversations", query_string={"limit": 10}).get_json()
        listed_chat = next(
            item for item in listing["items"] if item["id"] == "conversation-a"
        )
        self.assertEqual(listed_chat["tags"], reordered.get_json()["tags"])

    def test_tag_catalog_counts_shared_tags_and_clearing_is_safe(self) -> None:
        self.put_tags("conversation-a", ["Test Prompt", "First only"])
        shared_response = self.put_tags("conversation-b", ["test prompt"])
        self.assertEqual(shared_response.get_json()["tags"], ["Test Prompt"])

        catalog = self.client.get("/api/tags").get_json()["items"]
        by_name = {item["name"]: item for item in catalog}
        self.assertEqual(by_name["Test Prompt"]["conversation_count"], 2)
        self.assertEqual(by_name["First only"]["conversation_count"], 1)

        cleared = self.put_tags("conversation-a", [])
        self.assertEqual(cleared.status_code, 200)
        self.assertEqual(cleared.get_json()["tags"], [])
        catalog_after = self.client.get("/api/tags").get_json()["items"]
        counts_after = {item["name"]: item["conversation_count"] for item in catalog_after}
        self.assertEqual(counts_after, {"Test Prompt": 1})

    def test_tag_api_rejects_malformed_values_and_unknown_conversations(self) -> None:
        invalid_requests = [
            (None, "missing field"),
            ({"tags": "not-a-list"}, "not a list"),
            ({"tags": ["valid", 7]}, "non-text value"),
            ({"tags": [""]}, "empty value"),
            ({"tags": ["bad\nline"]}, "control character"),
            ({"tags": ["invisible\u200btext"]}, "invisible formatting"),
            ({"tags": ["alpha，beta"]}, "comma after Unicode normalization"),
            ({"tags": ["x" * 81]}, "too long"),
            ({"tags": [f"tag-{index}" for index in range(31)]}, "too many"),
        ]
        for payload, label in invalid_requests:
            with self.subTest(label=label):
                response = self.client.put(
                    "/api/conversations/conversation-a/tags",
                    json=payload,
                )
                self.assertEqual(response.status_code, 400)
                self.assertTrue(response.get_json()["error"])

        unknown = self.put_tags("missing-conversation", ["valid"])
        self.assertEqual(unknown.status_code, 404)

    def test_tag_search_supports_all_match_shapes_and_best_match_uses_tags(self) -> None:
        self.put_tags("conversation-a", ["Test Prompt", "model:gpt-5"])
        self.put_tags("conversation-b", ["Test Conditions"])

        expectations = {
            "test prompt": ["conversation-a"],
            "test": ["conversation-b", "conversation-a"],
            "prompt": ["conversation-a"],
            "conditions test": ["conversation-b"],
        }
        for query, expected_ids in expectations.items():
            with self.subTest(query=query):
                payload = self.client.get(
                    "/api/conversations",
                    query_string={"q": query, "mode": "tag", "limit": 20},
                ).get_json()
                self.assertEqual(payload["total"], len(expected_ids))
                self.assertEqual(
                    {item["id"] for item in payload["items"]},
                    set(expected_ids),
                )

        combined_tags = self.client.get(
            "/api/conversations",
            query_string={
                "q": "test prompt model:gpt-5",
                "mode": "tag",
                "limit": 20,
            },
        ).get_json()
        self.assertEqual(combined_tags["total"], 1)
        self.assertEqual(combined_tags["items"][0]["id"], "conversation-a")

        best_match = self.client.get(
            "/api/conversations",
            query_string={"q": "test prompt", "mode": "normal", "limit": 20},
        ).get_json()
        self.assertEqual(best_match["total"], 2)
        self.assertEqual(best_match["items"][0]["id"], "conversation-a")
        self.assertEqual(
            {item["id"] for item in best_match["items"]},
            {"conversation-a", "conversation-c"},
        )

        body_only = self.client.get(
            "/api/conversations",
            query_string={"q": "ordinary", "mode": "normal", "limit": 20},
        ).get_json()
        self.assertEqual(body_only["total"], 1)
        self.assertEqual(body_only["items"][0]["id"], "conversation-c")

    def test_activity_filter_is_applied_before_tag_search_pagination(self) -> None:
        self.put_tags("conversation-a", ["Test Prompt"])
        self.put_tags("conversation-b", ["Test Conditions"])

        payload = self.client.get(
            "/api/conversations",
            query_string={
                "q": "test",
                "mode": "tag",
                "activity_start": 90,
                "activity_end": 150,
                "limit": 1,
                "offset": 0,
            },
        ).get_json()
        self.assertEqual(payload["total"], 1)
        self.assertEqual(payload["items"][0]["id"], "conversation-a")
        self.assertEqual(payload["items"][0]["activity_time"], 100.0)

    def test_reimport_preserves_tags_and_does_not_pollute_raw_json(self) -> None:
        expected_tags = ["Test Prompt", "model:gpt-5"]
        self.put_tags("conversation-a", expected_tags)

        updated_conversation = {
            "id": "conversation-a",
            "title": "First tagged chat, updated",
            "create_time": 100.0,
            "update_time": 400.0,
            "mapping": {
                "root": {
                    "id": "root",
                    "parent": None,
                    "children": ["new-node"],
                    "message": None,
                },
                "new-node": {
                    "id": "new-node",
                    "parent": "root",
                    "children": [],
                    "message": {
                        "id": "new-message",
                        "author": {"role": "assistant", "name": None},
                        "create_time": 400.0,
                        "content": {
                            "content_type": "text",
                            "parts": ["Newly imported content."],
                        },
                        "metadata": {},
                    },
                },
            },
            "current_node": "new-node",
        }
        import_path = self.root / "updated-chat.json"
        import_path.write_text(json.dumps(updated_conversation), encoding="utf-8")

        db = connect_db(self.database_path)
        result = ingest_export(db, import_path)
        db.close()
        self.assertEqual(result["updated_conversation_count"], 1)

        detail = self.client.get("/api/conversations/conversation-a").get_json()
        self.assertEqual(detail["conversation"]["tags"], expected_tags)
        self.assertEqual(detail["messages"][0]["node_id"], "new-node")

        original_json = self.client.get(
            "/api/conversations/conversation-a/export"
        ).get_json()[0]
        self.assertNotIn("tags", original_json)
        self.assertIn("new-node", original_json["mapping"])

        db = connect_db(self.database_path)
        init_db(db)
        mapping_rows = db.execute(
            """
            SELECT display_order
            FROM conversation_tags
            WHERE conversation_id = 'conversation-a'
            ORDER BY display_order
            """
        ).fetchall()
        db.close()
        self.assertEqual([int(row[0]) for row in mapping_rows], [0, 1])


if __name__ == "__main__":
    unittest.main()
