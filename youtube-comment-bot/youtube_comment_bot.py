#!/usr/bin/env python3
"""
YouTube Comment Auto Reply Bot Service (Consolidated Single-File Version)
This file integrates configuration, logging, browser management, reply generation,
and FastAPI web routes for the automated Shorts comment reply system.
"""

import os
# Force Qt applications (and browsers using Qt integrations) to use X11/XWayland
# to prevent crashes on Gnome Wayland when the Qt wayland plugin is missing.
os.environ["QT_QPA_PLATFORM"] = "xcb"
os.environ["XDG_SESSION_TYPE"] = "x11"
os.environ.pop("WAYLAND_DISPLAY", None)

import sys
import re
import csv
import json
import random
import math
import asyncio
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional, List, Dict, Any, Set, Tuple
from urllib.parse import urlparse, parse_qs
from logging.handlers import RotatingFileHandler

import uvicorn
import httpx
from fastapi import FastAPI, APIRouter, HTTPException
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from pydantic_settings import BaseSettings, SettingsConfigDict
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page, Locator
from openai import AsyncOpenAI

# ──────────────────────────────────────────────────────────────────────
# 1. Settings & Config
# ──────────────────────────────────────────────────────────────────────

class Settings(BaseSettings):
    # Browser config
    CHROME_PROFILE_PATH: Optional[str] = None
    HEADLESS: bool = False
    
    # Human simulation config
    MIN_TYPING_DELAY: float = 0.05
    MAX_TYPING_DELAY: float = 0.15
    MIN_RANDOM_DELAY: float = 15.0
    MAX_RANDOM_DELAY: float = 45.0
    MIN_WATCH_DELAY: float = 6.0
    MAX_WATCH_DELAY: float = 15.0
    AUTO_REWRITE_REPLY: bool = True
    
    # Data storage config
    CSV_PATH: str = "replies/replies_log.csv"
    JSON_PATH: str = "replies/replies_log.json"
    RESUME_PATH: str = "replies/resume_state.json"
    
    # LLM Settings
    LLM_PROVIDER: str = "openai"  # openai, groq, ollama
    OPENAI_API_KEY: Optional[str] = None
    GROQ_API_KEY: Optional[str] = None
    OLLAMA_API_URL: str = "http://localhost:11434/v1"
    LLM_MODEL: str = "gpt-4o-mini"  # e.g., gpt-4o-mini
    
    # Limits
    MAX_REPLIES_PER_RUN: int = 50
    
    # Google Sheets (Optional)
    GOOGLE_SHEET_ID: Optional[str] = None
    GOOGLE_SHEET_CREDENTIALS_FILE: Optional[str] = None
    GOOGLE_SHEET_RULES_URL: str = "https://docs.google.com/spreadsheets/d/1bfT7e4GsiqbBPXV1UD7f5HioOa-N71Db/edit?gid=1122465407#gid=1122465407"
    
    # App root path resolver
    BASE_DIR: Path = Path(__file__).resolve().parent
    
    model_config = SettingsConfigDict(
        env_file=str(Path(__file__).resolve().parent / ".env"),
        env_file_encoding="utf-8",
        extra="ignore"
    )

    def get_absolute_path(self, relative_path: str) -> str:
        """Helper to resolve paths relative to base directory."""
        path = Path(relative_path)
        if path.is_absolute():
            return str(path)
        return str(self.BASE_DIR / path)

settings = Settings()

# Ensure standard directories exist
os.makedirs(settings.get_absolute_path("logs"), exist_ok=True)
os.makedirs(settings.get_absolute_path("replies"), exist_ok=True)


# ──────────────────────────────────────────────────────────────────────
# 2. Logger Setup
# ──────────────────────────────────────────────────────────────────────

def setup_logger(name: str = "youtube_reply_bot") -> logging.Logger:
    """Sets up a rotating file logger and a stream logger for console output."""
    logger = logging.getLogger(name)
    if logger.handlers:
        return logger
        
    logger.setLevel(logging.INFO)
    log_format = logging.Formatter(
        "%(asctime)s - %(name)s - %(levelname)s - [%(filename)s:%(lineno)d] - %(message)s"
    )
    
    # Console Handler
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.INFO)
    console_handler.setFormatter(log_format)
    logger.addHandler(console_handler)
    
    # Rotating File Handler
    log_file_path = settings.get_absolute_path(os.path.join("logs", "bot.log"))
    file_handler = RotatingFileHandler(
        log_file_path, maxBytes=5 * 1024 * 1024, backupCount=3
    )
    file_handler.setLevel(logging.INFO)
    file_handler.setFormatter(log_format)
    logger.addHandler(file_handler)
    
    return logger

logger = setup_logger()


# ──────────────────────────────────────────────────────────────────────
# 3. Helper Functions
# ──────────────────────────────────────────────────────────────────────

def extract_video_id(url: str) -> Optional[str]:
    """Extracts the YouTube Video ID from various YouTube URL structures."""
    if not url:
        return None
    url = url.strip()
    
    studio_match = re.search(r"studio\.youtube\.com/video/([^/]+)/comments", url)
    if studio_match:
        return studio_match.group(1)
        
    shorts_match = re.search(r"/shorts/([^/?#&]+)", url)
    if shorts_match:
        return shorts_match.group(1)
        
    parsed_url = urlparse(url)
    if parsed_url.hostname in ("www.youtube.com", "youtube.com", "m.youtube.com"):
        query = parse_qs(parsed_url.query)
        if "v" in query:
            return query["v"][0]
            
    if parsed_url.hostname == "youtu.be":
        return parsed_url.path.lstrip("/")
        
    path_parts = parsed_url.path.split("/")
    if len(path_parts) > 2 and path_parts[1] in ("embed", "v"):
        return path_parts[2]
        
    id_match = re.search(r"(?:v=|\/)([0-9A-Za-z_-]{11})(?:\?|&|$|\/)", url)
    if id_match:
        return id_match.group(1)
        
    if len(url) == 11 and re.match(r"^[0-9A-Za-z_-]{11}$", url):
        return url
        
    return None


# ──────────────────────────────────────────────────────────────────────
# 4. Human Simulation Helpers
# ──────────────────────────────────────────────────────────────────────

async def random_delay(min_sec: float = None, max_sec: float = None) -> None:
    """Pause execution for a random period of time."""
    if min_sec is None:
        min_sec = settings.MIN_RANDOM_DELAY
    if max_sec is None:
        max_sec = settings.MAX_RANDOM_DELAY
    await asyncio.sleep(random.uniform(min_sec, max_sec))

async def human_type(locator: Locator, text: str, page: Optional[Page] = None) -> None:
    """Types text character by character into an input with realistic delays."""
    await locator.focus()
    await locator.click()
    await asyncio.sleep(0.3)
    
    try:
        await locator.press("Control+A")
        await locator.press("Backspace")
    except Exception:
        pass
    await asyncio.sleep(0.3)

    for char in text:
        delay = random.uniform(settings.MIN_TYPING_DELAY, settings.MAX_TYPING_DELAY)
        if char in ".,!? ":
            delay += random.uniform(0.1, 0.3)
        if random.random() < 0.01:
            delay += random.uniform(0.5, 1.5)

        if page:
            await page.keyboard.type(char)
        else:
            try:
                await locator.press(char)
            except Exception:
                await locator.evaluate("(el, c) => { el.focus(); document.execCommand('insertText', false, c); }", char)
        await asyncio.sleep(delay)
        
    await asyncio.sleep(random.uniform(0.3, 0.6))

