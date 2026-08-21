"""Application configuration loaded from environment variables."""

import os
import sys
from pathlib import Path
from typing import Optional
from dotenv import load_dotenv

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict

# Determine if we are running as a compiled PyInstaller executable
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
    
    # Load local .env first if it exists in the executable directory (highest precedence)
    local_env = PROJECT_ROOT / ".env"
    if local_env.exists():
        load_dotenv(dotenv_path=local_env, override=True)
        
    # Load bundled .env second if it exists inside _MEIPASS (lower precedence fallback)
    bundled_env = Path(sys._MEIPASS) / ".env"
    if bundled_env.exists():
        load_dotenv(dotenv_path=bundled_env, override=False)

    # Configure Playwright browser path to look in standard locations:
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        bundled_browsers = Path(sys._MEIPASS) / "ms-playwright"
        local_browsers = PROJECT_ROOT / "ms-playwright"
        default_cache = Path.home() / ".cache" / "ms-playwright"
        
        if local_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
        elif bundled_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(default_cache)
else:
    # Running from source code
    PROJECT_ROOT = Path(__file__).resolve().parent.parent.parent
    local_env = PROJECT_ROOT / ".env"
    if local_env.exists():
        load_dotenv(dotenv_path=local_env, override=True)

LOGS_DIR = PROJECT_ROOT / "logs"
EXPORTS_DIR = PROJECT_ROOT / "exports"
PROCESSED_COMMENTS_PATH = EXPORTS_DIR / "processed_comments.json"


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=str(PROJECT_ROOT / ".env"),
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # Groq
    groq_api_key: str = Field(default="", alias="GROQ_API_KEY")
    groq_model: str = Field(default="llama-3.3-70b-versatile", alias="GROQ_MODEL")

    # Browser
    chrome_profile_path: str = Field(
        default=str(Path.home() / ".config" / "linkedin-bot-chrome"),
        alias="CHROME_PROFILE_PATH",
    )
    headless: bool = Field(default=False, alias="HEADLESS")
    browser_channel: str = Field(default="chromium", alias="BROWSER_CHANNEL")

    # LinkedIn Credentials & Session Storage
    linkedin_email: Optional[str] = Field(default=None, alias="LINKEDIN_EMAIL")
    linkedin_password: Optional[str] = Field(default=None, alias="LINKEDIN_PASSWORD")
    cookies_file_path: Path = Field(
        default=EXPORTS_DIR / "cookies.json",
        alias="COOKIES_FILE_PATH",
    )

    # Human behavior
    typing_speed_min_ms: int = Field(default=40, alias="TYPING_SPEED_MIN_MS")
    typing_speed_max_ms: int = Field(default=120, alias="TYPING_SPEED_MAX_MS")
    reply_delay_min_sec: float = Field(default=5.0, alias="REPLY_DELAY_MIN_SEC")
    reply_delay_max_sec: float = Field(default=20.0, alias="REPLY_DELAY_MAX_SEC")
    long_pause_every_n_replies: int = Field(default=10, alias="LONG_PAUSE_EVERY_N_REPLIES")
    long_pause_min_sec: float = Field(default=30.0, alias="LONG_PAUSE_MIN_SEC")
    long_pause_max_sec: float = Field(default=90.0, alias="LONG_PAUSE_MAX_SEC")

    # Limits
    max_replies: int = Field(default=50, alias="MAX_REPLIES")
    max_replies_per_day: int = Field(default=200, alias="MAX_REPLIES_PER_DAY")
    retry_count: int = Field(default=3, alias="RETRY_COUNT")
    max_days: Optional[int] = Field(default=None, alias="MAX_DAYS")

    # Logging
    csv_log_path: Path = Field(
        default=LOGS_DIR / "comment_replies.csv",
        alias="CSV_LOG_PATH",
    )
    enable_screenshots: bool = Field(default=False, alias="ENABLE_SCREENSHOTS")
    screenshot_dir: Path = Field(default=LOGS_DIR / "screenshots", alias="SCREENSHOT_DIR")
    failure_html_dir: Path = Field(default=LOGS_DIR / "failures", alias="FAILURE_HTML_DIR")

    # Google Sheets (optional)
    google_sheets_enabled: bool = Field(default=False, alias="GOOGLE_SHEETS_ENABLED")
    google_sheets_credentials_path: Optional[str] = Field(
        default=None, alias="GOOGLE_SHEETS_CREDENTIALS_PATH"
    )
    google_sheets_spreadsheet_id: Optional[str] = Field(
        default=None, alias="GOOGLE_SHEETS_SPREADSHEET_ID"
    )
    google_sheets_worksheet_name: str = Field(
        default="Comment Replies", alias="GOOGLE_SHEETS_WORKSHEET_NAME"
    )

    # Reply behavior
    industry: str = Field(default="Technology", alias="INDUSTRY")
    reply_only_questions: bool = Field(default=False, alias="REPLY_ONLY_QUESTIONS")
    ignore_offensive: bool = Field(default=True, alias="IGNORE_OFFENSIVE")
    ignore_bots: bool = Field(default=True, alias="IGNORE_BOTS")
    max_replies_per_user: int = Field(default=3, alias="MAX_REPLIES_PER_USER")
    enable_translation: bool = Field(default=False, alias="ENABLE_TRANSLATION")
    persona: str = Field(
        default="You are the owner of this LinkedIn account. You are a professional, helpful, and insightful industry expert.",
        alias="PERSONA",
    )

    # LinkedIn account identifier (display name fragment for already-replied detection)
    linkedin_account_name: str = Field(default="", alias="LINKEDIN_ACCOUNT_NAME")

    # Target post & Scan limits (newly migrated configuration attributes)
    max_posts: int = Field(default=15, alias="MAX_POSTS")
    target_post_url: str = Field(default="", alias="TARGET_POST_URL")
    run_mode: str = Field(default="groq", alias="RUN_MODE")

    # API
    api_host: str = Field(default="0.0.0.0", alias="API_HOST")
    api_port: int = Field(default=8000, alias="API_PORT")


def get_settings() -> Settings:
    return Settings()
