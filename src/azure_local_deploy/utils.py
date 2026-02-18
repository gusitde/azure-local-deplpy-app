"""Shared helpers: logging, retry, validation."""

from __future__ import annotations

import logging
import time
from functools import wraps
from typing import Any, Callable

from rich.console import Console
from rich.logging import RichHandler

console = Console()

# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

def get_logger(name: str, level: int = logging.INFO) -> logging.Logger:
    """Return a Rich-powered logger scoped to *name*."""
    logger = logging.getLogger(name)
    if not logger.handlers:
        handler = RichHandler(console=console, show_path=False, markup=True)
        handler.setFormatter(logging.Formatter("%(message)s"))
        logger.addHandler(handler)
    logger.setLevel(level)
    return logger

# ---------------------------------------------------------------------------
# Retry decorator
# ---------------------------------------------------------------------------

def retry(
    max_attempts: int = 3,
    delay_seconds: float = 5.0,
    backoff: float = 2.0,
    exceptions: tuple = (Exception,),
) -> Callable:
    """Simple exponential-backoff retry decorator."""

    def decorator(func: Callable) -> Callable:
        @wraps(func)
        def wrapper(*args: Any, **kwargs: Any) -> Any:
            last_exc: Exception | None = None
            wait = delay_seconds
            for attempt in range(1, max_attempts + 1):
                try:
                    return func(*args, **kwargs)
                except exceptions as exc:
                    last_exc = exc
                    if attempt < max_attempts:
                        logging.getLogger(func.__module__).warning(
                            "Attempt %d/%d for %s failed: %s – retrying in %.1fs",
                            attempt,
                            max_attempts,
                            func.__name__,
                            exc,
                            wait,
                        )
                        time.sleep(wait)
                        wait *= backoff
            raise last_exc  # type: ignore[misc]

        return wrapper

    return decorator

# ---------------------------------------------------------------------------
# Validation helpers
# ---------------------------------------------------------------------------

def require_keys(data: dict, keys: list[str], context: str = "") -> None:
    """Raise *ValueError* when any of *keys* is missing from *data*."""
    missing = [k for k in keys if k not in data or data[k] is None]
    if missing:
        ctx = f" ({context})" if context else ""
        raise ValueError(f"Missing required config keys{ctx}: {', '.join(missing)}")
