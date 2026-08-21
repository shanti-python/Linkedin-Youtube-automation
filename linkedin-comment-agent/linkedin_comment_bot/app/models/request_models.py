"""Pydantic request models for LinkedIn comment reply API."""

from enum import Enum
from typing import List, Optional

from pydantic import BaseModel, Field


class ReplyMode(str, Enum):
    GROQ = "groq"
    TEST = "test"


class CommentReplyRequest(BaseModel):
    post_url: str = Field(..., description="LinkedIn post URL to process")
    mode: ReplyMode = Field(default=ReplyMode.GROQ, description="Reply generation mode")
    post_content: Optional[str] = Field(default=None, description="Optional post body text")
    post_topic: Optional[str] = Field(default=None, description="Optional post topic")
    max_replies: Optional[int] = Field(default=None, description="Override max replies limit")


class MultiPostReplyRequest(BaseModel):
    posts: List[str] = Field(..., min_length=1, description="List of LinkedIn post URLs")
    mode: ReplyMode = Field(default=ReplyMode.GROQ)
    post_content: Optional[str] = None
    post_topic: Optional[str] = None
    max_replies: Optional[int] = None


class GroqReplyTestRequest(BaseModel):
    comment: str = Field(..., min_length=1)
    author_name: str = Field(default="Anonymous")
    post_content: Optional[str] = None
    post_topic: Optional[str] = None


class AccountReplyRequest(BaseModel):
    max_posts: Optional[int] = Field(default=15, description="Maximum number of account posts to scan")
    mode: ReplyMode = Field(default=ReplyMode.GROQ, description="Reply generation mode")
    max_replies: Optional[int] = Field(default=None, description="Max replies limit per post")
    max_days: Optional[int] = Field(default=None, description="Maximum age of posts to process in days")
