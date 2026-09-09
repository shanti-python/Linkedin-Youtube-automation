import asyncio
import random
import re
from typing import List, Dict, Any, Optional, Tuple
from playwright.async_api import Page, Locator
from youtube_reply_bot.app.config import settings
from youtube_reply_bot.app.utils.logger import logger
from youtube_reply_bot.app.utils.browser import browser_manager
from youtube_reply_bot.app.utils.helpers import extract_video_id
from youtube_reply_bot.app.utils.human_typing import (
    random_delay, human_type, human_click, human_scroll
)
from youtube_reply_bot.app.services.reply_generator import reply_generator, strip_emojis
from youtube_reply_bot.app.services.comment_service import comment_service
from youtube_reply_bot.app.utils.comment_publish import (
    dump_response_keys,
    is_create_comment_url,
    is_public_moderation_status,
    parse_create_comment_response,
)

# ───────────────────────────────────────────────
# YouTube Shorts DOM Selectors (regular youtube.com)
# ───────────────────────────────────────────────
SELECTORS = {
    # Button to open the comments panel on the Shorts page
    "comments_button": [
        "button[aria-label='Comments']",
        "#comments-button button",
        "ytd-button-renderer#comments-button button",
        "#comments-button",
        "button:has(> .yt-spec-button-shape-next__icon path[d*='M21,6'])",
    ],

    # The comments panel / engagement panel that slides in
    "comments_panel": [
        "ytd-engagement-panel-section-list-renderer[target-id='engagement-panel-comments-section']",
        "#panels ytd-engagement-panel-section-list-renderer",
        "ytd-engagement-panel-section-list-renderer",
    ],

    "comment_thread": [
        "ytd-comment-thread-renderer:not([is-sub-thread])",
        "ytd-comment-thread-renderer:not(#replies ytd-comment-thread-renderer)",
        "ytd-comment-view-model:not([is-reply])",
        "ytd-comment-renderer:not([is-reply])",
    ],

    # The comment body renderer inside a thread
    "comment_view": [
        "ytd-comment-view-model",
        "ytd-comment-renderer",
    ],

    # Author name
    "author": [
        "#author-text",
        "span.ytd-comment-view-model .yt-core-attributed-string--link-inherit-color",
        "#header-author h3 a span",
        "h3 a span",
        "#author-text span",
    ],

    # Comment text content
    "content": [
        "#content-text",
        "yt-attributed-string#content-text",
        "ytd-comment-view-model #content-text",
        "#content-text .yt-core-attributed-string",
        "span.yt-core-attributed-string",
    ],

    # Reply button on each comment
    "reply_button": [
        "#reply-button-end button",
        "#reply-button-end ytd-button-renderer button",
        "ytd-button-renderer#reply-button-end button",
        "button[aria-label*='Reply']",
        "button[aria-label='Reply']",
        "ytd-button-renderer#reply-button-end",
        "#reply-button-end",
        "button:has-text('Reply')",
    ],

    # Reply input text area (appears after clicking Reply)
    "reply_textbox": [
        "#contenteditable-root",
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

    # Submit reply button (strictly inside the active commentbox composer)
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

    # Replies section / expand button
    "view_replies_button": [
        "#more-replies-sub-thread button",
        "#more-replies button",
        "#more-replies-sub-thread",
        "#more-replies",
        "ytd-comment-replies-renderer #more-replies button",
    ],

    # Creator badge (to detect creator already replied)
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

    # ─── Helpers ──────────────────────────────────

    async def _find_locator(self, parent: Any, selector_keys: List[str]) -> Optional[Locator]:
        """Tries multiple selectors sequentially and returns the first matching locator."""
        for selector in selector_keys:
            locator = parent.locator(selector)
            try:
                count = await locator.count()
                if count > 0:
                    return locator.first
            except Exception:
                continue
        return None

    async def _wait_and_find_locator(self, parent: Any, selector_keys: List[str], timeout_ms: int = 5000) -> Optional[Locator]:
        """Waits up to timeout_ms for any selector in selector_keys to become available and visible."""
        start_time = asyncio.get_event_loop().time()
        while (asyncio.get_event_loop().time() - start_time) * 1000 < timeout_ms:
            for selector in selector_keys:
                try:
                    locator = parent.locator(selector)
                    count = await locator.count()
                    if count > 0:
                        first = locator.first
                        if await first.is_visible():
                            return first
                except Exception:
                    continue
            await asyncio.sleep(0.3)
        # Fallback: return any matching element that exists
        for selector in selector_keys:
            try:
                locator = parent.locator(selector)
                if await locator.count() > 0:
                    return locator.first
            except Exception:
                continue
        return None

    async def _get_text(self, parent: Any, selector_keys: List[str], default: str = "") -> str:
        """Extracts inner text using selector fallbacks."""
        locator = await self._find_locator(parent, selector_keys)
        if locator:
            try:
                text = await locator.inner_text()
                return text.strip()
            except Exception:
                pass
        return default

    # ─── Browser & Login ──────────────────────────

    async def open_browser(self) -> Page:
        """Launches the persistent browser."""
        _, page = await browser_manager.open_browser()
        return page

    async def ensure_signed_in(self, page: Page) -> bool:
        """
        Navigates to youtube.com and checks login state.
        If not signed in, navigates to Google login and pauses for manual sign-in.
        Returns True when the user is signed in, False if timeout.
        """
        logger.info("Checking YouTube sign-in status...")
        await page.goto("https://www.youtube.com/", wait_until="domcontentloaded")
        await asyncio.sleep(2.0)

        # Accept cookies dialog if present
        try:
            accept_btn = page.locator("button:has-text('Accept all'), button:has-text('Accept All')")
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                await asyncio.sleep(1.0)
        except Exception:
            pass

        # Check if signed in by looking for the avatar button
        avatar = page.locator("button#avatar-btn, img#img[alt='Avatar image']")
        try:
            if await avatar.count() > 0:
                logger.info("User is already signed in to YouTube.")
                return True
        except Exception:
            pass

        # Not signed in — navigate to Google sign-in
        logger.warning("User is NOT signed in. Navigating to Google sign-in page...")
        logger.info("Please sign in to your Google Account in the opened browser window.")
        await page.goto("https://accounts.google.com/ServiceLogin?service=youtube", wait_until="domcontentloaded")

        # Wait up to 120 seconds for user to finish sign-in
        max_wait = 120
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

        logger.error("Sign-in timeout exceeded (120s). Stopping automation.")
        return False

    # ─── Shorts Navigation ────────────────────────

    async def goto_shorts(self, page: Page, video_id: str) -> bool:
        """Navigates directly to a YouTube Short by its video ID."""
        shorts_url = f"https://www.youtube.com/shorts/{video_id}"
        logger.info(f"Navigating to YouTube Short: {shorts_url}")
        await page.goto(shorts_url, wait_until="domcontentloaded")
        await asyncio.sleep(3.0)

        # Wait for the Shorts player to be ready
        try:
            await page.wait_for_selector("ytd-reel-video-renderer, #shorts-player", timeout=15000)
            logger.info("YouTube Short loaded successfully.")
            return True
        except Exception as e:
            logger.warning(f"Timeout waiting for Shorts player: {str(e)}")
            # Still return True — the page might have loaded but with different selectors
            return True

    async def open_comments_panel(self, page: Page) -> bool:
        """Clicks the comments button on the Shorts page to open the comments panel."""
        logger.info("Opening comments panel...")
        
        # Find and click the comments button
        comments_btn = await self._find_locator(page, SELECTORS["comments_button"])
        if not comments_btn:
            # Fallback: try to find by the comments icon shape or aria-label
            logger.warning("Comments button not found via primary selectors. Trying JS evaluation...")
            try:
                clicked = await page.evaluate("""
                    () => {
                        // Look for buttons with comments-related aria-labels
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

        # Wait for the comments panel to appear
        try:
            panel = await self._find_locator(page, SELECTORS["comments_panel"])
            if panel:
                logger.info("Comments panel opened successfully.")
                return True
        except Exception:
            pass

        # Even if the panel locator isn't found, comments might still be loading
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

    # ─── Comment Loading ──────────────────────────

    async def load_comments(self, page: Page, limit: int) -> int:
        """Scrolls the comments panel to load comments."""
        logger.info(f"Loading comments (scrolling to fetch up to {limit} threads)...")

        # Find the scrollable comments container
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

            # Scroll inside the panel
            try:
                if panel:
                    await panel.evaluate("el => el.scrollTop += 600")
                else:
                    await human_scroll(page, 200, 500)
            except Exception:
                await human_scroll(page, 200, 500)

            await asyncio.sleep(random.uniform(1.0, 2.0))

        return threads_count

    # ─── Creator Reply Check ──────────────────────

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

    # ─── Reply Posting ────────────────────────────

    async def verify_comment_permalink_public(
        self,
        page: Page,
        video_id: str,
        comment_id: str,
        reply_text: str,
    ) -> bool:
        """
        Open the comment permalink in a fresh logged-out Chromium.
        Author-only / held comments will not appear for a guest session.
        """
        if not comment_id:
            return False

        guest_browser = None
        guest_context = None
        try:
            if not browser_manager.playwright:
                raise RuntimeError("Playwright is not running")

            guest_browser = await browser_manager.playwright.chromium.launch(
                headless=True,
                args=["--disable-blink-features=AutomationControlled", "--no-sandbox"],
            )
            guest_context = await guest_browser.new_context(
                viewport={"width": 1280, "height": 800},
                locale="en-US",
            )
            gpage = await guest_context.new_page()
            await browser_manager.stealth.apply_stealth_async(gpage)

            permalink = f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
            logger.info(f"Verifying public permalink: {permalink}")
            await gpage.goto(permalink, wait_until="domcontentloaded")
            await asyncio.sleep(3.5)

            try:
                accept = gpage.locator("button:has-text('Accept all'), button:has-text('Accept All')")
                if await accept.count() > 0:
                    await accept.first.click()
                    await asyncio.sleep(1.0)
            except Exception:
                pass

            avatar = gpage.locator("button#avatar-btn")
            if await avatar.count() > 0 and await avatar.first.is_visible():
                logger.error("Permalink check opened a signed-in session; aborting public check.")
                return False

            html = await gpage.content()
            body = (await gpage.locator("body").inner_text()).lower()
            needle = re.sub(r"[^\w\s]", " ", reply_text.lower())
            needle = " ".join(needle.split())[:40]
            comment_id_present = comment_id in html or comment_id.split(".")[-1] in html
            text_ok = bool(needle) and needle in re.sub(r"[^\w\s]", " ", body)

            if comment_id_present or text_ok:
                logger.info("Permalink public check passed.")
                return True

            logger.error(
                "Permalink public check FAILED — comment is not visible to logged-out users "
                "(held for review / spam filter / author-only)."
            )
            return False
        except Exception as e:
            logger.error(f"Permalink public verification error: {e}")
            return False
        finally:
            try:
                if guest_context:
                    await guest_context.close()
            except Exception:
                pass
            try:
                if guest_browser:
                    await guest_browser.close()
            except Exception:
                pass

    async def post_reply(self, page: Page, thread: Locator, reply_text: str, video_id: str = "") -> bool:
        """Clicks Reply on a comment, types the reply text, and verifies submission."""
        reply_text = strip_emojis(reply_text)
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

                # If element has contenteditable child, target that directly
                try:
                    editable_child = reply_textbox.locator("[contenteditable='true']")
                    if await editable_child.count() > 0:
                        reply_textbox = editable_child.first
                except Exception:
                    pass

                logger.info(f"Attempt {attempt}: Typing reply: '{reply_text[:50]}...'")
                typed = await human_type(reply_textbox, reply_text, page=page)
                if typed != reply_text.strip():
                    raise ValueError(
                        f"Composer text mismatch before submit (got '{typed}', expected '{reply_text.strip()}')"
                    )
                await asyncio.sleep(random.uniform(0.8, 1.5))

                # Locate active composer container
                composer_locator = thread.locator("ytd-commentbox, ytd-comment-reply-dialog-renderer, ytd-comment-simplebox-renderer").first

                # Find Submit button inside composer using a highly robust selector matching
                submit_btn = None
                
                # We want to find a button inside composer that has "submit-button" ID or text matching "Reply"/"Comment"
                # and does NOT have "cancel" in its ID or text
                candidate_selectors = [
                    "ytd-button-renderer#submit-button button",
                    "#submit-button button",
                    "button[aria-label='Reply']",
                    "button[aria-label='Comment']",
                    "ytd-button-renderer#submit-button",
                    "#submit-button",
                    "button:has-text('Reply')",
                    "button:has-text('Comment')"
                ]
                
                for sel in candidate_selectors:
                    loc = composer_locator.locator(sel)
                    if await loc.count() > 0:
                        # Inspect all matches for this selector to find the correct non-cancel button
                        for idx in range(await loc.count()):
                            candidate = loc.nth(idx)
                            if await candidate.is_visible():
                                # Verify it's not the cancel button
                                is_cancel = False
                                try:
                                    is_cancel = await candidate.evaluate("""el => {
                                        const parentRenderer = el.closest('ytd-button-renderer');
                                        if (parentRenderer && parentRenderer.id === 'cancel-button') return true;
                                        if (el.id === 'cancel-button') return true;
                                        const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                                        return text.includes('cancel');
                                    }""")
                                except Exception:
                                    pass
                                
                                if not is_cancel:
                                    submit_btn = candidate
                                    break
                    if submit_btn:
                        break
                        
                # Fallback to search thread if composer search failed
                if not submit_btn:
                    for sel in candidate_selectors:
                        loc = thread.locator(sel)
                        if await loc.count() > 0:
                            for idx in range(await loc.count()):
                                candidate = loc.nth(idx)
                                if await candidate.is_visible():
                                    is_cancel = False
                                    try:
                                        is_cancel = await candidate.evaluate("""el => {
                                            const parentRenderer = el.closest('ytd-button-renderer');
                                            if (parentRenderer && parentRenderer.id === 'cancel-button') return true;
                                            if (el.id === 'cancel-button') return true;
                                            const text = (el.innerText || el.textContent || '').trim().toLowerCase();
                                            return text.includes('cancel');
                                        }""")
                                    except Exception:
                                        pass
                                    if not is_cancel:
                                        submit_btn = candidate
                                        break
                        if submit_btn:
                            break

                if not submit_btn:
                    raise ValueError("Submit button not found inside reply composer")

                # SAFETY CHECK 2: Check if submit button or its parent ytd-button-renderer is disabled
                is_disabled = await submit_btn.evaluate("""el => {
                    const renderer = el.closest('ytd-button-renderer') || el;
                    const disabledAttr = renderer.getAttribute('disabled');
                    const ariaDisabled = renderer.getAttribute('aria-disabled');
                    return disabledAttr !== null || ariaDisabled === 'true';
                }""")

                if is_disabled:
                    logger.info("Submit button disabled; re-firing InputEvent...")
                    await reply_textbox.evaluate(
                        """(el, value) => {
                            el.focus();
                            el.dispatchEvent(new InputEvent('input', {
                                bubbles: true, composed: true, inputType: 'insertText', data: value
                            }));
                            el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
                        }""",
                        reply_text,
                    )
                    await asyncio.sleep(0.8)
                    is_disabled = await submit_btn.evaluate("""el => {
                        const renderer = el.closest('ytd-button-renderer') || el;
                        const disabledAttr = renderer.getAttribute('disabled');
                        const ariaDisabled = renderer.getAttribute('aria-disabled');
                        return disabledAttr !== null || ariaDisabled === 'true';
                    }""")

                if is_disabled:
                    raise ValueError("Submit button is disabled (YouTube commentbox did not activate submit button)")

                # Click Submit — ONLY accept real create_comment responses
                logger.info(f"Attempt {attempt}: Clicking Submit button...")
                api_success = False
                comment_id = None
                moderation_status = None
                try:
                    async with page.expect_response(
                        lambda r: is_create_comment_url(r.url, r.request.method),
                        timeout=15000,
                    ) as response_info:
                        if attempt > 1:
                            logger.info("Attempt > 1: Using direct click on Submit button...")
                            await submit_btn.click()
                        else:
                            await human_click(page, submit_btn)

                    response = await response_info.value
                    logger.info(f"create_comment response URL: {response.url} status={response.status}")
                    if response.status == 200:
                        try:
                            res_json = await response.json()
                            parsed = parse_create_comment_response(res_json)
                            comment_id = parsed.get("comment_id")
                            moderation_status = parsed.get("moderation_status")
                            api_error = parsed.get("error")
                            logger.info(
                                f"create_comment parsed: commentId={comment_id}, "
                                f"moderationStatus={moderation_status}, error={api_error}"
                            )
                            logger.debug(f"create_comment body excerpt: {dump_response_keys(res_json)}")

                            if api_error:
                                raise ValueError(f"YouTube API error: {api_error}")
                            if moderation_status and not is_public_moderation_status(moderation_status):
                                raise ValueError(
                                    f"Comment held by YouTube (moderationStatus={moderation_status}). "
                                    "Approve in YouTube Studio → Comments, or disable hold-for-review."
                                )
                            if not comment_id:
                                logger.warning(
                                    "create_comment HTTP 200 but no commentId found in payload — "
                                    f"excerpt: {dump_response_keys(res_json, 1500)}"
                                )
                            api_success = True
                        except ValueError:
                            raise
                        except Exception as parse_err:
                            logger.warning(f"Failed to parse create_comment JSON: {parse_err}")
                            api_success = True
                    else:
                        logger.warning(f"YouTube create_comment returned HTTP status {response.status}")
                except ValueError:
                    raise
                except Exception as api_err:
                    logger.warning(f"create_comment listener note: {str(api_err)}")

                if not api_success:
                    raise ValueError("YouTube create_comment API did not confirm comment creation")

                # Wait for editor to close
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

                # Same-session UI check (author can see optimistic / held replies here)
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
                    logger.warning("Could not visually confirm the reply in the signed-in UI.")

                # Public check via comment permalink in a logged-out context
                if video_id and comment_id:
                    public_ok = await self.verify_comment_permalink_public(
                        page, video_id, comment_id, reply_text
                    )
                    if not public_ok:
                        raise ValueError(
                            "Reply is NOT publicly visible (permalink check failed). "
                            "Check YouTube Studio → Comments for Held for review, "
                            "and disable 'Hold potentially inappropriate comments for review'."
                        )
                elif not comment_id:
                    logger.warning(
                        "Skipping permalink public check (no commentId). "
                        "Treat success cautiously."
                    )

                logger.info(
                    f"Attempt {attempt}: Reply published successfully "
                    f"(commentId={comment_id or 'unknown'}, moderation={moderation_status or 'n/a'})"
                )
                return True

            except Exception as e:
                logger.warning(f"Attempt {attempt} failed to post reply: {str(e)}")
                try:
                    await page.keyboard.press("Escape")
                except Exception:
                    pass
                await asyncio.sleep(2.0)

        return False

    # ─── Main Orchestrator ────────────────────────

    async def run_auto_reply(self, sheet_url: Optional[str] = None) -> Dict[str, Any]:
        """
        Main entry point. Reads rules from Google Sheet, navigates to each
        YouTube Short, opens comments, scans for keyword matches, and replies.
        No YouTube Studio involved — everything happens on youtube.com/shorts/.
        """
        self.is_running = True

        # Step 1: Fetch rules from Google Sheet
        await reply_generator.fetch_sheet_rules(sheet_url=sheet_url)

        if not reply_generator.sheet_rules:
            self.is_running = False
            logger.error("No rules found in Google Sheet. Nothing to process.")
            return {"status": "error", "message": "No rules found in Google Sheet"}

        # Collect all video IDs and their associated URLs from the sheet
        video_ids = list(reply_generator.sheet_rules.keys())
        logger.info(f"Loaded rules for {len(video_ids)} video(s): {video_ids}")

        # Stats
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

            # Step 2: Ensure user is signed in
            if not await self.ensure_signed_in(page):
                raise ConnectionError("Failed to sign in to YouTube")

            # Step 3: Process each video from the sheet
            for idx, video_id in enumerate(video_ids):
                if idx > 0:
                    transition_delay = random.uniform(15.0, 45.0)
                    logger.info(f"Pausing for {transition_delay:.1f} seconds before moving to next Short to prevent bot detection...")
                    await asyncio.sleep(transition_delay)

                self.current_video_id = video_id
                logger.info(f"=== Processing video {idx + 1}/{len(video_ids)} (ID: {video_id}) ===")

                # Navigate to the Short
                if not await self.goto_shorts(page, video_id):
                    logger.error(f"Failed to load Short {video_id}, skipping.")
                    stats["failed"] += 1
                    continue

                # Simulate watching the Short for a few seconds before opening comments
                watch_time = random.uniform(settings.MIN_WATCH_DELAY, settings.MAX_WATCH_DELAY)
                logger.info(f"Simulating watching Short {video_id} for {watch_time:.1f} seconds...")
                try:
                    # Move mouse slightly to simulate viewer interaction
                    await page.mouse.move(random.randint(100, 600), random.randint(100, 600), steps=5)
                except Exception:
                    pass
                await asyncio.sleep(watch_time)

                # Open comments panel
                if not await self.open_comments_panel(page):
                    logger.error(f"Failed to open comments for Short {video_id}, skipping.")
                    stats["failed"] += 1
                    continue

                # Sort comments by Newest first to ensure we process the most recent comments
                await self.sort_comments_by_newest(page)

                # Scroll to load comments
                limit = settings.MAX_REPLIES_PER_RUN
                total_loaded = await self.load_comments(page, limit)
                logger.info(f"Total comments loaded for {video_id}: {total_loaded}")

                # Get all comment threads
                threads_locator = page.locator(SELECTORS["comment_thread"][0])
                threads_count = await threads_locator.count()
                stats["total_comments"] += threads_count

                # Step 4: Scan comments and identify keyword matches
                matching_comments = []
                logger.info(f"Scanning {threads_count} comment(s) for keyword matches...")

                for i in range(threads_count):
                    thread = threads_locator.nth(i)

                    # Read comment text
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

                    # Normalize whitespace for both display and matching
                    text_normalized = " ".join(text.split())
                    text_lower = text_normalized.lower()
                    logger.info(f"  Comment {i+1} by '{author}': '{text_normalized[:80]}'")

                    import hashlib
                    comment_hash = hashlib.md5((author + text_normalized).encode("utf-8")).hexdigest()
                    comment_id = f"hash_{comment_hash}"

                    # Skip if already processed
                    if comment_service.is_processed(video_id, comment_id):
                        logger.info(f"    -> Skipped (already processed in log)")
                        stats["skipped"] += 1
                        continue

                    # Check if creator already replied in the UI
                    if await self.is_creator_replied(thread):
                        logger.info("    -> Skipped (creator has already replied)")
                        comment_service.mark_processed(video_id, comment_id)
                        stats["skipped"] += 1
                        continue

                    # Check if comment matches any keyword from the sheet rules FIRST
                    matched_reply = None
                    if video_id in reply_generator.sheet_rules:
                        clean_text = re.sub(r'[^\w\s]', ' ', text_lower)
                        clean_text = " ".join(clean_text.split())

                        for keyword, reply_text in reply_generator.sheet_rules[video_id].items():
                            clean_keyword = re.sub(r'[^\w\s]', ' ', keyword.lower())
                            clean_keyword = " ".join(clean_keyword.split())

                            if clean_keyword and clean_text == clean_keyword:
                                matched_reply = reply_text
                                logger.info(f"    -> MATCH: keyword '{clean_keyword}' => reply '{reply_text[:50]}'")
                                break

                    # Skip offensive comments ONLY if they didn't match a user's sheet rule
                    if not matched_reply:
                        sentiment, is_question, is_offensive = reply_generator.detect_sentiment_and_type(text)
                        if is_offensive:
                            logger.info(f"    -> Skipped (offensive)")
                            stats["skipped"] += 1
                            continue

                    if matched_reply:
                        matching_comments.append({
                            "index": i,
                            "thread": thread,
                            "author": author,
                            "text": text_normalized,
                            "comment_id": comment_id,
                            "reply_text": matched_reply,
                        })

                logger.info(f"Scan complete: {len(matching_comments)} comment(s) match keyword rules.")

                # Step 5: Reply to matching comments
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

                    # Humanize the reply template dynamically (supports spintax, random tags, emojis, or LLM rewriting)
                    humanized_reply = await reply_generator.humanize_static_reply(text, reply_text, author)
                    logger.info(f"Replying to '{author}': '{text[:50]}...' with: '{humanized_reply[:50]}...'")

                    success = await self.post_reply(page, thread, humanized_reply, video_id)

                    from datetime import datetime
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
                        
                        # Simulate a human pause every 3 replies to prevent velocity flagging
                        if replies_count % 3 == 0:
                            extra_pause = random.uniform(30.0, 90.0)
                            logger.info(f"Simulating human distraction/fatigue pause for {extra_pause:.1f} seconds...")
                            await asyncio.sleep(extra_pause)
                        else:
                            await random_delay()
                    else:
                        stats["failed"] += 1
                        comment_service.save_log(video_id, comment_id, author, text, humanized_reply, "failed", "Not publicly visible")

            # Final stats
            self.total_runs += 1
            from datetime import datetime
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


# Global instance
youtube_service = YouTubeService()
