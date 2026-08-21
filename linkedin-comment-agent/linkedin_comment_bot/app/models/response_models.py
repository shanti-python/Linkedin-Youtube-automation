"""Pydantic response models for LinkedIn comment reply API."""

from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field


class CommentReplyResponse(BaseModel):
    status: str = Field(default="success")
    total_comments: int = 0
    replied: int = 0
    skipped: int = 0
    errors: int = 0
    message: Optional[str] = None


class GroqTestResponse(BaseModel):
    status: str = "success"
    comment: str
    author_name: str
    generated_reply: str
    sentiment: Optional[str] = None
    is_question: bool = False
    detected_language: Optional[str] = None


class StatusResponse(BaseModel):
    status: str = "idle"
    is_processing: bool = False
    current_post_url: Optional[str] = None
    processed_today: int = 0
    max_replies_per_day: int = 0
    last_run: Optional[str] = None
    config_summary: Dict[str, Any] = Field(default_factory=dict)


class FinalReport(BaseModel):
    posts_processed: int = 0
    comments_found: int = 0
    replied: int = 0
    skipped: int = 0
    errors: int = 0
    duration: str = "0s"
    post_results: List[CommentReplyResponse] = Field(default_factory=list)