async def simulate_mouse_move(page: Page, target_x: float, target_y: float) -> None:
    """Moves the mouse to target coordinates using smooth Bézier-like steps."""
    current_x, current_y = 100.0, 100.0
    steps = random.randint(10, 20)
    for i in range(1, steps + 1):
        t = i / steps
        t_curved = t * t * (3 - 2 * t)
        x = current_x + (target_x - current_x) * t_curved
        y = current_y + (target_y - current_y) * t_curved
        x += random.uniform(-1, 1)
        y += random.uniform(-1, 1)
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.005, 0.015))
    await page.mouse.move(target_x, target_y)

async def human_click(page: Page, locator: Locator) -> None:
    """Hover mouse over element with human-like motion, pause, then click."""
    box = await locator.bounding_box()
    if not box:
        logger.warning("Could not calculate bounding box, performing direct click.")
        await locator.click()
        return
        
    target_x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
    target_y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
    
    await simulate_mouse_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(0.1, 0.4))
    await page.mouse.click(target_x, target_y)
    await asyncio.sleep(random.uniform(0.2, 0.5))

async def human_scroll(page: Page, distance_min: int = 150, distance_max: int = 400) -> None:
    """Performs a random human-like scroll down."""
    distance = random.randint(distance_min, distance_max)
    steps = random.randint(3, 7)
    for _ in range(steps):
        step_dist = distance // steps
        await page.mouse.wheel(0, step_dist)
        await asyncio.sleep(random.uniform(0.1, 0.3))
    await asyncio.sleep(random.uniform(0.5, 1.5))


# ──────────────────────────────────────────────────────────────────────
# 5. Browser Manager
# ──────────────────────────────────────────────────────────────────────

class BrowserManager:
    def __init__(self):
        self.playwright: Playwright = None
        self.context: BrowserContext = None
        self.page: Page = None

    async def open_browser(self) -> Tuple[BrowserContext, Page]:
        """Launches Chromium using a persistent browser context."""
        if self.context:
            logger.info("Browser already open. Returning existing context.")
            return self.context, self.page
            
        logger.info("Initializing Playwright...")
        self.playwright = await async_playwright().start()
        
        profile_path = settings.CHROME_PROFILE_PATH
        if not profile_path:
            profile_path = settings.get_absolute_path("replies/chrome_profile")
            logger.info(f"No CHROME_PROFILE_PATH provided in settings. Using default local path: {profile_path}")
        else:
            profile_path = settings.get_absolute_path(profile_path)
            logger.info(f"Using Chrome profile path: {profile_path}")
            
        os.makedirs(profile_path, exist_ok=True)
        
        args = [
            "--disable-blink-features=AutomationControlled",
            "--no-sandbox",
            "--disable-infobars",
            "--disable-dev-shm-usage",
            "--disable-gpu",
            "--ozone-platform=x11"
        ]
        
        logger.info(f"Launching persistent Chromium context (Headless: {settings.HEADLESS})...")
        self.context = await self.playwright.chromium.launch_persistent_context(
            user_data_dir=profile_path,
            headless=settings.HEADLESS,
            channel="chrome" if os.path.exists("/usr/bin/google-chrome") else None,
            args=args,
            viewport={"width": 1280, "height": 800},
            ignore_default_args=["--enable-automation"]
        )
        
        await self.context.grant_permissions(["clipboard-read", "clipboard-write"])
        self.context.set_default_timeout(30000)
        
        pages = self.context.pages
        self.page = pages[0] if pages else await self.context.new_page()
        
        # Anti-detection stealth shims
        await self.page.add_init_script("""
            delete Object.prototype.webdriver;
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });
            Object.defineProperty(navigator, 'plugins', {
                get: () => [
                    { description: "Portable Document Format", filename: "internal-pdf-viewer", name: "Chrome PDF Viewer" },
                    { description: "Google PDF Viewer", filename: "mhjfbgoooddegpeggmfomjpcclooenen", name: "Google Chrome PDF Viewer" }
                ]
            });
        """)
        
        logger.info("Browser successfully opened and configured.")
        return self.context, self.page

    async def close_browser(self) -> None:
        """Closes the browser context and stops Playwright."""
        logger.info("Closing browser session...")
        try:
            if self.context:
                await self.context.close()
                self.context = None
            if self.playwright:
                await self.playwright.stop()
                self.playwright = None
            self.page = None
            logger.info("Browser session closed successfully.")
        except Exception as e:
            logger.error(f"Error during browser cleanup: {str(e)}")

browser_manager = BrowserManager()


# ──────────────────────────────────────────────────────────────────────
# 6. Database / Log persistence (CommentService)
# ──────────────────────────────────────────────────────────────────────

