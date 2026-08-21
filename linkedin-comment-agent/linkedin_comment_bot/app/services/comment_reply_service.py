"""Orchestrates the full comment reply workflow."""

import asyncio
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

from linkedin_comment_bot.app.config import EXPORTS_DIR, Settings, get_settings
from linkedin_comment_bot.app.models.request_models import ReplyMode
from linkedin_comment_bot.app.models.response_models import CommentReplyResponse, FinalReport
from linkedin_comment_bot.app.services.comment_reader import CommentData, CommentFilter
from linkedin_comment_bot.app.services.groq_reply_service import GeneratedReply, GroqReplyService, ReplyContext
from linkedin_comment_bot.app.services.linkedin_service import LinkedInService
from linkedin_comment_bot.app.utils.browser import BrowserManager
from linkedin_comment_bot.app.utils.helpers import DailyReplyCounter, ProcessedCommentsStore, format_duration
from linkedin_comment_bot.app.utils.human_typing import reply_rate_limit_delay
from linkedin_comment_bot.app.utils.logger import export_json, get_logger, save_log

logger = get_logger()


@dataclass
class ProcessingState:
    is_processing: bool = False
    current_post_url: Optional[str] = None
    last_run: Optional[str] = None


_state = ProcessingState()


def get_processing_state() -> ProcessingState:
    return _state


class GoogleSheetsLogger:
    """Optional Google Sheets logging backend."""

    def __init__(self, settings: Settings) -> None:
        self.settings = settings
        self._worksheet = None

    def _connect(self) -> None:
        if not self.settings.google_sheets_enabled:
            return
        if self._worksheet is not None:
            return
        try:
            import gspread
            from google.oauth2.service_account import Credentials

            creds_path = self.settings.google_sheets_credentials_path
            if not creds_path:
                logger.warning("Google Sheets enabled but credentials path not set")
                return

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
            client = gspread.authorize(creds)
            spreadsheet = client.open_by_key(self.settings.google_sheets_spreadsheet_id)
            try:
                self._worksheet = spreadsheet.worksheet(self.settings.google_sheets_worksheet_name)
            except Exception:
                self._worksheet = spreadsheet.add_worksheet(
                    title=self.settings.google_sheets_worksheet_name,
                    rows=1000,
                    cols=10,
                )
                self._worksheet.append_row([
                    "Post URL", "Comment ID", "Author", "Comment",
                    "Generated Reply", "Status", "Reason", "Timestamp", "Processing Time",
                ])
        except Exception as exc:
            logger.error("Failed to connect to Google Sheets: %s", exc)

    def append_row(self, row: List[str]) -> None:
        self._connect()
        if self._worksheet is None:
            return
        try:
            self._worksheet.append_row(row)
        except Exception as exc:
            logger.error("Failed to append Google Sheets row: %s", exc)


