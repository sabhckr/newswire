"""Shared rate limiter instances for the API process."""

from __future__ import annotations

from app.core.config import get_settings
from app.core.ratelimit import SlidingWindowLimiter

_settings = get_settings()
api_limiter = SlidingWindowLimiter(_settings.rate_limit_per_minute, 60)
login_limiter = SlidingWindowLimiter(_settings.login_rate_limit_per_minute, 60)


def reset_limits() -> None:
    api_limiter.reset()
    login_limiter.reset()
    from app.api.news import full_text_limiter

    full_text_limiter.reset()
