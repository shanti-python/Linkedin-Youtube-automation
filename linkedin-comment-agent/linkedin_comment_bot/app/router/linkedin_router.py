"""FastAPI router for LinkedIn comment reply endpoints."""

from typing import Union
from pydantic import BaseModel

from fastapi import APIRouter, HTTPException, BackgroundTasks

from linkedin_comment_bot.app.config import get_settings
from linkedin_comment_bot.app.models.request_models import (
    AccountReplyRequest,
    CommentReplyRequest,
    GroqReplyTestRequest,
    MultiPostReplyRequest,
    ReplyMode,
)
from linkedin_comment_bot.app.models.response_models import (
    CommentReplyResponse,
    FinalReport,
    GroqTestResponse,
    StatusResponse,
)
from linkedin_comment_bot.app.services.comment_reply_service import (
    CommentReplyService,
    get_processing_state,
)
from linkedin_comment_bot.app.services.groq_reply_service import GroqReplyService
from linkedin_comment_bot.app.utils.logger import get_logger

logger = get_logger()
router = APIRouter(prefix="/linkedin", tags=["LinkedIn Comment Reply"])

_reply_service: CommentReplyService | None = None
_groq_service: GroqReplyService | None = None


def get_reply_service() -> CommentReplyService:
    global _reply_service
    if _reply_service is None:
        _reply_service = CommentReplyService()
    return _reply_service


def get_groq_service() -> GroqReplyService:
    global _groq_service
    if _groq_service is None:
        _groq_service = GroqReplyService()
    return _groq_service


@router.post("/comment/reply", response_model=Union[CommentReplyResponse, FinalReport])
async def reply_to_comments(request: CommentReplyRequest) -> Union[CommentReplyResponse, FinalReport]:
    """Process comments on a LinkedIn post and auto-reply."""
    state = get_processing_state()
    if state.is_processing:
        raise HTTPException(status_code=409, detail="Another reply job is already running")

    service = get_reply_service()
    logger.info("Starting comment reply for post: %s (mode=%s)", request.post_url, request.mode)

    return await service.process_post(
        post_url=request.post_url,
        mode=request.mode,
        post_content=request.post_content,
        post_topic=request.post_topic,
        max_replies=request.max_replies,
    )


@router.post("/comment/reply/groq", response_model=CommentReplyResponse)
async def reply_to_comments_groq(request: CommentReplyRequest) -> CommentReplyResponse:
    """Process comments using Groq mode explicitly."""
    request.mode = ReplyMode.GROQ
    result = await reply_to_comments(request)
    if isinstance(result, FinalReport):
        raise HTTPException(status_code=400, detail="Use single post URL for this endpoint")
    return result


@router.post("/comment/reply/test", response_model=CommentReplyResponse)
async def reply_to_comments_test(request: CommentReplyRequest) -> CommentReplyResponse:
    """Process comments using test mode (no Groq API calls)."""
    request.mode = ReplyMode.TEST
    result = await reply_to_comments(request)
    if isinstance(result, FinalReport):
        raise HTTPException(status_code=400, detail="Use single post URL for this endpoint")
    return result


@router.post("/comment/reply/batch", response_model=FinalReport)
async def reply_to_multiple_posts(request: MultiPostReplyRequest) -> FinalReport:
    """Process multiple LinkedIn posts sequentially."""
    state = get_processing_state()
    if state.is_processing:
        raise HTTPException(status_code=409, detail="Another reply job is already running")

    service = get_reply_service()
    logger.info("Starting batch reply for %d posts", len(request.posts))

    return await service.process_multiple_posts(
        post_urls=request.posts,
        mode=request.mode,
        post_content=request.post_content,
        post_topic=request.post_topic,
        max_replies=request.max_replies,
    )


