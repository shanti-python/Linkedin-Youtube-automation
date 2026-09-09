"""General helper utilities."""

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

from linkedin_comment_bot.app.config import PROCESSED_COMMENTS_PATH

SPAM_PATTERNS = [
    r"click here",
    r"free money",
    r"dm me for",
    r"whatsapp",
    r"telegram\.me",
    r"crypto giveaway",
    r"earn \$\d+",
    r"work from home \$\d+",
]

OFFENSIVE_PATTERNS = [
    r"\b(stupid|idiot|moron|hate you|kill yourself)\b",
]

BOT_NAME_PATTERNS = [
    r"bot\b",
    r"automation",
    r"lead gen",
    r"growth hack",
]


def normalize_text(text: str) -> str:
    return re.sub(r"\s+", " ", text.strip().lower())


def strip_emojis(text: str) -> str:
    """Removes emoji characters and symbols to ensure professional replies."""
    if not text:
        return ""
    clean = re.sub(r'[\U00010000-\U0010ffff\u2600-\u27bf\ufe00-\ufe0f]', '', text)
    return re.sub(r' +', ' ', clean).strip()


def comment_hash(author: str, text: str) -> str:
    payload = f"{normalize_text(author)}::{normalize_text(text)}"
    return hashlib.sha256(payload.encode()).hexdigest()[:16]


def is_spam(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered, re.IGNORECASE) for p in SPAM_PATTERNS)


def is_offensive(text: str) -> bool:
    lowered = text.lower()
    return any(re.search(p, lowered, re.IGNORECASE) for p in OFFENSIVE_PATTERNS)


def is_likely_bot(author: str) -> bool:
    lowered = author.lower()
    return any(re.search(p, lowered, re.IGNORECASE) for p in BOT_NAME_PATTERNS)


def is_question(text: str) -> bool:
    if "?" in text:
        return True
    question_starts = (
        "what", "why", "how", "when", "where", "who", "which",
        "can you", "could you", "would you", "is there", "are there",
        "do you", "does this", "any idea",
    )
    lowered = text.lower().strip()
    return any(lowered.startswith(q) for q in question_starts)


def detect_language_simple(text: str) -> str:
    """Lightweight language heuristic (not a full detector)."""
    if re.search(r"[\u0900-\u097F]", text):
        return "hi"
    if re.search(r"[\u4e00-\u9fff]", text):
        return "zh"
    if re.search(r"[\u0600-\u06FF]", text):
        return "ar"
    if re.search(r"[\u0400-\u04FF]", text):
        return "ru"
    spanish_markers = {"gracias", "hola", "buenos", "cómo", "qué"}
    if any(w in text.lower() for w in spanish_markers):
        return "es"
    french_markers = {"merci", "bonjour", "comment", "pourquoi"}
    if any(w in text.lower() for w in french_markers):
        return "fr"
    return "en"


def analyze_sentiment_simple(text: str) -> str:
    """Rule-based sentiment for logging/prioritization."""
    lowered = text.lower()
    positive = {"thank", "great", "awesome", "love", "helpful", "amazing", "excellent"}
    negative = {"bad", "wrong", "hate", "terrible", "awful", "disagree", "useless"}

    pos = sum(1 for w in positive if w in lowered)
    neg = sum(1 for w in negative if w in lowered)

    if neg > pos:
        return "negative"
    if pos > neg:
        return "positive"
    return "neutral"


def format_duration(seconds: float) -> str:
    minutes, secs = divmod(int(seconds), 60)
    hours, minutes = divmod(minutes, 60)
    if hours:
        return f"{hours}h {minutes}m {secs}s"
    if minutes:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class ProcessedCommentsStore:
    """Persistent store to avoid replying twice to the same comment."""

    def __init__(self, path: Path = PROCESSED_COMMENTS_PATH) -> None:
        self.path = path
        self._data: Dict[str, Dict[str, Any]] = {}
        self._load()

    def _load(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                self._data = json.load(f)

    def save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump(self._data, f, indent=2, ensure_ascii=False)

    def is_processed(self, comment_id: str) -> bool:
        entry = self._data.get(comment_id)
        return entry is not None and entry.get("status") == "replied"

    def mark(
        self,
        comment_id: str,
        *,
        author: str,
        status: str,
        reply_id: Optional[str] = None,
    ) -> None:
        self._data[comment_id] = {
            "comment_id": comment_id,
            "reply_id": reply_id,
            "timestamp": utc_now_iso(),
            "author": author,
            "status": status,
        }
        self.save()

    def get_all_ids(self) -> Set[str]:
        return set(self._data.keys())


class DailyReplyCounter:
    """Track replies per day for rate limiting."""

    def __init__(self, path: Optional[Path] = None) -> None:
        from linkedin_comment_bot.app.config import EXPORTS_DIR

        self.path = path or EXPORTS_DIR / "daily_reply_counter.json"
        self._count = 0
        self._date = datetime.now(timezone.utc).date().isoformat()
        self._load()

    def _load(self) -> None:
        if self.path.exists():
            with self.path.open("r", encoding="utf-8") as f:
                data = json.load(f)
            today = datetime.now(timezone.utc).date().isoformat()
            if data.get("date") == today:
                self._count = int(data.get("count", 0))
                self._date = today

    def increment(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self._date:
            self._date = today
            self._count = 0
        self._count += 1
        self._save()
        return self._count

    @property
    def count(self) -> int:
        today = datetime.now(timezone.utc).date().isoformat()
        if today != self._date:
            return 0
        return self._count

    def _save(self) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self.path.open("w", encoding="utf-8") as f:
            json.dump({"date": self._date, "count": self._count}, f)


def parse_time_to_days(time_text: str) -> float:
    """Parses LinkedIn relative time strings to number of days."""
    if not time_text:
        return 9999.0
    
    text = time_text.lower().strip()
    text = text.replace("ago", "").replace("edited", "").strip()
    
    if text in ('now', 'just now', '1s') or 'second' in text:
        return 0.0
        
    match = re.search(r'(\d+)\s*([a-zA-Z]+)', text)
    if not match:
        return 9999.0
        
    val = float(match.group(1))
    unit = match.group(2)
    
    if unit.startswith('s'):
        return val / (24.0 * 3600.0)
    elif unit.startswith('mi') or unit == 'm':
        # 'm' is minutes, 'mo' is months
        return val / (24.0 * 60.0)
    elif unit.startswith('h'):
        return val / 24.0
    elif unit.startswith('d'):
        return val
    elif unit.startswith('w'):
        return val * 7.0
    elif unit == 'mo' or unit.startswith('mon'):
        return val * 30.0
    elif unit.startswith('y'):
        return val * 365.0
        
    return 9999.0
