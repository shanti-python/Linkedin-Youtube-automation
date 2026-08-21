#!/usr/bin/env python3
"""CLI/GUI entry point for LinkedIn Comment Auto Reply Bot (linkedin_reply_agent)."""

import argparse
import asyncio
import os
import sys
from pathlib import Path

# Initialize Playwright browser path at the very beginning if running as a compiled PyInstaller executable
if getattr(sys, "frozen", False):
    if "PLAYWRIGHT_BROWSERS_PATH" not in os.environ:
        project_dir = Path(sys.executable).resolve().parent
        bundled_browsers = Path(sys._MEIPASS) / "ms-playwright"
        local_browsers = project_dir / "ms-playwright"
        default_cache = Path.home() / ".cache" / "ms-playwright"
        
        if local_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(local_browsers)
        elif bundled_browsers.exists():
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(bundled_browsers)
        else:
            os.environ["PLAYWRIGHT_BROWSERS_PATH"] = str(default_cache)

import playwright.__main__
from contextlib import asynccontextmanager
from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware
from fastapi.staticfiles import StaticFiles

from linkedin_comment_bot.app.config import EXPORTS_DIR, LOGS_DIR, get_settings
from linkedin_comment_bot.app.models.request_models import ReplyMode
from linkedin_comment_bot.app.router.linkedin_router import router as linkedin_router
from linkedin_comment_bot.app.router.youtube_bot import router as youtube_router
from linkedin_comment_bot.app.router.youtube_bot import browser_manager as youtube_browser_manager
from linkedin_comment_bot.app.services.comment_reply_service import CommentReplyService
from linkedin_comment_bot.app.utils.logger import setup_logger

logger = setup_logger()

# ----------------- EMBEDDED FASTAPI WEB SERVER -----------------
@asynccontextmanager
async def lifespan(app: FastAPI):
    settings = get_settings()
    LOGS_DIR.mkdir(parents=True, exist_ok=True)
    EXPORTS_DIR.mkdir(parents=True, exist_ok=True)
    settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
    settings.failure_html_dir.mkdir(parents=True, exist_ok=True)
    logger.info("LinkedIn Comment Bot started")
    logger.info("Chrome profile: %s", settings.chrome_profile_path)
    logger.info("Groq model: %s", settings.groq_model)
    yield
    logger.info("LinkedIn Comment Bot shutting down")
    await youtube_browser_manager.close_browser()


