import pytest

from app.core.db import close_pool


@pytest.fixture(autouse=True, scope="session")
def database_pool():
    yield
    close_pool()
