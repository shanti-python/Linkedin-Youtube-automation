import os
# Force Qt applications (and browsers using Qt integrations) to use X11/XWayland
# to prevent crashes on Gnome Wayland when the Qt wayland plugin is missing.
os.environ["QT_QPA_PLATFORM"] = "xcb"
os.environ["XDG_SESSION_TYPE"] = "x11"
os.environ.pop("WAYLAND_DISPLAY", None)

from typing import Tuple
from playwright.async_api import async_playwright, Playwright, BrowserContext, Page
try:
    from playwright_stealth import Stealth
except ImportError:
    Stealth = None
from youtube_reply_bot.app.config import settings
from youtube_reply_bot.app.utils.logger import logger

class BrowserManager:
    def __init__(self):
        self.playwright: Playwright = None
        self.context: BrowserContext = None
        self.page: Page = None
        self.stealth = Stealth() if Stealth else None

    async def open_browser(self) -> Tuple[BrowserContext, Page]:
        """Launches Chromium using a persistent browser context."""
        if self.context:
            logger.info("Browser already open. Returning existing context.")
            return self.context, self.page
            
        logger.info("Initializing Playwright...")
        self.playwright = await async_playwright().start()
        
        # Determine Chrome profile path
        profile_path = settings.CHROME_PROFILE_PATH
        if not profile_path:
            profile_path = settings.get_absolute_path("replies/chrome_profile")
            logger.info(f"No CHROME_PROFILE_PATH provided in settings. Using default local path: {profile_path}")
        else:
            profile_path = settings.get_absolute_path(profile_path)
            logger.info(f"Using Chrome profile path: {profile_path}")
            
        # Ensure profile path directory exists
        os.makedirs(profile_path, exist_ok=True)
        
        # Launch persistent context
        # Arguments to help bypass automation detection
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
            channel="chrome" if os.path.exists("/usr/bin/google-chrome") else None,  # Use system Chrome if available, fallback to chromium
            args=args,
            viewport={"width": 1280, "height": 800},
            ignore_default_args=["--enable-automation"]
        )
        
        # Grant clipboard-read/write permissions
        await self.context.grant_permissions(["clipboard-read", "clipboard-write"])
        
        # Set default timeout
        self.context.set_default_timeout(30000)  # 30 seconds

        # Get first page or open new page
        pages = self.context.pages
        if pages:
            self.page = pages[0]
        else:
            self.page = await self.context.new_page()
            
        if self.stealth:
            try:
                await self.stealth.apply_stealth_async(self.page)
            except Exception:
                pass
        
        # Avoid webdriver property detection and apply comprehensive stealth shims
        await self.page.add_init_script("""
            // 1. Delete and redefine navigator.webdriver
            delete Object.prototype.webdriver;
            Object.defineProperty(navigator, 'webdriver', { get: () => undefined });

            // 2. Mock Chrome runtime properties
            window.chrome = {
                runtime: {},
                loadTimes: function() {},
                csi: function() {},
                app: {}
            };

            // 3. Mock languages (force standard english)
            Object.defineProperty(navigator, 'languages', {
                get: () => ['en-US', 'en']
            });

            // 4. Overwrite plugins to look like standard desktop Chrome
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

# Global instance of BrowserManager
browser_manager = BrowserManager()
