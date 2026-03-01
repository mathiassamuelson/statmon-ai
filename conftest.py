import pytest


@pytest.fixture(autouse=True)
def _anyio_backend():
    """Default to asyncio for pytest-asyncio."""
    return "asyncio"