@router.post("/account/reply", response_model=FinalReport)
async def reply_to_account_posts(request: AccountReplyRequest) -> FinalReport:
    """Scan all posts on the user's account and auto-reply to comments."""
    state = get_processing_state()
    if state.is_processing:
        raise HTTPException(status_code=409, detail="Another reply job is already running")

    service = get_reply_service()
    logger.info("Starting account-wide posts scan (max_posts=%d, max_days=%s, mode=%s)", 
                request.max_posts, request.max_days, request.mode)

    return await service.process_account(
        mode=request.mode,
        max_posts=request.max_posts or 15,
        max_replies=request.max_replies,
        max_days=request.max_days,
    )


@router.post("/comment/groq/test", response_model=GroqTestResponse)
async def test_groq_reply(request: GroqReplyTestRequest) -> GroqTestResponse:
    """Test Groq reply generation without opening the browser."""
    groq = get_groq_service()
    try:
        result = groq.generate_test_reply(
            comment=request.comment,
            author_name=request.author_name,
            post_content=request.post_content,
            post_topic=request.post_topic,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=502, detail=f"Groq API error: {exc}") from exc

    return GroqTestResponse(
        status="success",
        comment=request.comment,
        author_name=request.author_name,
        generated_reply=result.text,
        sentiment=result.sentiment,
        is_question=result.is_question,
        detected_language=result.detected_language,
    )


@router.get("/comment/status", response_model=StatusResponse)
async def get_status() -> StatusResponse:
    """Return current processing status and configuration summary."""
    settings = get_settings()
    state = get_processing_state()
    service = get_reply_service()

    # Include additional configurations needed by the frontend dashboard
    return StatusResponse(
        status="processing" if state.is_processing else "idle",
        is_processing=state.is_processing,
        current_post_url=state.current_post_url,
        processed_today=service.daily_counter.count,
        max_replies_per_day=settings.max_replies_per_day,
        last_run=state.last_run,
        config_summary={
            "groq_api_key": settings.groq_api_key,
            "groq_model": settings.groq_model,
            "linkedin_email": settings.linkedin_email or "",
            "linkedin_password": settings.linkedin_password or "",
            "linkedin_account_name": settings.linkedin_account_name or "",
            "headless": settings.headless,
            "max_replies": settings.max_replies,
            "max_replies_per_day": settings.max_replies_per_day,
            "max_replies_per_user": settings.max_replies_per_user,
            "reply_only_questions": settings.reply_only_questions,
            "ignore_offensive": settings.ignore_offensive,
            "ignore_bots": settings.ignore_bots,
            "enable_screenshots": settings.enable_screenshots,
            "industry": settings.industry,
            "persona": settings.persona,
            "max_days": settings.max_days,
            "max_posts": settings.max_posts,
            "target_post_url": settings.target_post_url or "",
            "run_mode": settings.run_mode,
        },
    )


@router.post("/comment/start")
async def start_bot(background_tasks: BackgroundTasks):
    """Start bot run asynchronously using background tasks."""
    state = get_processing_state()
    if state.is_processing:
        raise HTTPException(status_code=409, detail="Another reply job is already running")

    settings = get_settings()
    service = get_reply_service()
    
    # Choose mode
    mode = ReplyMode.GROQ if settings.run_mode == "groq" else ReplyMode.TEST

    async def run_task():
        try:
            if settings.target_post_url and settings.target_post_url.strip():
                logger.info("Starting background single post run for: %s", settings.target_post_url)
                await service.process_post(
                    post_url=settings.target_post_url.strip(),
                    mode=mode,
                    max_replies=settings.max_replies,
                )
            else:
                logger.info("Starting background account posts scan")
                await service.process_account(
                    mode=mode,
                    max_posts=settings.max_posts,
                    max_replies=settings.max_replies,
                    max_days=settings.max_days,
                )
        except Exception as e:
            logger.error("Background bot execution failed: %s", e)

    background_tasks.add_task(run_task)
    return {"status": "starting", "message": "Bot background execution started"}


