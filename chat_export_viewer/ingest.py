from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Iterable


MESSAGE_TEXT_LIMIT = 80_000
SEARCH_TEXT_LIMIT = 250_000


def canonical_json_dumps(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def compute_sha256(file_path: str | Path) -> str:
    digest = hashlib.sha256()
    with Path(file_path).open("rb") as handle:
        while True:
            chunk = handle.read(1024 * 1024)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def iter_export_conversations(file_path: str | Path) -> Iterable[dict[str, Any]]:
    path = Path(file_path)
    with path.open("rb") as handle:
        opening_bytes = handle.read(4096)

    try:
        opening_text = opening_bytes.decode("utf-8-sig").lstrip()
    except UnicodeDecodeError as error:
        raise ValueError("Import file must be UTF-8 JSON.") from error

    if not opening_text:
        raise ValueError("Import file is empty.")

    container = opening_text[0]
    if container == "{":
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if not isinstance(data, dict):
            raise ValueError("Expected a single ChatGPT conversation object.")
        conversation_id = data.get("id") or data.get("conversation_id")
        if not isinstance(conversation_id, str) or not conversation_id.strip():
            raise ValueError("Single conversation JSON is missing a conversation id.")
        yield data
        return

    if container != "[":
        raise ValueError(
            "Expected a ChatGPT conversations.json array or a single conversation object."
        )

    try:
        import ijson  # type: ignore
    except ImportError:
        with path.open("r", encoding="utf-8-sig") as handle:
            data = json.load(handle)
        if not isinstance(data, list):
            raise ValueError("Expected top-level JSON array in export file.")
        for item in data:
            if isinstance(item, dict):
                yield item
        return

    try:
        with path.open("rb") as handle:
            if opening_bytes.startswith(b"\xef\xbb\xbf"):
                handle.seek(3)
            for item in ijson.items(handle, "item", use_float=True):
                if isinstance(item, dict):
                    yield item
    except Exception as error:
        raise ValueError(f"Could not read import JSON: {error}") from error


def as_float(value: Any) -> float | None:
    if isinstance(value, (int, float)):
        return float(value)
    return None


def as_int_bool(value: Any) -> int:
    return 1 if bool(value) else 0


def trim_text(value: str, limit: int) -> str:
    if len(value) <= limit:
        return value
    return value[:limit] + "\n...[truncated]..."


def merge_list_unique(old_list: list[Any], new_list: list[Any]) -> list[Any]:
    merged = list(old_list)
    for item in new_list:
        if item not in merged:
            merged.append(item)
    return merged


def merge_values(old: Any, new: Any, key: str | None = None) -> Any:
    if new is None:
        return old
    if old is None:
        return new

    if key == "create_time":
        old_num = as_float(old)
        new_num = as_float(new)
        if old_num is not None and new_num is not None:
            return min(old_num, new_num)
        return old if new_num is None else new

    if key == "update_time":
        old_num = as_float(old)
        new_num = as_float(new)
        if old_num is not None and new_num is not None:
            return max(old_num, new_num)
        return old if new_num is None else new

    if isinstance(old, dict) and isinstance(new, dict):
        merged: dict[str, Any] = dict(old)
        for child_key, child_value in new.items():
            merged[child_key] = merge_values(merged.get(child_key), child_value, child_key)
        return merged

    if isinstance(old, list) and isinstance(new, list):
        return merge_list_unique(old, new)

    return new


def merge_conversation(existing: dict[str, Any], incoming: dict[str, Any]) -> dict[str, Any]:
    merged = merge_values(existing, incoming)
    if not isinstance(merged, dict):
        return incoming
    conversation_id = incoming.get("id") or incoming.get("conversation_id") or existing.get("id")
    if conversation_id:
        merged["id"] = conversation_id
    return merged


def _part_to_text(part: Any) -> str:
    if part is None:
        return ""
    if isinstance(part, str):
        return part
    if isinstance(part, (int, float, bool)):
        return str(part)
    if isinstance(part, dict):
        if isinstance(part.get("text"), str):
            return part["text"]
        pointer = (
            part.get("asset_pointer")
            or part.get("video_container_asset_pointer")
            or part.get("audio_asset_pointer")
        )
        content_type = part.get("content_type")
        if pointer and content_type:
            return f"[{content_type}: {pointer}]"
        if content_type:
            return f"[{content_type}]"
        return canonical_json_dumps(part)
    if isinstance(part, list):
        return "\n".join(_part_to_text(item) for item in part if item is not None)
    return str(part)


def extract_text_from_content(content: Any) -> str:
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, (int, float, bool)):
        return str(content)
    if isinstance(content, list):
        return "\n".join(_part_to_text(item) for item in content if item is not None)
    if not isinstance(content, dict):
        return str(content)

    content_type = content.get("content_type")
    if isinstance(content.get("parts"), list):
        text_parts = [_part_to_text(part) for part in content["parts"]]
        return "\n".join(part for part in text_parts if part)

    if content_type == "code":
        return str(content.get("text") or "")
    if content_type == "reasoning_recap":
        return str(content.get("content") or "")
    if content_type == "execution_output":
        return str(content.get("text") or "")
    if content_type == "system_error":
        name = str(content.get("name") or "system_error")
        text = str(content.get("text") or "")
        return f"{name}: {text}".strip()
    if content_type == "user_editable_context":
        profile = str(content.get("user_profile") or "")
        instructions = str(content.get("user_instructions") or "")
        return "\n\n".join([item for item in [profile, instructions] if item])
    if content_type == "thoughts":
        thoughts = content.get("thoughts")
        if isinstance(thoughts, list):
            return "\n".join(_part_to_text(item) for item in thoughts if item is not None)
        return str(thoughts or "")
    if content_type == "tether_quote":
        quote_text = str(content.get("text") or "")
        quote_title = str(content.get("title") or "")
        return "\n".join(item for item in [quote_title, quote_text] if item)
    if content_type == "tether_browsing_display":
        summary = str(content.get("summary") or "")
        result = str(content.get("result") or "")
        return "\n".join(item for item in [summary, result] if item)

    for key in ("text", "content", "summary", "result"):
        if isinstance(content.get(key), str):
            return content[key]

    return canonical_json_dumps(content)