class CommentService:
    def __init__(self):
        self.csv_path = settings.get_absolute_path(settings.CSV_PATH)
        self.json_path = settings.get_absolute_path(settings.JSON_PATH)
        self.resume_path = settings.get_absolute_path(settings.RESUME_PATH)
        
        os.makedirs(os.path.dirname(self.csv_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.json_path), exist_ok=True)
        os.makedirs(os.path.dirname(self.resume_path), exist_ok=True)
        
        self._init_csv()
        self._init_json()
        self.processed_comments: Dict[str, Set[str]] = self._load_resume_state()

    def _init_csv(self) -> None:
        if not os.path.exists(self.csv_path):
            try:
                with open(self.csv_path, mode="w", newline="", encoding="utf-8") as f:
                    writer = csv.writer(f)
                    writer.writerow([
                        "Video ID", "Comment ID", "Author", "Comment", 
                        "Generated Reply", "Status", "Timestamp", "Reason"
                    ])
                logger.info(f"Initialized CSV log file at {self.csv_path}")
            except Exception as e:
                logger.error(f"Failed to initialize CSV log file: {str(e)}")

    def _init_json(self) -> None:
        if not os.path.exists(self.json_path):
            try:
                with open(self.json_path, mode="w", encoding="utf-8") as f:
                    json.dump([], f)
                logger.info(f"Initialized JSON log file at {self.json_path}")
            except Exception as e:
                logger.error(f"Failed to initialize JSON log file: {str(e)}")

    def _load_resume_state(self) -> Dict[str, Set[str]]:
        if os.path.exists(self.resume_path):
            try:
                with open(self.resume_path, mode="r", encoding="utf-8") as f:
                    data = json.load(f)
                    return {vid: set(ids) for vid, ids in data.items()}
            except Exception as e:
                logger.error(f"Failed to load resume state: {str(e)}")
        return {}

    def _save_resume_state(self) -> None:
        try:
            data = {vid: list(ids) for vid, ids in self.processed_comments.items()}
            with open(self.resume_path, mode="w", encoding="utf-8") as f:
                json.dump(data, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to save resume state: {str(e)}")

    def is_processed(self, video_id: str, comment_id: str) -> bool:
        return comment_id in self.processed_comments.get(video_id, set())

    def mark_processed(self, video_id: str, comment_id: str) -> None:
        if video_id not in self.processed_comments:
            self.processed_comments[video_id] = set()
        self.processed_comments[video_id].add(comment_id)
        self._save_resume_state()

    def save_log(
        self,
        video_id: str,
        comment_id: str,
        author: str,
        comment: str,
        generated_reply: Optional[str],
        status: str,
        reason: Optional[str] = None
    ) -> None:
        timestamp = datetime.now().isoformat()
        
        # Append to CSV
        try:
            with open(self.csv_path, mode="a", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow([
                    video_id, comment_id, author, comment, 
                    generated_reply or "", status, timestamp, reason or ""
                ])
        except Exception as e:
            logger.error(f"Failed to write to CSV log: {str(e)}")
            
        # Append to JSON
        try:
            log_entry = {
                "video_id": video_id,
                "comment_id": comment_id,
                "author": author,
                "comment": comment,
                "generated_reply": generated_reply,
                "status": status,
                "timestamp": timestamp,
                "reason": reason
            }
            logs = []
            if os.path.exists(self.json_path):
                with open(self.json_path, mode="r", encoding="utf-8") as f:
                    try:
                        logs = json.load(f)
                    except json.JSONDecodeError:
                        logs = []
            logs.append(log_entry)
            with open(self.json_path, mode="w", encoding="utf-8") as f:
                json.dump(logs, f, indent=4)
        except Exception as e:
            logger.error(f"Failed to write to JSON log: {str(e)}")

        if status == "replied":
            self.mark_processed(video_id, comment_id)

        # Google Sheets Appending
        if settings.GOOGLE_SHEET_ID and settings.GOOGLE_SHEET_CREDENTIALS_FILE:
            self._append_to_google_sheet([
                video_id, comment_id, author, comment,
                generated_reply or "", status, timestamp, reason or ""
            ])

    def _append_to_google_sheet(self, row_data: List[Any]) -> None:
        credentials_path = settings.get_absolute_path(settings.GOOGLE_SHEET_CREDENTIALS_FILE)
        if not os.path.exists(credentials_path):
            logger.warning(f"Google Sheet credentials file not found: {credentials_path}")
            return

        try:
            from google.oauth2 import service_account
            from googleapiclient.discovery import build
            
            scopes = ["https://www.googleapis.com/auth/spreadsheets"]
            creds = service_account.Credentials.from_service_account_file(
                credentials_path, scopes=scopes
            )
            service = build("sheets", "v4", credentials=creds)
            sheet = service.spreadsheets()
            body = {"values": [row_data]}
            sheet.values().append(
                spreadsheetId=settings.GOOGLE_SHEET_ID,
                range="A1",
                valueInputOption="RAW",
                insertDataOption="INSERT_ROWS",
                body=body
            ).execute()
            logger.info("Successfully logged entry to Google Sheets.")
        except Exception as e:
            logger.error(f"Failed to append log to Google Sheet: {str(e)}")

comment_service = CommentService()


# ──────────────────────────────────────────────────────────────────────
# 7. LLM & Spintax Generator (ReplyGenerator)
# ──────────────────────────────────────────────────────────────────────

class ReplyGenerator:
    def __init__(self):
        self.predefined_keywords = {
            "guide": "Thank you! I'll make a detailed guide soon.",
            "tutorial": "Thank you! I'll make a detailed guide soon.",
            "python": "Python tutorial is coming soon.",
            "thanks": "You're welcome 😊",
            "thank you": "You're welcome 😊",
            "nice": "Thanks for watching!",
            "awesome": "Thanks for watching!",
            "great": "Thanks for watching!",
        }
        self.default_reply = "Thank you for your support."
        self.sheet_rules: Dict[str, Dict[str, str]] = {}
        self.client = None
        self._init_llm_client()

    async def fetch_sheet_rules(self) -> None:
        """Fetches the auto-reply rules CSV dynamically from the public Google Sheet."""
        if not settings.GOOGLE_SHEET_RULES_URL:
            logger.info("No GOOGLE_SHEET_RULES_URL configured. Using default local rules.")
            return

        url = settings.GOOGLE_SHEET_RULES_URL
        gid = None
        if "gid=" in url:
            m = re.search(r"[?#&]gid=(\d+)", url)
            if m:
                gid = m.group(1)

        if "/edit" in url:
            url = url.split("/edit")[0] + "/export?format=csv"
            if gid:
                url += f"&gid={gid}"

        logger.info(f"Fetching latest auto-reply rules from Google Sheet: {url}")
        try:
            async with httpx.AsyncClient(timeout=10.0, follow_redirects=True) as client:
                response = await client.get(url)
                if response.status_code != 200:
                    logger.error(f"Failed to fetch Google Sheet rules: HTTP {response.status_code}")
                    return

                csv_data = response.text
                reader = csv.reader(csv_data.splitlines())
                header = next(reader, None)
                if not header:
                    logger.warning("Google Sheet rules CSV is empty.")
                    return

                col_indices = {col.strip().lower(): idx for idx, col in enumerate(header)}
                video_idx = next((idx for name, idx in col_indices.items() if name in ("video url", "url", "video_url") or "url" in name), 1)
                comment_idx = next((idx for name, idx in col_indices.items() if name in ("comment text (keyword)", "comment text", "keyword", "user_comment", "user comment", "comment") or "keyword" in name or "comment" in name), 2)
                reply_idx = next((idx for name, idx in col_indices.items() if name in ("reply message", "reply", "message", "reply_text") or "reply" in name or "message" in name), 3)

                rules = {}
                for row in reader:
                    if len(row) <= max(video_idx, comment_idx, reply_idx):
                        continue
                    video_url = row[video_idx].strip()
                    user_comment = row[comment_idx].strip().lower()
                    reply_text = row[reply_idx].strip()

                    if not video_url or not user_comment or not reply_text:
                        continue

                    vid_id = extract_video_id(video_url)
                    if not vid_id:
                        continue

                    if vid_id not in rules:
                        rules[vid_id] = {}
                    rules[vid_id][user_comment] = reply_text

                self.sheet_rules = rules
                logger.info(f"Loaded rules for {len(rules)} videos from Google Sheet.")
        except Exception as e:
            logger.error(f"Failed to fetch/parse Google Sheet rules: {str(e)}")

    def _init_llm_client(self):
        provider = settings.LLM_PROVIDER.lower()
        api_key = None
        base_url = None

        try:
            if provider == "openai":
                api_key = settings.OPENAI_API_KEY
            elif provider == "groq":
                api_key = settings.GROQ_API_KEY
                base_url = "https://api.groq.com/openai/v1"
            elif provider == "ollama":
                api_key = "ollama"
                base_url = settings.OLLAMA_API_URL
                
            if provider in ("openai", "groq") and not api_key:
                logger.warning(f"{settings.LLM_PROVIDER.upper()} selected, but API key is missing. AI replies will fall back to predefined responses.")
                return

            self.client = AsyncOpenAI(api_key=api_key, base_url=base_url)
            logger.info(f"Initialized LLM client for provider: {provider} using model: {settings.LLM_MODEL}")
        except Exception as e:
            logger.error(f"Failed to initialize LLM client: {str(e)}")
            self.client = None

    def detect_sentiment_and_type(self, text: str) -> Tuple[str, bool, bool]:
        text_lower = text.lower()
        offensive_keywords = [
            "abuse", "idiot", "stupid", "fuck", "shit", "bitch", "scam", 
            "fake", "crap", "bastard", "asshole", "hate you"
        ]
        is_offensive = any(re.search(r'\b' + re.escape(word) + r'\b', text_lower) for word in offensive_keywords)
        is_question = "?" in text or any(text_lower.startswith(word) for word in [
            "how", "why", "what", "when", "where", "who", "can you", "is there", "do you", "please"
        ])
        
        positive_words = ["love", "great", "awesome", "nice", "thanks", "thank", "good", "perfect", "helpful", "cool", "best"]
        negative_words = ["bad", "worst", "waste", "boring", "useless", "wrong", "fail", "slow", "annoying", "hate"]
        pos_score = sum(1 for word in positive_words if word in text_lower)
        neg_score = sum(1 for word in negative_words if word in text_lower)
        
        if is_offensive:
            sentiment = "negative"
        elif pos_score > neg_score:
            sentiment = "positive"
        elif neg_score > pos_score:
            sentiment = "negative"
        else:
            sentiment = "neutral"
            
        return sentiment, is_question, is_offensive

    def generate_predefined_reply(self, text: str, video_id: Optional[str] = None) -> str:
        text_lower = text.lower()
        if video_id and video_id in self.sheet_rules:
            for keyword, response in self.sheet_rules[video_id].items():
                if keyword in text_lower:
                    logger.info(f"Matched Google Sheet rule: Keyword '{keyword}' -> Reply '{response[:30]}...'")
                    return response
        
        for keyword, response in self.predefined_keywords.items():
            if keyword in text_lower:
                logger.info(f"Matched default local rule: Keyword '{keyword}' -> Reply '{response[:30]}...'")
                return response
        return self.default_reply

    async def generate_ai_reply(self, text: str, video_id: Optional[str] = None) -> str:
        if not self.client:
            return self.generate_predefined_reply(text, video_id)

        system_prompt = (
            "You are the owner of this YouTube channel.\n"
            "Reply professionally and politely to the following comment.\n"
            "Constraints:\n"
            "- Maximum 40 words.\n"
            "- Never argue.\n"
            "- Never use offensive language.\n"
            "- Keep replies friendly and support-oriented.\n"
            "- Reply in the same language as the commenter's comment.\n"
            "- Return ONLY the direct reply text, with no wrapping quotes, no introduction, and no extra text."
        )

        try:
            response = await self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": text}
                ],
                max_tokens=100,
                temperature=0.7
            )
            reply = response.choices[0].message.content.strip()
            if reply.startswith('"') and reply.endswith('"'):
                reply = reply[1:-1].strip()
            if reply.startswith("'") and reply.endswith("'"):
                reply = reply[1:-1].strip()
            return reply
        except Exception as e:
            logger.error(f"Error during LLM reply generation: {str(e)}. Falling back to predefined reply.")
            return self.generate_predefined_reply(text, video_id)

    def resolve_spintax(self, text: str) -> str:
        pattern = re.compile(r'\{([^{}]+)\}')
        while True:
            match = pattern.search(text)
            if not match:
                break
            choices = match.group(1).split('|')
            text = text.replace(match.group(0), random.choice(choices), 1)
        return text

    def humanize_reply_rules(self, reply_text: str, author_handle: str) -> str:
        reply = self.resolve_spintax(reply_text)
        handle = author_handle.strip()
        if handle and not handle.startswith("@"):
            if " " not in handle:
                handle = f"@{handle}"
        
        r = random.random()
        if handle and handle != "@Unknown" and "Unknown" not in handle:
            if r < 0.25:
                if not reply.startswith(handle):
                    reply = f"{handle} {reply}"
            elif r < 0.45:
                if not reply.endswith(handle):
                    reply = f"{reply} {handle}"

        emojis = ["😊", "👍", "🙌", "✨", "🎉", "❤️", "🔥", "💯", "🙏"]
        if random.random() < 0.45 and not any(e in reply for e in emojis):
            reply = f"{reply} {random.choice(emojis)}"
            
        if reply.endswith(".") and random.random() < 0.3:
            reply = reply[:-1] + "!"
        return reply.strip()

    async def rewrite_reply_with_llm(self, comment_text: str, reply_template: str) -> str:
        if not self.client:
            return reply_template

        system_prompt = (
            "You are a YouTube channel owner replying to a comment.\n"
            "You want to convey this message (the reply template):\n"
            f"'{reply_template}'\n\n"
            "Rewrite this reply template so that it sounds extremely natural, conversational, and tailored "
            "as a direct response to the user's comment, while keeping the core message/intent of the template completely intact.\n"
            "Constraints:\n"
            "- Do NOT output the template verbatim. Rephrase it uniquely.\n"
            "- Never say something like 'Thanks for the comment' unless the template implies it.\n"
            "- Do not include any HTML, quotes, or metadata in your output.\n"
            "- Return ONLY the rephrased reply text. Maximum 35 words."
        )

        try:
            response = await self.client.chat.completions.create(
                model=settings.LLM_MODEL,
                messages=[
                    {"role": "system", "content": system_prompt},
                    {"role": "user", "content": f"User's comment: {comment_text}"}
                ],
                max_tokens=80,
                temperature=0.8
            )
            reply = response.choices[0].message.content.strip()
            if reply.startswith('"') and reply.endswith('"'):
                reply = reply[1:-1].strip()
            if reply.startswith("'") and reply.endswith("'"):
                reply = reply[1:-1].strip()
            return reply
        except Exception as e:
            logger.error(f"Error rewriting reply with LLM: {str(e)}")
            return reply_template

    async def humanize_static_reply(self, comment_text: str, reply_template: str, author_handle: str) -> str:
        if settings.AUTO_REWRITE_REPLY and self.client:
            logger.info("Auto-rewriting static reply with LLM...")
            rewritten = await self.rewrite_reply_with_llm(comment_text, reply_template)
            if rewritten and rewritten != reply_template:
                return self.resolve_spintax(rewritten)
        
        logger.info("Applying rule-based humanization to static reply...")
        return self.humanize_reply_rules(reply_template, author_handle)

    async def generate_reply(self, text: str, mode: str = "predefined", video_id: Optional[str] = None) -> str:
        if mode == "ai":
            return await self.generate_ai_reply(text, video_id)
        return self.generate_predefined_reply(text, video_id)