class CommentReplyService:
    """Main orchestrator for LinkedIn comment auto-reply."""

    def __init__(
        self,
        settings: Optional[Settings] = None,
        groq_service: Optional[GroqReplyService] = None,
    ) -> None:
        self.settings = settings or get_settings()
        self.groq_service = groq_service or GroqReplyService(self.settings)
        self.processed_store = ProcessedCommentsStore()
        self.daily_counter = DailyReplyCounter()
        self.sheets_logger = GoogleSheetsLogger(self.settings)
        self._session_records: List[Dict[str, Any]] = []

    async def process_post(
        self,
        post_url: str,
        mode: ReplyMode = ReplyMode.GROQ,
        post_content: Optional[str] = None,
        post_topic: Optional[str] = None,
        max_replies: Optional[int] = None,
        is_batch: bool = False,
    ) -> CommentReplyResponse:
        global _state
        if not is_batch:
            _state.is_processing = True
        _state.current_post_url = post_url

        replied = 0
        skipped = 0
        errors = 0
        total_comments = 0
        reply_limit = max_replies or self.settings.max_replies

        manager = BrowserManager(self.settings)
        start_time = time.monotonic()

        try:
            page = await manager.open_browser()
            linkedin = LinkedInService(page, self.settings)

            await linkedin.ensure_logged_in(manager)
            await linkedin.open_post(post_url)
            await linkedin.expand_comments()
            await linkedin.scroll_comments()
            extraction = await linkedin.extract_comments()

            effective_post_content = post_content or extraction.post_content
            comments = extraction.comments
            total_comments = len(comments)

            if total_comments == 0:
                logger.info("No comments found on post: %s", post_url)
                return CommentReplyResponse(
                    status="success",
                    total_comments=0,
                    replied=0,
                    skipped=0,
                    errors=0,
                    message="No comments found on post",
                )

            comment_filter = CommentFilter(
                ignore_offensive=self.settings.ignore_offensive,
                ignore_bots=self.settings.ignore_bots,
                reply_only_questions=self.settings.reply_only_questions,
                max_replies_per_user=self.settings.max_replies_per_user,
                processed_ids=self.processed_store.get_all_ids(),
                account_name=linkedin._account_name,
                max_days=self.settings.max_days,
            )

            for comment in comments:
                if not _state.is_processing:
                    logger.info("Cancellation requested: Aborting comment processing loop.")
                    break

                if replied >= reply_limit:
                    skipped += 1
                    self._log_entry(
                        post_url, comment, "", "skipped", "max replies limit reached", 0
                    )
                    continue

                if self.daily_counter.count >= self.settings.max_replies_per_day:
                    skipped += 1
                    self._log_entry(
                        post_url, comment, "", "skipped", "daily reply limit reached", 0
                    )
                    continue

                skip_reason = comment_filter.should_skip(comment)
                if skip_reason:
                    skipped += 1
                    self._log_entry(post_url, comment, "", "skipped", skip_reason, 0)
                    self.processed_store.mark(
                        comment.comment_id, author=comment.author, status="skipped"
                    )
                    continue

                item_start = time.monotonic()
                generated_reply = ""

                try:
                    if mode == ReplyMode.TEST:
                        generated_reply = (
                            f"Thank you for your comment, {comment.author}. "
                            "I appreciate you taking the time to engage with this post."
                        )
                    else:
                        generated_reply = await asyncio.to_thread(
                            self._generate_groq_reply,
                            comment,
                            effective_post_content,
                            post_topic,
                        )

                    import re
                    cleaned_reply = re.sub(r"[^\w]", "", generated_reply).strip().upper()
                    if cleaned_reply == "SKIPCOMMENT":
                        skipped += 1
                        self._log_entry(post_url, comment, "", "skipped", "CFBR comment (LLM skipped)", 0)
                        self.processed_store.mark(
                            comment.comment_id, author=comment.author, status="skipped"
                        )
                        continue

                    success = await self._post_reply(linkedin, comment, generated_reply, manager)

                    elapsed = time.monotonic() - item_start
                    if success:
                        replied += 1
                        self.daily_counter.increment()
                        comment_filter.record_reply(comment.author)
                        self.processed_store.mark(
                            comment.comment_id,
                            author=comment.author,
                            status="replied",
                            reply_id=f"reply_{comment.comment_id}",
                        )
                        self._log_entry(
                            post_url, comment, generated_reply, "replied", "success", elapsed
                        )
                        await reply_rate_limit_delay(self.settings, replied)
                    else:
                        errors += 1
                        self._log_entry(
                            post_url, comment, generated_reply, "error",
                            "failed to submit reply", elapsed,
                        )
                        await manager.save_failure_html(f"reply_fail_{comment.comment_id}")

                except Exception as exc:
                    elapsed = time.monotonic() - item_start
                    errors += 1
                    logger.error(
                        "Error processing comment %s: %s", comment.comment_id, exc, exc_info=True
                    )
                    self._log_entry(
                        post_url, comment, generated_reply, "error", str(exc), elapsed
                    )
                    await manager.save_failure_html(f"error_{comment.comment_id}")
                    continue

            export_json(self._session_records, EXPORTS_DIR / "session_report.json")

            return CommentReplyResponse(
                status="success",
                total_comments=total_comments,
                replied=replied,
                skipped=skipped,
                errors=errors,
            )

        except Exception as exc:
            logger.error("Fatal error processing post: %s", exc, exc_info=True)
            await manager.save_failure_html("fatal_error")
            return CommentReplyResponse(
                status="error",
                total_comments=total_comments,
                replied=replied,
                skipped=skipped,
                errors=errors + 1,
                message=str(exc),
            )
        finally:
            await manager.close_browser()
            if not is_batch:
                from linkedin_comment_bot.app.utils.helpers import utc_now_iso
                _state.is_processing = False
                _state.current_post_url = None
                _state.last_run = utc_now_iso()

    async def process_multiple_posts(
        self,
        post_urls: List[str],
        mode: ReplyMode = ReplyMode.GROQ,
        post_content: Optional[str] = None,
        post_topic: Optional[str] = None,
        max_replies: Optional[int] = None,
    ) -> FinalReport:
        global _state
        _state.is_processing = True
        start = time.monotonic()
        results: List[CommentReplyResponse] = []
        totals = {"comments_found": 0, "replied": 0, "skipped": 0, "errors": 0}

        try:
            for url in post_urls:
                if not _state.is_processing:
                    logger.info("Cancellation requested: Aborting batch post iteration.")
                    break
                logger.info("Processing post %d/%d: %s", post_urls.index(url) + 1, len(post_urls), url)
                result = await self.process_post(
                    post_url=url,
                    mode=mode,
                    post_content=post_content,
                    post_topic=post_topic,
                    max_replies=max_replies,
                    is_batch=True,
                )
                results.append(result)
                totals["comments_found"] += result.total_comments
                totals["replied"] += result.replied
                totals["skipped"] += result.skipped
                totals["errors"] += result.errors
        finally:
            from linkedin_comment_bot.app.utils.helpers import utc_now_iso
            _state.is_processing = False
            _state.current_post_url = None
            _state.last_run = utc_now_iso()

        duration = format_duration(time.monotonic() - start)
        return FinalReport(
            posts_processed=len(post_urls),
            comments_found=totals["comments_found"],
            replied=totals["replied"],
            skipped=totals["skipped"],
            errors=totals["errors"],
            duration=duration,
            post_results=results,
        )

    async def process_account(
        self,
        mode: ReplyMode = ReplyMode.GROQ,
        max_posts: int = 15,
        max_replies: Optional[int] = None,
        max_days: Optional[int] = None,
    ) -> FinalReport:
        """Automatically scan all posts on the user's account and reply to received comments."""
        global _state
        _state.is_processing = True
        start = time.monotonic()
        manager = BrowserManager(self.settings)

        # Fallback to self.settings.max_days if max_days is None
        effective_max_days = max_days if max_days is not None else self.settings.max_days

        post_urls = []
        try:
            page = await manager.open_browser()
            linkedin = LinkedInService(page, self.settings)

            await linkedin.ensure_logged_in(manager)
            post_urls = await linkedin.fetch_account_post_urls(max_posts=max_posts, max_days=effective_max_days)

            if not post_urls:
                logger.warning("No post URLs discovered for the account.")
                duration = format_duration(time.monotonic() - start)
                return FinalReport(
                    posts_processed=0,
                    comments_found=0,
                    replied=0,
                    skipped=0,
                    errors=0,
                    duration=duration,
                    post_results=[],
                )

            logger.info("Found %d posts on account. Starting comment processing...", len(post_urls))
        finally:
            await manager.close_browser()
            if not post_urls:
                from linkedin_comment_bot.app.utils.helpers import utc_now_iso
                _state.is_processing = False
                _state.current_post_url = None
                _state.last_run = utc_now_iso()

        return await self.process_multiple_posts(
            post_urls=post_urls,
            mode=mode,
            max_replies=max_replies,
        )

    def _generate_groq_reply(
        self,
        comment: CommentData,
        post_content: Optional[str],
        post_topic: Optional[str],
    ) -> str:
        ctx = ReplyContext(
            comment=comment.text,
            author_name=comment.author,
            post_content=post_content,
            post_topic=post_topic,
            industry=self.settings.industry,
        )
        result: GeneratedReply = self.groq_service.generate_reply(ctx)
        return result.text

    async def _post_reply(
        self,
        linkedin: LinkedInService,
        comment: CommentData,
        reply_text: str,
        manager: BrowserManager,
    ) -> bool:
        clicked = await linkedin.click_reply(comment.element_index, author=comment.author, text=comment.text)
        if not clicked:
            return False

        typed = await linkedin.type_reply(reply_text)
        if not typed:
            return False

        submitted = await linkedin.submit_reply()
        if submitted and self.settings.enable_screenshots:
            await manager.take_screenshot(f"reply_{comment.comment_id}")

        return submitted

    def _log_entry(
        self,
        post_url: str,
        comment: CommentData,
        generated_reply: str,
        status: str,
        reason: str,
        processing_time: float,
    ) -> None:
        save_log(
            post_url=post_url,
            comment_id=comment.comment_id,
            author=comment.author,
            comment=comment.text,
            generated_reply=generated_reply,
            status=status,
            reason=reason,
            processing_time_sec=processing_time,
        )
        self.sheets_logger.append_row([
            post_url,
            comment.comment_id,
            comment.author,
            comment.text,
            generated_reply,
            status,
            reason,
            time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
            f"{processing_time:.2f}s",
        ])
        self._session_records.append({
            "post_url": post_url,
            "comment_id": comment.comment_id,
            "author": comment.author,
            "comment": comment.text,
            "generated_reply": generated_reply,
            "status": status,
            "reason": reason,
            "processing_time": processing_time,
        })
