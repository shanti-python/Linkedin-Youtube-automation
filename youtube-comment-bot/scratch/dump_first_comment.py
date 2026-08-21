import asyncio
import os
from playwright.async_api import async_playwright

async def dump_first_comment():
    profile_dir = "/home/shanti/Youtube/youtube-linkedin-comment-bot/youtube-comment-bot/replies/chrome_profile"
    os.makedirs(profile_dir, exist_ok=True)
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir=profile_dir,
            headless=True,
            viewport={"width": 1280, "height": 800}
        )
        page = await context.new_page()
        print("Navigating to Short...")
        await page.goto("https://www.youtube.com/shorts/mica9qEFGMU")
        await page.wait_for_timeout(5000)
        
        # Click cookie accept button if present
        try:
            accept_btn = page.locator("button:has-text('Accept all'), button:has-text('Accept All')")
            if await accept_btn.count() > 0:
                await accept_btn.first.click()
                print("Accepted cookies.")
                await page.wait_for_timeout(2000)
        except Exception:
            pass

        print("Opening comments...")
        comments_btn = page.locator("ytd-reel-video-renderer[is-active] button#comments-button, ytd-reel-video-renderer button#comments-button, button#comments-button").first
        if await comments_btn.count() > 0:
            await comments_btn.click()
        else:
            await page.evaluate("""() => {
                const buttons = document.querySelectorAll('button');
                for (const btn of buttons) {
                    const label = (btn.getAttribute('aria-label') || '').toLowerCase();
                    if (label.includes('comment')) {
                        btn.click();
                        break;
                    }
                }
            }""")
        await page.wait_for_timeout(3000)
        
        # Find the first top-level thread
        toplevel_selector = "ytd-comment-thread-renderer:not(#replies ytd-comment-thread-renderer)"
        threads = page.locator(toplevel_selector)
        count = await threads.count()
        print(f"Top-level threads count: {count}")
        
        if count >= 1:
            first_thread = threads.nth(0)
            author = await first_thread.locator("#author-text").first.inner_text()
            print(f"First thread author: {author.strip()}")
            
            # Dump the HTML of the first thread
            html = await first_thread.evaluate("el => el.outerHTML")
            output_file = "/home/shanti/Youtube/youtube-linkedin-comment-bot/youtube-comment-bot/scratch/first_comment_html.txt"
            with open(output_file, "w", encoding="utf-8") as f:
                f.write(html)
            print(f"Dumped first comment HTML to {output_file}")
            
            # Print elements inside pinned-comment-badge
            badge_html = await first_thread.locator("#pinned-comment-badge").first.evaluate("el => el.outerHTML")
            print("Pinned Comment Badge HTML:")
            print(badge_html)
            
            # Check count with new multi-selector
            pinned_renderer = first_thread.locator("ytd-pinned-comment-badge-renderer")
            print(f"ytd-pinned-comment-badge-renderer count: {await pinned_renderer.count()}")

            pinned_renderer_shorts = first_thread.locator("ytw-pinned-comment-badge-renderer")
            print(f"ytw-pinned-comment-badge-renderer count: {await pinned_renderer_shorts.count()}")

            pinned_badge_children = first_thread.locator("#pinned-comment-badge > *")
            print(f"#pinned-comment-badge > * count: {await pinned_badge_children.count()}")

            combined_selector = "ytd-pinned-comment-badge-renderer, ytw-pinned-comment-badge-renderer, #pinned-comment-badge > *"
            combined = first_thread.locator(combined_selector)
            print(f"Combined selector count: {await combined.count()}")
        else:
            print("No threads found!")
            
        await context.close()

if __name__ == "__main__":
    asyncio.run(dump_first_comment())
