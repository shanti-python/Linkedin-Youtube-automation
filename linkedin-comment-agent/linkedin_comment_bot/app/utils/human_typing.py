"""Human-like typing simulation for Playwright with realistic rhythm, pauses, and anti-detection features."""

import asyncio
import random
from typing import Optional

from playwright.async_api import Locator, Page

from linkedin_comment_bot.app.config import Settings
from linkedin_comment_bot.app.utils.logger import get_logger

logger = get_logger()

# Common QWERTY key adjacencies for realistic human typos
TYPO_MAP = {
    "a": "s", "b": "v", "c": "v", "d": "s", "e": "r", "f": "d", "g": "f",
    "h": "g", "i": "o", "j": "h", "k": "j", "l": "k", "m": "n", "n": "b",
    "o": "p", "p": "o", "r": "e", "s": "a", "t": "r", "u": "y", "v": "c",
    "w": "q", "x": "z", "y": "t", "z": "x"
}


async def human_type(
    locator: Locator,
    text: str,
    settings: Settings,
    *,
    clear_first: bool = True,
    enable_typos: bool = True,
) -> None:
    """Type text into a field mimicking natural human typing rhythm, pauses, typos, and backspaces."""
    # 1. Scroll into view and focus naturally
    try:
        await locator.scroll_into_view_if_needed()
    except Exception:
        pass

    page = locator.page
    await locator.focus()
    await asyncio.sleep(random.uniform(0.15, 0.35))
    await locator.click()
    await asyncio.sleep(random.uniform(0.15, 0.3))

    if clear_first:
        try:
            # Clear editor using natural keyboard shortcuts
            await page.keyboard.press("Control+A")
            await page.keyboard.press("Backspace")
            await asyncio.sleep(random.uniform(0.1, 0.25))
        except Exception:
            pass

    # 2. Type character-by-character with human typing dynamics
    i = 0
    while i < len(text):
        char = text[i]

        # Simulate occasional human typo (~2% chance for letters, corrected immediately)
        if enable_typos and char.lower() in TYPO_MAP and random.random() < 0.02 and i > 2:
            wrong_char = TYPO_MAP[char.lower()]
            if char.isupper():
                wrong_char = wrong_char.upper()

            # Type wrong character
            await page.keyboard.type(wrong_char, delay=random.randint(40, 90))
            await asyncio.sleep(random.uniform(0.15, 0.35))  # Realization pause
            await page.keyboard.press("Backspace")           # Correct typo
            await asyncio.sleep(random.uniform(0.1, 0.25))

        # Dynamic delay calculation based on character type
        if char == " ":
            # Space between words
            delay_ms = random.randint(80, 220)
        elif char in ".!?,;:":
            # Sentence/phrase punctuation pause
            delay_ms = random.randint(220, 500)
        elif char.isupper():
            # Shift key overhead for uppercase letters
            delay_ms = random.randint(90, 190)
        else:
            # Standard key delay
            delay_ms = random.randint(
                settings.typing_speed_min_ms,
                settings.typing_speed_max_ms,
            )

        # Type character with calculated delay
        await page.keyboard.type(char, delay=delay_ms)

        # Micro-pause mid-typing (~3% chance, as if thinking or reading)
        if random.random() < 0.03:
            await asyncio.sleep(random.uniform(0.3, 0.7))

        i += 1

    # Brief pause after typing completes before continuing
    await asyncio.sleep(random.uniform(0.4, 0.9))


async def random_mouse_movement(page: Page) -> None:
    """Perform subtle random mouse movement with organic curved steps."""
    try:
        viewport = page.viewport_size or {"width": 1280, "height": 720}
        x = random.randint(120, max(121, viewport["width"] - 120))
        y = random.randint(120, max(121, viewport["height"] - 120))
        await page.mouse.move(x, y, steps=random.randint(6, 14))
        await asyncio.sleep(random.uniform(0.1, 0.3))
    except Exception:
        pass


async def random_scroll(page: Page, direction: str = "down") -> None:
    """Scroll randomly to mimic human browsing behavior."""
    amount = random.randint(150, 450) * (1 if direction == "down" else -1)
    await page.mouse.wheel(0, amount)
    await asyncio.sleep(random.uniform(0.4, 0.9))


async def random_wait(min_sec: float, max_sec: float) -> None:
    """Wait a random duration."""
    await asyncio.sleep(random.uniform(min_sec, max_sec))


async def pause_before_submit(settings: Settings) -> None:
    """Brief pause before submitting a reply."""
    await random_wait(1.2, 3.0)


async def reply_rate_limit_delay(settings: Settings, reply_count: int) -> None:
    """Apply rate limiting between replies."""
    await random_wait(settings.reply_delay_min_sec, settings.reply_delay_max_sec)

    if reply_count > 0 and reply_count % settings.long_pause_every_n_replies == 0:
        logger.info("Taking scheduled long pause of %d-%ds to mimic natural human behavior...",
                    settings.long_pause_min_sec, settings.long_pause_max_sec)
        await random_wait(settings.long_pause_min_sec, settings.long_pause_max_sec)