reply_generator = ReplyGenerator()


# ──────────────────────────────────────────────────────────────────────
# 8. Core Selector Mappings & YouTube Service
# ──────────────────────────────────────────────────────────────────────

SELECTORS = {
    "comments_button": [
        "ytd-reel-video-renderer[is-active] button#comments-button",
        "ytd-reel-video-renderer button#comments-button",
        "button#comments-button",
        "ytd-button-renderer#comments-button button",
    ],
    "comments_panel": [
        "ytd-engagement-panel-section-list-renderer[target-id='engagement-panel-comments-section']",
        "ytd-engagement-panel-section-list-renderer",
        "#engagement-panel-comments-section",
        "ytd-engagement-panel-title-header-renderer",
    ],
    "comment_thread": [
        "ytd-comment-thread-renderer:not([is-sub-thread])",
        "ytd-comment-thread-renderer:not(#replies ytd-comment-thread-renderer)",
        "ytd-comment-view-model:not([is-reply])",
        "ytd-comment-renderer:not([is-reply])",
    ],
    "content": [
        "#content-text",
        "yt-attributed-string#content-text",
        ".comment-content",
    ],
    "author": [
        "#author-text",
        "#header-author span",
        ".comment-author",
    ],
    "reply_button": [
        "#reply-button-end button",
        "#reply-button-end",
        "ytd-button-renderer#reply-button-end button",
        "button[aria-label='Reply to this comment']",
        "button[aria-label='Reply']",
    ],
    "reply_textbox": [
        "div#contenteditable-root[contenteditable='true']",
        "div#contenteditable-root",
        "#placeholder-area #contenteditable-root",
        "ytd-commentbox #contenteditable-root",
        "ytd-comment-reply-dialog-renderer #contenteditable-root",
        "yt-user-mention-autosuggest-input #contenteditable-root",
        "div[role='textbox']",
        "[contenteditable='true']",
        "#simplebox-placeholder",
        "#placeholder-area",
        "ytd-commentbox #input-content",
        "ytd-commentbox",
    ],
    "submit_reply": [
        "ytd-commentbox #submit-button button",
        "ytd-commentbox #submit-button",
        "#buttons #submit-button button",
        "#buttons #submit-button",
        "ytd-button-renderer#submit-button button",
        "ytd-button-renderer#submit-button",
        "#submit-button button",
        "#submit-button",
    ],
    "view_replies_button": [
        "#more-replies-sub-thread button",
        "#more-replies button",
        "#more-replies-sub-thread",
        "#more-replies",
        "ytd-comment-replies-renderer #more-replies button",
    ],
    "creator_badge": [
        "ytd-author-comment-badge-renderer",
        "#author-comment-badge",
        ".badge-style-type-owner",
    ],
}

