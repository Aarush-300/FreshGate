import pytest
from core.security import rate_limiter
from core.circuit_breaker import circuit_breaker

@pytest.fixture(autouse=True)
def reset_test_state():
    """Ensure every test has a clean rate limiter and circuit breaker state."""
    rate_limiter.reset()
    circuit_breaker.reset_all()
    yield
    rate_limiter.reset()
    circuit_breaker.reset_all()
