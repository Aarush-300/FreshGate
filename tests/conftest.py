import pytest
from core.security import rate_limiter

@pytest.fixture(autouse=True)
def reset_rate_limiter_for_all_tests():
    """Ensure every test has a clean rate limiter state."""
    rate_limiter.reset()
    yield
    rate_limiter.reset()
