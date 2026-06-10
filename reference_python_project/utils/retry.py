"""
Retry decorators.
"""

import asyncio
import logging
from functools import wraps

logger = logging.getLogger(__name__)

class RetryError(Exception):
    pass

def async_retry(max_attempts=4, base_delay=1.0, max_delay=30.0, exceptions=(Exception,)):
    def decorator(func):
        @wraps(func)
        async def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_attempts):
                try:
                    return await func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"Failed after {max_attempts} attempts: {e}")
                        raise RetryError(f"Function {func.__name__} failed after {max_attempts} retries") from e
                    logger.warning(f"Attempt {attempt+1}/{max_attempts} failed for {func.__name__}: {e}. Retrying in {delay}s...")
                    await asyncio.sleep(delay)
                    delay = min(delay * 2, max_delay)
        return wrapper
    return decorator

def sync_retry(max_attempts=4, base_delay=1.0, max_delay=30.0, exceptions=(Exception,)):
    import time
    def decorator(func):
        @wraps(func)
        def wrapper(*args, **kwargs):
            delay = base_delay
            for attempt in range(max_attempts):
                try:
                    return func(*args, **kwargs)
                except exceptions as e:
                    if attempt == max_attempts - 1:
                        logger.error(f"Failed after {max_attempts} attempts: {e}")
                        raise RetryError(f"Function {func.__name__} failed after {max_attempts} retries") from e
                    logger.warning(f"Attempt {attempt+1}/{max_attempts} failed for {func.__name__}: {e}. Retrying in {delay}s...")
                    time.sleep(delay)
                    delay = min(delay * 2, max_delay)
        return wrapper
    return decorator

__all__ = ["async_retry", "sync_retry", "RetryError"]
