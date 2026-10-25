"""Integration tests for quarantine replay, diagnostics, and reject export with real PostgreSQL."""

import json
from pathlib import Path
from typing import Generator

import pandas as pd
import psycopg
import pytest
from psycopg import Connection

from pulsecheck.config.loader import ConfigBundle, load_config
from pulsecheck.config.models import (
    ChecksConfigFile,
    CitiesConfigFile,
    DatasetConfigFile,
    PulseCheckSettings,
    RulesConfigFile,
)
from pulsecheck.db.ddl import init_db
from pulsecheck.diagnostics import QuarantineDiagnostics
from pulsecheck.export import QuarantineExporter
from pulsecheck.pipeline import PipelineRunner
from pulsecheck.replay import QuarantineReplayer

pytestmark = pytest.mark.integration

EXPECTED_COUNT_ZERO = 0
EXPECTED_COUNT_ONE = 1
EXPECTED_COUNT_TWO = 2
TEST_SOURCE_REPLAY = "test_replay_src"


@pytest.fixture(scope="session")
def db_settings() -> PulseCheckSettings:
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
    with conn.cursor() as cur:
        cur.execute("DELETE FROM quarantine WHERE source = %s;", (source,))
        cur.execute("DELETE FROM batch_metrics WHERE source = %s;", (source,))
        cur.execute("DELETE FROM batch_registry WHERE source = %s;", (source,))
        cur.execute("DELETE FROM trips WHERE trip_id LIKE %s;", (f"{source}%",))
    conn.commit()


