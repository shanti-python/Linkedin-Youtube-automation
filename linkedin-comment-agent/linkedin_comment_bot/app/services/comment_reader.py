"""LinkedIn comment data models and extraction logic."""

from dataclasses import dataclass, field
from typing import List, Optional, Set


@dataclass
class CommentData:
    comment_id: str
    author: str
    author_profile_url: str
    text: str
    time: str
    reaction_count: int
    already_replied: bool
    reply_count: int
    nested_level: int
    is_deleted: bool = False
    is_hidden: bool = False
    element_index: int = 0
    skip_reason: Optional[str] = None


@dataclass
class CommentExtractionResult:
    comments: List[CommentData] = field(default_factory=list)
    post_content: str = ""
    account_name: str = ""


class CommentFilter:
    """Applies skip filters to extracted comments."""

    def __init__(
        self,
        *,
        ignore_offensive: bool = True,
        ignore_bots: bool = True,
        reply_only_questions: bool = False,
        max_replies_per_user: int = 3,
        processed_ids: Optional[Set[str]] = None,
        seen_hashes: Optional[Set[str]] = None,
        account_name: Optional[str] = None,
        max_days: Optional[float] = None,
    ) -> None:
        self.ignore_offensive = ignore_offensive
        self.ignore_bots = ignore_bots
        self.reply_only_questions = reply_only_questions
        self.max_replies_per_user = max_replies_per_user
        self.processed_ids = processed_ids or set()
        self.seen_hashes = seen_hashes or set()
        self.account_name = account_name
        self.max_days = max_days
        self._user_reply_counts: dict[str, int] = {}

    def should_skip(self, comment: CommentData) -> Optional[str]:
        from linkedin_comment_bot.app.utils.helpers import (
            comment_hash,
            is_likely_bot,
            is_offensive,
            is_question,
            is_spam,
            normalize_text,
            parse_time_to_days,
        )

        if comment.is_deleted:
            return "deleted comment"
        if comment.is_hidden:
            return "hidden comment"
        if not normalize_text(comment.text):
            return "empty comment"
        import re
        text_no_hashtag = re.sub(r"\bhashtag\b", "", comment.text, flags=re.IGNORECASE)
        cleaned_cfbr = re.sub(r"[^\w#]", "", text_no_hashtag).strip().lower()
        if cleaned_cfbr in ("cfbr", "#cfbr"):
            return "CFBR comment"
        if self.account_name and comment.author.lower() == self.account_name.lower():
            return "own comment"
        if comment.already_replied:
            return "already replied on LinkedIn"
        if comment.comment_id in self.processed_ids:
            return "already processed (resume store)"
        if self.max_days is not None:
            comment_age = parse_time_to_days(comment.time)
            if comment_age > self.max_days:
                return f"comment age ({comment_age:.2f} days) exceeds max_days ({self.max_days} days)"
        h = comment_hash(comment.author, comment.text)
        if h in self.seen_hashes:
            return "duplicate comment"
        if is_spam(comment.text):
            return "spam detected"
        if self.ignore_offensive and is_offensive(comment.text):
            return "offensive content"
        if self.ignore_bots and is_likely_bot(comment.author):
            return "likely bot account"
        if self.reply_only_questions and not is_question(comment.text):
            return "not a question (filter enabled)"
        user_count = self._user_reply_counts.get(comment.author, 0)
        if user_count >= self.max_replies_per_user:
            return f"max replies per user ({self.max_replies_per_user}) reached"

        self.seen_hashes.add(h)
        return None

    def record_reply(self, author: str) -> None:
        self._user_reply_counts[author] = self._user_reply_counts.get(author, 0) + 1
