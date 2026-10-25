from __future__ import annotations

import json
import re
import sqlite3
from dataclasses import dataclass


@dataclass(frozen=True)
class ChatTurn:
    speaker: str
    text: str


@dataclass(frozen=True)
class ChatMarkdown:
    title: str
    turns: list[ChatTurn]


def safe_filename(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "-", value).strip("-")
    return cleaned or "conversation"


def build_chat_only_markdown(
    db: sqlite3.Connection,
    conversation_id: str,
    path_mode: str = "current",
    *,
    speaker_labels: dict[str, str] | None = None,
) -> str | None:
    chat = build_chat_markdown_model(db, conversation_id, path_mode, speaker_labels=speaker_labels)
    if chat is None:
        return None
    return render_chat_markdown(chat)


def build_chat_markdown_model(
    db: sqlite3.Connection,
    conversation_id: str,
    path_mode: str = "current",
    *,
    speaker_labels: dict[str, str] | None = None,
) -> ChatMarkdown | None:
    conversation = db.execute(
        "SELECT title FROM conversations WHERE id = ?",
        (conversation_id,),
    ).fetchone()
    if conversation is None:
        return None

    message_filter = "AND is_current_path = 1" if path_mode == "current" else ""
    rows = db.execute(
        f"""
        SELECT role, text_content, raw_json
        FROM messages
        WHERE conversation_id = ?
            AND role IN ('user', 'assistant')
            AND text_content IS NOT NULL
            AND TRIM(text_content) != ''
            {message_filter}
        ORDER BY (create_time IS NOT NULL), create_time, sort_index
        """,
        (conversation_id,),
    ).fetchall()

    title = str(conversation["title"] or "Untitled conversation").strip()
    turns: list[ChatTurn] = []
    for row in rows:
        if not is_exportable_chat_turn(row["raw_json"]):
            continue
        labels = speaker_labels or {}
        speaker = labels.get(row["role"]) or ("User" if row["role"] == "user" else "Assistant")
        text = str(row["text_content"] or "").strip()
        if not text:
            continue
        turns.append(ChatTurn(speaker=speaker, text=text))

    return ChatMarkdown(title=title, turns=turns)


def render_chat_markdown(chat: ChatMarkdown) -> str:
    parts = [f"# {chat.title}", ""]
    for turn in chat.turns:
        parts.append(f"## {turn.speaker}")
        parts.append("")
        parts.append(turn.text)
        parts.append("")

    return "\n".join(parts).rstrip() + "\n"


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def render_split_chat_markdown(
    chat: ChatMarkdown,
    target_tokens: int = 30_000,
) -> list[str]:
    target_tokens = max(1_000, target_tokens)
    chunks: list[list[ChatTurn]] = []
    current_chunk: list[ChatTurn] = []
    current_tokens = 0

    for turn in chat.turns:
        turn_tokens = estimate_tokens(f"## {turn.speaker}\n\n{turn.text}\n")
        should_start_next = (
            current_chunk
            and current_tokens + turn_tokens > target_tokens
        )
        if should_start_next:
            chunks.append(current_chunk)
            current_chunk = []
            current_tokens = 0
        current_chunk.append(turn)
        current_tokens += turn_tokens

    if current_chunk or not chunks:
        chunks.append(current_chunk)

    rendered: list[str] = []
    total = len(chunks)
    for index, chunk_turns in enumerate(chunks, start=1):
        parts = [
            f"# {chat.title}",
            "",
            f"Part {index} of {total}",
            f"Approx token target: {target_tokens}",
            "",
        ]
        for turn in chunk_turns:
            parts.append(f"## {turn.speaker}")
            parts.append("")
            parts.append(turn.text)
            parts.append("")
        rendered.append("\n".join(parts).rstrip() + "\n")
    return rendered


def is_exportable_chat_turn(raw_json: str) -> bool:
    try:
        message = json.loads(raw_json)
    except json.JSONDecodeError:
        return True

    if not isinstance(message, dict):
        return True

    content = message.get("content")
    if isinstance(content, dict) and content.get("content_type") == "user_editable_context":
        return False

    metadata = message.get("metadata")
    if not isinstance(metadata, dict):
        return True

    hidden_markers: tuple[str, ...] = (
        "is_user_system_message",
        "is_visually_hidden_from_conversation",
    )
    if any(bool(metadata.get(marker)) for marker in hidden_markers):
        return False
    return not isinstance(metadata.get("user_context_message_data"), dict)
