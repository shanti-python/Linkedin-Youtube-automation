"""Structured logging and CSV export utilities."""

import csv
import logging
import sys
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any, Dict, List, Optional

from linkedin_comment_bot.app.config import get_settings

CSV_COLUMNS = [
    "Post URL",
    "Comment ID",
    "Author",
    "Comment",
    "Generated Reply",
    "Status",
    "Reason",
    "Timestamp",
    "Processing Time",
]

_lock = Lock()
_logger: Optional[logging.Logger] = None


def setup_logger(name: str = "linkedin_comment_bot") -> logging.Logger:
    global _logger
    if _logger is not None:
        return _logger

    settings = get_settings()
    settings.csv_log_path.parent.mkdir(parents=True, exist_ok=True)

    logger = logging.getLogger(name)
    logger.setLevel(logging.INFO)
    logger.handlers.clear()

    formatter = logging.Formatter(
        "%(asctime)s | %(levelname)-8s | %(name)s | %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    console = logging.StreamHandler(sys.stdout)
    console.setFormatter(formatter)
    logger.addHandler(console)

    file_handler = logging.FileHandler(
        settings.csv_log_path.parent / "app.log",
        encoding="utf-8",
    )
    file_handler.setFormatter(formatter)
    logger.addHandler(file_handler)

    _logger = logger
    return logger


def get_logger(name: str = "linkedin_comment_bot") -> logging.Logger:
    return setup_logger(name)


def _ensure_csv_header(csv_path: Path) -> None:
    csv_path.parent.mkdir(parents=True, exist_ok=True)
    if not csv_path.exists():
        with csv_path.open("w", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writeheader()


def save_log(
    post_url: str,
    comment_id: str,
    author: str,
    comment: str,
    generated_reply: str,
    status: str,
    reason: str,
    processing_time_sec: float,
    csv_path: Optional[Path] = None,
) -> None:
    settings = get_settings()
    path = csv_path or settings.csv_log_path

    row = {
        "Post URL": post_url,
        "Comment ID": comment_id,
        "Author": author,
        "Comment": comment,
        "Generated Reply": generated_reply,
        "Status": status,
        "Reason": reason,
        "Timestamp": datetime.now(timezone.utc).isoformat(),
        "Processing Time": f"{processing_time_sec:.2f}s",
    }

    with _lock:
        _ensure_csv_header(path)
        with path.open("a", newline="", encoding="utf-8") as f:
            writer = csv.DictWriter(f, fieldnames=CSV_COLUMNS)
            writer.writerow(row)

    get_logger().info(
        "Logged comment %s | status=%s | author=%s | reason=%s",
        comment_id,
        status,
        author,
        reason,
    )


def export_json(records: List[Dict[str, Any]], output_path: Path) -> Path:
    import json

    output_path.parent.mkdir(parents=True, exist_ok=True)
    with output_path.open("w", encoding="utf-8") as f:
        json.dump(records, f, indent=2, ensure_ascii=False)
    return output_path