app = FastAPI(
    title="LinkedIn Comment Auto Reply",
    description="Automatically monitor LinkedIn post comments and generate professional replies using Groq LLM.",
    version="1.0.0",
    lifespan=lifespan,
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

app.include_router(linkedin_router)
app.include_router(youtube_router)


@app.get("/health")
async def health_check():
    return {"status": "healthy", "service": "linkedin-comment-bot"}


# Serve static web dashboard frontend assets
frontend_out = Path(__file__).resolve().parent / "frontend" / "out"
if getattr(sys, "frozen", False):
    frontend_out = Path(sys._MEIPASS) / "frontend" / "out"

if frontend_out.exists():
    app.mount("/", StaticFiles(directory=str(frontend_out), html=True), name="frontend")
# ----------------------------------------------------------------


def parse_args():
    parser = argparse.ArgumentParser(
        description="Automated LinkedIn account comment scanner and Groq LLM auto-reply bot."
    )
    parser.add_argument(
        "--post-url",
        type=str,
        default=None,
        help="Specific LinkedIn post URL to process. If omitted, the bot scans all posts on your account.",
    )
    parser.add_argument(
        "--max-posts",
        type=int,
        default=15,
        help="Maximum number of account posts to scan if no post URL is specified (default: 15).",
    )
    parser.add_argument(
        "--mode",
        type=str,
        choices=["groq", "test"],
        default="groq",
        help="Reply mode: 'groq' (uses Groq API) or 'test' (mock reply without LLM API call).",
    )
    parser.add_argument(
        "--email",
        type=str,
        default=None,
        help="LinkedIn account email (overrides LINKEDIN_EMAIL in .env).",
    )
    parser.add_argument(
        "--password",
        type=str,
        default=None,
        help="LinkedIn account password (overrides LINKEDIN_PASSWORD in .env).",
    )
    parser.add_argument(
        "--headless",
        action="store_true",
        help="Run browser in headless mode.",
    )
    parser.add_argument(
        "--max-replies",
        type=int,
        default=None,
        help="Maximum number of replies to submit per post.",
    )
    parser.add_argument(
        "--persona",
        type=str,
        default=None,
        help="Custom persona description to use for generating replies (overrides PERSONA in .env).",
    )
    parser.add_argument(
        "--gui",
        action="store_true",
        help="Run in GUI mode.",
    )
    return parser.parse_args()


def main():
    # Launch GUI by default if no CLI arguments are passed or --gui is requested
    cli_args = [arg for arg in sys.argv[1:] if arg != "--gui"]
    if len(cli_args) == 0 or "--gui" in sys.argv:
        if sys.platform.startswith("linux") and "DISPLAY" not in os.environ:
            print("No DISPLAY environment variable detected. Running in CLI mode.")
        else:
            print("Launching Web Dashboard...")
            import uvicorn
            import webbrowser
            import threading
            import time
            from linkedin_comment_bot.app.config import get_settings

            settings = get_settings()
            browser_host = "127.0.0.1" if settings.api_host == "0.0.0.0" else settings.api_host
            url = f"http://{browser_host}:{settings.api_port}"
            
            def open_browser():
                time.sleep(1.0)
                print(f"Opening dashboard in browser: {url}")
                webbrowser.open(url)

            threading.Thread(target=open_browser, daemon=True).start()
            
            uvicorn.run(
                app,
                host=settings.api_host,
                port=settings.api_port,
                access_log=False,
            )
            return

    args = parse_args()

    if args.email:
        os.environ["LINKEDIN_EMAIL"] = args.email
    if args.password:
        os.environ["LINKEDIN_PASSWORD"] = args.password
    if args.headless:
        os.environ["HEADLESS"] = "true"

    settings = get_settings()
    if args.persona:
        os.environ["PERSONA"] = args.persona
        settings.persona = args.persona
    mode = ReplyMode.GROQ if args.mode == "groq" else ReplyMode.TEST

    # Verify Groq API Key if running in GROQ mode
    if mode == ReplyMode.GROQ and not settings.groq_api_key:
        print("ERROR: GROQ_API_KEY is not set. Please set it in your .env file or environment variables.")
        sys.exit(1)


    print("=" * 60)
    print("LinkedIn Comment Auto Reply Bot")
    if args.post_url:
        print(f"Target: Single Post ({args.post_url})")
    else:
        print(f"Target: Account Posts Scan (Up to {args.max_posts} posts)")
    print(f"Mode: {mode.value.upper()}")
    print(f"Cookies Storage: {settings.cookies_file_path}")
    print("=" * 60)

    service = CommentReplyService(settings=settings)

    if args.post_url:
        response = asyncio.run(service.process_post(
            post_url=args.post_url,
            mode=mode,
            max_replies=args.max_replies,
        ))

        print("\n" + "=" * 60)
        print("RESULTS:")
        print(f"Status: {response.status}")
        print(f"Total Comments Found: {response.total_comments}")
        print(f"Replied: {response.replied}")
        print(f"Skipped: {response.skipped}")
        print(f"Errors: {response.errors}")
        if response.message:
            print(f"Message: {response.message}")
        print("=" * 60)

        if response.status == "error":
            sys.exit(1)

    else:
        report = asyncio.run(service.process_account(
            mode=mode,
            max_posts=args.max_posts,
            max_replies=args.max_replies,
        ))

        print("\n" + "=" * 60)
        print("ACCOUNT SCAN RESULTS:")
        print(f"Posts Processed: {report.posts_processed}")
        print(f"Total Comments Found: {report.comments_found}")
        print(f"Replied: {report.replied}")
        print(f"Skipped: {report.skipped}")
        print(f"Errors: {report.errors}")
        print(f"Duration: {report.duration}")
        print("=" * 60)


if __name__ == "__main__":
    main()
