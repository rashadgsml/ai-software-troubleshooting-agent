import pytest

from app.api.security import RateLimiter, validate_settings_for_startup
from app.core.config import Settings


class Clock:
    def __init__(self):
        self.now = 1000.0

    def __call__(self):
        return self.now


def test_rate_limiter_blocks_then_recovers_as_window_slides():
    clock = Clock()
    limiter = RateLimiter(limit=2, window=60, clock=clock)
    assert limiter.check("k") is None and limiter.check("k") is None
    wait = limiter.check("k")
    assert wait == pytest.approx(60)
    clock.now += 30
    assert limiter.check("k") == pytest.approx(30)  # still blocked, retry-after shrinks
    clock.now += 31
    assert limiter.check("k") is None  # oldest hit aged out


def test_rate_limiter_keys_are_independent():
    limiter = RateLimiter(limit=1, window=60, clock=Clock())
    assert limiter.check("a") is None
    assert limiter.check("b") is None
    assert limiter.check("a") is not None


@pytest.mark.parametrize("env,key,ok", [
    ("production", "change-me", False),
    ("production", "", False),
    ("production", "short", False),
    ("production", "x" * 16, True),
    ("dev", "change-me", True),
])
def test_startup_guard_rejects_weak_keys_in_production(env, key, ok):
    settings = Settings(app_env=env, api_key=key)
    if ok:
        validate_settings_for_startup(settings)
    else:
        with pytest.raises(RuntimeError, match="API_KEY"):
            validate_settings_for_startup(settings)
