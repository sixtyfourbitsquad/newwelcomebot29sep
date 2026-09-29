"""Validate outbound message payloads coming from the web admin panel."""

from __future__ import annotations

from typing import Any


def clean_keyboard(raw: Any) -> list[list[dict[str, str]]] | None:
    if raw in (None, "", []):
        return None
    if not isinstance(raw, list):
        raise ValueError("keyboard must be a list of rows")
    rows: list[list[dict[str, str]]] = []
    for row in raw:
        if not isinstance(row, list):
            raise ValueError("each keyboard row must be a list")
        out_row: list[dict[str, str]] = []
        for button in row:
            if not isinstance(button, dict):
                raise ValueError("button must be an object")
            text = str(button.get("text") or "").strip()
            url = str(button.get("url") or "").strip()
            if not text or not url:
                raise ValueError("each button needs text and url")
            if not (url.startswith("https://") or url.startswith("http://") or url.startswith("tg://")):
                raise ValueError("button url must start with http://, https://, or tg://")
            out_row.append({"text": text[:64], "url": url[:500]})
        if out_row:
            rows.append(out_row)
    return rows or None


def message_payload(
    *,
    kind: str,
    text: str = "",
    caption: str = "",
    file_id: str = "",
    keyboard: Any = None,
) -> dict[str, Any]:
    allowed = {"text", "photo", "video", "voice", "document", "sticker"}
    if kind not in allowed:
        raise ValueError("unsupported message kind")
    buttons = clean_keyboard(keyboard)
    payload: dict[str, Any]
    if kind == "text":
        body = text.strip()
        if not body:
            raise ValueError("text is required")
        payload = {"kind": "text", "text": body[:4000]}
    else:
        media = file_id.strip()
        if not media:
            raise ValueError("file_id is required for media")
        payload = {"kind": kind, "file_id": media[:300]}
        if caption.strip():
            payload["caption"] = caption.strip()[:1000]
    if buttons:
        payload["inline_keyboard"] = buttons
    return payload