def test_replay_idempotency_and_dry_run(pg_conn: Connection, tmp_path: Path) -> None:
    """Verifies dry-run changes nothing, replay loads valid rows, and repeat has 0 changes."""
    bundle = load_config()
    source = TEST_SOURCE_REPLAY
    cleanup_test_data(pg_conn, source)

    # 1. Insert a mock quarantine entry that has an invalid phone
    raw_row = {
        "trip_id": f"{source}_T01",
        "pickup_time": "2026-03-01 10:00:00+0000",
        "delivery_time": "2026-03-01 10:30:00+0000",
        "pickup_lat": 19.0760,
        "pickup_lon": 72.8777,
        "city": "Mumbai",
        "driver_phone": "12345",  # Invalid
        "postal_code": "400001",
        "vehicle_plate": "MH01AB1234",
        "trip_status": "completed",
        "trip_distance_km": 10.0,
    }
    reasons = [{"code": "ERR_INVALID_PHONE", "rule": "RULE_PHONE_FORMAT", "message": "Bad phone"}]

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quarantine (
                batch_id, source, row_number, record_key, raw_row, reasons, scope, status, attempts
            ) VALUES ('BATCH_REPLAY_1', %s, 1, %s, %s::jsonb, %s::jsonb, 'Mumbai', 'open', 0)
            RETURNING id;
            """,
            (source, f"{source}_T01", json.dumps(raw_row), json.dumps(reasons)),
        )
        q_id = cur.fetchone()[0]
    pg_conn.commit()

    # 2. Loosen config so phone is no longer invalid
    modified_rules = [
        r.model_copy(update={"enabled": False}) if r.rule_id == "RULE_PHONE_FORMAT" else r
        for r in bundle.rules
    ]
    loosened_bundle = ConfigBundle(
        dataset_file=DatasetConfigFile(dataset=bundle.dataset_config),
        rules_file=RulesConfigFile(rules=modified_rules),
        checks_file=ChecksConfigFile(batch_checks=bundle.checks),
        cities_file=CitiesConfigFile(cities=bundle.cities),
        settings=bundle.settings,
        config_dir=bundle.config_dir,
    )

    replayer = QuarantineReplayer(loosened_bundle)

    # 3. Dry-run: table state must not change
    summary_dry = replayer.replay_from_db(conn=pg_conn, dry_run=True, batch_id="BATCH_REPLAY_1")
    assert summary_dry.total_examined == EXPECTED_COUNT_ONE
    assert summary_dry.replayed_loaded == EXPECTED_COUNT_ONE

    # Verify quarantine still open and trips table still empty
    with pg_conn.cursor() as cur:
        cur.execute("SELECT status, attempts FROM quarantine WHERE id = %s;", (q_id,))
        row_state = cur.fetchone()
        assert row_state[0] == "open"
        assert row_state[1] == EXPECTED_COUNT_ZERO

        cur.execute("SELECT COUNT(*) FROM trips WHERE trip_id = %s;", (f"{source}_T01",))
        assert cur.fetchone()[0] == EXPECTED_COUNT_ZERO

    # 4. Real replay: now-valid row loaded and marked 'replayed'
    summary_real = replayer.replay_from_db(conn=pg_conn, dry_run=False, batch_id="BATCH_REPLAY_1")
    assert summary_real.replayed_loaded == EXPECTED_COUNT_ONE

    with pg_conn.cursor() as cur:
        cur.execute("SELECT status FROM quarantine WHERE id = %s;", (q_id,))
        assert cur.fetchone()[0] == "replayed"

        cur.execute("SELECT COUNT(*) FROM trips WHERE trip_id = %s;", (f"{source}_T01",))
        assert cur.fetchone()[0] == EXPECTED_COUNT_ONE

    # 5. Replay a second time: reports zero changes, creates no duplicate rows
    summary_second = replayer.replay_from_db(conn=pg_conn, dry_run=False, batch_id="BATCH_REPLAY_1")
    assert summary_second.total_examined == EXPECTED_COUNT_ZERO
    assert summary_second.replayed_loaded == EXPECTED_COUNT_ZERO

    with pg_conn.cursor() as cur:
        cur.execute("SELECT COUNT(*) FROM trips WHERE trip_id = %s;", (f"{source}_T01",))
        assert cur.fetchone()[0] == EXPECTED_COUNT_ONE

    cleanup_test_data(pg_conn, source)


def test_diagnose_and_export_correction_loop(pg_conn: Connection, tmp_path: Path) -> None:
    """Verifies diagnose on known set, then export -> correct -> run resolves quarantine."""
    bundle = load_config()
    source = "test_diagnose_src"
    cleanup_test_data(pg_conn, source)

    # Insert known set of quarantine rows:
    # Row 1: Bad phone
    # Row 2: Bad phone + bad plate
    raw1 = {
        "trip_id": f"{source}_T01",
        "pickup_time": "2026-03-01 10:00:00+0000",
        "delivery_time": "2026-03-01 10:30:00+0000",
        "pickup_lat": 19.0760,
        "pickup_lon": 72.8777,
        "city": "Mumbai",
        "driver_phone": "12345",
        "postal_code": "400001",
        "vehicle_plate": "MH01AB1234",
        "trip_status": "completed",
        "trip_distance_km": 10.0,
    }
    raw2 = {
        "trip_id": f"{source}_T02",
        "pickup_time": "2026-03-01 11:00:00+0000",
        "delivery_time": "2026-03-01 11:30:00+0000",
        "pickup_lat": 19.0760,
        "pickup_lon": 72.8777,
        "city": "Mumbai",
        "driver_phone": "12345",
        "postal_code": "400001",
        "vehicle_plate": "INVALID_PLATE",
        "trip_status": "completed",
        "trip_distance_km": 10.0,
    }

    reasons1 = json.dumps([{"code": "ERR_INVALID_PHONE"}])
    reasons2 = json.dumps([{"code": "ERR_INVALID_PHONE"}, {"code": "ERR_INVALID_PLATE"}])

    with pg_conn.cursor() as cur:
        cur.execute(
            """
            INSERT INTO quarantine (
                batch_id, source, row_number, record_key, raw_row, reasons, scope, status, attempts
            ) VALUES
            ('BATCH_DIAG_1', %s, 1, %s, %s::jsonb, %s::jsonb, 'Mumbai', 'open', 0),
            ('BATCH_DIAG_1', %s, 2, %s, %s::jsonb, %s::jsonb, 'Mumbai', 'open', 0);
            """,
            (
                source,
                f"{source}_T01",
                json.dumps(raw1),
                reasons1,
                source,
                f"{source}_T02",
                json.dumps(raw2),
                reasons2,
            ),
        )
    pg_conn.commit()

    # 1. Diagnose matches counts computed independently
    diagnostics = QuarantineDiagnostics(bundle)
    report = diagnostics.analyze_from_db(conn=pg_conn, source=source)
    assert report.total_quarantined_records == EXPECTED_COUNT_TWO

    phone_stat = next(s for s in report.reasons_summary if s.code == "ERR_INVALID_PHONE")
    plate_stat = next(s for s in report.reasons_summary if s.code == "ERR_INVALID_PLATE")
    assert phone_stat.count == EXPECTED_COUNT_TWO
    assert plate_stat.count == EXPECTED_COUNT_ONE

    # 2. Export rejects to CSV
    exporter = QuarantineExporter(bundle)
    rejects_file = tmp_path / "rejects.csv"
    exporter.export_from_db(conn=pg_conn, output_path=rejects_file, source=source)
    assert rejects_file.exists()

    df_rejects = pd.read_csv(rejects_file)
    assert len(df_rejects) == EXPECTED_COUNT_TWO

    # 3. Data owner corrects values in the file
    df_rejects["driver_phone"] = "9820098200"
    df_rejects["vehicle_plate"] = "MH01AB1234"
    # Drop export metadata columns before resubmitting to run pipeline
    corrected_cols = [c for c in df_rejects.columns if not c.startswith("quarantine_")]
    corrected_file = tmp_path / "corrected_trips.csv"
    df_rejects[corrected_cols].to_csv(corrected_file, index=False)

    # 4. Resubmit corrected file through pipeline runner
    runner = PipelineRunner(config_bundle=bundle)
    res = runner.run(
        file_path=corrected_file,
        source=source,
        conn=pg_conn,
    )
    assert res.rows_loaded == EXPECTED_COUNT_TWO
    assert res.rows_quarantined == EXPECTED_COUNT_ZERO

    # 5. Quarantine entries are automatically resolved by load
    with pg_conn.cursor() as cur:
        cur.execute(
            "SELECT status FROM quarantine WHERE record_key IN (%s, %s);",
            (f"{source}_T01", f"{source}_T02"),
        )
        statuses = [r[0] for r in cur.fetchall()]
        assert all(s == "resolved_by_load" for s in statuses)

    cleanup_test_data(pg_conn, source)
