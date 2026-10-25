"""Integration tests for batch-level verification checks and alert deduplication with PostgreSQL."""

from datetime import date, datetime, timedelta, timezone
from typing import Generator

import psycopg
import pytest
from psycopg import Connection

from pulsecheck.checks.engine import BatchCheckEngine
from pulsecheck.clock import FrozenClock
from pulsecheck.config.loader import load_config
from pulsecheck.config.models import PulseCheckSettings
from pulsecheck.db.ddl import init_db

pytestmark = pytest.mark.integration

SOURCE_NAME = "int_test_batch_source"
COUNT_ZERO = 0
COUNT_ONE = 1
COUNT_TWO = 2
VOLUME_NORMAL = 100
VOLUME_DROPPED = 20
COOLDOWN_ADVANCE_SEC = 300


@pytest.fixture(scope="session")
def db_settings() -> PulseCheckSettings:
    """Load settings from environment."""
    return PulseCheckSettings()


@pytest.fixture
def pg_conn(db_settings: PulseCheckSettings) -> Generator[Connection, None, None]:
    """Provide a real PostgreSQL connection or skip if unavailable."""
    try:
        conn = psycopg.connect(
            host=db_settings.postgres_host,
            port=db_settings.postgres_port,
            dbname=db_settings.postgres_db,
            user=db_settings.postgres_user,
            password=db_settings.postgres_password,
            connect_timeout=2,
        )
    except Exception as e:
        pytest.skip(
            f"PostgreSQL not available at {db_settings.postgres_host}:"
            f"{db_settings.postgres_port}: {e}"
        )

    yield conn
    conn.close()


def cleanup_test_data(conn: Connection, source: str) -> None:
    """Remove records created by tests."""
    with conn.cursor() as cur:
        cur.execute(
            "DELETE FROM alerts WHERE check_name LIKE 'volume_%' OR check_name LIKE 'missing_%';"
        )
        cur.execute("DELETE FROM batch_metrics WHERE source = %s;", (source,))
        cur.execute("DELETE FROM batch_registry WHERE source = %s;", (source,))
    conn.commit()