def trace_current_path(mapping: dict[str, Any], current_node: Any) -> set[str]:
    path_nodes: set[str] = set()
    node_id = current_node if isinstance(current_node, str) else None
    while node_id and node_id not in path_nodes:
        node = mapping.get(node_id)
        if not isinstance(node, dict):
            break
        path_nodes.add(node_id)
        parent = node.get("parent")
        node_id = parent if isinstance(parent, str) else None
    return path_nodes


def extract_messages(conversation: dict[str, Any]) -> tuple[list[dict[str, Any]], dict[str, int], str]:
    mapping = conversation.get("mapping")
    if not isinstance(mapping, dict):
        return [], {"user": 0, "assistant": 0, "system": 0, "tool": 0}, conversation.get("title") or ""

    current_path_nodes = trace_current_path(mapping, conversation.get("current_node"))
    conversation_id = str(conversation.get("id") or conversation.get("conversation_id") or "")
    default_model_slug = conversation.get("default_model_slug")

    role_counts = {"user": 0, "assistant": 0, "system": 0, "tool": 0}
    messages: list[dict[str, Any]] = []
    search_fragments: list[str] = []

    for sort_index, (node_id, node) in enumerate(mapping.items()):
        if not isinstance(node, dict):
            continue
        message = node.get("message")
        if not isinstance(message, dict):
            continue

        author = message.get("author")
        author_role = author.get("role") if isinstance(author, dict) else None
        author_name = author.get("name") if isinstance(author, dict) else None
        role = str(author_role or "unknown")
        if role in role_counts:
            role_counts[role] += 1

        metadata = message.get("metadata")
        metadata_dict = metadata if isinstance(metadata, dict) else {}
        model_slug = (
            metadata_dict.get("model_slug")
            or metadata_dict.get("default_model_slug")
            or default_model_slug
        )

        content = message.get("content")
        content_type = content.get("content_type") if isinstance(content, dict) else None
        text_content = trim_text(extract_text_from_content(content), MESSAGE_TEXT_LIMIT)
        is_hidden = bool(metadata_dict.get("is_visually_hidden_from_conversation"))
        searchable_text = text_content
        if is_hidden or content_type == "user_editable_context":
            searchable_text = ""
        if searchable_text:
            search_fragments.append(searchable_text)

        message_id = str(message.get("id") or f"{conversation_id}:{node_id}")
        message_pk = f"{conversation_id}:{node_id}"
        row = {
            "message_pk": message_pk,
            "message_id": message_id,
            "conversation_id": conversation_id,
            "node_id": str(node_id),
            "parent_id": node.get("parent"),
            "role": role,
            "author_name": author_name,
            "recipient": message.get("recipient"),
            "status": message.get("status"),
            "create_time": as_float(message.get("create_time")),
            "update_time": as_float(message.get("update_time")),
            "model_slug": model_slug,
            "content_type": content_type,
            "text_content": text_content,
            "search_text": searchable_text,
            "is_current_path": 1 if node_id in current_path_nodes else 0,
            "sort_index": sort_index,
            "raw_json": canonical_json_dumps(message),
        }
        messages.append(row)

    title = str(conversation.get("title") or "")
    search_text = "\n".join([title, *search_fragments]).strip()
    search_text = trim_text(search_text, SEARCH_TEXT_LIMIT)
    return messages, role_counts, search_text


