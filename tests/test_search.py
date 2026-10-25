from __future__ import annotations

import sqlite3
import tempfile
import unittest
from pathlib import Path

from chat_export_viewer.database import init_db
from chat_export_viewer.server import (
    _filter_items_by_activity,
    _vocabulary_cache,
    activity_range_search_conversations,
    create_app,
    date_search_conversations,
    fulltext_search_conversations,
    fuzzy_search_conversations,
    title_search_conversations,
    tokenize_query,
)


class SearchTests(unittest.TestCase):
    def setUp(self) -> None:
        _vocabulary_cache.clear()
        self.db = sqlite3.connect(":memory:")
        self.db.row_factory = sqlite3.Row
        init_db(self.db)
        self.add_conversation("alpha-title", "Alpha Project", "ordinary planning notes", 10)
        self.add_conversation("alpha-body", "Recent chat", "alpha appears in this message", 100)
        self.add_conversation(
            "memory",
            "ExampleAssistant Memory Archive",
            "This memory should survive a small spelling mistake.",
            20,
        )
        self.add_conversation(
            "unicode",
            "Überraschung",
            "Ein überraschender deutscher Suchtreffer.",
            30,
        )
        self.add_conversation(
            "snippet",
            "A long conversation",
            "Words before the needle and useful words after the needle.",
            40,
        )
        self.add_conversation("insight-title", "InsightGPT", "A conversation about something else.", 50)
        self.add_conversation("insight-body", "Unrelated conversation", "InsightGPT appears only here.", 60)
        date_start = 1_704_067_200.0
        self.add_conversation(
            "date-message",
            "Message activity",
            "This rangeword message happened on the selected date.",
            date_start - 100,
            message_time=date_start + 3_600,
        )
        self.add_conversation(
            "date-update",
            "Conversation update activity",
            "The message itself is older.",
            date_start + 7_200,
            message_time=date_start - 100,
        )
        self.add_conversation(
            "date-create",
            "Conversation creation activity",
            "The message timestamp is older.",
            date_start - 100,
            message_time=date_start - 100,
            create_time=date_start + 1_800,
        )
        self.add_conversation(
            "date-next",
            "Next-day activity",
            "The range includes this rangeword message.",
            date_start - 100,
            message_time=date_start + 86_400 + 3_600,
        )
        self.add_conversation(
            "date-boundary",
            "Midnight boundary activity",
            "This begins exactly at the next local-day boundary.",
            date_start - 100,
            message_time=date_start + 86_400,
        )
        self.add_conversation(
            "date-outside",
            "Outside-range activity",
            "This rangeword message is beyond the range.",
            date_start - 100,
            message_time=date_start + (2 * 86_400) + 3_600,
        )
        self.db.commit()

    def tearDown(self) -> None:
        self.db.close()

    def add_conversation(
        self,
        conversation_id: str,
        title: str,
        text: str,
        update_time: float,
        message_time: float | None = None,
        create_time: float | None = None,
    ) -> None:
        self.db.execute(
            """
            INSERT INTO conversations (
                id, title, create_time, update_time, raw_json, search_text, message_count,
                first_seen_export_id, last_seen_export_id,
                first_seen_at, last_seen_at, merged_at
            ) VALUES (?, ?, ?, ?, '{}', ?, 1, 1, 1, 'now', 'now', 'now')
            """,
            (conversation_id, title, create_time, update_time, f"{title}\n{text}"),
        )
        message_pk = f"{conversation_id}:message"
        self.db.execute(
            """
            INSERT INTO messages (
                message_pk, conversation_id, message_id, node_id,
                create_time, text_content, is_current_path, sort_index, raw_json
            ) VALUES (?, ?, ?, 'node', ?, ?, 1, 0, '{}')
            """,
            (message_pk, conversation_id, message_pk, message_time, text),
        )
        self.db.execute(
            """
            INSERT INTO message_fts (
                message_pk, conversation_id, conversation_title, role, text_content
            ) VALUES (?, ?, ?, 'assistant', ?)
            """,
            (message_pk, conversation_id, title, text),
        )

    def test_fulltext_search_ranks_title_match_above_newer_body_match(self) -> None:
        items, total = fulltext_search_conversations(self.db, "alpha", 10, 0)
        self.assertEqual(total, 2)
        self.assertEqual(items[0]["id"], "alpha-title")

    def test_fulltext_search_returns_the_matching_passage(self) -> None:
        items, total = fulltext_search_conversations(self.db, "needle", 10, 0)
        self.assertEqual(total, 1)
        self.assertIn("<mark>needle</mark>", items[0]["snippet"])

    def test_fuzzy_search_really_tolerates_a_typo(self) -> None:
        items, total = fuzzy_search_conversations(self.db, "memry", 10, 0)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "memory")

    def test_fuzzy_search_handles_multiple_words_without_requiring_a_phrase(self) -> None:
        items, total = fuzzy_search_conversations(self.db, "ExampleAssistant memory", 10, 0)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "memory")

    def test_unicode_query_words_are_preserved(self) -> None:
        self.assertEqual(tokenize_query("Überraschung"), ["überraschung"])
        items, total = fulltext_search_conversations(self.db, "Überraschung", 10, 0)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "unicode")

    def test_title_search_ignores_matches_in_message_text(self) -> None:
        items, total = title_search_conversations(self.db, "InsightGPT", 10, 0)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "insight-title")

    def test_title_search_tolerates_a_close_title(self) -> None:
        items, total = title_search_conversations(self.db, "InsigtGPT", 10, 0)
        self.assertEqual(total, 1)
        self.assertEqual(items[0]["id"], "insight-title")
        self.assertEqual(items[0]["title_highlight"], "<mark>InsightGPT</mark>")

    def test_date_search_finds_message_and_conversation_activity(self) -> None:
        start = 1_704_067_200.0
        items, total = date_search_conversations(
            self.db,
            "2024-01-01",
            10,
            0,
            start,
            start + 86_400,
        )
        self.assertEqual(total, 3)
        self.assertEqual(
            [item["id"] for item in items],
            ["date-update", "date-message", "date-create"],
        )
        message_item = next(item for item in items if item["id"] == "date-message")
        self.assertEqual(message_item["activity_message_count"], 1)

    def test_date_search_rejects_an_invalid_date(self) -> None:
        with self.assertRaisesRegex(ValueError, "YYYY-MM-DD"):
            date_search_conversations(self.db, "sometime-ish", 10, 0)

    def test_activity_range_includes_through_date_and_excludes_next_day(self) -> None:
        start = 1_704_067_200.0
        items, total = activity_range_search_conversations(
            self.db,
            start,
            start + (2 * 86_400),
            10,
            0,
        )
        self.assertEqual(total, 5)
        self.assertIn("date-boundary", [item["id"] for item in items])
        self.assertIn("date-next", [item["id"] for item in items])
        self.assertNotIn("date-outside", [item["id"] for item in items])

    def test_activity_filter_intersects_search_before_pagination(self) -> None:
        start = 1_704_067_200.0
        search_items, search_total = fulltext_search_conversations(self.db, "rangeword", 100, 0)
        activity_items, _ = activity_range_search_conversations(
            self.db,
            start,
            start + 86_400,
            100,
            0,
        )
        filtered = _filter_items_by_activity(search_items, activity_items)
        self.assertEqual(search_total, 3)
        self.assertEqual([item["id"] for item in filtered], ["date-message"])
        self.assertEqual(filtered[0]["activity_message_count"], 1)

    def test_api_stacks_activity_range_before_paginating(self) -> None:
        start = 1_704_067_200.0
        with tempfile.TemporaryDirectory() as temp_directory:
            database_path = Path(temp_directory) / "search-test.db"
            disk_db = sqlite3.connect(database_path)
            self.db.backup(disk_db)
            disk_db.close()

            client = create_app(database_path).test_client()
            base_query = {
                "q": "rangeword",
                "mode": "normal",
                "activity_start": start,
                "activity_end": start + (2 * 86_400),
                "limit": 1,
            }
            first_page = client.get(
                "/api/conversations",
                query_string={**base_query, "offset": 0},
            ).get_json()
            second_page = client.get(
                "/api/conversations",
                query_string={**base_query, "offset": 1},
            ).get_json()

            self.assertEqual(first_page["total"], 2)
            self.assertEqual(second_page["total"], 2)
            page_ids = {first_page["items"][0]["id"], second_page["items"][0]["id"]}
            self.assertEqual(page_ids, {"date-message", "date-next"})

            filter_only = client.get(
                "/api/conversations",
                query_string={
                    "activity_start": start,
                    "activity_end": start + (2 * 86_400),
                    "limit": 40,
                },
            ).get_json()
            title_stacked = client.get(
                "/api/conversations",
                query_string={
                    "q": "activity",
                    "mode": "title",
                    "activity_start": start,
                    "activity_end": start + (2 * 86_400),
                },
            ).get_json()
            fuzzy_stacked = client.get(
                "/api/conversations",
                query_string={
                    "q": "rangewrd",
                    "mode": "fuzzy",
                    "activity_start": start,
                    "activity_end": start + (2 * 86_400),
                },
            ).get_json()
            self.assertEqual(filter_only["total"], 5)
            self.assertEqual(title_stacked["total"], 5)
            self.assertEqual(fuzzy_stacked["total"], 2)

    def test_api_rejects_incomplete_and_nonfinite_activity_bounds(self) -> None:
        with tempfile.TemporaryDirectory() as temp_directory:
            database_path = Path(temp_directory) / "search-test.db"
            disk_db = sqlite3.connect(database_path)
            self.db.backup(disk_db)
            disk_db.close()
            client = create_app(database_path).test_client()

            missing_end = client.get(
                "/api/conversations",
                query_string={"activity_start": 1_704_067_200},
            )
            nonfinite = client.get(
                "/api/conversations",
                query_string={"activity_start": "nan", "activity_end": "inf"},
            )
            self.assertEqual(missing_end.status_code, 400)
            self.assertEqual(nonfinite.status_code, 400)


if __name__ == "__main__":
    unittest.main()