def test_batch_checks_seed_history_and_detect_volume_drop(pg_conn: Connection) -> None:
    """Seeds historical batches in database and verifies volume drop detection."""
    bundle = load_config()
    init_db(pg_conn, bundle.dataset_config)
    cleanup_test_data(pg_conn, SOURCE_NAME)

    base_date = date(2026, 3, 2)  # Monday
    t_now = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(t_now)

    # 1. Seed 3 historical Monday batches
    with pg_conn.cursor() as cur:
        for i in range(1, 4):
            hist_date = base_date - timedelta(days=7 * i)
            batch_id = f"HIST_BATCH_{i}"
            cur.execute(
                """
                INSERT INTO batch_registry (
                    batch_id, source, logical_date, file_hash, status, started_at, completed_at
                ) VALUES (%s, %s, %s, %s, 'completed', %s, %s);
                """,
                (batch_id, SOURCE_NAME, hist_date, f"hash_{i}", t_now, t_now),
            )
            cur.execute(
                """
                INSERT INTO batch_metrics (
                    batch_id, source, scope, rows_received, rows_loaded,
                    rows_quarantined, rows_duplicate, latest_event_time, duration_seconds
                ) VALUES (%s, %s, 'Mumbai', %s, %s, 0, 0, %s, 1.0);
                """,
                (batch_id, SOURCE_NAME, VOLUME_NORMAL, VOLUME_NORMAL, t_now),
            )

        # 2. Seed current batch with dropped volume (20 rows)
        curr_batch_id = "CURR_BATCH_DROP"
        cur.execute(
            """
            INSERT INTO batch_registry (
                batch_id, source, logical_date, file_hash, status, started_at, completed_at
            ) VALUES (%s, %s, %s, 'hash_curr', 'completed', %s, %s);
            """,
            (curr_batch_id, SOURCE_NAME, base_date, t_now, t_now),
        )
        cur.execute(
            """
            INSERT INTO batch_metrics (
                batch_id, source, scope, rows_received, rows_loaded,
                rows_quarantined, rows_duplicate, latest_event_time, duration_seconds
            ) VALUES (%s, %s, 'Mumbai', %s, %s, 0, 0, %s, 1.0);
            """,
            (curr_batch_id, SOURCE_NAME, VOLUME_DROPPED, VOLUME_DROPPED, t_now),
        )
    pg_conn.commit()

    # 3. Execute batch checks from DB
    engine = BatchCheckEngine(config_bundle=bundle, clock=clock)
    report = engine.run_batch_checks_from_db(pg_conn, batch_id=curr_batch_id)

    assert report.all_passed is False
    vol_findings = [
        f for f in report.findings if f.check_name == "volume_anomaly" and not f.passed
    ]
    assert len(vol_findings) == COUNT_ONE
    assert "Volume drop detected" in vol_findings[0].message

    # 4. Verify alert persisted in PostgreSQL alerts table
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT check_name, scope, severity, status, occurrence_count
            FROM alerts WHERE scope = 'Mumbai';
            """
        )
        row = cur.fetchone()
        assert row is not None
        assert row[0] == "volume_anomaly"
        assert row[1] == "Mumbai"
        assert row[3] == "active"
        assert row[4] == COUNT_ONE


def test_batch_checks_detect_missing_scope_and_cooldown_in_db(pg_conn: Connection) -> None:
    """Verifies missing scope detection and alert deduplication count incrementing in DB."""
    bundle = load_config()
    init_db(pg_conn, bundle.dataset_config)
    cleanup_test_data(pg_conn, SOURCE_NAME)

    base_date = date(2026, 3, 2)
    t_now = datetime(2026, 3, 2, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(t_now)

    # 1. Seed history containing Bengaluru
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO batch_registry (
                batch_id, source, logical_date, file_hash, status, started_at, completed_at
            ) VALUES ('HIST_BLR', %s, %s, 'hash_blr', 'completed', %s, %s);
            """,
            (SOURCE_NAME, base_date - timedelta(days=1), t_now, t_now),
        )
        cur.execute(
            """
            INSERT INTO batch_metrics (
                batch_id, source, scope, rows_received, rows_loaded,
                rows_quarantined, rows_duplicate, latest_event_time, duration_seconds
            ) VALUES ('HIST_BLR', %s, 'Bengaluru', 50, 50, 0, 0, %s, 1.0);
            """,
            (SOURCE_NAME, t_now),
        )

        # Current batch only has Mumbai (Bengaluru missing)
        cur.execute(
            """
            INSERT INTO batch_registry (
                batch_id, source, logical_date, file_hash, status, started_at, completed_at
            ) VALUES ('CURR_MISSING_BLR', %s, %s, 'hash_miss', 'completed', %s, %s);
            """,
            (SOURCE_NAME, base_date, t_now, t_now),
        )
        cur.execute(
            """
            INSERT INTO batch_metrics (
                batch_id, source, scope, rows_received, rows_loaded,
                rows_quarantined, rows_duplicate, latest_event_time, duration_seconds
            ) VALUES ('CURR_MISSING_BLR', %s, 'Mumbai', 50, 50, 0, 0, %s, 1.0);
            """,
            (SOURCE_NAME, t_now),
        )
    pg_conn.commit()

    engine = BatchCheckEngine(config_bundle=bundle, clock=clock)
    report1 = engine.run_batch_checks_from_db(pg_conn, batch_id="CURR_MISSING_BLR")

    missing = [f for f in report1.findings if f.check_name == "missing_scope"]
    assert len(missing) == COUNT_ONE
    assert missing[0].scope == "Bengaluru"

    # Verify occurrence count in DB is 1
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT occurrence_count FROM alerts
            WHERE check_name = 'missing_scope' AND scope = 'Bengaluru';
            """
        )
        assert cur.fetchone()[0] == COUNT_ONE

    # Run again 5 minutes later (inside cooldown)
    clock.advance(seconds=COOLDOWN_ADVANCE_SEC)
    report2 = engine.run_batch_checks_from_db(pg_conn, batch_id="CURR_MISSING_BLR")
    assert len(report2.suppressed_alerts) >= COUNT_ONE

    # Verify occurrence count in DB incremented to 2
    with pg_conn.cursor() as cur:
        cur.execute(
            """
            SELECT occurrence_count FROM alerts
            WHERE check_name = 'missing_scope' AND scope = 'Bengaluru';
            """
        )
        assert cur.fetchone()[0] == COUNT_TWO