class YouTubeService:
    def __init__(self):
        self.is_running = False
        self.current_video_id = None
        self.total_runs = 0
        self.last_run_timestamp = None
        self.last_run_statistics = {}

    async def _find_locator(self, parent: Any, selector_keys: List[str]) -> Optional[Locator]:
        for selector in selector_keys:
            locator = parent.locator(selector)
            try:
                if await locator.count() > 0:
                    return locator.first
            except Exception:
                continue
        return None

    async def _wait_and_find_locator(self, parent: Any, selector_keys: List[str], timeout_ms: int = 5000) -> Optional[Locator]:
        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout_ms:
            for selector in selector_keys:
                try:
                    locator = parent.locator(selector)
                    if await locator.count() > 0:
                        first = locator.first
                        if await first.is_visible():
                            return first
                except Exception:
                    continue
            await asyncio.sleep(0.3)
        for selector in selector_keys:
            try:
                locator = parent.locator(selector)
                if await locator.count() > 0:
                    return locator.first
            except Exception:
                continue
        return None

    async def _get_text(self, parent: Any, selector_keys: List[str], default: str = "") -> str:
        locator = await self._find_locator(parent, selector_keys)
        if locator:
            try:
                text = await locator.inner_text()
                return text.strip()
            except Exception:
                pass
        return default

    async def open_browser(self) -> Page:
        _, page = await browser_manager.open_browser()
        return page

    async def ensure_signed_in(self, page: Page) -> bool:
        logger.info("Checking YouTube sign-in status...")
        await page.goto("https://www.youtube.com/", wait_until="domcontentloaded")
        await asyncio.sleep(2.0)

        try:
            accept_btn = page.locator("button:has-text('Accept all'), button:has-text('Accept All')")
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                await asyncio.sleep(1.0)
        except Exception:
            pass

        avatar = page.locator("button#avatar-btn, img#img[alt='Avatar image']")
        try:
            if await avatar.count() > 0:
                logger.info("User is already signed in to YouTube.")
                logger.info("Pausing for 25 seconds to allow you to switch accounts in the browser window if this is not the correct account...")
                for remaining in range(25, 0, -5):
                    logger.info(f"Continuing in {remaining} seconds...")
                    await asyncio.sleep(5.0)
                # Re-verify sign-in status after the wait
                if await avatar.count() > 0:
                    logger.info("Proceeding with the currently logged-in account.")
                    return True
        except Exception:
            pass

        logger.warning("User is NOT signed in. Navigating to Google sign-in page...")
        logger.info("Please sign in to your Google Account in the opened browser window.")
        await page.goto("https://accounts.google.com/ServiceLogin?service=youtube", wait_until="domcontentloaded")

        max_wait = 300
        poll_interval = 3
        waited = 0

        while waited < max_wait:
            await asyncio.sleep(poll_interval)
            waited += poll_interval
            current_url = page.url
            if "youtube.com" in current_url and "accounts.google.com" not in current_url and "signin" not in current_url:
                logger.info("Sign-in detected! Redirected back to YouTube.")
                await page.wait_for_load_state("networkidle")
                return True

        logger.error("Sign-in timeout exceeded (300s). Stopping automation.")
        return False

    async def goto_shorts(self, page: Page, video_id: str) -> bool:
        shorts_url = f"https://www.youtube.com/shorts/{video_id}"
        logger.info(f"Navigating to YouTube Short: {shorts_url}")
        await page.goto(shorts_url, wait_until="domcontentloaded")
        await asyncio.sleep(3.0)

        try:
            await page.wait_for_selector("ytd-reel-video-renderer, #shorts-player", timeout=15000)
            logger.info("YouTube Short loaded successfully.")
            return True
        except Exception as e:
            logger.warning(f"Timeout waiting for Shorts player: {str(e)}")
            return True

    async def open_comments_panel(self, page: Page) -> bool:
        logger.info("Opening comments panel...")
        comments_btn = await self._find_locator(page, SELECTORS["comments_button"])
        if not comments_btn:
            logger.warning("Comments button not found via primary selectors. Trying JS evaluation...")
            try:
                clicked = await page.evaluate("""
                    () => {
                        const buttons = document.querySelectorAll('button');
                        for (const btn of buttons) {
                            const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                            if (label.includes('comment')) {
                                btn.click();
                                return true;
                            }
                        }
                        return false;
                    }
                """)
                if clicked:
                    logger.info("Comments button clicked via JS fallback.")
                    await asyncio.sleep(2.0)
                    return True
            except Exception:
                pass
            logger.error("Could not find the comments button on this Short.")
            return False

        await human_click(page, comments_btn)
        await asyncio.sleep(2.0)

        try:
            panel = await self._find_locator(page, SELECTORS["comments_panel"])
            if panel:
                logger.info("Comments panel opened successfully.")
                return True
        except Exception:
            pass

        logger.info("Comments panel may be loading, proceeding...")
        await asyncio.sleep(2.0)
        return True

    async def sort_comments_by_newest(self, page: Page) -> bool:
        logger.info("Attempting to sort comments by Newest first...")
        try:
            sort_btn_selectors = [
                "yt-sort-filter-sub-menu-renderer",
                "#sort-menu",
                "button[aria-label='Sort comments']",
                "yt-dropdown-menu #label",
            ]
            sort_btn = None
            for selector in sort_btn_selectors:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible():
                    sort_btn = loc
                    break
            
            if not sort_btn:
                logger.warning("Sort menu button not found or not visible.")
                return False

            logger.info("Clicking Sort Menu button...")
            await sort_btn.click()
            await asyncio.sleep(1.0)

            newest_option_selectors = [
                "ytd-menu-service-item-renderer:has-text('Newest')",
                "tp-yt-paper-item:has-text('Newest')",
                "ytd-menu-navigation-item-renderer:has-text('Newest')",
                "yt-formatted-string:has-text('Newest')",
                "span:has-text('Newest')",
                "a:has-text('Newest')",
            ]
            newest_opt = None
            for selector in newest_option_selectors:
                loc = page.locator(selector).first
                if await loc.count() > 0 and await loc.is_visible():
                    newest_opt = loc
                    break
            
            if not newest_opt:
                logger.warning("Newest option not found by primary text matching. Trying fallback matching...")
                items = page.locator("ytd-menu-service-item-renderer, tp-yt-paper-item, yt-formatted-string")
                for idx in range(await items.count()):
                    item = items.nth(idx)
                    text = await item.inner_text()
                    if "newest" in text.lower():
                        newest_opt = item
                        break

            if newest_opt:
                logger.info("Clicking 'Newest' option...")
                await newest_opt.click()
                await asyncio.sleep(2.0)
                logger.info("Successfully requested comments sorted by Newest first.")
                return True
            else:
                logger.warning("Could not find the 'Newest' sort option in the menu.")
                return False
        except Exception as e:
            logger.warning(f"Error sorting comments: {e}")
            return False

    async def load_comments(self, page: Page, limit: int) -> int:
        logger.info(f"Loading comments (scrolling to fetch up to {limit} threads)...")
        panel = await self._find_locator(page, SELECTORS["comments_panel"])
        scroll_target = panel if panel else page

        threads_count = 0
        no_change_count = 0
        max_scrolls = 25

        for scroll_idx in range(max_scrolls):
            threads = page.locator(SELECTORS["comment_thread"][0])
            current_count = await threads.count()
            logger.info(f"Scroll {scroll_idx + 1}/{max_scrolls} - Loaded threads: {current_count}")

            if current_count >= limit:
                logger.info(f"Reached thread limit ({limit}). Stopping scrolls.")
                threads_count = current_count
                break

            if current_count == threads_count:
                no_change_count += 1
                if no_change_count >= 3:
                    logger.info("No more comments loading (end of list reached).")
                    break
            else:
                no_change_count = 0

            threads_count = current_count

            try:
                if panel:
                    await panel.evaluate("el => el.scrollTop += 600")
                else:
                    await human_scroll(page, 200, 500)
            except Exception:
                await human_scroll(page, 200, 500)

            await asyncio.sleep(random.uniform(1.0, 2.0))
        return threads_count

    async def is_creator_replied(self, thread: Locator) -> bool:
        # We only check for the creator badge inside the replies container
        replies_container = thread.locator("#replies, ytd-comment-replies-renderer")
        
        # Check if creator badge is already visible in replies container
        badge = replies_container.locator("ytd-author-comment-badge-renderer, #author-comment-badge, .badge-style-type-owner").first
        if await badge.count() > 0 and await badge.is_visible():
            logger.info("Found creator badge in already expanded replies.")
            return True

        # Try expanding replies to check
        view_replies_btn = None
        for selector in SELECTORS["view_replies_button"]:
            loc = thread.locator(selector).first
            if await loc.count() > 0 and await loc.is_visible():
                view_replies_btn = loc
                break

        if view_replies_btn:
            try:
                # Check button text to see if replies exist and can be expanded
                btn_text = await view_replies_btn.inner_text()
                if "reply" in btn_text.lower() or "replies" in btn_text.lower():
                    logger.info("Expanding replies to check for creator reply...")
                    await view_replies_btn.click()
                    # Wait up to 5 seconds (polling every 0.5s) for replies to load and the badge to be visible
                    for _ in range(10):
                        await asyncio.sleep(0.5)
                        badge = replies_container.locator("ytd-author-comment-badge-renderer, #author-comment-badge, .badge-style-type-owner").first
                        if await badge.count() > 0 and await badge.is_visible():
                            logger.info("Found creator reply in expanded thread.")
                            return True
            except Exception as e:
                logger.debug(f"Failed to expand replies: {e}")

        return False

    async def post_reply(self, page: Page, thread: Locator, reply_text: str) -> bool:
        """Clicks Reply on a comment, types the reply text, and verifies submission."""
        max_retries = 3

        for attempt in range(1, max_retries + 1):
            try:
                # Find and click the Reply button strictly inside the thread
                reply_btn = await self._wait_and_find_locator(thread, SELECTORS["reply_button"], timeout_ms=3000)
                if not reply_btn:
                    raise ValueError("Reply button not found inside the comment thread")

                logger.info(f"Attempt {attempt}: Clicking Reply button...")
                if attempt > 1:
                    logger.info("Attempt > 1: Using direct click for reliability...")
                    await reply_btn.click()
                else:
                    await human_click(page, reply_btn)
                await asyncio.sleep(random.uniform(1.0, 2.0))

                # Find the reply text input strictly inside the thread
                reply_textbox = await self._wait_and_find_locator(thread, SELECTORS["reply_textbox"], timeout_ms=5000)
                if not reply_textbox:
                    # Try placeholder inside the thread
                    placeholder = thread.locator("#simplebox-placeholder, #placeholder-area")
                    if await placeholder.count() > 0 and await placeholder.first.is_visible():
                        await placeholder.first.click()
                        await asyncio.sleep(1.0)
                        reply_textbox = await self._wait_and_find_locator(thread, SELECTORS["reply_textbox"], timeout_ms=3000)

                if not reply_textbox:
                    raise ValueError("Reply text input not found inside the comment thread")

                logger.info(f"Attempt {attempt}: Typing reply: '{reply_text[:50]}...'")
                await human_type(reply_textbox, reply_text, page=page)
                await asyncio.sleep(random.uniform(1.0, 2.0))

                # Identify active composer's submit button strictly inside the thread
                composer = thread.locator("ytd-commentbox, ytd-comment-reply-dialog-renderer")
                submit_btn = None
                if await composer.count() > 0:
                    submit_btn = await self._find_locator(composer.first, SELECTORS["submit_reply"])
                if not submit_btn:
                    submit_btn = await self._find_locator(thread, SELECTORS["submit_reply"])
                if not submit_btn:
                    raise ValueError("Submit button not found inside the comment thread composer")

                # Filter buttons to locate the active non-cancel button inside the thread
                buttons = thread.locator(SELECTORS["submit_reply"][0])
                count = await buttons.count()
                for idx in range(count):
                    btn = buttons.nth(idx)
                    text = await btn.inner_text()
                    is_visible = await btn.is_visible()
                    if is_visible and "cancel" not in text.lower():
                        submit_btn = btn
                        break

                if await submit_btn.is_disabled() or await submit_btn.get_attribute("disabled") is not None:
                    logger.warning("Submit button is disabled. Dispatched input events to enable it...")
                    await reply_textbox.focus()
                    await page.keyboard.press("Space")
                    await page.keyboard.press("Backspace")
                    await asyncio.sleep(1.0)

                api_success = False
                logger.info(f"Attempt {attempt}: Clicking Submit button...")

                # Listener for the comment creation network request
                try:
                    async with page.expect_response(
                        lambda response: "create_comment" in response.url and response.request.method == "POST",
                        timeout=10000
                    ) as response_info:
                        if attempt > 1:
                            logger.info("Attempt > 1: Using direct click on Submit button...")
                            await submit_btn.click()
                        else:
                            await human_click(page, submit_btn)

                    response = await response_info.value
                    if response.status == 200:
                        try:
                            res_json = await response.json()
                            if "error" not in res_json:
                                api_success = True
                                logger.info("YouTube backend API confirmed comment creation (HTTP 200 OK).")
                            else:
                                logger.warning(f"YouTube backend API returned error: {res_json.get('error')}")
                        except Exception:
                            api_success = True
                            logger.info("YouTube backend API returned HTTP 200 OK.")
                    else:
                        logger.warning(f"YouTube backend API returned HTTP status {response.status}")
                except Exception as api_err:
                    logger.warning(f"API confirmation listener note: {str(api_err)}")

                if not api_success:
                    raise ValueError("YouTube backend API did not confirm comment creation (no HTTP 200 OK response)")

                # VERIFICATION 1: Wait for the reply editor box to close/disappear
                logger.info("Waiting for YouTube to process reply and close editor box...")
                editor_closed = False
                for _ in range(10):
                    await asyncio.sleep(0.5)
                    try:
                        if not await reply_textbox.is_visible():
                            editor_closed = True
                            break
                    except Exception:
                        editor_closed = True
                        break

                if not editor_closed:
                    logger.warning("Reply editor box did not close.")

                # VERIFICATION 2: Verify that the reply is successfully rendered in the UI/DOM
                logger.info("Verifying reply visibility on the UI...")
                ui_verified = False
                search_text = reply_text.strip()
                sub_text = search_text[:30] if len(search_text) > 30 else search_text

                for check_attempt in range(6):
                    try:
                        reply_locator = thread.locator(f"text={sub_text}")
                        if await reply_locator.count() > 0:
                            ui_verified = True
                            logger.info("Successfully verified reply visibility in the UI!")
                            break

                        thread_text = await thread.inner_text()
                        if sub_text in thread_text:
                            ui_verified = True
                            logger.info("Verified reply visibility in thread text content.")
                            break
                    except Exception as check_err:
                        logger.debug(f"UI check exception on attempt {check_attempt + 1}: {check_err}")
                    await asyncio.sleep(0.5)

                if not ui_verified:
                    logger.warning("Could not visually confirm the reply in the UI/DOM after posting, but backend API confirmed success. Proceeding to prevent duplicates.")

                logger.info(f"Attempt {attempt}: Reply posted and verified successfully on both API and UI levels!" if ui_verified else f"Attempt {attempt}: Reply posted successfully on API level!")
                return True

            except Exception as e:
                logger.warning(f"Attempt {attempt} failed to post reply: {str(e)}")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                await asyncio.sleep(2.0)
        return False

    async def run_auto_reply(self) -> Dict[str, Any]:
        """Main orchestrator to process all videos and matching comments."""
        self.is_running = True
        await reply_generator.fetch_sheet_rules()

        if not reply_generator.sheet_rules:
            self.is_running = False
            logger.error("No rules found in Google Sheet. Nothing to process.")
            return {"status": "error", "message": "No rules found in Google Sheet"}

        video_ids = list(reply_generator.sheet_rules.keys())
        logger.info(f"Loaded rules for {len(video_ids)} video(s): {video_ids}")

        stats = {
            "total_comments": 0,
            "replied": 0,
            "skipped": 0,
            "failed": 0,
        }
        processed_details = []
        page = None

        try:
            page = await self.open_browser()
            if not await self.ensure_signed_in(page):
                raise ConnectionError("Failed to sign in to YouTube")

            for idx, video_id in enumerate(video_ids):
                if idx > 0:
                    transition_delay = random.uniform(15.0, 45.0)
                    logger.info(f"Pausing for {transition_delay:.1f} seconds before moving to next Short to prevent bot detection...")
                    await asyncio.sleep(transition_delay)

                self.current_video_id = video_id
                logger.info(f"=== Processing video {idx + 1}/{len(video_ids)} (ID: {video_id}) ===")

                if not await self.goto_shorts(page, video_id):
                    logger.error(f"Failed to load Short {video_id}, skipping.")
                    stats["failed"] += 1
                    continue

                watch_time = random.uniform(settings.MIN_WATCH_DELAY, settings.MAX_WATCH_DELAY)
                logger.info(f"Simulating watching Short {video_id} for {watch_time:.1f} seconds...")
                try:
                    await page.mouse.move(random.randint(100, 600), random.randint(100, 600), steps=5)
                except Exception:
                    pass
                await asyncio.sleep(watch_time)

                if not await self.open_comments_panel(page):
                    logger.error(f"Failed to open comments for Short {video_id}, skipping.")
                    stats["failed"] += 1
                    continue

                # Sort comments by Newest first to ensure we process the most recent comments
                await self.sort_comments_by_newest(page)

                limit = settings.MAX_REPLIES_PER_RUN
                total_loaded = await self.load_comments(page, limit)
                logger.info(f"Total comments loaded for {video_id}: {total_loaded}")

                threads_locator = page.locator(SELECTORS["comment_thread"][0])
                threads_count = await threads_locator.count()
                stats["total_comments"] += threads_count

                matching_comments = []
                logger.info(f"Scanning {threads_count} comment(s) for keyword matches...")

                for i in range(threads_count):
                    thread = threads_locator.nth(i)
                    text = await self._get_text(thread, SELECTORS["content"], "")
                    author = await self._get_text(thread, SELECTORS["author"], "Unknown")

                    if not text.strip():
                        logger.info(f"  Comment {i+1}: [empty text, skipping]")
                        continue

                    # Extra check: Skip thread replies / sub-threads
                    is_sub_thread = await thread.get_attribute("is-sub-thread")
                    is_reply = await thread.get_attribute("is-reply")
                    if is_sub_thread is not None or is_reply is not None:
                        logger.info(f"  Comment {i+1} by '{author}': [thread reply, skipping]")
                        stats["skipped"] += 1
                        continue

                    # Skip pinned comments
                    pinned_badge = thread.locator("ytd-pinned-comment-badge-renderer, ytw-pinned-comment-badge-renderer, #pinned-comment-badge > *")
                    if await pinned_badge.count() > 0:
                        logger.info(f"  Comment {i+1} by '{author}': [pinned comment, skipping]")
                        stats["skipped"] += 1
                        continue

                    text_normalized = " ".join(text.split())
                    text_lower = text_normalized.lower()
                    logger.info(f"  Comment {i+1} by '{author}': '{text_normalized[:80]}'")

                    import hashlib
                    comment_hash = hashlib.md5((author + text_normalized).encode("utf-8")).hexdigest()
                    comment_id = f"hash_{comment_hash}"

                    if comment_service.is_processed(video_id, comment_id):
                        logger.info("    -> Skipped (already processed in log)")
                        stats["skipped"] += 1
                        continue

                    if await self.is_creator_replied(thread):
                        logger.info("    -> Skipped (creator has already replied)")
                        comment_service.mark_processed(video_id, comment_id)
                        stats["skipped"] += 1
                        continue

                    matched_reply = None
                    if video_id in reply_generator.sheet_rules:
                        clean_text = re.sub(r'[^\w\s]', ' ', text_lower)
                        clean_text = " ".join(clean_text.split())

                        for keyword, reply_text in reply_generator.sheet_rules[video_id].items():
                            clean_keyword = re.sub(r'[^\w\s]', ' ', keyword.lower())
                            clean_keyword = " ".join(clean_keyword.split())

                            if clean_keyword and (clean_keyword in clean_text or clean_text in clean_keyword):
                                matched_reply = reply_text
                                logger.info(f"    -> MATCH: keyword '{clean_keyword}' => reply '{reply_text[:50]}'")
                                break

                    if not matched_reply:
                        sentiment, is_question, is_offensive = reply_generator.detect_sentiment_and_type(text)
                        if is_offensive:
                            logger.info("    -> Skipped (offensive)")
                            stats["skipped"] += 1
                            continue
                        # No keyword match and not offensive -> requires manual review
                        logger.info(f"    -> [MANUAL REVIEW REQUIRED] Comment by '{author}' requires a manual reply: '{text_normalized}'")
                        comment_service.mark_processed(video_id, comment_id)
                        comment_service.save_log(
                            video_id=video_id,
                            comment_id=comment_id,
                            author=author,
                            comment=text_normalized,
                            generated_reply=None,
                            status="manual_review",
                            reason="No matching keyword. Requires manual review."
                        )
                        stats["skipped"] += 1
                        continue
                    else:
                        matching_comments.append({
                            "index": i,
                            "thread": thread,
                            "author": author,
                            "text": text_normalized,
                            "comment_id": comment_id,
                            "reply_text": matched_reply,
                        })

                logger.info(f"Scan complete: {len(matching_comments)} comment(s) match keyword rules.")

                replies_count = 0
                for item in matching_comments:
                    if replies_count >= limit:
                        logger.info(f"Reached reply cap of {limit}. Stopping.")
                        break

                    author = item["author"]
                    text = item["text"]
                    comment_id = item["comment_id"]
                    reply_text = item["reply_text"]
                    thread = item["thread"]

                    humanized_reply = await reply_generator.humanize_static_reply(text, reply_text, author)
                    logger.info(f"Replying to '{author}': '{text[:50]}...' with: '{humanized_reply[:50]}...'")

                    success = await self.post_reply(page, thread, humanized_reply)

                    detail = {
                        "comment_id": comment_id,
                        "author": author,
                        "comment_text": text,
                        "generated_reply": humanized_reply,
                        "status": "replied" if success else "failed",
                        "reason": None if success else "Failed to submit reply",
                        "timestamp": datetime.now().isoformat(),
                    }
                    processed_details.append(detail)

                    if success:
                        replies_count += 1
                        stats["replied"] += 1
                        comment_service.save_log(video_id, comment_id, author, text, humanized_reply, "replied")
                        
                        if replies_count % 3 == 0:
                            extra_pause = random.uniform(30.0, 90.0)
                            logger.info(f"Simulating human distraction pause for {extra_pause:.1f} seconds...")
                            await asyncio.sleep(extra_pause)
                        else:
                            await random_delay()
                    else:
                        stats["failed"] += 1
                        comment_service.save_log(video_id, comment_id, author, text, humanized_reply, "failed", "Submit failed")

            self.total_runs += 1
            self.last_run_timestamp = datetime.now().isoformat()
            self.last_run_statistics = stats

            return {
                "status": "success",
                "total_comments": stats["total_comments"],
                "replied": stats["replied"],
                "skipped": stats["skipped"] + stats["failed"],
                "details": processed_details,
            }
        except Exception as e:
            logger.error(f"Fatal error during auto-reply run: {str(e)}")
            return {"status": "error", "message": str(e)}
        finally:
            self.is_running = False
            self.current_video_id = None
            await browser_manager.close_browser()

