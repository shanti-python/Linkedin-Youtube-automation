"""LinkedIn page interaction service using Playwright."""

import asyncio
import hashlib
import random
import re
from typing import Any, List, Optional

from playwright.async_api import Locator, Page, TimeoutError as PlaywrightTimeout

from linkedin_comment_bot.app.config import Settings, get_settings
from linkedin_comment_bot.app.services.comment_reader import CommentData, CommentExtractionResult
from linkedin_comment_bot.app.utils.human_typing import (
    human_type,
    pause_before_submit,
    random_mouse_movement,
    random_scroll,
    random_wait,
)
from linkedin_comment_bot.app.utils.logger import get_logger
from linkedin_comment_bot.app.utils.retry import retry_async

logger = get_logger()


class LinkedInService:
    """Handles LinkedIn post navigation, comment extraction, and reply actions."""

    # ── New LinkedIn UI (obfuscated CSS, Aug 2026+) ───────────────────────────
    # Comment containers now carry the comment URN in their id/componentkey.
    # All interaction selectors rely on aria-label / role / data-* attributes.

    # Section detection: new LazyColumn container OR first comment element
    COMMENT_SECTION_SELECTORS = [
        "div[data-component-type='LazyColumn']",
        "div[id^='replaceableComment_urn:li:comment:']",
        # Legacy class-based fallbacks (pre-Aug 2026)
        "div.comments-comments-list--cr",
        "div.comments-comments-list",
        "article.comments-comment-entity",
    ]

    LOAD_MORE_SELECTORS = [
        "button:has-text('Load more comments')",
        "button:has-text('View more comments')",
        "button:has-text('Show more comments')",
        "button.comments-comments-list__load-more-comments-button",
    ]

    # Reply button: LinkedIn now uses generic aria-label="Reply" per comment
    REPLY_BUTTON_SELECTORS = [
        "button[aria-label='Reply']",
        "button[aria-label^='Reply to']",
        "button.comments-comment-social-bar__reply-action-button--cr",
        "button.comments-comment-social-bar__reply-action-button",
    ]

    # Text editor: LinkedIn migrated from Quill (ql-editor) to TipTap/ProseMirror
    COMMENT_BOX_SELECTORS = [
        # New TipTap editor (confirmed in DOM)
        "div.tiptap[role='textbox']",
        "div.ProseMirror[role='textbox']",
        "div[role='textbox'][aria-label*='reply']",
        "div[role='textbox'][aria-label*='Reply']",
        # Generic textbox fallback
        "div[role='textbox']",
        # Legacy Quill fallbacks
        "div.comments-comment-box--reply div.ql-editor",
        "div.ql-editor[aria-placeholder*='reply']",
        "div[contenteditable='true'][role='textbox']",
        "div.ql-editor[contenteditable='true']",
        "div.ql-editor",
    ]

    SUBMIT_SELECTORS = [
        # New UI: post/reply button appears after typing in the textbox.
        # It has aria-label containing 'post' or just text 'Post'.
        "button[aria-label='Post reply']",
        "button[aria-label='Post comment']",
        "button[aria-label='Post']",
        "button[aria-label='Reply']",  # submit context button
        # Legacy class-based selectors
        "button.comments-comment-box__submit-button--cr",
        "button[class*='comments-comment-box__submit-button']",
        "button.artdeco-button--primary:has-text('Reply')",
        "button.artdeco-button--primary:has-text('Post')",
        "button:has-text('Post')",
    ]

    def __init__(self, page: Page, settings: Optional[Settings] = None) -> None:
        self.page = page
        self.settings = settings or get_settings()
        self._account_name: str = self.settings.linkedin_account_name

    async def is_logged_in(self) -> bool:
        """Check if current session is logged into LinkedIn."""
        current_url = self.page.url
        if "linkedin.com/feed" in current_url or "linkedin.com/in/" in current_url:
            return True

        logged_in_selectors = [
            "nav.global-nav",
            "#global-nav",
            "a.global-nav__primary-link",
            ".feed-identity-module",
            "img.global-nav__me-photo",
        ]
        for sel in logged_in_selectors:
            try:
                if await self.page.locator(sel).first.is_visible(timeout=2000):
                    return True
            except Exception:
                continue

        return False

    async def login(self, email: Optional[str] = None, password: Optional[str] = None, manager: Optional[Any] = None) -> bool:
        """Log into LinkedIn using saved or provided email and password."""
        target_email = email or self.settings.linkedin_email
        target_password = password or self.settings.linkedin_password

        if not target_email or not target_password:
            logger.info("LinkedIn credentials not configured. Opening login page for manual authentication...")
            await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
            logger.info("Please enter your email and password manually in the browser window to log in...")
            
            import asyncio
            # Wait up to 5 minutes (300 seconds) for the user to log in manually
            for _ in range(150):
                if await self.is_logged_in():
                    logger.info("Manual login detected successfully!")
                    if manager:
                        await manager.save_cookies()
                    return True
                await asyncio.sleep(2)
            
            logger.error("Manual login timeout (5 minutes) exceeded.")
            return False

        logger.info("Navigating to LinkedIn login page...")
        await self.page.goto("https://www.linkedin.com/login", wait_until="domcontentloaded", timeout=60000)
        await random_wait(1.5, 3.0)

        if await self.is_logged_in():
            logger.info("Already logged in.")
            if manager:
                await manager.save_cookies()
            return True

        username_field = None
        for sel in ["#username", "input[name='session_key']", "input#username"]:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=3000):
                    username_field = loc
                    break
            except PlaywrightTimeout:
                continue

        if not username_field:
            logger.error("Could not locate username/email input on login page")
            return False

        await human_type(username_field, target_email, self.settings)
        await random_wait(0.5, 1.2)

        password_field = None
        for sel in ["#password", "input[name='session_password']", "input#password"]:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=3000):
                    password_field = loc
                    break
            except PlaywrightTimeout:
                continue

        if not password_field:
            logger.error("Could not locate password input on login page")
            return False

        await human_type(password_field, target_password, self.settings)
        await pause_before_submit(self.settings)

        submit_btn = None
        for sel in ["button[type='submit']", "button.btn__primary--large", "button[aria-label='Sign in']"]:
            try:
                loc = self.page.locator(sel).first
                if await loc.is_visible(timeout=3000):
                    submit_btn = loc
                    break
            except PlaywrightTimeout:
                continue

        if not submit_btn:
            logger.error("Could not locate Sign in submit button on login page")
            return False

        await submit_btn.click()
        logger.info("Submitted login form, awaiting authentication...")
        await random_wait(3.0, 5.0)

        if "checkpoint" in self.page.url:
            logger.error("LinkedIn security verification/CAPTCHA encountered")
            if manager:
                await manager.save_failure_html("login_checkpoint")
            raise RuntimeError("LinkedIn security checkpoint (2FA/CAPTCHA) triggered. Verification required.")

        logged_in = await self.is_logged_in()
        if logged_in:
            logger.info("LinkedIn login successful!")
            if manager:
                await manager.save_cookies()
            return True
        else:
            logger.error("LinkedIn login check failed after submission")
            if manager:
                await manager.save_failure_html("login_failed")
            return False

    async def ensure_logged_in(self, manager: Optional[Any] = None) -> bool:
        """Verify LinkedIn authentication status; log in and save cookies if not logged in."""
        logger.info("Checking LinkedIn authentication status...")
        await self.page.goto("https://www.linkedin.com/feed", wait_until="domcontentloaded", timeout=30000)
        await random_wait(1.5, 3.0)

        if await self.is_logged_in():
            logger.info("Session is active and authenticated.")
            if manager:
                await manager.save_cookies()
            return True

        logger.info("Session not authenticated. Initiating auto-login...")
        return await self.login(manager=manager)

    def _parse_time_text_to_days(self, time_text: str) -> float:
        """Parses LinkedIn relative time strings to number of days."""
        if not time_text:
            return 9999.0
        
        text = time_text.lower().strip()
        text = text.replace("ago", "").replace("edited", "").strip()
        
        if text in ('now', 'just now', '1s') or 'second' in text:
            return 0.0
            
        match = re.search(r'(\d+)\s*([a-zA-Z]+)', text)
        if not match:
            return 9999.0
            
        val = float(match.group(1))
        unit = match.group(2)
        
        if unit.startswith('s'):
            return val / (24.0 * 3600.0)
        elif unit.startswith('mi') or unit == 'm':
            # 'm' is minutes, 'mo' is months
            return val / (24.0 * 60.0)
        elif unit.startswith('h'):
            return val / 24.0
        elif unit.startswith('d'):
            return val
        elif unit.startswith('w'):
            return val * 7.0
        elif unit == 'mo' or unit.startswith('mon'):
            return val * 30.0
        elif unit.startswith('y'):
            return val * 365.0
            
        return 9999.0

    async def fetch_account_post_urls(self, max_posts: int = 15, max_days: Optional[int] = None) -> List[str]:
        """Navigate to the logged in user's profile activity page and discover post URLs."""
        logger.info("Scanning account's recent posts...")
        target_urls = [
            "https://www.linkedin.com/in/me/recent-activity/shares/",
            "https://www.linkedin.com/in/me/recent-activity/all/",
        ]

        post_urls: List[str] = []

        for activity_url in target_urls:
            try:
                logger.info("Navigating to activity feed: %s", activity_url)
                await self.page.goto(activity_url, wait_until="domcontentloaded", timeout=60000)
                await random_wait(2.0, 4.0)
                await self._dismiss_popups()

                for _ in range(5):
                    await random_scroll(self.page, "down")
                    await random_wait(1.0, 2.0)

                extracted = await self.page.evaluate(
                    """() => {
                        const posts = [];
                        const containers = document.querySelectorAll('.feed-shared-update-v2, [data-urn*="urn:li:activity:"], [data-urn*="urn:li:share:"], [data-urn*="urn:li:ugcPost:"], .profile-creator-shared-feed-update__container, .feed-shared-update');
                        
                        containers.forEach(container => {
                            let url = '';
                            const urn = container.getAttribute('data-urn');
                            if (urn && (urn.includes('urn:li:activity:') || urn.includes('urn:li:share:') || urn.includes('urn:li:ugcPost:'))) {
                                const match = urn.match(/urn:li:(activity|share|ugcPost):\\d+/);
                                if (match) {
                                    url = `https://www.linkedin.com/feed/update/${match[0]}/`;
                                }
                            }
                            
                            if (!url) {
                                const anchor = container.querySelector('a[href*="/feed/update/urn:li:"], a[href*="/posts/"]');
                                if (anchor) {
                                    let href = anchor.getAttribute('href');
                                    if (href) {
                                        if (href.startsWith('/')) href = 'https://www.linkedin.com' + href;
                                        url = href.split('?')[0];
                                    }
                                }
                            }
                            
                            if (!url) return;
                            
                            let timeText = '';
                            const timeEl = container.querySelector('.feed-shared-actor__sub-description, time, .update-components-actor__sub-description, .feed-shared-actor__sub-description-container');
                            if (timeEl) {
                                timeText = timeEl.textContent || '';
                            }
                            
                            if (!timeText) {
                                const anchors = container.querySelectorAll('a');
                                for (let a of anchors) {
                                    const txt = (a.textContent || '').trim();
                                    if (/^\\d+\\s*(d|h|w|mo|y|m|s|day|hour|week|month|year)s?\\s*(ago)?$/i.test(txt)) {
                                        timeText = txt;
                                        break;
                                    }
                                }
                            }
                            
                            if (!timeText) {
                                const allElements = container.querySelectorAll('*');
                                for (let el of allElements) {
                                    if (el.children.length === 0) {
                                        const txt = (el.textContent || '').trim();
                                        if (/^\\d+\\s*(d|h|w|mo|y|m|s|day|hour|week|month|year)s?\\s*(ago)?$/i.test(txt)) {
                                            timeText = txt;
                                            break;
                                        }
                                    }
                                }
                            }
                            
                            posts.push({
                                url: url,
                                timeText: timeText.trim()
                            });
                        });
                        
                        if (posts.length === 0) {
                            const anchors = document.querySelectorAll('a[href*="/feed/update/urn:li:"], a[href*="/posts/"]');
                            anchors.forEach(a => {
                                let href = a.getAttribute('href');
                                if (!href) return;
                                if (href.startsWith('/')) href = 'https://www.linkedin.com' + href;
                                const clean = href.split('?')[0];
                                
                                let timeText = '';
                                let text = (a.textContent || '').trim();
                                if (/^\\d+\\s*(d|h|w|mo|y|m|s|day|hour|week|month|year)s?\\s*(ago)?$/i.test(text)) {
                                    timeText = text;
                                }
                                
                                posts.push({
                                    url: clean,
                                    timeText: timeText
                                });
                            });
                        }
                        
                        return posts;
                    }"""
                )

                logger.info("Extracted %d raw post elements from feed page: %s", len(extracted), activity_url)
                for item in extracted:
                    url = item["url"]
                    time_text = item["timeText"]
                    
                    if url not in post_urls:
                        if max_days is not None:
                            age_in_days = self._parse_time_text_to_days(time_text)
                            if age_in_days > max_days:
                                logger.info(
                                    "Skipping post %s: age (%s, approx %.1f days) exceeds max_days (%d)",
                                    url, time_text or "unknown", age_in_days, max_days
                                )
                                continue
                            else:
                                logger.info(
                                    "Including post %s: age (%s, approx %.1f days) within max_days (%d)",
                                    url, time_text or "unknown", age_in_days, max_days
                                )
                        post_urls.append(url)

                if len(post_urls) >= max_posts:
                    break
            except Exception as exc:
                logger.warning("Error fetching activity feed from %s: %s", activity_url, exc)
                continue

        logger.info("Discovered %d post URLs for the account matching the criteria (max_days=%s)", len(post_urls), str(max_days) if max_days is not None else "None")
        return post_urls[:max_posts]

    @retry_async(exceptions=(PlaywrightTimeout, Exception), max_attempts=3)
    async def open_post(self, post_url: str) -> None:
        logger.info("Opening post: %s", post_url)
        await self.page.goto(post_url, wait_until="domcontentloaded", timeout=60000)
        await random_wait(2.0, 4.0)
        await self._dismiss_popups()
        await self._wait_for_comments_section()

    async def _dismiss_popups(self) -> None:
        popup_selectors = [
            "button.artdeco-modal__dismiss",
            "button[aria-label='Dismiss']",
            "button:has-text('Not now')",
            "button:has-text('Skip')",
        ]
        for selector in popup_selectors:
            try:
                btn = self.page.locator(selector).first
                if await btn.is_visible(timeout=1500):
                    await btn.click()
                    await random_wait(0.5, 1.0)
            except PlaywrightTimeout:
                continue

    async def _wait_for_comments_section(self) -> None:
        for selector in self.COMMENT_SECTION_SELECTORS:
            try:
                await self.page.wait_for_selector(selector, timeout=15000)
                logger.info("Comments section loaded via: %s", selector)
                return
            except PlaywrightTimeout:
                continue
        logger.warning("Comments section selector not found; continuing anyway")

    async def expand_comments(self) -> None:
        """Click all 'Load more comments' buttons (top-level only)."""
        logger.info("Expanding comments...")
        for _ in range(20):
            clicked = False
            for selector in self.LOAD_MORE_SELECTORS:
                try:
                    btn = self.page.locator(selector).first
                    if await btn.is_visible(timeout=2000):
                        await btn.scroll_into_view_if_needed()
                        await random_wait(0.5, 1.2)
                        await btn.click()
                        clicked = True
                        await random_wait(1.0, 2.5)
                        break
                except PlaywrightTimeout:
                    continue
            if not clicked:
                break

    async def expand_replies(self) -> None:
        """Expand nested reply threads if explicitly requested."""
        logger.info("Expanding nested replies...")
        reply_expand_selectors = [
            "button.comments-replies-list__replies-button",
            "button:has-text('replies')",
            "button:has-text('Repl')",
            "span.comments-comment-item__show-replies",
        ]
        for _ in range(15):
            clicked = False
            for selector in reply_expand_selectors:
                buttons = self.page.locator(selector)
                count = await buttons.count()
                for i in range(count):
                    try:
                        btn = buttons.nth(i)
                        if await btn.is_visible(timeout=1000):
                            await btn.scroll_into_view_if_needed()
                            await btn.click()
                            clicked = True
                            await random_wait(0.8, 1.5)
                    except PlaywrightTimeout:
                        continue
            if not clicked:
                break

    async def scroll_comments(self) -> None:
        """Scroll to load lazy-loaded comments."""
        logger.info("Scrolling to load all comments...")
        for _ in range(12):
            await random_scroll(self.page, "down")
            await random_mouse_movement(self.page)
            await random_wait(0.5, 1.2)

    async def extract_comments(self, top_level_only: bool = True) -> CommentExtractionResult:
        """Extract top-level comments from the post."""
        await self._detect_account_name()
        post_content = await self._extract_post_content()
        comments = await self._parse_comment_elements()
        if top_level_only:
            comments = [c for c in comments if c.nested_level == 0]
        logger.info("Extracted %d top-level comments", len(comments))
        return CommentExtractionResult(
            comments=comments,
            post_content=post_content,
            account_name=self._account_name,
        )

    async def _detect_account_name(self) -> None:
        if self._account_name and self._account_name.lower() not in ("me", "you"):
            return

        # 1. Try finding the name from image avatars (alt attributes)
        try:
            selectors = [
                "img.global-nav__me-photo",
                "img[class*='global-nav__me-photo']",
                "img.comments-quick-comment-box__user-img",
                "img.comments-comment-box__avatar",
                "img[class*='comments-avatar']",
                "img[class*='comments-comment-box']",
                "img[class*='quick-comment-box']"
            ]
            for sel in selectors:
                img = self.page.locator(sel).first
                if await img.is_visible(timeout=1000):
                    alt = await img.get_attribute("alt")
                    if alt:
                        name = alt
                        # Remove common suffixes/prefixes
                        for suffix in ["'s photo", "'s profile picture", "'s profile", " photo", " profile", " avatar", "'s avatar", " picture"]:
                            if name.lower().endswith(suffix):
                                name = name[:-len(suffix)]
                        for prefix in ["photo of ", "profile photo of ", "profile of ", "avatar of "]:
                            if name.lower().startswith(prefix):
                                name = name[len(prefix):]
                        name = name.strip()
                        if name and name.lower() not in ("me", "you", "home", "my network", "messaging", "notifications", "jobs"):
                            self._account_name = name
                            logger.info("Detected logged-in user name from avatar (%s): %s", sel, self._account_name)
                            return
        except Exception:
            pass

        # 2. Try finding from profile link on the feed page left rail
        try:
            profile_selectors = [
                "a.ember-view.profile-card-name",
                "div.identity-headline",
                "div[class*='feed-identity-module'] a.ember-view",
            ]
            for sel in profile_selectors:
                el = self.page.locator(sel).first
                if await el.is_visible(timeout=1000):
                    text = await el.inner_text()
                    if text:
                        text = text.strip()
                        if text and text.lower() not in ("me", "you", "home", "my network", "messaging", "notifications", "jobs"):
                            self._account_name = text
                            logger.info("Detected logged-in user name from profile rail: %s", self._account_name)
                            return
        except Exception:
            pass

        # 3. Fallback: Click "Me" navigation dropdown to read name
        try:
            me_btn = self.page.locator("button.global-nav__primary-link--me, button#global-nav-typeahead-trigger, button:has-text('Me'), button:has-text('me')").first
            if await me_btn.is_visible(timeout=1000):
                await me_btn.click()
                await self.page.wait_for_timeout(1000)
                
                name_selectors = [
                    "div.global-nav__me-username",
                    "div[class*='me-username']",
                    "a.global-nav__me-profile-link",
                    "div.global-nav__me-profile-card div.t-16"
                ]
                for name_sel in name_selectors:
                    el = self.page.locator(name_sel).first
                    if await el.is_visible(timeout=1000):
                        name_text = await el.inner_text()
                        if name_text:
                            name_text = name_text.strip()
                            if name_text and name_text.lower() not in ("me", "you", "home", "my network", "messaging", "notifications", "jobs"):
                                self._account_name = name_text
                                logger.info("Detected logged-in user name from Me dropdown: %s", self._account_name)
                                await me_btn.click() # Close dropdown
                                return
                await me_btn.click() # Close dropdown if not matched
        except Exception as e:
            logger.warning("Failed to detect name from Me dropdown: %s", e)

        # Last resort fallback
        self._account_name = self.settings.linkedin_account_name or "You"

    async def _extract_post_content(self) -> str:
        # New LinkedIn UI uses different selectors; also try JS extraction
        selectors = [
            "div.feed-shared-update-v2__description span.break-words",
            "div.feed-shared-text span.break-words",
            "div.update-components-text span.break-words",
        ]
        for selector in selectors:
            try:
                el = self.page.locator(selector).first
                if await el.is_visible(timeout=3000):
                    return (await el.inner_text()).strip()
            except PlaywrightTimeout:
                continue
        # JS fallback: grab the post text from the feed detail
        try:
            text = await self.page.evaluate(
                """() => {
                    // Look for the primary post text container
                    const candidates = [
                        document.querySelector('[data-test-id*="main-feed-activity"] [role="region"]'),
                        document.querySelector('[aria-label="Feed detail update"]'),
                    ];
                    for (const c of candidates) {
                        if (!c) continue;
                        // Grab all paragraph/span text, skip comment sections
                        const textEl = c.querySelector('p, [dir="ltr"]');
                        if (textEl && textEl.textContent.trim().length > 20) {
                            return textEl.textContent.trim();
                        }
                    }
                    return '';
                }"""
            )
            if text:
                return text.strip()
        except Exception:
            pass
        return ""

    async def _parse_comment_elements(self) -> List[CommentData]:
        """Extract comments using JavaScript for LinkedIn's new obfuscated-class UI."""
        # Strategy 1: New UI — containers have id^="replaceableComment_urn:li:comment:"
        raw_comments = await self.page.evaluate(
            """() => {
                const results = [];

                // ── Strategy A: New LinkedIn UI (Aug 2026+) ─────────────────
                const containers = document.querySelectorAll(
                    '[id^="replaceableComment_urn:li:comment:"]'
                );

                containers.forEach((container, idx) => {
                    const id = container.id;
                    const urn = id.replace('replaceableComment_', '');

                    // Determine nesting: if the URN contains a reply URN pattern it's nested
                    // Top-level comments have id like: replaceableComment_urn:li:comment:(urn:li:activity:XXX,YYY)
                    // Replies would be nested inside another replaceableComment
                    let nestedLevel = 0;
                    let parent = container.parentElement;
                    while (parent) {
                        if (parent.id && parent.id.startsWith('replaceableComment_urn:li:comment:')) {
                            nestedLevel++;
                        }
                        parent = parent.parentElement;
                    }

                    // Author: first link to /in/ profile
                    let author = '';
                    let profileUrl = '';
                    const profileLinks = container.querySelectorAll('a[href*="/in/"]');
                    for (const a of profileLinks) {
                        const txt = (a.textContent || '').trim();
                        if (txt && txt.length > 1) {
                            // Clean up name: LinkedIn appends connection degree and badge text
                            author = txt.split('\\n')[0]
                                .replace(/,\\s*Open to work.*/i, '')
                                .replace(/,\\s*Hiring.*/i, '')
                                .replace(/\\s*(1st|2nd|3rd|\\d+\\+).*$/i, '')
                                .replace(/•.*/g, '')
                                .trim();
                            profileUrl = a.href;
                            break;
                        }
                    }

                    // Comment text: look for the longest leaf-span that isn't the author name
                    let commentText = '';
                    const spans = container.querySelectorAll('span, p');
                    let maxLen = 0;
                    for (const el of spans) {
                        if (el.children.length > 0) continue; // skip parents
                        const txt = (el.textContent || '').trim();
                        if (txt.length > maxLen && txt !== author && !txt.includes('•') &&
                            !txt.startsWith('Python') && txt.length < 2000 && txt.length > 5) {
                            // Skip likely metadata (short, no spaces)
                            if (txt.split(' ').length > 1 || txt.length > 20) {
                                maxLen = txt.length;
                                commentText = txt;
                            }
                        }
                    }

                    // Time: look for <time> element or aria-label with time hint
                    let timeStr = '';
                    const timeEl = container.querySelector('time');
                    if (timeEl) {
                        timeStr = timeEl.getAttribute('datetime') || timeEl.textContent.trim();
                    }
                    if (!timeStr) {
                        // Try aria-label patterns like "2 hours ago"
                        const allEls = container.querySelectorAll('[aria-label]');
                        for (const el of allEls) {
                            const label = el.getAttribute('aria-label') || '';
                            if (/\\d+\\s*(min|hour|day|week|month|year|second|h|d|w|m)/i.test(label)) {
                                timeStr = label;
                                break;
                            }
                        }
                    }

                    // Reaction count from aria-label
                    let reactionCount = 0;
                    const reactionBtns = container.querySelectorAll('button[aria-label*="Reaction"]');
                    for (const btn of reactionBtns) {
                        const label = btn.getAttribute('aria-label') || '';
                        const m = label.match(/(\\d+)\\s*Reaction/);
                        if (m) { reactionCount = parseInt(m[1], 10); break; }
                    }

                    // Reply button present?
                    const hasReplyBtn = !!container.querySelector('button[aria-label="Reply"], button[aria-label^="Reply to"]');

                    results.push({
                        urn,
                        author,
                        profileUrl,
                        text: commentText,
                        timeStr,
                        reactionCount,
                        replyCount: 0,
                        nestedLevel,
                        index: idx,
                        hasReplyBtn,
                        isDeleted: commentText.toLowerCase().includes('comment removed') ||
                                   commentText.toLowerCase().includes('deleted'),
                        isHidden: commentText.toLowerCase().includes('unavailable'),
                    });
                });

                // ── Strategy B: Legacy class-based UI ───────────────────────
                if (results.length === 0) {
                    const legacyEls = document.querySelectorAll(
                        'article.comments-comment-entity, div.comments-comment-item'
                    );
                    legacyEls.forEach((el, idx) => {
                        const authorEl = el.querySelector(
                            'span.comments-comment-meta__description-title, span.comments-post-meta__name-text'
                        );
                        const textEl = el.querySelector('span.comments-comment-item__main-content');
                        const timeEl = el.querySelector('time, span.comments-comment-item__timestamp');
                        const linkEl = el.querySelector('a[href*="/in/"]');
                        results.push({
                            urn: el.getAttribute('data-urn') || el.id || String(idx),
                            author: (authorEl && authorEl.textContent.trim()) || 'Unknown',
                            profileUrl: (linkEl && linkEl.href) || '',
                            text: (textEl && textEl.textContent.trim()) || '',
                            timeStr: (timeEl && timeEl.textContent.trim()) || '',
                            reactionCount: 0,
                            replyCount: 0,
                            nestedLevel: el.className.includes('reply') ? 1 : 0,
                            index: idx,
                            hasReplyBtn: !!el.querySelector('button[aria-label*="Reply"]'),
                            isDeleted: false,
                            isHidden: false,
                        });
                    });
                }

                return results;
            }"""
        )

        if not raw_comments:
            logger.warning("JavaScript comment extraction found 0 comments")
            return []

        logger.info("JavaScript found %d raw comment containers", len(raw_comments))

        comments: List[CommentData] = []
        for item in raw_comments:
            try:
                urn = item.get("urn", "")
                author = item.get("author", "Unknown") or "Unknown"
                comment_id = hashlib.md5(urn.encode()).hexdigest()[:12] if urn else hashlib.md5(
                    f"{author}::{item.get('text', '')}".encode()
                ).hexdigest()[:12]

                # Check already_replied by looking at the text of nested reply authors
                already_replied = await self._check_already_replied_js(urn)

                comments.append(CommentData(
                    comment_id=comment_id,
                    author=author.strip(),
                    author_profile_url=item.get("profileUrl", ""),
                    text=item.get("text", "").strip(),
                    time=item.get("timeStr", "").strip(),
                    reaction_count=item.get("reactionCount", 0),
                    already_replied=already_replied,
                    reply_count=item.get("replyCount", 0),
                    nested_level=item.get("nestedLevel", 0),
                    is_deleted=item.get("isDeleted", False),
                    is_hidden=item.get("isHidden", False),
                    element_index=item.get("index", 0),
                ))
            except Exception as exc:
                logger.warning("Failed to build CommentData from JS result: %s", exc)
        return comments

    async def _check_already_replied_js(self, urn: str) -> bool:
        """Check via JS if the current account has already replied to a comment URN."""
        account = self._account_name.lower()
        if not account or account in ("you", "me"):
            return False
        try:
            return await self.page.evaluate(
                """([urn, accountName]) => {
                    const container = document.querySelector(
                        '[id^="replaceableComment_" + urn] , [id="replaceableComment_' + urn + '"]'
                    );
                    // Also try by componentkey
                    const containers = Array.from(
                        document.querySelectorAll('[id^="replaceableComment_urn:li:comment:"]')
                    ).filter(el => el.id.includes(urn));
                    for (const c of containers) {
                        const links = c.querySelectorAll('a[href*="/in/"]');
                        for (const a of links) {
                            if ((a.textContent || '').toLowerCase().includes(accountName)) return true;
                            const ariaLabel = a.getAttribute('aria-label') || '';
                            if (ariaLabel.toLowerCase().includes(accountName)) return true;
                        }
                    }
                    return false;
                }""",
                [urn, account]
            )
        except Exception:
            return False

    async def _parse_single_comment(self, el: Locator, index: int) -> Optional[CommentData]:
        """Legacy Locator-based parser (used as fallback if JS extraction fails)."""
        text = await self._safe_inner_text(el, [
            "span.comments-comment-item__main-content",
            "section.comments-comment-entity__content span",
            "span.feed-shared-main-content",
        ])
        if not text:
            text = await self._safe_inner_text(el, ["span", "p"])

        is_deleted = "comment removed" in text.lower() or "this comment has been deleted" in text.lower()
        is_hidden = "comment unavailable" in text.lower()

        author = await self._safe_inner_text(el, [
            "span.comments-comment-meta__description-title",
            "span.comments-post-meta__name-text",
        ]) or "Unknown"

        profile_url = await self._safe_attr(el, "a[href*='/in/']", "href") or ""
        time_str = await self._safe_inner_text(el, ["time", "span.comments-comment-item__timestamp"]) or ""
        reaction_count = 0
        reply_count = 0
        nested_level = await self._get_nested_level(el)
        already_replied = await self.already_replied(el)
        comment_id = await self._generate_comment_id(el, author, text, index)

        return CommentData(
            comment_id=comment_id,
            author=author.strip(),
            author_profile_url=profile_url,
            text=text.strip(),
            time=time_str.strip(),
            reaction_count=reaction_count,
            already_replied=already_replied,
            reply_count=reply_count,
            nested_level=nested_level,
            is_deleted=is_deleted,
            is_hidden=is_hidden,
            element_index=index,
        )

    async def _get_nested_level(self, el: Locator) -> int:
        try:
            cls = await el.get_attribute("class") or ""
            if "comments-comment-entity--reply" in cls:
                return 1
            parent_classes = await el.evaluate(
                """el => {
                    let depth = 0;
                    let node = el.parentElement;
                    while (node) {
                        if ((node.id || '').startsWith('replaceableComment_urn:li:comment:')) depth++;
                        if (node.className && node.className.includes('reply')) depth++;
                        node = node.parentElement;
                    }
                    return depth;
                }"""
            )
            return min(int(parent_classes), 3)
        except Exception:
            return 0

    async def already_replied(self, comment_el: Locator) -> bool:
        """Check if our account has already replied under this comment (legacy Locator path)."""
        account = self._account_name.lower()
        if not account or account in ("you", "me"):
            return False
        try:
            # Check nested replaceableComment divs for our account name
            nested = comment_el.locator("[id^='replaceableComment_urn:li:comment:']")
            count = await nested.count()
            for i in range(count):
                reply_el = nested.nth(i)
                links = reply_el.locator("a[href*='/in/']")
                lcount = await links.count()
                for j in range(lcount):
                    txt = (await links.nth(j).inner_text()).strip().lower()
                    if account in txt:
                        return True
        except Exception:
            pass
        return False

    async def click_reply(self, comment_index: int, author: Optional[str] = None, text: Optional[str] = None) -> bool:
        """Click the Reply button for a comment by index.

        Strategy for the new LinkedIn UI:
        1. Collect all visible Reply buttons on the page (one per comment).
        2. Click the one at comment_index position.
        3. Fallback: scroll to any container matching the comment URN/author.
        """
        self._active_comment_el = None

        # Collect all reply buttons on page ordered by DOM position
        reply_btn_selectors = [
            "button[aria-label='Reply']",
            "button[aria-label^='Reply to']",
            "button.comments-comment-social-bar__reply-action-button--cr",
            "button.comments-comment-social-bar__reply-action-button",
        ]

        for selector in reply_btn_selectors:
            loc = self.page.locator(selector)
            total = await loc.count()
            if total == 0:
                continue

            logger.info("Found %d reply buttons using selector '%s'", total, selector)

            # Try the button at comment_index; if out of range use the last one
            target_idx = min(comment_index, total - 1)
            btn = loc.nth(target_idx)

            try:
                if await btn.is_visible(timeout=3000):
                    await btn.scroll_into_view_if_needed()
                    await random_wait(0.5, 1.0)
                    await random_mouse_movement(self.page)
                    await btn.click()
                    await random_wait(0.8, 1.5)
                    logger.info("Clicked reply button at index %d (author: %s)", target_idx, author)
                    return True
            except PlaywrightTimeout:
                pass
            except Exception as e:
                logger.debug("Error clicking reply button: %s", e)

        logger.warning("Reply button not found for comment (index: %d, author: %s)", comment_index, author)
        return False

    async def type_reply(self, reply_text: str) -> bool:
        """Type reply using human-like typing."""
        # Wait briefly for the reply box to animate into view after clicking Reply
        await asyncio.sleep(1.0)

        # Strategy 1: Look for the reply box (comments-comment-box--reply) anywhere on page
        # since LinkedIn renders it as a sibling after the comment, not always inside it.
        for reply_box_sel in [
            "div.comments-comment-box--reply div.ql-editor",
            "div.comments-comment-box--reply div[contenteditable='true']",
        ]:
            try:
                boxes = self.page.locator(reply_box_sel)
                count = await boxes.count()
                if count > 0:
                    box = boxes.last  # use the last opened reply box
                    if await box.is_visible(timeout=3000):
                        await box.scroll_into_view_if_needed()
                        await box.click()
                        await asyncio.sleep(0.3)
                        await human_type(box, reply_text, self.settings)
                        logger.info("Typed reply using reply-box selector: %s", reply_box_sel)
                        return True
            except PlaywrightTimeout:
                continue

        # Strategy 2: Try finding the box inside the active comment element
        if hasattr(self, "_active_comment_el") and self._active_comment_el is not None:
            for selector in self.COMMENT_BOX_SELECTORS:
                try:
                    box = self._active_comment_el.locator(selector).first
                    if await box.is_visible(timeout=3000):
                        await box.scroll_into_view_if_needed()
                        await box.click()
                        await asyncio.sleep(0.3)
                        await human_type(box, reply_text, self.settings)
                        logger.info("Typed reply using active-comment-el selector: %s", selector)
                        return True
                except PlaywrightTimeout:
                    continue

        # Strategy 3: Fallback to page-wide search (use last visible, which is the newly opened one)
        for selector in self.COMMENT_BOX_SELECTORS:
            try:
                boxes = self.page.locator(selector)
                count = await boxes.count()
                for i in range(count - 1, -1, -1):  # iterate in reverse; last one is the reply box
                    box = boxes.nth(i)
                    placeholder = await box.get_attribute("data-placeholder") or ""
                    aria_placeholder = await box.get_attribute("aria-placeholder") or ""
                    is_reply = "reply" in placeholder.lower() or "reply" in aria_placeholder.lower()
                    if await box.is_visible(timeout=2000):
                        if is_reply or count == 1:
                            await box.scroll_into_view_if_needed()
                            await box.click()
                            await asyncio.sleep(0.3)
                            await human_type(box, reply_text, self.settings)
                            logger.info("Typed reply using page-wide fallback selector: %s (idx=%d)", selector, i)
                            return True
            except PlaywrightTimeout:
                continue
        logger.warning("Comment text box not found")
        return False

    async def submit_reply(self) -> bool:
        """Submit the typed reply."""
        await pause_before_submit(self.settings)
        
        # Try finding the submit button inside the active comment element first
        if hasattr(self, "_active_comment_el") and self._active_comment_el is not None:
            for selector in self.SUBMIT_SELECTORS:
                try:
                    btn = self._active_comment_el.locator(selector).first
                    if await btn.is_visible(timeout=3000):
                        is_disabled = await btn.is_disabled()
                        if is_disabled:
                            logger.info("Submit button disabled, pausing briefly...")
                            await random_wait(0.8, 1.5)
                        await btn.scroll_into_view_if_needed()
                        await btn.click()
                        logger.info("Clicked reply submit button inside active comment using selector: %s", selector)
                        await random_wait(2.0, 4.0)
                        return True
                except PlaywrightTimeout:
                    continue
                except Exception as exc:
                    logger.warning("Error clicking submit button (%s) inside active comment: %s", selector, exc)
                    continue

        # Fallback to page-wide search
        for selector in self.SUBMIT_SELECTORS:
            try:
                loc = self.page.locator(selector)
                count = await loc.count()
                for i in range(count - 1, -1, -1):
                    btn = loc.nth(i)
                    if await btn.is_visible(timeout=1500):
                        is_disabled = await btn.is_disabled()
                        if is_disabled:
                            logger.info("Submit button disabled, pausing briefly...")
                            await random_wait(0.8, 1.5)
                        await btn.scroll_into_view_if_needed()
                        await btn.click()
                        logger.info("Clicked reply submit button using selector: %s", selector)
                        await random_wait(2.0, 4.0)
                        return True
            except PlaywrightTimeout:
                continue
            except Exception as exc:
                logger.warning("Error clicking submit button (%s): %s", selector, exc)
                continue
        logger.warning("Submit button not found")
        return False

    async def _safe_inner_text(self, parent: Locator, selectors: List[str]) -> str:
        for selector in selectors:
            try:
                el = parent.locator(selector).first
                if await el.count() > 0 and await el.is_visible(timeout=1000):
                    return (await el.inner_text()).strip()
            except PlaywrightTimeout:
                continue
        return ""

    async def _safe_attr(self, parent: Locator, selector: str, attr: str) -> str:
        try:
            el = parent.locator(selector).first
            if await el.count() > 0:
                value = await el.get_attribute(attr)
                return value or ""
        except PlaywrightTimeout:
            pass
        return ""

    @staticmethod
    def _parse_count(text: str) -> int:
        digits = re.sub(r"[^\d]", "", text)
        return int(digits) if digits else 0

    async def _generate_comment_id(
        self, el: Locator, author: str, text: str, index: int
    ) -> str:
        for attr in ("data-id", "data-urn", "id"):
            try:
                value = await el.get_attribute(attr)
                if value and not (attr == "id" and value.startswith("ember")):
                    return hashlib.md5(value.encode()).hexdigest()[:12]
            except Exception:
                continue
        try:
            url = self.page.url
        except Exception:
            url = ""
        payload = f"{url}::{author}::{text}"
        return hashlib.md5(payload.encode()).hexdigest()[:12]
