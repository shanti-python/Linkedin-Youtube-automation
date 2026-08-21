import asyncio
import random
import math
from typing import Optional
from playwright.async_api import Page, Locator
from youtube_reply_bot.app.config import settings
from youtube_reply_bot.app.utils.logger import logger

async def random_delay(min_sec: float = None, max_sec: float = None) -> None:
    """Pause execution for a random period of time."""
    if min_sec is None:
        min_sec = settings.MIN_RANDOM_DELAY
    if max_sec is None:
        max_sec = settings.MAX_RANDOM_DELAY
    
    delay = random.uniform(min_sec, max_sec)
    await asyncio.sleep(delay)

async def human_type(locator: Locator, text: str, page: Optional[Page] = None) -> str:
    """
    Types into YouTube contenteditable boxes using native insertText so Polymer
    binds the value correctly. Falls back to keyboard typing if needed.
    Returns the text read back from the composer.
    """
    await locator.click()
    await locator.focus()
    await asyncio.sleep(0.25)

    try:
        if page:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        else:
            await locator.press("Control+A")
            await locator.press("Backspace")
    except Exception:
        pass
    await asyncio.sleep(0.2)

    # Preferred path: execCommand insertText (fires correct InputEvents for YouTube)
    read_back = await locator.evaluate(
        """(el, value) => {
            el.focus();
            el.textContent = '';
            const ok = document.execCommand('insertText', false, value);
            if (!ok) {
                el.textContent = value;
            }
            el.dispatchEvent(new InputEvent('input', {
                bubbles: true,
                composed: true,
                inputType: 'insertText',
                data: value,
            }));
            el.dispatchEvent(new Event('change', { bubbles: true, composed: true }));
            return (el.innerText || el.textContent || '').trim();
        }""",
        text,
    )

    if (read_back or "").strip() != text.strip() and page:
        await locator.focus()
        try:
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
        except Exception:
            pass
        for char in text:
            delay = random.uniform(settings.MIN_TYPING_DELAY, settings.MAX_TYPING_DELAY)
            if char in ".,!? ":
                delay += random.uniform(0.05, 0.2)
            await page.keyboard.type(char, delay=int(delay * 1000))
        await asyncio.sleep(0.3)
        read_back = await locator.evaluate(
            "(el) => (el.innerText || el.textContent || '').trim()"
        )

    await asyncio.sleep(random.uniform(0.4, 0.8))
    return (read_back or "").strip()

async def simulate_mouse_move(page: Page, target_x: float, target_y: float) -> None:
    """Moves the mouse to target coordinates using smooth Bézier-like steps."""
    # Get current mouse position (we assume standard start or use viewport center)
    current_x, current_y = 100.0, 100.0
    
    steps = random.randint(10, 20)
    for i in range(1, steps + 1):
        t = i / steps
        # Simple cubic ease-in-out interpolation
        t_curved = t * t * (3 - 2 * t)
        x = current_x + (target_x - current_x) * t_curved
        y = current_y + (target_y - current_y) * t_curved
        
        # Add a tiny bit of jitter to simulate shaking hand
        x += random.uniform(-1, 1)
        y += random.uniform(-1, 1)
        
        await page.mouse.move(x, y)
        await asyncio.sleep(random.uniform(0.005, 0.015))
        
    await page.mouse.move(target_x, target_y)

async def human_click(page: Page, locator: Locator) -> None:
    """Hover mouse over element with human-like motion, pause, then click."""
    box = await locator.bounding_box()
    if not box:
        # If bounding box is not available, do a direct click
        logger.warning("Could not calculate bounding box, performing direct click.")
        await locator.click()
        return
        
    # Pick a random point within the middle 60% of the element
    target_x = box["x"] + box["width"] * random.uniform(0.2, 0.8)
    target_y = box["y"] + box["height"] * random.uniform(0.2, 0.8)
    
    # Move mouse smoothly to target
    await simulate_mouse_move(page, target_x, target_y)
    await asyncio.sleep(random.uniform(0.1, 0.4))
    
    # Click
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
