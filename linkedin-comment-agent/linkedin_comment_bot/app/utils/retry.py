"""Retry decorator with exponential backoff."""

import asyncio
import functools
import random
import time
from typing import Any, Callable, Optional, Tuple, Type, TypeVar

from linkedin_comment_bot.app.config import get_settings
from linkedin_comment_bot.app.utils.logger import get_logger

T = TypeVar("T")
logger = get_logger()


def retry_sync(
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    max_attempts: Optional[int] = None,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Callable[[Callable[..., T]], Callable[..., T]]:
    def decorator(func: Callable[..., T]) -> Callable[..., T]:
        @functools.wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> T:
            settings = get_settings()
            attempts = max_attempts or settings.retry_count
            last_error: Optional[BaseException] = None

            for attempt in range(1, attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_error = exc
                    if attempt >= attempts:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            attempts,
                            exc,
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    delay += random.uniform(0, delay * 0.3)
                    logger.warning(
                        "%s attempt %d/%d failed: %s. Retrying in %.1fs",
                        func.__name__,
                        attempt,
                        attempts,
                        exc,
                        delay,
                    )
                    time.sleep(delay)

            raise last_error  # type: ignore[misc]

        return wrapper

    return decorator


def retry_async(
    exceptions: Tuple[Type[BaseException], ...] = (Exception,),
    max_attempts: Optional[int] = None,
    base_delay: float = 1.0,
    max_delay: float = 30.0,
) -> Callable[[Callable[..., Any]], Callable[..., Any]]:
    def decorator(func: Callable[..., Any]) -> Callable[..., Any]:
        @functools.wraps(func)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:
            settings = get_settings()
            attempts = max_attempts or settings.retry_count
            last_error: Optional[BaseException] = None

            for attempt in range(1, attempts + 1):
                try:
                    return await func(*args, **kwargs)
                except exceptions as exc:
                    last_error = exc
                    if attempt >= attempts:
                        logger.error(
                            "%s failed after %d attempts: %s",
                            func.__name__,
                            attempts,
                            exc,
                        )
                        raise
                    delay = min(base_delay * (2 ** (attempt - 1)), max_delay)
                    delay += random.uniform(0, delay * 0.3)
                    logger.warning(
                        "%s attempt %d/%d failed: %s. Retrying in %.1fs",
                        func.__name__,
                        attempt,
                        attempts,
                        exc,
                        delay,
                    )
                    await asyncio.sleep(delay)

            raise last_error  # type: ignore[misc]

        return wrapper

    return decorator