def ingest_export(
    connection: sqlite3.Connection,
    export_path: str | Path,
    logger: Callable[[str], None] | None = None,
    progress_every: int = 10,
) -> dict[str, Any]:
    path = Path(export_path).expanduser().resolve()
    if not path.exists():
        raise FileNotFoundError(f"Export file not found: {path}")

    file_hash = compute_sha256(path)
    existing_export = connection.execute(
        "SELECT id, imported_at, source_path FROM exports WHERE file_sha256 = ?",
        (file_hash,),
    ).fetchone()
    if existing_export:
        return {
            "status": "skipped",
            "reason": "duplicate_export",
            "export_id": int(existing_export["id"]),
            "imported_at": existing_export["imported_at"],
            "source_path": existing_export["source_path"],
        }

    imported_at = utc_now_iso()
    export_cursor = connection.execute(
        """
        INSERT INTO exports (
            source_path,
            source_name,
            file_sha256,
            imported_at
        ) VALUES (?, ?, ?, ?)
        """,
        (str(path), path.name, file_hash, imported_at),
    )
    export_id = int(export_cursor.lastrowid)

    conversation_count = 0
    merged_conversation_count = 0
    new_conversation_count = 0
    updated_conversation_count = 0

    try:
        for item in iter_export_conversations(path):
            conversation_count += 1
            conversation_id = item.get("id") or item.get("conversation_id")
            if not isinstance(conversation_id, str):
                continue

            item["id"] = conversation_id
            existing_row = connection.execute(
                """
                SELECT id, raw_json, first_seen_export_id, first_seen_at
                FROM conversations
                WHERE id = ?
                """,
                (conversation_id,),
            ).fetchone()

            if existing_row:
                existing_json = json.loads(existing_row["raw_json"])
                merged = merge_conversation(existing_json, item)
            else:
                merged = item

            merged["id"] = conversation_id
            merged_raw_json = canonical_json_dumps(merged)

            if existing_row is None:
                new_conversation_count += 1
            elif existing_row["raw_json"] != merged_raw_json:
                updated_conversation_count += 1

            messages, role_counts, search_text = extract_messages(merged)
            title = str(merged.get("title") or "(untitled conversation)")
            create_time = as_float(merged.get("create_time"))
            update_time = as_float(merged.get("update_time"))
            default_model_slug = merged.get("default_model_slug")
            current_node = merged.get("current_node")
            is_archived = as_int_bool(merged.get("is_archived"))
            now_iso = utc_now_iso()

            first_seen_export_id = export_id
            first_seen_at = imported_at
            if existing_row:
                first_seen_export_id = int(existing_row["first_seen_export_id"])
                first_seen_at = str(existing_row["first_seen_at"])

            connection.execute(
                """
                INSERT INTO conversations (
                    id,
                    title,
                    create_time,
                    update_time,
                    default_model_slug,
                    current_node,
                    is_archived,
                    raw_json,
                    search_text,
                    message_count,
                    user_message_count,
                    assistant_message_count,
                    system_message_count,
                    tool_message_count,
                    first_seen_export_id,
                    last_seen_export_id,
                    first_seen_at,
                    last_seen_at,
                    merged_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(id) DO UPDATE SET
                    title = excluded.title,
                    create_time = CASE
                        WHEN conversations.create_time IS NULL THEN excluded.create_time
                        WHEN excluded.create_time IS NULL THEN conversations.create_time
                        ELSE MIN(conversations.create_time, excluded.create_time)
                    END,
                    update_time = CASE
                        WHEN conversations.update_time IS NULL THEN excluded.update_time
                        WHEN excluded.update_time IS NULL THEN conversations.update_time
                        ELSE MAX(conversations.update_time, excluded.update_time)
                    END,
                    default_model_slug = COALESCE(excluded.default_model_slug, conversations.default_model_slug),
                    current_node = COALESCE(excluded.current_node, conversations.current_node),
                    is_archived = excluded.is_archived,
                    raw_json = excluded.raw_json,
                    search_text = excluded.search_text,
                    message_count = excluded.message_count,
                    user_message_count = excluded.user_message_count,
                    assistant_message_count = excluded.assistant_message_count,
                    system_message_count = excluded.system_message_count,
                    tool_message_count = excluded.tool_message_count,
                    last_seen_export_id = excluded.last_seen_export_id,
                    last_seen_at = excluded.last_seen_at,
                    merged_at = excluded.merged_at
                """,
                (
                    conversation_id,
                    title,
                    create_time,
                    update_time,
                    default_model_slug,
                    current_node,
                    is_archived,
                    merged_raw_json,
                    search_text,
                    len(messages),
                    role_counts["user"],
                    role_counts["assistant"],
                    role_counts["system"],
                    role_counts["tool"],
                    first_seen_export_id,
                    export_id,
                    first_seen_at,
                    imported_at,
                    now_iso,
                ),
            )

            connection.execute(
                """
                INSERT OR IGNORE INTO conversation_exports (conversation_id, export_id, seen_at)
                VALUES (?, ?, ?)
                """,
                (conversation_id, export_id, imported_at),
            )

            connection.execute("DELETE FROM messages WHERE conversation_id = ?", (conversation_id,))
            connection.execute("DELETE FROM message_fts WHERE conversation_id = ?", (conversation_id,))

            if messages:
                connection.executemany(
                    """
                    INSERT INTO messages (
                        message_pk,
                        conversation_id,
                        message_id,
                        node_id,
                        parent_id,
                        role,
                        author_name,
                        recipient,
                        status,
                        create_time,
                        update_time,
                        model_slug,
                        content_type,
                        text_content,
                        is_current_path,
                        sort_index,
                        raw_json
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row["message_pk"],
                            row["conversation_id"],
                            row["message_id"],
                            row["node_id"],
                            row["parent_id"],
                            row["role"],
                            row["author_name"],
                            row["recipient"],
                            row["status"],
                            row["create_time"],
                            row["update_time"],
                            row["model_slug"],
                            row["content_type"],
                            row["text_content"],
                            row["is_current_path"],
                            row["sort_index"],
                            row["raw_json"],
                        )
                        for row in messages
                    ],
                )
                connection.executemany(
                    """
                    INSERT INTO message_fts (
                        message_pk,
                        conversation_id,
                        conversation_title,
                        role,
                        text_content
                    ) VALUES (?, ?, ?, ?, ?)
                    """,
                    [
                        (
                            row["message_pk"],
                            row["conversation_id"],
                            title,
                            row["role"] or "",
                            row["search_text"] or "",
                        )
                        for row in messages
                    ],
                )

            merged_conversation_count += 1
            if logger and merged_conversation_count % max(progress_every, 1) == 0:
                logger(
                    f"Processed {merged_conversation_count} conversations "
                    f"(new: {new_conversation_count}, updated: {updated_conversation_count})"
                )

        connection.execute(
            """
            UPDATE exports
            SET
                conversation_count = ?,
                merged_conversation_count = ?,
                new_conversation_count = ?,
                updated_conversation_count = ?
            WHERE id = ?
            """,
            (
                conversation_count,
                merged_conversation_count,
                new_conversation_count,
                updated_conversation_count,
                export_id,
            ),
        )
        connection.commit()
    except Exception:
        connection.rollback()
        raise

    return {
        "status": "imported",
        "export_id": export_id,
        "source_path": str(path),
        "conversation_count": conversation_count,
        "merged_conversation_count": merged_conversation_count,
        "new_conversation_count": new_conversation_count,
        "updated_conversation_count": updated_conversation_count,
    }
