"""Integration tests for PulseCheck database initialization, loader, and pipeline."""

import hashlib
from datetime import date
from pathlib import Path
from typing import Generator

import psycopg
import pytest
from psycopg import Connection

from pulsecheck.config.loader import load_config
from pulsecheck.config.models import PulseCheckSettings
from pulsecheck.db.ddl import init_db
from pulsecheck.pipeline import PipelineRunner
from pulsecheck.schema_check import SchemaValidationError

pytestmark = pytest.mark.integration

CSV_HEADER = "trip_id,driver_id,vehicle_plate,city,pickup_time,dropoff_time,distance_km,fare_inr\n"
EXPECTED_COUNT_ONE = 1
EXPECTED_COUNT_TWO = 2
MIN_REASONS_COUNT = 2
EXPECTED_FARE_SKIP = 100.0
EXPECTED_FARE_UPDATE = 250.0


def make_row(
    trip_id: str,
    driver: str = "DRV001",
    plate: str = "MH01AB1234",
    city: str = "Mumbai",
    pickup: str = "2026-03-01 10:00:00",
    dropoff: str = "2026-03-01 10:30:00",
    distance: float = 10.0,
    fare: float = 100.0,
) -> str:
    """Helper to generate a formatted CSV row."""
    return f"{trip_id},{driver},{plate},{city},{pickup},{dropoff},{distance},{fare}\n"


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
            autocommit=False,
            connect_timeout=1,
        )
    except Exception as e:
        pytest.skip(f"PostgreSQL container is not reachable ({e}). Run docker compose up -d.")
    try:
        bundle = load_config()
        init_db(conn, bundle.dataset_config)
        yield conn
    finally:
        conn.close()


def cleanup_test_data(conn: Connection, source: str) -> None:
    """Clean up only records created by this test run."""
    with conn.cursor() as cur:
        cur.execute("DELETE FROM quarantine WHERE source = %s;", (source,))
        cur.execute("DELETE FROM batch_metrics WHERE source = %s;", (source,))
        cur.execute("DELETE FROM batch_registry WHERE source = %s;", (source,))
        cur.execute("DELETE FROM trips WHERE trip_id LIKE %s;", (f"{source}%",))
    conn.commit()


def compute_table_checksum(conn: Connection, source: str) -> str:
    """Compute deterministic checksum of loaded rows for a test source."""
    with conn.cursor() as cur:
        cur.execute(
            """
            SELECT trip_id, city, fare
            FROM trips
            WHERE trip_id LIKE %s
            ORDER BY trip_id;
            """,
            (f"{source}%",),
        )
        rows = cur.fetchall()
        content = "|".join(f"{r[0]}:{r[1]}:{r[2]}" for r in rows)
        return hashlib.sha256(content.encode("utf-8")).hexdigest()


def test_init_db_twice_does_not_fail(pg_conn: Connection) -> None:
    """Verify init-db can be executed multiple times idempotently without error."""
    bundle = load_config()
    init_db(pg_conn, bundle.dataset_config)
    init_db(pg_conn, bundle.dataset_config)


def test_idempotent_duplicate_file_load(pg_conn: Connection, tmp_path: Path) -> None:
    """Verify loading the same file twice produces identical data count and checksum."""
    source = "test_idem"
    try:
        row1 = make_row(trip_id="test_idem_1", fare=150.0)
        row2 = make_row(trip_id="test_idem_2", city="Bengaluru", fare=220.0)
        csv_file = tmp_path / "test_idem.csv"
        csv_file.write_text(f"{CSV_HEADER}{row1}{row2}")

        bundle = load_config()
        runner = PipelineRunner(config_bundle=bundle)

        res1 = runner.run(file_path=csv_file, source=source, conn=pg_conn)
        assert res1.rows_loaded == EXPECTED_COUNT_TWO
        checksum1 = compute_table_checksum(pg_conn, source)

        # Run identical file again
        res2 = runner.run(file_path=csv_file, source=source, conn=pg_conn)
        assert res2.status == "skipped"

        checksum2 = compute_table_checksum(pg_conn, source)
        assert checksum1 == checksum2
    finally:
        cleanup_test_data(pg_conn, source)


