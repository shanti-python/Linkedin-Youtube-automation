import json
import re
from typing import Any, Dict, Optional


HELD_STATUSES = {"heldforreview", "likelyspam", "rejected", "awaitingmoderation"}


def is_create_comment_url(url: str, method: str) -> bool:
    """Match only YouTube innertube create_comment / create_comment_reply endpoints."""
    if method.upper() != "POST":
        return False
    lower = (url or "").lower()
    return "create_comment" in lower


def _walk(node: Any, visitor) -> None:
    if isinstance(node, dict):
        visitor(node)
        for value in node.values():
            _walk(value, visitor)
    elif isinstance(node, list):
        for item in node:
            _walk(item, visitor)


def parse_create_comment_response(payload: Any) -> Dict[str, Optional[str]]:
    """Extract commentId / moderationStatus / error from create_comment JSON."""
    result: Dict[str, Optional[str]] = {
        "comment_id": None,
        "moderation_status": None,
        "error": None,
    }

    def visit(obj: dict) -> None:
        for key in ("commentId", "comment_id"):
            value = obj.get(key)
            if isinstance(value, str) and value.startswith("Ug"):
                # Prefer the newest/deepest reply id (contains a dot for replies)
                current = result["comment_id"]
                if current is None or ("." in value and "." not in current) or len(value) > len(current or ""):
                    result["comment_id"] = value

        for key in ("moderationStatus", "moderation_status"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                result["moderation_status"] = value.strip()

        for key in ("error", "errorMessage"):
            value = obj.get(key)
            if isinstance(value, str) and value.strip():
                result["error"] = value.strip()
            elif isinstance(value, dict):
                msg = value.get("simpleText") or value.get("message") or value.get("text")
                if isinstance(msg, str) and msg.strip():
                    result["error"] = msg.strip()

    _walk(payload, visit)
    return result


def is_public_moderation_status(status: Optional[str]) -> bool:
    if not status:
        return True
    return status.strip().lower() not in HELD_STATUSES


def dump_response_keys(payload: Any, limit: int = 4000) -> str:
    """Compact JSON dump for logging (truncated)."""
    try:
        text = json.dumps(payload, ensure_ascii=False)
    except Exception:
        text = str(payload)
    return text[:limit]
