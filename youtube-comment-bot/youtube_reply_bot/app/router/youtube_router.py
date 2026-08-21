from fastapi import APIRouter, HTTPException
from youtube_reply_bot.app.models.response_models import ReplyResponse, BotStatusResponse
from youtube_reply_bot.app.services.youtube_service import youtube_service
from youtube_reply_bot.app.utils.logger import logger

router = APIRouter(prefix="/youtube", tags=["YouTube Auto Reply"])


@router.post("/reply", response_model=ReplyResponse)
async def auto_reply():
    """
    Triggers the YouTube Shorts comment auto-reply flow.
    
    No request body is needed. The bot reads all target videos and
    keyword-reply rules from the configured Google Sheet, then navigates
    to each YouTube Short, opens comments, and replies to matching comments.
    """
    if youtube_service.is_running:
        raise HTTPException(
            status_code=409,
            detail=f"The bot is already running a task on video: {youtube_service.current_video_id}"
        )

    logger.info("Received auto-reply trigger. Reading rules from Google Sheet...")
    result = await youtube_service.run_auto_reply()

    if result.get("status") == "error":
        raise HTTPException(
            status_code=400,
            detail=result.get("message", "An error occurred during execution")
        )

    return ReplyResponse(
        status="success",
        total_comments=result.get("total_comments", 0),
        replied=result.get("replied", 0),
        skipped=result.get("skipped", 0),
        details=result.get("details", None),
    )


@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status():
    """Returns the current operational status and statistics of the bot."""
    return BotStatusResponse(
        is_running=youtube_service.is_running,
        current_video_id=youtube_service.current_video_id,
        total_runs=youtube_service.total_runs,
        last_run_timestamp=youtube_service.last_run_timestamp,
        last_run_statistics=youtube_service.last_run_statistics,
    )