def test_partial_invalid_rows_load_valid_and_quarantine_invalid(
    pg_conn: Connection, tmp_path: Path
) -> None:
    """Verify file with invalid rows loads the valid rows and quarantines invalid ones."""
    source = "test_partial"
    try:
        row1 = make_row(trip_id="test_part_1", fare=150.0)
        row2 = make_row(
            trip_id="test_part_2",
            pickup="2026-03-01 11:00:00",
            dropoff="2026-03-01 10:00:00",
            fare=220.0,
        )
        csv_file = tmp_path / "test_partial.csv"
        csv_file.write_text(f"{CSV_HEADER}{row1}{row2}")

        bundle = load_config()
        runner = PipelineRunner(config_bundle=bundle)
        res = runner.run(file_path=csv_file, source=source, conn=pg_conn)

        assert res.rows_loaded == EXPECTED_COUNT_ONE
        assert res.rows_quarantined == EXPECTED_COUNT_ONE

        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM trips WHERE trip_id = 'test_part_1';")
            assert cur.fetchone()[0] == EXPECTED_COUNT_ONE
            cur.execute(
                "SELECT COUNT(*) FROM quarantine "
                "WHERE record_key = 'test_part_2' AND status = 'open';"
            )
            assert cur.fetchone()[0] == EXPECTED_COUNT_ONE
    finally:
        cleanup_test_data(pg_conn, source)


def test_row_failing_two_rules_appears_once_with_both_codes(
    pg_conn: Connection, tmp_path: Path
) -> None:
    """Verify a row failing two rules appears once in quarantine with both reason codes."""
    source = "test_dual_fail"
    try:
        row = make_row(
            trip_id="test_dual_1",
            pickup="2026-03-01 10:00:00",
            dropoff="2026-03-01 09:00:00",
            fare=-50.0,
        )
        csv_file = tmp_path / "test_dual.csv"
        csv_file.write_text(f"{CSV_HEADER}{row}")

        bundle = load_config()
        runner = PipelineRunner(config_bundle=bundle)
        res = runner.run(file_path=csv_file, source=source, conn=pg_conn)

        assert res.rows_quarantined == EXPECTED_COUNT_ONE

        with pg_conn.cursor() as cur:
            cur.execute("SELECT reasons FROM quarantine WHERE source = %s;", (source,))
            row_data = cur.fetchone()
            assert row_data is not None
            reasons = row_data[0]
            assert len(reasons) >= MIN_REASONS_COUNT
            codes = {r["code"] for r in reasons}
            assert "ERR_NEGATIVE_FARE" in codes
            assert "ERR_CHRONOLOGICAL_TIME" in codes
    finally:
        cleanup_test_data(pg_conn, source)


def test_corrected_row_resolves_quarantine_entry(
    pg_conn: Connection, tmp_path: Path
) -> None:
    """Verify loading a corrected row later resolves its earlier open quarantine entry."""
    source = "test_resolve"
    try:
        bad_row = make_row(trip_id="test_res_1", fare=-10.0)
        file1 = tmp_path / "bad.csv"
        file1.write_text(f"{CSV_HEADER}{bad_row}")

        bundle = load_config()
        runner = PipelineRunner(config_bundle=bundle)
        res1 = runner.run(
            file_path=file1, source=source, logical_date=date(2026, 3, 1), conn=pg_conn
        )
        assert res1.rows_quarantined == EXPECTED_COUNT_ONE

        with pg_conn.cursor() as cur:
            cur.execute("SELECT status FROM quarantine WHERE record_key = 'test_res_1';")
            assert cur.fetchone()[0] == "open"

        # Now load corrected row in file 2
        good_row = make_row(trip_id="test_res_1", fare=150.0)
        file2 = tmp_path / "corrected.csv"
        file2.write_text(f"{CSV_HEADER}{good_row}")
        res2 = runner.run(
            file_path=file2, source=source, logical_date=date(2026, 3, 2), conn=pg_conn
        )
        assert res2.rows_loaded == EXPECTED_COUNT_ONE

        # Check quarantine status is now resolved_by_load
        with pg_conn.cursor() as cur:
            cur.execute("SELECT status FROM quarantine WHERE record_key = 'test_res_1';")
            assert cur.fetchone()[0] == "resolved_by_load"
    finally:
        cleanup_test_data(pg_conn, source)


