"""Database connections.

The API and the worker connect as the database owner, which bypasses row-level security.
That is deliberate: every write goes through this code, which checks who the caller is
before touching a row. Browsers reading directly through Supabase still go through RLS.
"""

from collections.abc import Iterator
from contextlib import contextmanager

from psycopg import Connection
from psycopg.rows import dict_row
from psycopg_pool import ConnectionPool

from app.core.config import get_settings

_pool: ConnectionPool | None = None


def get_pool() -> ConnectionPool:
    global _pool
    if _pool is None:
        _pool = ConnectionPool(
            get_settings().database_url,
            min_size=1,
            max_size=5,
            kwargs={"row_factory": dict_row},
            open=True,
        )
    return _pool


@contextmanager
def transaction() -> Iterator[Connection]:
    """One transaction: everything inside commits together, or nothing does."""
    with get_pool().connection() as conn:
        with conn.transaction():
            yield conn
