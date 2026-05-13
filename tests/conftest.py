"""Shared test fixtures."""

import asyncio
import os
import shutil
from contextlib import contextmanager

import pytest
from httpx import AsyncClient, ASGITransport

# Use in-memory DB for tests
os.environ.setdefault("MIMIR_DATA_DIR", "/tmp/mimir_test")
os.environ.setdefault("MIMIR_VECTOR_DIR", "/tmp/mimir_test/vectors")
os.environ.setdefault("MIMIR_ENV", "development")
# Enable system mutation endpoints in tests (worker_stability eval suite needs them).
os.environ.setdefault("MIMIR_ENABLE_SYSTEM_MUTATION_ENDPOINTS", "true")

# Clear any stale data from previous runs so each session starts clean.
_test_db = "/tmp/mimir_test/mimir.db"
if os.path.exists(_test_db):
    os.remove(_test_db)

# Also clear the vector store so accumulated vectors from prior test runs
# don't pollute retrieval tests (e.g. n_results=5 search missing a new memory).
_vector_dir = "/tmp/mimir_test/vectors"
if os.path.exists(_vector_dir):
    shutil.rmtree(_vector_dir)


@pytest.fixture(scope="session")
def event_loop():
    loop = asyncio.new_event_loop()
    yield loop
    loop.close()


@pytest.fixture(scope="session")
async def app():
    from storage.database import init_db
    from api.main import app as _app
    await init_db()
    return _app


@pytest.fixture
async def client(app):
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c


# ── Auth override helpers ──────────────────────────────────────────────────────

@contextmanager
def as_user(app, user_id: str, display_name: str = "Test User", email: str = "test@example.com"):
    """Override get_current_user so all requests in the block are authenticated as user_id."""
    from api.deps import get_current_user, UserContext

    async def _override():
        return UserContext(id=user_id, email=email, display_name=display_name, is_dev=False)

    app.dependency_overrides[get_current_user] = _override
    try:
        yield
    finally:
        app.dependency_overrides.pop(get_current_user, None)
