from __future__ import annotations

import re
import sqlite3
import unicodedata
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any


MAX_TAGS_PER_CONVERSATION = 30
MAX_TAG_LENGTH = 80
_WHITESPACE_RE = re.compile(r"\s+")
_DISALLOWED_CATEGORIES = {"Cc", "Cf", "Cs"}


class TagValidationError(ValueError):
    pass


class ConversationNotFoundError(LookupError):
    pass


@dataclass(frozen=True)
class NormalizedTag:
    name: str
    normalized_name: str


def _contains_control_characters(value: str) -> bool:
    return any(unicodedata.category(character) in _DISALLOWED_CATEGORIES for character in value)


def normalize_tag_name(value: Any) -> NormalizedTag:
    if not isinstance(value, str):
        raise TagValidationError("Each tag must be text.")

    normalized = unicodedata.normalize("NFKC", value)
    if _contains_control_characters(normalized):
        raise TagValidationError("Tags cannot contain control or invisible formatting characters.")
    if "," in normalized:
        raise TagValidationError("Tags cannot contain commas. Use commas only to separate tags.")

    display_name = _WHITESPACE_RE.sub(" ", normalized).strip()
    if not display_name:
        raise TagValidationError("Tags cannot be empty.")
    if len(display_name) > MAX_TAG_LENGTH:
        raise TagValidationError(f"Tags can be at most {MAX_TAG_LENGTH} characters long.")

    normalized_name = unicodedata.normalize("NFKC", display_name.casefold())
    return NormalizedTag(name=display_name, normalized_name=normalized_name)


def normalize_tag_search_query(value: str) -> str:
    if not isinstance(value, str):
        return ""
    normalized = unicodedata.normalize("NFKC", value)
    if _contains_control_characters(normalized):
        return ""
    return unicodedata.normalize(
        "NFKC",
        _WHITESPACE_RE.sub(" ", normalized).strip().casefold(),
    )


def validate_tag_names(values: Any) -> list[NormalizedTag]:
    if not isinstance(values, list):
        raise TagValidationError("JSON field 'tags' must be a list of tag names.")
    if len(values) > MAX_TAGS_PER_CONVERSATION:
        raise TagValidationError(
            f"A conversation can have at most {MAX_TAGS_PER_CONVERSATION} tags."
        )

    unique_tags: list[NormalizedTag] = []
    seen_names: set[str] = set()
    for value in values:
        tag = normalize_tag_name(value)
        if tag.normalized_name in seen_names:
            continue
        seen_names.add(tag.normalized_name)
        unique_tags.append(tag)
    return unique_tags


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def replace_conversation_tags(
    connection: sqlite3.Connection,
    conversation_id: str,
    values: Any,
) -> list[str]:
    requested_tags = validate_tag_names(values)
    now_iso = _utc_now_iso()

    with connection:
        conversation = connection.execute(
            "SELECT 1 FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if conversation is None:
            raise ConversationNotFoundError(
                f"Conversation not found: {conversation_id}"
            )

        resolved_tags: list[tuple[int, str]] = []
        for tag in requested_tags:
            connection.execute(
                """
                INSERT INTO tags (name, normalized_name, created_at)
                VALUES (?, ?, ?)
                ON CONFLICT(normalized_name) DO NOTHING
                """,
                (tag.name, tag.normalized_name, now_iso),
            )
            row = connection.execute(
                "SELECT id, name FROM tags WHERE normalized_name = ?",
                (tag.normalized_name,),
            ).fetchone()
            if row is None:
                raise sqlite3.IntegrityError(
                    f"Could not resolve tag: {tag.name}"
                )
            resolved_tags.append((int(row[0]), str(row[1])))

        connection.execute(
            "DELETE FROM conversation_tags WHERE conversation_id = ?",
            (conversation_id,),
        )
        if resolved_tags:
            connection.executemany(
                """
                INSERT INTO conversation_tags (
                    conversation_id,
                    tag_id,
                    display_order,
                    added_at
                ) VALUES (?, ?, ?, ?)
                """,
                [
                    (conversation_id, tag_id, display_order, now_iso)
                    for display_order, (tag_id, _name) in enumerate(resolved_tags)
                ],
            )

    return [name for _tag_id, name in resolved_tags]


def get_conversation_tags(
    connection: sqlite3.Connection,
    conversation_id: str,
) -> list[str]:
    rows = connection.execute(
        """
        SELECT t.name
        FROM conversation_tags ct
        JOIN tags t ON t.id = ct.tag_id
        WHERE ct.conversation_id = ?
        ORDER BY ct.display_order, t.normalized_name, t.id
        """,
        (conversation_id,),
    ).fetchall()
    return [str(row[0]) for row in rows]


def attach_conversation_tags(
    connection: sqlite3.Connection,
    items: list[dict[str, Any]],
) -> None:
    for item in items:
        item["tags"] = []

    conversation_ids = list(
        dict.fromkeys(
            str(item["id"])
            for item in items
            if item.get("id") is not None
        )
    )
    if not conversation_ids:
        return

    tags_by_conversation: dict[str, list[str]] = {
        conversation_id: [] for conversation_id in conversation_ids
    }
    chunk_size = 900
    for start in range(0, len(conversation_ids), chunk_size):
        chunk = conversation_ids[start : start + chunk_size]
        placeholders = ",".join("?" for _item in chunk)
        rows = connection.execute(
            f"""
            SELECT ct.conversation_id, t.name
            FROM conversation_tags ct
            JOIN tags t ON t.id = ct.tag_id
            WHERE ct.conversation_id IN ({placeholders})
            ORDER BY ct.conversation_id, ct.display_order, t.normalized_name, t.id
            """,
            chunk,
        ).fetchall()
        for row in rows:
            tags_by_conversation[str(row[0])].append(str(row[1]))

    for item in items:
        item["tags"] = list(tags_by_conversation.get(str(item.get("id")), []))


def list_tags(connection: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = connection.execute(
        """
        SELECT
            t.name,
            t.normalized_name,
            COUNT(ct.conversation_id) AS conversation_count
        FROM tags t
        JOIN conversation_tags ct ON ct.tag_id = t.id
        GROUP BY t.id, t.name, t.normalized_name
        ORDER BY t.normalized_name, t.id
        """
    ).fetchall()
    return [
        {
            "name": str(row[0]),
            "normalized_name": str(row[1]),
            "conversation_count": int(row[2]),
        }
        for row in rows
    ]