def test_conflict_policies_skip_update_quarantine(
    pg_conn: Connection, tmp_path: Path
) -> None:
    """Verify behavior of conflict policies: skip, update, quarantine."""
    source = "test_conflict"
    try:
        base_row = make_row(trip_id="test_conf_1", fare=100.0)
        base_file = tmp_path / "base.csv"
        base_file.write_text(f"{CSV_HEADER}{base_row}")

        bundle = load_config()
        runner = PipelineRunner(config_bundle=bundle)
        runner.run(file_path=base_file, source=source, logical_date=date(2026, 3, 1), conn=pg_conn)

        diff_row = make_row(trip_id="test_conf_1", fare=250.0)
        diff_file = tmp_path / "diff.csv"
        diff_file.write_text(f"{CSV_HEADER}{diff_row}")

        # 1. SKIP policy
        bundle.dataset_config.conflict_policy = "skip"
        res_skip = runner.run(
            file_path=diff_file, source=source, logical_date=date(2026, 3, 2), conn=pg_conn
        )
        assert res_skip.rows_duplicate == EXPECTED_COUNT_ONE
        with pg_conn.cursor() as cur:
            cur.execute("SELECT fare_inr FROM trips WHERE trip_id = 'test_conf_1';")
            assert float(cur.fetchone()[0]) == EXPECTED_FARE_SKIP

        # 2. UPDATE policy
        bundle.dataset_config.conflict_policy = "update"
        res_update = runner.run(
            file_path=diff_file, source=source, logical_date=date(2026, 3, 3), conn=pg_conn
        )
        assert res_update.rows_loaded == EXPECTED_COUNT_ONE
        with pg_conn.cursor() as cur:
            cur.execute("SELECT fare_inr FROM trips WHERE trip_id = 'test_conf_1';")
            assert float(cur.fetchone()[0]) == EXPECTED_FARE_UPDATE

        # 3. QUARANTINE policy
        bundle.dataset_config.conflict_policy = "quarantine"
        res_quar = runner.run(
            file_path=diff_file, source=source, logical_date=date(2026, 3, 4), conn=pg_conn
        )
        assert res_quar.rows_quarantined == EXPECTED_COUNT_ONE
        with pg_conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(*) FROM quarantine "
                "WHERE record_key = 'test_conf_1' "
                "AND reasons @> '[{\"code\": \"ERR_DUPLICATE_KEY\"}]';"
            )
            assert cur.fetchone()[0] == EXPECTED_COUNT_ONE
    finally:
        cleanup_test_data(pg_conn, source)


def test_missing_required_column_rejects_whole_file(
    pg_conn: Connection, tmp_path: Path
) -> None:
    """Verify a file with missing required column is rejected whole without loading data."""
    source = "test_missing_col"
    try:
        # Missing required pickup_time column
        bad_header = "trip_id,driver_id,vehicle_plate,city,dropoff_time,distance_km,fare_inr\n"
        bad_row = "test_miss_1,DRV001,MH01AB1234,Mumbai,2026-03-01 10:30:00,10.0,100.0\n"
        csv_file = tmp_path / "missing_col.csv"
        csv_file.write_text(f"{bad_header}{bad_row}")

        bundle = load_config()
        runner = PipelineRunner(config_bundle=bundle)

        with pytest.raises(SchemaValidationError):
            runner.run(file_path=csv_file, source=source, conn=pg_conn)

        # Check nothing loaded into data table
        with pg_conn.cursor() as cur:
            cur.execute("SELECT COUNT(*) FROM trips WHERE trip_id = 'test_miss_1';")
            assert cur.fetchone()[0] == 0
    finally:
        cleanup_test_data(pg_conn, source)
