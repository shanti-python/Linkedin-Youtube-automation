"""Extended DOM dump - scroll more aggressively and capture comment HTML."""
import asyncio
import json
from pathlib import Path

from playwright.async_api import async_playwright

POST_URL = "https://www.linkedin.com/feed/update/urn:li:activity:7493198956135387137/"
COOKIES_FILE = Path("exports/cookies.json")
PROFILE_DIR = "/home/ubuntu/.config/linkedin-bot-chrome"
OUTPUT_FILE = Path("logs/dom_dump2.json")


async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir=PROFILE_DIR,
            headless=True,
            args=["--no-sandbox", "--disable-blink-features=AutomationControlled", "--disable-dev-shm-usage"],
        )
        page = browser.pages[0] if browser.pages else await browser.new_page()

        if COOKIES_FILE.exists():
            cookies = json.loads(COOKIES_FILE.read_text())
            await browser.add_cookies(cookies)
            print(f"Loaded {len(cookies)} cookies")

        print(f"Navigating to {POST_URL}")
        await page.goto(POST_URL, wait_until="networkidle", timeout=60000)
        await asyncio.sleep(3)

        # Try clicking load more comments button
        for btn_label in ["Show more comments", "Load more comments", "View more comments"]:
            try:
                btn = page.get_by_role("button", name=btn_label)
                if await btn.is_visible(timeout=2000):
                    await btn.click()
                    print(f"Clicked: {btn_label}")
                    await asyncio.sleep(2)
            except Exception:
                pass

        # Scroll aggressively
        for i in range(15):
            await page.evaluate("window.scrollBy(0, 800)")
            await asyncio.sleep(1.2)
            print(f"Scroll {i+1}")

        result = await page.evaluate("""() => {
            const info = {};

            // Count comment containers by ID pattern
            const replaceableComments = document.querySelectorAll('[id^="replaceableComment_urn:li:comment:"]');
            info.replaceable_comment_count = replaceableComments.length;

            const compKeyComments = document.querySelectorAll('[componentkey^="CommentComponentReference_urn:li:comment:"]');
            info.compkey_comment_count = compKeyComments.length;

            // Count reply buttons
            info.reply_btn_count = document.querySelectorAll('button[aria-label="Reply"]').length;
            info.reply_to_btn_count = document.querySelectorAll('button[aria-label^="Reply to"]').length;
            info.open_options_count = document.querySelectorAll('button[aria-label^="Open options for"]').length;

            // Textbox info
            const textboxes = document.querySelectorAll('[role="textbox"]');
            info.textbox_count = textboxes.length;
            info.textboxes = Array.from(textboxes).map(tb => ({
                tag: tb.tagName.toLowerCase(),
                class: tb.className,
                ariaLabel: tb.getAttribute('aria-label'),
                placeholder: tb.getAttribute('data-placeholder') || tb.getAttribute('aria-placeholder'),
                componentkey: tb.getAttribute('componentkey')
            }));

            // Get full HTML of first replaceableComment container
            if (replaceableComments.length > 0) {
                info.first_comment_html = replaceableComments[0].outerHTML.slice(0, 6000);
            }

            // Check comments container
            const lazyCol = document.querySelector('[data-component-type="LazyColumn"]');
            if (lazyCol) {
                info.lazy_column_attrs = {
                    id: lazyCol.id,
                    dataTestId: lazyCol.getAttribute('data-testid'),
                    componentkey: lazyCol.getAttribute('componentkey')
                };
                info.lazy_column_html_start = lazyCol.outerHTML.slice(0, 500);
            }

            // Find submit buttons
            const submitBtns = Array.from(document.querySelectorAll('button')).filter(b => {
                const l = b.getAttribute('aria-label') || '';
                const txt = b.textContent || '';
                return l.toLowerCase().includes('post') || l.toLowerCase().includes('submit') || 
                       txt.trim().toLowerCase() === 'post' || txt.trim().toLowerCase() === 'reply';
            });
            info.submit_btns = submitBtns.slice(0, 5).map(b => ({
                tag: 'button',
                class: b.className,
                ariaLabel: b.getAttribute('aria-label'),
                text: b.textContent.trim().slice(0, 30),
                componentkey: b.getAttribute('componentkey')
            }));

            // Author extraction from comments
            if (replaceableComments.length > 0) {
                const comment = replaceableComments[0];
                const links = Array.from(comment.querySelectorAll('a[href*="/in/"]'));
                info.author_links = links.slice(0, 3).map(a => ({
                    href: a.getAttribute('href'),
                    ariaLabel: a.getAttribute('aria-label'),
                    text: a.textContent.trim().slice(0, 50),
                    class: a.className
                }));

                // Find text content elements
                const spans = Array.from(comment.querySelectorAll('span, p, div[dir="ltr"]')).filter(el => {
                    const txt = el.textContent.trim();
                    return txt.length > 20 && txt.length < 500 && el.children.length === 0;
                });
                info.text_candidates = spans.slice(0, 5).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    class: el.className,
                    text: el.textContent.trim().slice(0, 100),
                    ariaLabel: el.getAttribute('aria-label')
                }));

                // Time elements
                const timeEls = comment.querySelectorAll('time, [datetime], [aria-label*="ago"], [aria-label*="hour"], [aria-label*="day"]');
                info.time_elements = Array.from(timeEls).slice(0, 3).map(el => ({
                    tag: el.tagName.toLowerCase(),
                    datetime: el.getAttribute('datetime'),
                    ariaLabel: el.getAttribute('aria-label'),
                    text: el.textContent.trim()
                }));
            }

            return info;
        }""")

        OUTPUT_FILE.write_text(json.dumps(result, indent=2, ensure_ascii=False))
        print("\n=== RESULTS ===")
        # Print everything except the long HTML
        concise = {k: v for k, v in result.items() if k != 'first_comment_html' and k != 'lazy_column_html_start'}
        print(json.dumps(concise, indent=2, ensure_ascii=False))
        print("\n--- First comment HTML ---")
        print((result.get('first_comment_html') or '')[:4000])

        await browser.close()


asyncio.run(main())
