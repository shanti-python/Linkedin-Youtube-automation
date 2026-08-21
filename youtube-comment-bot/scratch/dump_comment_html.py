import asyncio
from playwright.async_api import async_playwright

async def dump_reply_box():
    async with async_playwright() as p:
        context = await p.chromium.launch_persistent_context(
            user_data_dir="/home/shanti/youtube-comment/replies/chrome_profile",
            headless=True,
            viewport={"width": 1280, "height": 720}
        )
        page = await context.new_page()
        print("Navigating to Short...")
        await page.goto("https://www.youtube.com/shorts/BJ3h6W_gM4E")
        await page.wait_for_timeout(5000)
        
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
        
        # Click sort menu and select Newest
        print("Clicking sort menu...")
        sort_btn = page.locator("yt-sort-filter-sub-menu-renderer, #sort-menu, button[aria-label='Sort comments']").first
        if await sort_btn.count() > 0:
            await sort_btn.click()
            await page.wait_for_timeout(1000)
            newest_opt = page.locator("ytd-menu-service-item-renderer:has-text('Newest'), tp-yt-paper-item:has-text('Newest'), yt-formatted-string:has-text('Newest')").first
            if await newest_opt.count() > 0:
                await newest_opt.click()
                await page.wait_for_timeout(2000)
        
        # Find the second top-level thread (which is index 1 using the top-level selector)
        toplevel_selector = "ytd-comment-thread-renderer:not(#replies ytd-comment-thread-renderer)"
        threads = page.locator(toplevel_selector)
        count = await threads.count()
        print(f"Top-level threads count: {count}")
        
        if count >= 2:
            second_thread = threads.nth(1)
            author = await second_thread.locator("#author-text").first.inner_text()
            print(f"Second thread author: {author.strip()}")
            
            # Click reply button on second thread
            reply_btn = second_thread.locator("#reply-button-end button, #reply-button-end, button[aria-label='Reply']").first
            print("Clicking Reply button...")
            await reply_btn.click()
            await page.wait_for_timeout(4000)
            
            # Dump the HTML of the second thread to see where the reply box is
            html = await second_thread.evaluate("el => el.outerHTML")
            with open("scratch/reply_box_thread_html.txt", "w", encoding="utf-8") as f:
                f.write(html)
            print("Dumped thread HTML to scratch/reply_box_thread_html.txt")
        else:
            print("Less than 2 threads found!")
            
        await context.close()

if __name__ == "__main__":
    asyncio.run(dump_reply_box())