youtube_service = YouTubeService()


# ──────────────────────────────────────────────────────────────────────
# 9. FastAPI App Definition & Routing Models
# ──────────────────────────────────────────────────────────────────────

from contextlib import asynccontextmanager

@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Starting YouTube Comment Auto Reply Bot Service...")
    yield
    logger.info("Stopping YouTube Comment Auto Reply Bot Service...")
    await browser_manager.close_browser()

app = FastAPI(
    title="YouTube Comment Auto Reply Bot API",
    description="A single-file consolidated FastAPI bot that replies to YouTube comments.",
    version="1.0.0",
    lifespan=lifespan
)

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# Pydantic response models
class CommentDetail(BaseModel):
    comment_id: str
    author: str
    comment_text: str
    generated_reply: Optional[str] = None
    status: str
    reason: Optional[str] = None
    timestamp: str

class ReplyResponse(BaseModel):
    status: str = "success"
    total_comments: int = 0
    replied: int = 0
    skipped: int = 0
    details: Optional[List[CommentDetail]] = None
    message: Optional[str] = None

class BotStatusResponse(BaseModel):
    is_running: bool = False
    current_video_id: Optional[str] = None
    total_runs: int = 0
    last_run_timestamp: Optional[str] = None
    last_run_statistics: Optional[Dict[str, Any]] = None

# Router Group
router = APIRouter(prefix="/youtube", tags=["YouTube Auto Reply"])

@router.post("/reply", response_model=ReplyResponse)
async def auto_reply():
    """Triggers the YouTube Shorts comment auto-reply flow."""
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

app.include_router(router)

@app.get("/")
async def root():
    return {
        "message": "Welcome to the YouTube Comment Auto Reply Bot API",
        "documentation": "/docs",
        "status_endpoint": "/youtube/status"
    }

# Startup Runner
if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="YouTube Auto Reply Bot")
    parser.add_argument("--server", action="store_true", help="Run as FastAPI server")
    parser.add_argument("--port", type=int, default=8000, help="Port for the FastAPI server")
    args = parser.parse_args()
    
    if args.server:
        logger.info(f"Starting Uvicorn server on http://127.0.0.1:{args.port}")
        uvicorn.run(app, host="127.0.0.1", port=args.port)
    else:
        logger.info("Starting YouTube Comment Auto Reply Bot flow directly...")
        try:
            result = asyncio.run(youtube_service.run_auto_reply())
            logger.info("Auto-reply execution finished.")
            print(json.dumps(result, indent=2))
        except Exception as e:
            logger.error(f"Fatal error during execution: {e}")
            sys.exit(1)
