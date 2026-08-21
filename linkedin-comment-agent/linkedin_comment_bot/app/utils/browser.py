"""Playwright browser lifecycle management with persistent Chrome profile."""

from contextlib import asynccontextmanager
from typing import AsyncGenerator, Optional

import json
from pathlib import Path

from playwright.async_api import BrowserContext, Page, Playwright, async_playwright

from linkedin_comment_bot.app.config import Settings, get_settings
from linkedin_comment_bot.app.utils.logger import get_logger

logger = get_logger()


class BrowserManager:
    """Manages Playwright persistent browser context."""

    def __init__(self, settings: Optional[Settings] = None) -> None:
        self.settings = settings or get_settings()
        self._playwright: Optional[Playwright] = None
        self._context: Optional[BrowserContext] = None
        self._page: Optional[Page] = None

    @property
    def context(self) -> BrowserContext:
        if self._context is None:
            raise RuntimeError("Browser context is not open. Call open_browser() first.")
        return self._context

    @property
    def page(self) -> Page:
        if self._page is None:
            raise RuntimeError("Browser page is not open. Call open_browser() first.")
        return self._page

    async def open_browser(self) -> Page:
        """Launch persistent Chromium context using existing Chrome profile and stored cookies."""
        if self._context is not None:
            return self._page  # type: ignore[return-value]

        profile_path = self.settings.chrome_profile_path
        logger.info("Opening browser with profile: %s", profile_path)

        self._playwright = await async_playwright().start()
        
        launch_args = {
            "user_data_dir": profile_path,
            "headless": self.settings.headless,
            "channel": "chrome" if self.settings.browser_channel == "chrome" else None,
            "viewport": {"width": 1366, "height": 768},
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--no-first-run",
                "--no-default-browser-check",
            ],
            "ignore_default_args": ["--enable-automation"],
        }
        
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(**launch_args)
        except Exception as e:
            if "Executable doesn't exist at" in str(e) or "playwright install" in str(e):
                logger.info("Playwright browser binaries not found. Installing chromium automatically...")
                import sys
                from playwright.__main__ import main
                orig_argv = sys.argv
                sys.argv = ["playwright", "install", "chromium"]
                try:
                    main()
                    logger.info("Chromium installed successfully. Retrying browser launch...")
                except Exception as install_err:
                    logger.error("Failed to automatically install chromium: %s", install_err)
                    raise e
                finally:
                    sys.argv = orig_argv
                
                # Retry launching chromium context
                self._context = await self._playwright.chromium.launch_persistent_context(**launch_args)
            else:
                raise e

        if self._context.pages:
            self._page = self._context.pages[0]
        else:
            self._page = await self._context.new_page()

        await self.load_cookies()

        logger.info("Browser opened successfully")
        return self._page

    async def save_cookies(self, file_path: Optional[Path] = None) -> None:
        """Save current context cookies to a JSON file."""
        path = file_path or self.settings.cookies_file_path
        if self._context:
            try:
                cookies = await self._context.cookies()
                path.parent.mkdir(parents=True, exist_ok=True)
                path.write_text(json.dumps(cookies, indent=2), encoding="utf-8")
                logger.info("Saved %d cookies to %s", len(cookies), path)
            except Exception as exc:
                logger.warning("Failed to save cookies to %s: %s", path, exc)

    async def load_cookies(self, file_path: Optional[Path] = None) -> None:
        """Load cookies from a JSON file into current context."""
        path = file_path or self.settings.cookies_file_path
        if self._context and path.exists():
            try:
                cookies = json.loads(path.read_text(encoding="utf-8"))
                if isinstance(cookies, list) and len(cookies) > 0:
                    await self._context.add_cookies(cookies)
                    logger.info("Loaded %d cookies from %s", len(cookies), path)
            except Exception as exc:
                logger.warning("Failed to load cookies from %s: %s", path, exc)

    async def close_browser(self) -> None:
        """Close browser context and Playwright."""
        if self._context:
            await self.save_cookies()
            await self._context.close()
            self._context = None
            self._page = None
        if self._playwright:
            await self._playwright.stop()
            self._playwright = None
        logger.info("Browser closed")

    async def save_failure_html(self, prefix: str) -> Optional[str]:
        """Save page HTML on failure for debugging."""
        if self._page is None:
            return None
        from datetime import datetime

        self.settings.failure_html_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.settings.failure_html_dir / f"{prefix}_{timestamp}.html"
        content = await self._page.content()
        path.write_text(content, encoding="utf-8")
        logger.info("Saved failure HTML to %s", path)
        return str(path)

    async def take_screenshot(self, name: str) -> Optional[str]:
        """Capture screenshot if enabled."""
        if not self.settings.enable_screenshots or self._page is None:
            return None
        from datetime import datetime

        self.settings.screenshot_dir.mkdir(parents=True, exist_ok=True)
        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        path = self.settings.screenshot_dir / f"{name}_{timestamp}.png"
        await self._page.screenshot(path=str(path), full_page=False)
        logger.info("Screenshot saved: %s", path)
        return str(path)


@asynccontextmanager
async def browser_session(
    settings: Optional[Settings] = None,
) -> AsyncGenerator[BrowserManager, None]:
    """Context manager for browser lifecycle."""
    manager = BrowserManager(settings)
    try:
        await manager.open_browser()
        yield manager
    finally:
        await manager.close_browser()
