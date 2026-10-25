from __future__ import annotations

import difflib
import io
import json
import math
import re
import sqlite3
import tempfile
import zipfile
from datetime import date, datetime, time, timedelta
from pathlib import Path
from threading import Lock
from typing import Any

from flask import Flask, Response, g, jsonify, render_template, request

from .database import connect_db, init_db
from .exporting import build_chat_markdown_model, build_chat_only_markdown, render_split_chat_markdown, safe_filename
from .ingest import ingest_export
from .tags import (
    ConversationNotFoundError,
    TagValidationError,
    attach_conversation_tags,
    get_conversation_tags,
    list_tags,
    normalize_tag_search_query,
    replace_conversation_tags,
)

try:
    from rapidfuzz import fuzz, process  # type: ignore
    from rapidfuzz.distance import Levenshtein  # type: ignore
except ImportError:
    fuzz = None
    process = None
    Levenshtein = None


_vocabulary_cache: dict[str, tuple[tuple[tuple[str, int, int], ...], list[tuple[str, int]]]] = {}
_vocabulary_cache_lock = Lock()


def create_app(db_path: str | Path) -> Flask:
    app = Flask(__name__, static_folder="static", template_folder="templates")
    app.config["DB_PATH"] = str(Path(db_path).expanduser().resolve())

    bootstrap_connection = connect_db(app.config["DB_PATH"])
    init_db(bootstrap_connection)
    bootstrap_connection.close()

    def get_db() -> sqlite3.Connection:
        if "db" not in g:
            g.db = connect_db(app.config["DB_PATH"])
            init_db(g.db)
        return g.db

    @app.teardown_appcontext
    def close_db(exception: BaseException | None) -> None:
        connection = g.pop("db", None)
        if connection is not None:
            connection.close()

    @app.get("/")
    def home() -> str:
        return render_template("index.html")

    @app.get("/api/stats")
    def api_stats() -> Any:
        db = get_db()
        row = db.execute(
            """
            SELECT
                COUNT(*) AS conversation_count,
                COALESCE(SUM(message_count), 0) AS message_count
            FROM conversations
            """
        ).fetchone()
        export_row = db.execute(
            """
            SELECT
                COUNT(*) AS export_count,
                MAX(imported_at) AS last_imported_at
            FROM exports
            """
        ).fetchone()
        return jsonify(
            {
                "conversation_count": int(row["conversation_count"]),
                "message_count": int(row["message_count"]),
                "export_count": int(export_row["export_count"]),
                "last_imported_at": export_row["last_imported_at"],
            }
        )

    @app.get("/api/exports")
    def api_exports() -> Any:
        db = get_db()
        rows = db.execute(
            """
            SELECT
                id,
                source_name,
                source_path,
                imported_at,
                conversation_count,
                merged_conversation_count,
                new_conversation_count,
                updated_conversation_count
            FROM exports
            ORDER BY id DESC
            LIMIT 50
            """
        ).fetchall()
        return jsonify({"items": [dict(row) for row in rows]})

    @app.get("/api/tags")
    def api_tags() -> Any:
        return jsonify({"items": list_tags(get_db())})

    @app.post("/api/import-path")
    def api_import_path() -> Any:
        body = request.get_json(silent=True) or {}
        import_path = body.get("path")
        if not isinstance(import_path, str) or not import_path.strip():
            return jsonify({"error": "Missing JSON field: path"}), 400
        db = get_db()
        try:
            result = ingest_export(db, import_path.strip())
        except FileNotFoundError as error:
            return jsonify({"error": str(error)}), 404
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            return jsonify({"error": str(error)}), 500
        return jsonify(result)

    @app.post("/api/import-file")
    def api_import_file() -> Any:
        upload = request.files.get("file")
        if upload is None or not upload.filename:
            return jsonify({"error": "Missing file upload field: file"}), 400

        suffix = Path(upload.filename).suffix or ".json"
        temp_file = tempfile.NamedTemporaryFile(delete=False, suffix=suffix)
        temp_path = Path(temp_file.name)
        temp_file.close()
        try:
            upload.save(temp_path)
            db = get_db()
            result = ingest_export(db, temp_path)
        except ValueError as error:
            return jsonify({"error": str(error)}), 400
        except Exception as error:
            return jsonify({"error": str(error)}), 500
        finally:
            temp_path.unlink(missing_ok=True)

        return jsonify(result)

    @app.get("/api/conversations")
    def api_conversations() -> Any:
        db = get_db()
        query = request.args.get("q", "").strip()
        mode = request.args.get("mode", "normal").strip().lower()
        try:
            limit = max(1, min(int(request.args.get("limit", 40)), 200))
            offset = max(0, int(request.args.get("offset", 0)))
        except ValueError:
            return jsonify({"error": "limit and offset must be whole numbers."}), 400
        start_value = request.args.get("activity_start")
        end_value = request.args.get("activity_end")

        try:
            if bool(start_value) != bool(end_value):
                raise ValueError("Activity filter needs both From and Through dates.")
            activity_start = float(start_value) if start_value else None
            activity_end = float(end_value) if end_value else None
            if activity_start is not None and not math.isfinite(activity_start):
                raise ValueError("Activity filter start is invalid.")
            if activity_end is not None and not math.isfinite(activity_end):
                raise ValueError("Activity filter end is invalid.")
            if activity_start is not None and activity_end is not None and activity_end <= activity_start:
                raise ValueError("Activity filter end must be after its start.")
        except ValueError as error:
            return jsonify({"error": str(error)}), 400

        has_activity_filter = activity_start is not None and activity_end is not None

        if not query and not has_activity_filter:
            total = int(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
            rows = db.execute(
                """
                SELECT
                    id,
                    title,
                    update_time,
                    create_time,
                    default_model_slug,
                    message_count,
                    user_message_count,
                    assistant_message_count,
                    tool_message_count,
                    system_message_count,
                    last_seen_at
                FROM conversations
                ORDER BY (update_time IS NULL), update_time DESC, last_seen_at DESC, id
                LIMIT ? OFFSET ?
                """,
                (limit, offset),
            ).fetchall()
            items = [dict(row) for row in rows]
            attach_conversation_tags(db, items)
            return jsonify(
                {
                    "items": items,
                    "total": total,
                    "offset": offset,
                    "limit": limit,
                    "mode": "default",
                    "query": "",
                }
            )

        archive_count = int(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
        activity_items: list[dict[str, Any]] = []
        if has_activity_filter:
            activity_items, _activity_total = activity_range_search_conversations(
                db,
                float(activity_start),
                float(activity_end),
                max(archive_count, 1),
                0,
            )

        if not query:
            total = len(activity_items)
            items = activity_items[offset : offset + limit]
        else:
            search_limit = max(archive_count, 1) if has_activity_filter else limit
            search_offset = 0 if has_activity_filter else offset
            if mode == "title":
                items, total = title_search_conversations(db, query, search_limit, search_offset)
            elif mode == "fuzzy":
                items, total = fuzzy_search_conversations(db, query, search_limit, search_offset)
            elif mode == "tag":
                items, total = tag_search_conversations(db, query, search_limit, search_offset)
            else:
                items, total = fulltext_search_conversations(db, query, search_limit, search_offset)

            if has_activity_filter:
                items = _filter_items_by_activity(items, activity_items)
                total = len(items)
                items = items[offset : offset + limit]

        attach_conversation_tags(db, items)

        return jsonify(
            {
                "items": items,
                "total": total,
                "offset": offset,
                "limit": limit,
                "mode": mode,
                "query": query,
            }
        )

    @app.get("/api/conversations/<conversation_id>")
    def api_conversation(conversation_id: str) -> Any:
        db = get_db()
        path_mode = request.args.get("path", "current").strip().lower()
        include_raw = request.args.get("include_raw", "1").strip() != "0"
        hide_empty_system = request.args.get("hide_empty_system", "1").strip() != "0"

        row = db.execute(
            """
            SELECT
                id,
                title,
                create_time,
                update_time,
                default_model_slug,
                current_node,
                is_archived,
                message_count,
                user_message_count,
                assistant_message_count,
                tool_message_count,
                system_message_count,
                first_seen_at,
                last_seen_at,
                raw_json
            FROM conversations
            WHERE id = ?
            """,
            (conversation_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": f"Conversation not found: {conversation_id}"}), 404

        message_filter = ""
        if path_mode == "current":
            message_filter = "AND is_current_path = 1"
        elif path_mode != "all":
            return jsonify({"error": "Invalid path mode. Use 'current' or 'all'."}), 400

        message_rows = db.execute(
            f"""
            SELECT
                message_pk,
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
            FROM messages
            WHERE conversation_id = ?
            {message_filter}
            ORDER BY (create_time IS NOT NULL), create_time, sort_index
            """,
            (conversation_id,),
        ).fetchall()

        messages = []
        for item in message_rows:
            message_dict = dict(item)
            parsed_raw: Any = None
            try:
                parsed_raw = json.loads(message_dict["raw_json"])
            except json.JSONDecodeError:
                parsed_raw = message_dict["raw_json"]

            if hide_empty_system and is_empty_hidden_system_message(message_dict, parsed_raw):
                continue

            if include_raw:
                message_dict["raw"] = parsed_raw
            message_dict.pop("raw_json", None)
            messages.append(message_dict)

        conversation_payload = dict(row)
        raw_conversation: Any = None
        if include_raw:
            try:
                raw_conversation = json.loads(conversation_payload["raw_json"])
            except json.JSONDecodeError:
                raw_conversation = conversation_payload["raw_json"]
        conversation_payload.pop("raw_json", None)
        conversation_payload["tags"] = get_conversation_tags(db, conversation_id)

        return jsonify(
            {
                "conversation": conversation_payload,
                "messages": messages,
                "raw_conversation": raw_conversation,
                "path_mode": path_mode,
                "hide_empty_system": hide_empty_system,
            }
        )

    @app.put("/api/conversations/<conversation_id>/tags")
    def api_replace_conversation_tags(conversation_id: str) -> Any:
        body = request.get_json(silent=True)
        if not isinstance(body, dict) or "tags" not in body:
            return jsonify({"error": "Missing JSON field: tags"}), 400

        try:
            tags = replace_conversation_tags(get_db(), conversation_id, body["tags"])
        except TagValidationError as error:
            return jsonify({"error": str(error)}), 400
        except ConversationNotFoundError as error:
            return jsonify({"error": str(error)}), 404
        except sqlite3.Error as error:
            return jsonify({"error": str(error)}), 500

        return jsonify({"conversation_id": conversation_id, "tags": tags})

    @app.get("/api/conversations/<conversation_id>/export")
    def api_export_conversation(conversation_id: str) -> Response:
        db = get_db()
        row = db.execute(
            "SELECT raw_json FROM conversations WHERE id = ?",
            (conversation_id,),
        ).fetchone()
        if row is None:
            return jsonify({"error": f"Conversation not found: {conversation_id}"}), 404
        payload = f"[{row['raw_json']}]"
        filename = f"{conversation_id}.json"
        return Response(
            payload,
            mimetype="application/json",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/conversations/<conversation_id>/export-chat")
    def api_export_chat_only(conversation_id: str) -> Response:
        db = get_db()
        path_mode = request.args.get("path", "current").strip().lower()
        if path_mode not in {"current", "all"}:
            return jsonify({"error": "Invalid path mode. Use 'current' or 'all'."}), 400

        payload = build_chat_only_markdown(db, conversation_id, path_mode,
                                            speaker_labels=app.config.get("EXPORT_SPEAKER_LABELS"))
        if payload is None:
            return jsonify({"error": f"Conversation not found: {conversation_id}"}), 404

        filename = f"{safe_filename(conversation_id)}-chat-only.md"
        return Response(
            payload,
            mimetype="text/markdown",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    @app.get("/api/conversations/<conversation_id>/export-chat-split")
    def api_export_split_chat(conversation_id: str) -> Response:
        db = get_db()
        path_mode = request.args.get("path", "current").strip().lower()
        if path_mode not in {"current", "all"}:
            return jsonify({"error": "Invalid path mode. Use 'current' or 'all'."}), 400

        try:
            target_tokens = int(request.args.get("target_tokens", "30000"))
        except ValueError:
            return jsonify({"error": "target_tokens must be a number."}), 400

        chat = build_chat_markdown_model(db, conversation_id, path_mode,
                                         speaker_labels=app.config.get("EXPORT_SPEAKER_LABELS"))
        if chat is None:
            return jsonify({"error": f"Conversation not found: {conversation_id}"}), 404

        chunks = render_split_chat_markdown(chat, target_tokens)
        base_name = safe_filename(chat.title or conversation_id)
        buffer = io.BytesIO()
        with zipfile.ZipFile(buffer, "w", compression=zipfile.ZIP_DEFLATED) as archive:
            for index, chunk in enumerate(chunks, start=1):
                archive.writestr(f"{base_name}_part-{index:02d}.md", chunk)
        buffer.seek(0)

        filename = f"{base_name}-chat-split.zip"
        return Response(
            buffer.getvalue(),
            mimetype="application/zip",
            headers={"Content-Disposition": f'attachment; filename="{filename}"'},
        )

    return app


def is_empty_hidden_system_message(message_row: dict[str, Any], parsed_raw: Any) -> bool:
    if (message_row.get("role") or "").lower() != "system":
        return False
    text_content = (message_row.get("text_content") or "").strip()
    if text_content:
        return False
    if not isinstance(parsed_raw, dict):
        return False
    metadata = parsed_raw.get("metadata")
    if not isinstance(metadata, dict):
        return False
    if not bool(metadata.get("is_visually_hidden_from_conversation")):
        return False
    return True


def tokenize_query(query: str) -> list[str]:
    # Keep this aligned with FTS5's unicode61 tokenizer.  The old ASCII-only
    # expression silently discarded searches written in many languages.
    return [token.strip("_") for token in re.findall(r"\w+", query.lower()) if token.strip("_")]


def build_fts_query(query: str) -> str | None:
    tokens = tokenize_query(query)
    if not tokens:
        return None
    terms = [f'"{token}"' for token in tokens[:16]]
    expression = " AND ".join(terms)
    return f"{{conversation_title text_content}} : ({expression})"


def _run_fts_search(
    db: sqlite3.Connection,
    fts_query: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    rows = db.execute(
        """
        WITH ranked_matches AS (
            SELECT
                message_fts.conversation_id,
                message_fts.rowid AS fts_rowid,
                bm25(message_fts, 0.0, 0.0, 8.0, 0.0, 1.0) AS relevance,
                highlight(message_fts, 2, '<mark>', '</mark>') AS title_highlight,
                snippet(message_fts, 4, '<mark>', '</mark>', ' ... ', 22) AS snippet
            FROM message_fts
            JOIN messages m ON m.message_pk = message_fts.message_pk
            WHERE message_fts MATCH ?
                AND m.is_current_path = 1
        ),
        best_matches AS (
            SELECT conversation_id, fts_rowid, relevance, title_highlight, snippet
            FROM (
                SELECT
                    conversation_id,
                    fts_rowid,
                    relevance,
                    title_highlight,
                    snippet,
                    ROW_NUMBER() OVER (
                        PARTITION BY conversation_id
                        ORDER BY relevance, fts_rowid
                    ) AS match_number
                FROM ranked_matches
            )
            WHERE match_number = 1
        )
        SELECT
            c.id,
            c.title,
            c.update_time,
            c.create_time,
            c.default_model_slug,
            c.message_count,
            c.user_message_count,
            c.assistant_message_count,
            c.tool_message_count,
            c.system_message_count,
            c.last_seen_at,
            best_matches.relevance,
            best_matches.title_highlight,
            best_matches.snippet
        FROM best_matches
        JOIN conversations c ON c.id = best_matches.conversation_id
        ORDER BY best_matches.relevance, (c.update_time IS NULL), c.update_time DESC, c.id
        LIMIT ? OFFSET ?
        """,
        (fts_query, limit, offset),
    ).fetchall()

    total = int(
        db.execute(
            """
            SELECT COUNT(DISTINCT message_fts.conversation_id)
            FROM message_fts
            JOIN messages m ON m.message_pk = message_fts.message_pk
            WHERE message_fts MATCH ?
                AND m.is_current_path = 1
            """,
            (fts_query,),
        ).fetchone()[0]
    )

    items = [dict(row) for row in rows]
    for item in items:
        title_highlight = str(item.get("title_highlight") or "")
        snippet = str(item.get("snippet") or "")
        if "<mark>" in title_highlight and "<mark>" not in snippet:
            item["snippet"] = ""
    return items, total


def _tag_match_score(
    query_normalized: str,
    query_tokens: list[str],
    tag_normalized: str,
) -> float | None:
    if not query_normalized or not tag_normalized:
        return None
    if query_normalized == tag_normalized:
        return 400.0
    if tag_normalized.startswith(query_normalized):
        return 320.0
    if query_normalized in tag_normalized:
        return 280.0

    tag_tokens = set(tokenize_query(tag_normalized))
    if query_tokens and all(token in tag_tokens for token in query_tokens):
        return 240.0
    return None


def tag_search_conversations(
    db: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    query_normalized = normalize_tag_search_query(query)
    if not query_normalized:
        return [], 0
    query_tokens = tokenize_query(query_normalized)

    rows = db.execute(
        """
        SELECT
            c.id,
            c.title,
            c.update_time,
            c.create_time,
            c.default_model_slug,
            c.message_count,
            c.user_message_count,
            c.assistant_message_count,
            c.tool_message_count,
            c.system_message_count,
            c.last_seen_at,
            t.name AS matched_tag,
            t.normalized_name AS matched_tag_normalized,
            ct.display_order AS matched_tag_order
        FROM conversation_tags ct
        JOIN tags t ON t.id = ct.tag_id
        JOIN conversations c ON c.id = ct.conversation_id
        ORDER BY c.id, ct.display_order, t.normalized_name, t.id
        """
    ).fetchall()

    grouped_by_id: dict[str, dict[str, Any]] = {}
    for row in rows:
        item = dict(row)
        tag_normalized = str(item.pop("matched_tag_normalized") or "")
        matched_tag = str(item.pop("matched_tag") or "")
        item.pop("matched_tag_order", None)
        conversation_id = str(item["id"])
        existing = grouped_by_id.get(conversation_id)
        if existing is None:
            item["matched_tags"] = []
            item["_tag_entries"] = []
            grouped_by_id[conversation_id] = item
            existing = item

        existing["_tag_entries"].append((matched_tag, tag_normalized))
        score = _tag_match_score(query_normalized, query_tokens, tag_normalized)
        if score is not None:
            current_score = existing.get("tag_match_score")
            if current_score is None or score > float(current_score):
                existing["tag_match_score"] = score
            if matched_tag not in existing["matched_tags"]:
                existing["matched_tags"].append(matched_tag)

    matches: list[dict[str, Any]] = []
    query_token_set = set(query_tokens)
    for item in grouped_by_id.values():
        tag_entries = list(item.pop("_tag_entries"))
        if item.get("tag_match_score") is None and query_token_set:
            available_tokens: set[str] = set()
            for _tag_name, tag_normalized in tag_entries:
                available_tokens.update(tokenize_query(tag_normalized))
            if query_token_set.issubset(available_tokens):
                item["tag_match_score"] = 200.0
                item["matched_tags"] = [
                    tag_name
                    for tag_name, tag_normalized in tag_entries
                    if query_token_set.intersection(tokenize_query(tag_normalized))
                ]
        if item.get("tag_match_score") is not None:
            matches.append(item)

    matches.sort(
        key=lambda item: (
            -float(item["tag_match_score"]),
            -(item.get("update_time") or 0.0),
            str(item["id"]),
        )
    )
    total = len(matches)
    return matches[offset : offset + limit], total


def _merge_text_and_tag_matches(
    text_items: list[dict[str, Any]],
    tag_items: list[dict[str, Any]],
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    merged_by_id: dict[str, dict[str, Any]] = {}
    for text_order, source_item in enumerate(text_items):
        item = dict(source_item)
        item["_text_order"] = text_order
        merged_by_id[str(item["id"])] = item

    for source_item in tag_items:
        conversation_id = str(source_item["id"])
        existing = merged_by_id.get(conversation_id)
        if existing is None:
            item = dict(source_item)
            item["_text_order"] = math.inf
            merged_by_id[conversation_id] = item
            continue
        existing["tag_match_score"] = source_item["tag_match_score"]
        existing["matched_tags"] = list(source_item.get("matched_tags") or [])

    matches = list(merged_by_id.values())
    matches.sort(
        key=lambda item: (
            0 if item.get("tag_match_score") is not None else 1,
            -float(item.get("tag_match_score") or 0.0),
            float(item.get("relevance", math.inf)),
            float(item.get("_text_order", math.inf)),
            -(item.get("update_time") or 0.0),
            str(item["id"]),
        )
    )
    for item in matches:
        item.pop("_text_order", None)
    total = len(matches)
    return matches[offset : offset + limit], total


def fulltext_search_conversations(
    db: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    archive_count = int(db.execute("SELECT COUNT(*) FROM conversations").fetchone()[0])
    all_tag_items, tag_total = tag_search_conversations(
        db,
        query,
        max(archive_count, 1),
        0,
    )
    fts_query = build_fts_query(query)

    if tag_total == 0:
        if not fts_query:
            return fuzzy_search_conversations(db, query, limit, offset)
        try:
            return _run_fts_search(db, fts_query, limit, offset)
        except sqlite3.OperationalError:
            return fuzzy_search_conversations(db, query, limit, offset)

    if not fts_query:
        all_text_items, _text_total = fuzzy_search_conversations(
            db,
            query,
            max(archive_count, 1),
            0,
        )
    else:
        try:
            all_text_items, _text_total = _run_fts_search(
                db,
                fts_query,
                max(archive_count, 1),
                0,
            )
        except sqlite3.OperationalError:
            all_text_items, _text_total = fuzzy_search_conversations(
                db,
                query,
                max(archive_count, 1),
                0,
            )

    return _merge_text_and_tag_matches(
        all_text_items,
        all_tag_items,
        limit,
        offset,
    )


def _highlight_title(title: str, query: str) -> str:
    query_text = query.strip()
    if not title or not query_text:
        return title

    if query_text.lower() in title.lower():
        patterns = [query_text]
    else:
        title_lower = title.lower()
        patterns = [token for token in tokenize_query(query_text) if token in title_lower]

    if not patterns:
        # A fuzzy-only result has no literal letters to highlight. Marking the
        # whole title makes it clear why the card was returned.
        return f"<mark>{title}</mark>"

    expression = re.compile("|".join(re.escape(item) for item in sorted(set(patterns), key=len, reverse=True)), re.IGNORECASE)
    return expression.sub(lambda match: f"<mark>{match.group(0)}</mark>", title)


def _title_match_score(query: str, title: str) -> float | None:
    query_lower = query.strip().lower()
    title_lower = title.strip().lower()
    if not query_lower or not title_lower:
        return None

    if query_lower == title_lower:
        return 130.0
    if title_lower.startswith(query_lower):
        return 120.0
    if query_lower in title_lower:
        return 115.0

    query_tokens = tokenize_query(query_lower)
    title_tokens = set(tokenize_query(title_lower))
    if query_tokens and all(token in title_tokens for token in query_tokens):
        return 110.0

    if fuzz is not None:
        similarity = float(fuzz.ratio(query_lower, title_lower))
    else:
        similarity = difflib.SequenceMatcher(a=query_lower, b=title_lower).ratio() * 100.0

    minimum_similarity = 82.0 if len(query_lower) <= 5 else 72.0
    return similarity if similarity >= minimum_similarity else None


def title_search_conversations(
    db: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    rows = db.execute(
        """
        SELECT
            id,
            title,
            update_time,
            create_time,
            default_model_slug,
            message_count,
            user_message_count,
            assistant_message_count,
            tool_message_count,
            system_message_count,
            last_seen_at
        FROM conversations
        WHERE title IS NOT NULL AND title != ''
        """
    ).fetchall()

    matches: list[dict[str, Any]] = []
    for row in rows:
        item = dict(row)
        title = str(item.get("title") or "")
        score = _title_match_score(query, title)
        if score is None:
            continue
        item["score"] = score
        item["title_highlight"] = _highlight_title(title, query)
        matches.append(item)

    matches.sort(
        key=lambda item: (
            -float(item["score"]),
            -(item["update_time"] or 0.0),
            str(item["id"]),
        )
    )
    total = len(matches)
    return matches[offset : offset + limit], total


def _activity_date_bounds(query: str) -> tuple[float, float]:
    try:
        selected_date = date.fromisoformat(query)
    except ValueError as error:
        raise ValueError("Activity date must use YYYY-MM-DD format.") from error

    start_local = datetime.combine(selected_date, time.min).astimezone()
    end_local = datetime.combine(selected_date + timedelta(days=1), time.min).astimezone()
    return start_local.timestamp(), end_local.timestamp()


def date_search_conversations(
    db: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int,
    activity_start: float | None = None,
    activity_end: float | None = None,
) -> tuple[list[dict[str, Any]], int]:
    # Validate the displayed date even when the browser supplies exact local
    # midnight boundaries for us.
    _activity_date_bounds(query)
    if (activity_start is None) != (activity_end is None):
        raise ValueError("Activity date needs both a start and end time.")
    if activity_start is None or activity_end is None:
        activity_start, activity_end = _activity_date_bounds(query)
    return activity_range_search_conversations(db, activity_start, activity_end, limit, offset)


def activity_range_search_conversations(
    db: sqlite3.Connection,
    activity_start: float,
    activity_end: float,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    if not math.isfinite(activity_start) or not math.isfinite(activity_end):
        raise ValueError("Activity filter times must be finite numbers.")
    if activity_end <= activity_start:
        raise ValueError("Activity filter end must be after its start.")

    rows = db.execute(
        """
        WITH message_activity AS (
            SELECT
                conversation_id,
                COUNT(*) AS activity_message_count,
                MAX(
                    CASE
                        WHEN update_time >= ? AND update_time < ? THEN update_time
                        ELSE create_time
                    END
                ) AS latest_message_activity
            FROM messages
            WHERE (create_time >= ? AND create_time < ?)
                OR (update_time >= ? AND update_time < ?)
            GROUP BY conversation_id
        )
        SELECT
            c.id,
            c.title,
            c.update_time,
            c.create_time,
            c.default_model_slug,
            c.message_count,
            c.user_message_count,
            c.assistant_message_count,
            c.tool_message_count,
            c.system_message_count,
            c.last_seen_at,
            COALESCE(message_activity.activity_message_count, 0) AS activity_message_count,
            MAX(
                COALESCE(message_activity.latest_message_activity, -1),
                CASE
                    WHEN c.create_time >= ? AND c.create_time < ? THEN c.create_time
                    ELSE -1
                END,
                CASE
                    WHEN c.update_time >= ? AND c.update_time < ? THEN c.update_time
                    ELSE -1
                END
            ) AS activity_time
        FROM conversations c
        LEFT JOIN message_activity ON message_activity.conversation_id = c.id
        WHERE message_activity.conversation_id IS NOT NULL
            OR (c.create_time >= ? AND c.create_time < ?)
            OR (c.update_time >= ? AND c.update_time < ?)
        ORDER BY activity_time DESC, (c.update_time IS NULL), c.update_time DESC, c.id
        """,
        (
            activity_start,
            activity_end,
            activity_start,
            activity_end,
            activity_start,
            activity_end,
            activity_start,
            activity_end,
            activity_start,
            activity_end,
            activity_start,
            activity_end,
            activity_start,
            activity_end,
        ),
    ).fetchall()

    items = [dict(row) for row in rows]
    total = len(items)
    return items[offset : offset + limit], total


def _filter_items_by_activity(
    items: list[dict[str, Any]],
    activity_items: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    activity_by_id = {item["id"]: item for item in activity_items}
    filtered: list[dict[str, Any]] = []
    for item in items:
        activity_item = activity_by_id.get(item["id"])
        if activity_item is None:
            continue
        payload = dict(item)
        payload["activity_time"] = activity_item["activity_time"]
        payload["activity_message_count"] = activity_item["activity_message_count"]
        filtered.append(payload)
    return filtered


def _database_fingerprint(db: sqlite3.Connection) -> tuple[str, tuple[tuple[str, int, int], ...]]:
    database_rows = db.execute("PRAGMA database_list").fetchall()
    database_path = next((str(row[2]) for row in database_rows if row[1] == "main"), "")
    fingerprints: list[tuple[str, int, int]] = []
    for candidate in (Path(database_path), Path(f"{database_path}-wal")):
        try:
            stat = candidate.stat()
        except (FileNotFoundError, OSError):
            continue
        fingerprints.append((str(candidate), stat.st_size, stat.st_mtime_ns))
    return database_path or f"connection:{id(db)}", tuple(fingerprints)


def _search_vocabulary(db: sqlite3.Connection) -> list[tuple[str, int]]:
    cache_key, fingerprint = _database_fingerprint(db)
    with _vocabulary_cache_lock:
        cached = _vocabulary_cache.get(cache_key)
        if cached is not None and cached[0] == fingerprint:
            return cached[1]

        db.execute("DROP TABLE IF EXISTS temp.message_search_vocab")
        db.execute(
            "CREATE VIRTUAL TABLE temp.message_search_vocab "
            "USING fts5vocab('main', 'message_fts', 'row')"
        )
        vocabulary = [
            (str(row[0]), int(row[1]))
            for row in db.execute("SELECT term, doc FROM temp.message_search_vocab")
        ]
        _vocabulary_cache[cache_key] = (fingerprint, vocabulary)
        return vocabulary


def _fuzzy_terms(token: str, vocabulary: list[tuple[str, int]], limit: int = 4) -> list[str]:
    if not vocabulary:
        return [token]

    terms = [item[0] for item in vocabulary]
    document_counts = {term: count for term, count in vocabulary}
    exact_exists = token in document_counts
    if exact_exists and len(token) <= 4:
        return [token]

    max_edits = 1 if len(token) <= 5 else 2 if len(token) <= 9 else 3
    candidates: list[tuple[int, int, str]] = []

    if process is not None and fuzz is not None and Levenshtein is not None:
        score_cutoff = max(55.0, 100.0 * (1.0 - max_edits / max(len(token), 1)))
        matches = process.extract(
            token,
            terms,
            scorer=fuzz.ratio,
            score_cutoff=score_cutoff,
            limit=32,
        )
        for term, _score, _index in matches:
            edit_distance = int(Levenshtein.distance(token, term, score_cutoff=max_edits))
            if edit_distance <= max_edits:
                candidates.append((edit_distance, -document_counts[term], term))
    else:
        cutoff = max(0.55, 1.0 - max_edits / max(len(token), 1))
        for term in difflib.get_close_matches(token, terms, n=32, cutoff=cutoff):
            estimated_edits = round((1.0 - difflib.SequenceMatcher(a=token, b=term).ratio()) * max(len(token), len(term)))
            if estimated_edits <= max_edits:
                candidates.append((estimated_edits, -document_counts[term], term))

    if exact_exists:
        candidates.append((0, -document_counts[token], token))
    if candidates:
        highest_document_count = max(-candidate[1] for candidate in candidates)
        popularity_floor = max(1, int(highest_document_count * 0.10))
        candidates = [
            candidate
            for candidate in candidates
            if candidate[0] == 0 or -candidate[1] >= popularity_floor
        ]
    candidates.sort()

    selected: list[str] = []
    for _distance, _negative_count, term in candidates:
        if term not in selected:
            selected.append(term)
        if len(selected) >= limit:
            break
    return selected or [token]


def build_fuzzy_fts_query(db: sqlite3.Connection, query: str) -> str | None:
    tokens = tokenize_query(query)[:16]
    if not tokens:
        return None
    vocabulary = _search_vocabulary(db)
    groups: list[str] = []
    for token in tokens:
        terms = _fuzzy_terms(token, vocabulary)
        quoted_terms = [f'"{term}"' for term in terms]
        groups.append(quoted_terms[0] if len(quoted_terms) == 1 else f"({' OR '.join(quoted_terms)})")
    return f"{{conversation_title text_content}} : ({' AND '.join(groups)})"


def fuzzy_search_conversations(
    db: sqlite3.Connection,
    query: str,
    limit: int,
    offset: int,
) -> tuple[list[dict[str, Any]], int]:
    fts_query = build_fuzzy_fts_query(db, query)
    if not fts_query:
        return [], 0
    try:
        return _run_fts_search(db, fts_query, limit, offset)
    except sqlite3.OperationalError:
        return [], 0
