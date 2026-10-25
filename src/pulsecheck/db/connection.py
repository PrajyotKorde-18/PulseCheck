"""Database connection management using psycopg 3."""

from contextlib import contextmanager
from typing import Generator, Optional

import psycopg
from psycopg import Connection

from pulsecheck.config.models import PulseCheckSettings


def get_connection(settings: Optional[PulseCheckSettings] = None) -> Connection:
    """Create and return a new psycopg connection."""
    cfg = settings or PulseCheckSettings()
    return psycopg.connect(
        host=cfg.postgres_host,
        port=cfg.postgres_port,
        dbname=cfg.postgres_db,
        user=cfg.postgres_user,
        password=cfg.postgres_password,
        autocommit=False,
    )


@contextmanager
def get_db_connection(
    settings: Optional[PulseCheckSettings] = None,
) -> Generator[Connection, None, None]:
    """Context manager yielding a database connection with auto-closing."""
    conn = get_connection(settings)
    try:
        yield conn
    finally:
        conn.close()
