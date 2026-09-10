"""
Verification tests for YouTube Comment Auto-Reply Scheduler.
"""

import sys
from pathlib import Path
from datetime import datetime, time
import zoneinfo

# Add paths
root = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(root))

from youtube_reply_bot.app.utils.scheduler import (
    parse_schedule_time,
    calculate_schedule,
    format_countdown,
)
from youtube_reply_bot.app.config import settings
from youtube_reply_bot.app.services.youtube_service import youtube_service

def test_time_parsing():
    print("Testing parse_schedule_time...")
    test_cases = [
        ("07:00 PM IST", time(19, 0, 0), "Asia/Kolkata"),
        ("7:00 PM", time(19, 0, 0), "Asia/Kolkata"),
        ("19:00", time(19, 0, 0), "Asia/Kolkata"),
        ("19:00:00", time(19, 0, 0), "Asia/Kolkata"),
        ("07:00:00 PM", time(19, 0, 0), "Asia/Kolkata"),
        ("7:00pm", time(19, 0, 0), "Asia/Kolkata"),
        ("09:30 AM IST", time(9, 30, 0), "Asia/Kolkata"),
        ("12:00 PM IST", time(12, 0, 0), "Asia/Kolkata"),
        ("12:00 AM IST", time(0, 0, 0), "Asia/Kolkata"),
        ("07:00 PM UTC", time(19, 0, 0), "UTC"),
    ]
    for inp, expected_time, expected_tz in test_cases:
        t, tz = parse_schedule_time(inp)
        assert t == expected_time, f"Failed time for '{inp}': expected {expected_time}, got {t}"
        assert tz.key == expected_tz, f"Failed tz for '{inp}': expected {expected_tz}, got {tz.key}"
        print(f"  [PASS] '{inp}' -> {t} in {tz.key}")
    print("All time parsing tests PASSED!\n")

def test_schedule_calculations():
    print("Testing calculate_schedule states...")
    tz = zoneinfo.ZoneInfo("Asia/Kolkata")
    start_t = time(19, 0, 0)
    window_hours = 4.0

    # Test 1: Morning (11:00 AM)
    d1 = datetime(2026, 9, 10, 11, 0, 0, tzinfo=tz)
    state, target, wait_sec = calculate_schedule(d1, start_t, window_hours)
    assert state == "WAIT_TODAY"
    assert wait_sec == 8 * 3600
    assert target.hour == 19 and target.minute == 0
    print(f"  [PASS] 11:00 AM IST -> {state}, wait {format_countdown(wait_sec)}")

    # Test 2: In window (07:30 PM)
    d2 = datetime(2026, 9, 10, 19, 30, 0, tzinfo=tz)
    state, target, remaining_sec = calculate_schedule(d2, start_t, window_hours)
    assert state == "ACTIVE_WINDOW"
    assert remaining_sec == 3.5 * 3600
    print(f"  [PASS] 07:30 PM IST -> {state}, window remaining {format_countdown(remaining_sec)}")

    # Test 3: Past window (11:30 PM)
    d3 = datetime(2026, 9, 10, 23, 30, 0, tzinfo=tz)
    state, target, wait_sec = calculate_schedule(d3, start_t, window_hours)
    assert state == "WAIT_TOMORROW"
    assert target.day == 11
    assert target.hour == 19
    print(f"  [PASS] 11:30 PM IST -> {state}, next target {target} (in {format_countdown(wait_sec)})")
    print("All schedule calculation tests PASSED!\n")

def test_settings_loaded():
    print("Testing settings loaded from .env...")
    print(f"  settings.START_TIME = {settings.START_TIME}")
    print(f"  settings.TIMEZONE = {settings.TIMEZONE}")
    print(f"  settings.CHECK_INTERVAL_MINUTES = {settings.CHECK_INTERVAL_MINUTES}")
    print(f"  settings.CHECK_DURATION_HOURS = {settings.CHECK_DURATION_HOURS}")
    print(f"  settings.SCHEDULE_ENABLED = {settings.SCHEDULE_ENABLED}")
    assert isinstance(settings.SCHEDULE_ENABLED, bool)
    print("Settings verified successfully!\n")

def test_service_attributes():
    print("Testing YouTubeService scheduler attributes...")
    assert hasattr(youtube_service, "run_scheduled_monitor")
    assert hasattr(youtube_service, "stop_monitor")
    assert hasattr(youtube_service, "is_scheduled_running")
    assert hasattr(youtube_service, "schedule_status")
    print("YouTubeService attributes verified successfully!\n")

def test_schedule_disabled_bypass():
    print("Testing schedule disabled exit when SCHEDULE_ENABLED is False...")
    import asyncio
    from unittest.mock import AsyncMock

    original_val = settings.SCHEDULE_ENABLED
    try:
        settings.SCHEDULE_ENABLED = False
        youtube_service.run_auto_reply = AsyncMock(return_value={"status": "success", "replied": 0})
        
        result = asyncio.run(youtube_service.run_scheduled_monitor())
        assert result.get("status") == "disabled", f"Expected disabled status, got {result}"
        assert not youtube_service.run_auto_reply.called, "Expected run_auto_reply NOT to be called when SCHEDULE_ENABLED is False"
        assert youtube_service.is_scheduled_running is False, "is_scheduled_running should remain False"
        print("  [PASS] run_scheduled_monitor stopped and returned disabled without running when SCHEDULE_ENABLED=False!")
    finally:
        settings.SCHEDULE_ENABLED = original_val
    print("Schedule disabled verification PASSED!\n")

if __name__ == "__main__":
    test_time_parsing()
    test_schedule_calculations()
    test_settings_loaded()
    test_service_attributes()
    test_schedule_disabled_bypass()
    print("=== ALL SCHEDULER TESTS PASSED SUCCESSFULLY ===")
