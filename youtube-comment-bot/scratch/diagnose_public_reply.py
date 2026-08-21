"""Diagnose whether a reply is truly public on a Short."""
import asyncio
import re
import sys

from playwright.async_api import async_playwright
from playwright_stealth import Stealth


async def main() -> int:
    video_id = sys.argv[1] if len(sys.argv) > 1 else "BJ3h6W_gM4E"
    reply_text = sys.argv[2] if len(sys.argv) > 2 else "thanks for you connect"
    comment_id = sys.argv[3] if len(sys.argv) > 3 else "Ugzu1-x2mzTq2tuNF8d4AaABAg.A_A92GQ_xt8A_A9DxMKsYW"

    needle = " ".join(re.sub(r"[^\w\s]", " ", reply_text.lower()).split())
    stealth = Stealth()

    async with async_playwright() as p:
        browser = await p.chromium.launch(headless=True, args=["--no-sandbox"])
        context = await browser.new_context(viewport={"width": 1280, "height": 900}, locale="en-US")
        page = await context.new_page()
        await stealth.apply_stealth_async(page)

        # 1) Shorts comments panel
        shorts = f"https://www.youtube.com/shorts/{video_id}"
        print(f"OPEN shorts={shorts}")
        await page.goto(shorts, wait_until="domcontentloaded")
        await asyncio.sleep(3)
        await page.evaluate("""() => {
            for (const btn of document.querySelectorAll('button')) {
                const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                if (label.includes('comment')) { btn.click(); return true; }
            }
            return false;
        }""")
        await asyncio.sleep(3)
        # expand replies
        for _ in range(5):
            btns = page.locator("#more-replies button, #more-replies-sub-thread button, button:has-text('replies'), button:has-text('reply')")
            count = await btns.count()
            for i in range(min(count, 10)):
                try:
                    b = btns.nth(i)
                    if await b.is_visible():
                        await b.click()
                        await asyncio.sleep(0.8)
                except Exception:
                    pass

        content_nodes = page.locator("#content-text, yt-attributed-string#content-text, span.yt-core-attributed-string")
        found_in_panel = False
        texts = []
        for i in range(min(await content_nodes.count(), 80)):
            try:
                t = (await content_nodes.nth(i).inner_text()).strip()
                texts.append(t)
                norm = " ".join(re.sub(r"[^\w\s]", " ", t.lower()).split())
                if needle and needle in norm:
                    found_in_panel = True
            except Exception:
                pass
        print(f"SHORTS_PANEL found_reply={found_in_panel}")
        print("SHORTS_PANEL comments:")
        for t in texts[:20]:
            print(f"  - {t[:120]}")

        # 2) Permalink page — naive HTML contains lc= always
        permalink = f"https://www.youtube.com/watch?v={video_id}&lc={comment_id}"
        print(f"OPEN permalink={permalink}")
        await page.goto(permalink, wait_until="domcontentloaded")
        await asyncio.sleep(4)
        html = await page.content()
        body = await page.locator("body").inner_text()
        naive_id_in_html = comment_id in html
        naive_tail_in_html = comment_id.split(".")[-1] in html
        body_norm = " ".join(re.sub(r"[^\w\s]", " ", body.lower()).split())
        text_in_body = needle in body_norm

        # Strict: only visible comment content nodes
        content_nodes = page.locator("#content-text, yt-attributed-string#content-text, ytd-comment-view-model #content-text")
        found_in_comment_dom = False
        comment_texts = []
        for i in range(min(await content_nodes.count(), 80)):
            try:
                t = (await content_nodes.nth(i).inner_text()).strip()
                if t:
                    comment_texts.append(t)
                norm = " ".join(re.sub(r"[^\w\s]", " ", t.lower()).split())
                if needle and needle in norm:
                    found_in_comment_dom = True
            except Exception:
                pass

        print(f"PERMALINK naive_id_in_html={naive_id_in_html} (FALSE POSITIVE RISK)")
        print(f"PERMALINK naive_tail_in_html={naive_tail_in_html}")
        print(f"PERMALINK text_in_body={text_in_body}")
        print(f"PERMALINK found_in_comment_dom={found_in_comment_dom}")
        print("PERMALINK comment DOM texts:")
        for t in comment_texts[:20]:
            print(f"  - {t[:120]}")

        await browser.close()
        return 0 if found_in_panel or found_in_comment_dom else 1


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