@router.get("/logs/csv")
async def get_csv_logs():
    """Read logs/comment_replies.csv and return as a JSON list."""
    from linkedin_comment_bot.app.config import PROJECT_ROOT
    csv_path = PROJECT_ROOT / "logs" / "comment_replies.csv"
    if not csv_path.exists():
        return []
    
    logs = []
    try:
        import csv
        with csv_path.open("r", newline="", encoding="utf-8") as f:
            reader = csv.DictReader(f)
            for row in reader:
                logs.append({
                    "post_url": row.get("Post URL", ""),
                    "comment_id": row.get("Comment ID", ""),
                    "author": row.get("Author", ""),
                    "comment": row.get("Comment", ""),
                    "reply": row.get("Generated Reply", ""),
                    "status": row.get("Status", "").strip().lower(),
                    "reason": row.get("Reason", ""),
                    "timestamp": row.get("Timestamp", ""),
                    "processing_time": row.get("Processing Time", "0.00s"),
                })
    except Exception as e:
        logger.error("Failed to read CSV logs: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
    return logs


@router.get("/logs/app")
async def get_app_logs(lines: int = 200):
    """Read the last N lines of logs/app.log."""
    from linkedin_comment_bot.app.config import PROJECT_ROOT
    log_path = PROJECT_ROOT / "logs" / "app.log"
    if not log_path.exists():
        return ""
        
    try:
        file_size = log_path.stat().st_size
        max_read = lines * 250
        
        with open(log_path, "r", encoding="utf-8", errors="ignore") as f:
            if file_size > max_read:
                f.seek(file_size - max_read)
                f.readline()  # discard partial line
            content = f.read()
        return content
    except Exception as e:
        logger.error("Failed to read app logs: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


@router.post("/logs/clear")
async def clear_app_logs():
    """Truncate the logs/app.log file to clear system logs."""
    from linkedin_comment_bot.app.config import PROJECT_ROOT
    log_path = PROJECT_ROOT / "logs" / "app.log"
    try:
        if log_path.exists():
            with open(log_path, "w", encoding="utf-8") as f:
                f.truncate(0)
        return {"status": "success", "message": "App logs cleared successfully."}
    except Exception as e:
        logger.error("Failed to clear app logs: %s", e)
        raise HTTPException(status_code=500, detail=str(e))


class ConfigUpdateRequest(BaseModel):
    settings: dict


@router.post("/config/save")
async def save_config(request: ConfigUpdateRequest):
    """Save the configuration dictionary into the .env file."""
    from linkedin_comment_bot.app.config import PROJECT_ROOT
    env_path = PROJECT_ROOT / ".env"
    
    updates = {}
    for k, v in request.settings.items():
        updates[str(k).upper()] = str(v)
        
    lines = []
    existing_keys = set()
    
    if env_path.exists():
        with open(env_path, "r", encoding="utf-8") as f:
            for line in f:
                line_strip = line.strip()
                if line_strip and not line_strip.startswith("#") and "=" in line_strip:
                    key = line_strip.split("=", 1)[0].strip()
                    existing_keys.add(key)
                    if key in updates:
                        lines.append(f"{key}={updates[key]}\n")
                    else:
                        lines.append(line)
                else:
                    lines.append(line)

    for key, val in updates.items():
        if key not in existing_keys:
            lines.append(f"{key}={val}\n")

    try:
        with open(env_path, "w", encoding="utf-8") as f:
            f.writelines(lines)
            
        # Reload env locally in python settings
        from dotenv import load_dotenv
        load_dotenv(dotenv_path=env_path, override=True)
        # Force refresh settings
        from linkedin_comment_bot.app.config import Settings
        import linkedin_comment_bot.app.config
        linkedin_comment_bot.app.config._settings = Settings()
    except Exception as e:
        logger.error("Failed to save config: %s", e)
        raise HTTPException(status_code=500, detail=str(e))
        
    return {"status": "success", "message": "Configuration saved"}


@router.post("/comment/stop")
async def stop_bot():
    """Request stopping the current run by clearing is_processing."""
    state = get_processing_state()
    if not state.is_processing:
        return {"status": "idle", "message": "Bot is not running"}
    state.is_processing = False
    return {"status": "stopping", "message": "Stop command sent"}
