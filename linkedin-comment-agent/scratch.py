import asyncio
from playwright.async_api import async_playwright

async def main():
    async with async_playwright() as p:
        browser = await p.chromium.launch_persistent_context(
            user_data_dir="/home/ubuntu/.config/linkedin-bot-chrome",
            headless=True
        )
        page = browser.pages[0]
        print("Navigating...")
        await page.goto("https://www.linkedin.com/feed/update/urn:li:activity:7490296164987342850/")
        await page.wait_for_timeout(5000)
        print("Saving screenshot...")
        await page.screenshot(path="screenshot.png")
        html = await page.content()
        with open("page.html", "w") as f:
            f.write(html)
        await browser.close()

asyncio.run(main())
