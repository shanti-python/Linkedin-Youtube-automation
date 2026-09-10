"""
Scheduler utilities for YouTube comment auto-reply monitoring.
Supports parsing human-friendly times (e.g. '07:00 PM IST', '19:00'),
calculating schedule windows, and responsive async waiting.
"""

import re
import asyncio
from datetime import datetime, timedelta, time
from typing import Tuple, Optional, Callable, Dict, Any
import zoneinfo


def parse_schedule_time(time_str: str, default_tz_name: str = "Asia/Kolkata") -> Tuple[time, zoneinfo.ZoneInfo]:
    """
    Parses a time string in 12-hour or 24-hour format with optional timezone suffix.
    Examples:
        '07:00 PM IST'  -> time(19, 0), ZoneInfo('Asia/Kolkata')
        '19:00'         -> time(19, 0), ZoneInfo('Asia/Kolkata')
        '7:00 PM'       -> time(19, 0), ZoneInfo('Asia/Kolkata')
        '07:00:00 PM'   -> time(19, 0), ZoneInfo('Asia/Kolkata')
        '19:00:00 UTC'  -> time(19, 0), ZoneInfo('UTC')
    """
    if not time_str or not isinstance(time_str, str):
        return time(19, 0, 0), zoneinfo.ZoneInfo(default_tz_name)

    time_str = time_str.strip()
    tz_name = default_tz_name

    # Check for common timezone abbreviations
    tz_match = re.search(r'\b(IST|UTC|EST|EDT|CST|CDT|PST|PDT|GMT)\b', time_str, re.IGNORECASE)
    if tz_match:
        tz_abbr = tz_match.group(1).upper()
        if tz_abbr == "IST":
            tz_name = "Asia/Kolkata"
        elif tz_abbr in ("UTC", "GMT"):
            tz_name = "UTC"
        elif tz_abbr in ("EST", "EDT"):
            tz_name = "America/New_York"
        elif tz_abbr in ("CST", "CDT"):
            tz_name = "America/Chicago"
        elif tz_abbr in ("PST", "PDT"):
            tz_name = "America/Los_Angeles"
        # Strip timezone word from the time string
        time_str = re.sub(r'\b(IST|UTC|EST|EDT|CST|CDT|PST|PDT|GMT)\b', '', time_str, flags=re.IGNORECASE).strip()

    try:
        tz = zoneinfo.ZoneInfo(tz_name)
    except Exception:
        tz = zoneinfo.ZoneInfo("Asia/Kolkata")

    # Match 12-hour format with AM/PM
    match_12 = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?\s*(AM|PM)$', time_str, re.IGNORECASE)
    if match_12:
        hr = int(match_12.group(1))
        minute = int(match_12.group(2))
        sec = int(match_12.group(3) or 0)
        ampm = match_12.group(4).upper()
        if ampm == "PM" and hr < 12:
            hr += 12
        elif ampm == "AM" and hr == 12:
            hr = 0
        return time(hr, minute, sec), tz

    # Match 24-hour format
    match_24 = re.match(r'^(\d{1,2}):(\d{2})(?::(\d{2}))?$', time_str)
    if match_24:
        hr = int(match_24.group(1))
        minute = int(match_24.group(2))
        sec = int(match_24.group(3) or 0)
        return time(hr, minute, sec), tz

    # Fallback to default 19:00 IST
    return time(19, 0, 0), tz


def calculate_schedule(
    current_dt: datetime,
    start_t: time,
    duration_hours: float = 4.0
) -> Tuple[str, datetime, float]:
    """
    Calculates whether the bot should wait for today's start, is currently
    inside the active post-upload monitoring window, or should wait for tomorrow.

    Returns:
        (state, target_datetime, remaining_seconds)
        - state: 'WAIT_TODAY', 'ACTIVE_WINDOW', or 'WAIT_TOMORROW'
        - target_datetime: the scheduled start datetime or end of window
        - remaining_seconds: seconds until target (wait time or remaining window)
    """
    # Start datetime for today
    start_today = current_dt.replace(
        hour=start_t.hour,
        minute=start_t.minute,
        second=start_t.second,
        microsecond=0
    )

    # If duration is 0 or negative, consider monitoring window unlimited for today
    if duration_hours <= 0:
        window_end_today = start_today + timedelta(days=1)
    else:
        window_end_today = start_today + timedelta(hours=duration_hours)

    if current_dt < start_today:
        wait_seconds = (start_today - current_dt).total_seconds()
        return "WAIT_TODAY", start_today, wait_seconds
    elif current_dt <= window_end_today:
        remaining_window = (window_end_today - current_dt).total_seconds()
        return "ACTIVE_WINDOW", start_today, remaining_window
    else:
        start_tomorrow = start_today + timedelta(days=1)
        wait_seconds = (start_tomorrow - current_dt).total_seconds()
        return "WAIT_TOMORROW", start_tomorrow, wait_seconds


def format_countdown(seconds: float) -> str:
    """Formats seconds into human-readable duration (e.g. '2h 15m 30s')."""
    total_sec = max(0, int(seconds))
    hours, remainder = divmod(total_sec, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours > 0:
        return f"{hours}h {minutes}m {secs}s"
    elif minutes > 0:
        return f"{minutes}m {secs}s"
    return f"{secs}s"


async def wait_until_target(
    target_dt: datetime,
    tz: zoneinfo.ZoneInfo,
    cancel_checker: Optional[Callable[[], bool]] = None,
    logger=None,
    label: str = "Scheduled comment checking"
) -> bool:
    """
    Waits asynchronously until target_dt is reached.
    Checks cancel_checker periodically so cancellations respond immediately.
    Logs heartbeat messages periodically.
    Returns True if target reached, False if cancelled.
    """
    last_logged_minute = -1

    while True:
        if cancel_checker and cancel_checker():
            if logger:
                logger.info(f"{label} wait cancelled.")
            return False

        now = datetime.now(tz)
        diff = (target_dt - now).total_seconds()
        if diff <= 0:
            if logger:
                logger.info(f"Target time reached ({target_dt.strftime('%I:%M:%S %p %Z')}). Starting {label}!")
            return True

        # Periodic log: every 30 mins if > 1 hr, every 5 mins if <= 1 hr, every min if <= 5 mins
        current_minute = int(diff // 60)
        should_log = False
        if current_minute != last_logged_minute:
            if diff > 3600 and current_minute % 30 == 0:
                should_log = True
            elif 300 < diff <= 3600 and current_minute % 5 == 0:
                should_log = True
            elif diff <= 300:
                should_log = True

        if should_log and logger:
            logger.info(
                f"[Scheduler] {label} set for {target_dt.strftime('%I:%M %p %Z')}. "
                f"Time remaining: {format_countdown(diff)}."
            )
            last_logged_minute = current_minute

        # Sleep in small 2-second slices for immediate stop responsiveness
        sleep_chunk = min(2.0, max(0.2, diff))
        await asyncio.sleep(sleep_chunk)
