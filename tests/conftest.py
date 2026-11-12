from __future__ import annotations

import uuid
from pathlib import Path

import pytest
from core.security import rate_limiter


@pytest.fixture(autouse=True)
def isolate_global_rate_limiter():
    rate_limiter.reset()
    yield
    rate_limiter.reset()


@pytest.fixture
def workspace_tmp_path():
    """Temporary test directory under the checkout for restricted Windows runners."""
    path = Path(__file__).resolve().parents[1] / f".freshgate-test-{uuid.uuid4().hex}"
    path.mkdir()
    try:
        yield path
    finally:
        for child in path.iterdir():
            if child.is_file():
                child.unlink()
        path.rmdir()
