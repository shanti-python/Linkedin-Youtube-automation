from typing import Optional, Dict, Any
from fastapi import APIRouter, HTTPException, BackgroundTasks, Body
from youtube_reply_bot.app.config import settings
from youtube_reply_bot.app.models.response_models import ReplyResponse, BotStatusResponse
from youtube_reply_bot.app.services.youtube_service import youtube_service
from youtube_reply_bot.app.utils.logger import logger

router = APIRouter(prefix="/youtube", tags=["YouTube Auto Reply"])


@router.post("/reply", response_model=ReplyResponse)
async def auto_reply():
    """
    Triggers an immediate, single-pass YouTube Shorts comment auto-reply flow.
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


@router.post("/start")
async def start_bot(
    background_tasks: BackgroundTasks,
    request: Optional[dict] = Body(default=None)
):
    """
    Starts the bot in background.
    Supports mode='scheduled' (waits for START_TIME e.g. 07:00 PM IST then monitors)
    or mode='now' (immediate check).
    """
    if youtube_service.is_running or youtube_service.is_scheduled_running:
        raise HTTPException(
            status_code=409,
            detail="The bot is already running."
        )

    mode = "scheduled" if settings.SCHEDULE_ENABLED else "now"
    sheet_url = None

    if request and isinstance(request, dict):
        if settings.SCHEDULE_ENABLED:
            mode = request.get("mode", "scheduled")
        else:
            mode = "now"
        sheet_url = request.get("google_sheet_rules_url")

    async def run_task():
        try:
            if mode == "scheduled":
                await youtube_service.run_scheduled_monitor(sheet_url=sheet_url)
            else:
                await youtube_service.run_auto_reply(sheet_url=sheet_url)
        except Exception as e:
            logger.error(f"Background task failed: {e}")

    background_tasks.add_task(run_task)
    return {
        "status": "starting",
        "mode": mode,
        "message": f"Bot started in {mode} mode."
    }


@router.post("/stop")
async def stop_bot():
    """Stops the active scheduled monitor or comment reply run."""
    youtube_service.stop_monitor()
    return {"status": "stopping", "message": "Stop signal sent to bot."}


@router.get("/status", response_model=BotStatusResponse)
async def get_bot_status():
    """Returns the current operational status, schedule details, and statistics of the bot."""
    return BotStatusResponse(
        is_running=youtube_service.is_running or youtube_service.is_scheduled_running,
        is_scheduled_running=youtube_service.is_scheduled_running,
        is_waiting_for_schedule=youtube_service.is_waiting_for_schedule,
        schedule_status=youtube_service.schedule_status,
        next_run_timestamp=youtube_service.next_run_timestamp,
        time_until_start=youtube_service.time_until_start,
        current_cycle=youtube_service.current_cycle,
        schedule_config={
            "start_time": settings.START_TIME,
            "timezone": settings.TIMEZONE,
            "check_interval_minutes": settings.CHECK_INTERVAL_MINUTES,
            "check_duration_hours": settings.CHECK_DURATION_HOURS,
            "schedule_enabled": settings.SCHEDULE_ENABLED,
        },
        current_video_id=youtube_service.current_video_id,
        total_runs=youtube_service.total_runs,
        last_run_timestamp=youtube_service.last_run_timestamp,
        last_run_statistics=youtube_service.last_run_statistics,
    )

