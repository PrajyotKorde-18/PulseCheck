"""End-to-end ingestion pipeline runner for PulseCheck."""

import hashlib
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from psycopg import Connection

from pulsecheck.checks.engine import BatchCheckEngine
from pulsecheck.checks.models import BatchCheckReport
from pulsecheck.clock import Clock, SystemClock
from pulsecheck.config.loader import ConfigBundle
from pulsecheck.config.models import PulseCheckSettings
from pulsecheck.db.connection import get_db_connection
from pulsecheck.loader import IdempotentLoader
from pulsecheck.quarantine import QuarantineWriter
from pulsecheck.readers.factory import get_reader
from pulsecheck.schema_check import check_schema
from pulsecheck.validator import Validator

HASH_CHUNK_SIZE = 65536
BATCH_ID_LENGTH = 32


@dataclass
class PipelineResult:
    """Summary of batch pipeline execution."""

    batch_id: str
    source: str
    logical_date: date
    file_path: str
    file_hash: str
    status: str  # 'completed', 'skipped', 'failed'
    rows_received: int
    rows_loaded: int
    rows_quarantined: int
    rows_duplicate: int
    duration_seconds: float
    message: str = ""
    batch_check_report: Optional[BatchCheckReport] = None
    quarantined_records: List[Dict[str, Any]] = field(default_factory=list)



def compute_file_hash(path: Path) -> str:
    """Compute SHA-256 hash of file contents in chunks."""
    hasher = hashlib.sha256()
    with open(path, "rb") as f:
        while chunk := f.read(HASH_CHUNK_SIZE):
            hasher.update(chunk)
    return hasher.hexdigest()


def compute_batch_id(source: str, logical_date: date, file_hash: str) -> str:
    """Compute deterministic batch identity string."""
    raw = f"{source}|{logical_date.isoformat()}|{file_hash}"
    return hashlib.sha256(raw.encode("utf-8")).hexdigest()[:BATCH_ID_LENGTH]


class PipelineRunner:
    """Orchestrates reading, schema check, row validation, loading, and batch metrics."""

    def __init__(
        self,
        config_bundle: ConfigBundle,
        clock: Optional[Clock] = None,
        settings: Optional[PulseCheckSettings] = None,
    ) -> None:
        self.config_bundle = config_bundle
        self.clock = clock or SystemClock()
        self.settings = settings or PulseCheckSettings()
        self.dataset_config = config_bundle.dataset_config

    def run(
        self,
        file_path: Path,
        source: str,
        logical_date: Optional[date] = None,
        conn: Optional[Connection] = None,
        dry_run: bool = False,
    ) -> PipelineResult:
        """Execute full batch pipeline on the provided file."""
        start_time = time.time()
        file_path = Path(file_path).resolve()
        if not file_path.exists():
            raise FileNotFoundError(f"Data file not found: {file_path}")

        run_date = logical_date or self.clock.now().date()
        file_hash = compute_file_hash(file_path)
        batch_id = compute_batch_id(source, run_date, file_hash)

        if conn is not None:
            return self._execute_with_conn(
                conn=conn,
                file_path=file_path,
                source=source,
                run_date=run_date,
                file_hash=file_hash,
                batch_id=batch_id,
                start_time=start_time,
            )

        if dry_run:
            return self._execute_dry_run(
                file_path=file_path,
                source=source,
                run_date=run_date,
                file_hash=file_hash,
                batch_id=batch_id,
                start_time=start_time,
            )

        try:
            with get_db_connection(self.settings) as db_conn:
                return self._execute_with_conn(
                    conn=db_conn,
                    file_path=file_path,
                    source=source,
                    run_date=run_date,
                    file_hash=file_hash,
                    batch_id=batch_id,
                    start_time=start_time,
                )
        except Exception as e:
            return self._execute_dry_run(
                file_path=file_path,
                source=source,
                run_date=run_date,
                file_hash=file_hash,
                batch_id=batch_id,
                start_time=start_time,
                offline_warning=str(e),
            )

    def _execute_dry_run(
        self,
        file_path: Path,
        source: str,
        run_date: date,
        file_hash: str,
        batch_id: str,
        start_time: float,
        offline_warning: Optional[str] = None,
    ) -> PipelineResult:
        reader = get_reader(self.dataset_config)
        validator = Validator(config_bundle=self.config_bundle, clock=self.clock)

        total_received = 0
        total_loaded = 0
        total_quarantined = 0
        total_duplicate = 0
        schema_checked = False
        chunk_size = self.dataset_config.chunk_size

        scope_role_col = (
            self.dataset_config.get_primary_column_for_role("scope")
            or self.dataset_config.get_primary_column_for_role("city")
        )
        event_time_col = (
            self.dataset_config.get_primary_column_for_role("event_time")
            or self.dataset_config.get_primary_column_for_role("pickup_time")
        )
        scope_volumes: Dict[Optional[str], int] = {}
        latest_event_time: Optional[datetime] = None
        quarantined_records: List[Dict[str, Any]] = []

        for df_chunk in reader.read_chunks(file_path, chunk_size=chunk_size):
            if not schema_checked:
                check_schema(df_chunk, self.dataset_config)
                schema_checked = True

            chunk_len = len(df_chunk)
            if chunk_len == 0:
                continue

            total_received += chunk_len
            val_result = validator.validate(df_chunk)
            summary = val_result.get_summary()
            total_loaded += summary.valid_rows
            total_quarantined += summary.quarantined_rows

            is_valid = val_result.is_valid_mask
            invalid_mask = ~is_valid
            if invalid_mask.any():
                invalid_df = df_chunk[invalid_mask]
                invalid_keys = val_result.derived_keys[invalid_mask]
                invalid_reasons = [
                    reasons
                    for idx, reasons in enumerate(val_result.row_reasons)
                    if not is_valid.iloc[idx]
                ]
                for row_dict, key, reasons in zip(
                    invalid_df.to_dict(orient="records"),
                    invalid_keys,
                    invalid_reasons,
                    strict=True,
                ):
                    scope_val = (
                        str(row_dict.get(scope_role_col))
                        if scope_role_col and scope_role_col in row_dict
                        else None
                    )
                    quarantined_records.append({
                        "record_key": key,
                        "raw_row": row_dict,
                        "reasons": reasons,
                        "scope": scope_val,
                    })

            if scope_role_col and scope_role_col in df_chunk.columns:
                is_valid = val_result.is_valid_mask
                valid_df = df_chunk[is_valid]
                for sc, grp in valid_df.groupby(scope_role_col):
                    sc_str = str(sc)
                    scope_volumes[sc_str] = scope_volumes.get(sc_str, 0) + len(grp)

            if event_time_col and event_time_col in df_chunk.columns:
                try:
                    ts_series = pd.to_datetime(df_chunk[event_time_col], errors="coerce")
                    chunk_max = ts_series.max()
                    if pd.notna(chunk_max):
                        chunk_max_dt = chunk_max.to_pydatetime()
                        if chunk_max_dt.tzinfo is None:
                            chunk_max_dt = chunk_max_dt.replace(tzinfo=timezone.utc)
                        if latest_event_time is None or chunk_max_dt > latest_event_time:
                            latest_event_time = chunk_max_dt
                except Exception:
                    pass

        duration = time.time() - start_time
        status_msg = "completed (offline validation)" if offline_warning else "completed (dry-run)"
        message = (
            f"Note: Database unavailable ({offline_warning}). Validated in-memory."
            if offline_warning
            else "Dry-run validation completed successfully."
        )

        check_report = None
        if self.config_bundle.checks.auto_check_batch:
            engine = BatchCheckEngine(self.config_bundle, self.clock)
            check_report = engine.run_checks(
                batch_id=batch_id,
                source=source,
                logical_date=run_date,
                current_scope_volumes=scope_volumes,
                latest_event_time=latest_event_time,
                history_by_scope={},
                recent_historical_scopes=set(scope_volumes.keys()),
                conn=None,
            )

        return PipelineResult(
            batch_id=batch_id,
            source=source,
            logical_date=run_date,
            file_path=str(file_path),
            file_hash=file_hash,
            status=status_msg,
            rows_received=total_received,
            rows_loaded=total_loaded,
            rows_quarantined=total_quarantined,
            rows_duplicate=total_duplicate,
            duration_seconds=duration,
            message=message,
            batch_check_report=check_report,
            quarantined_records=quarantined_records,
        )

    def _execute_with_conn(
        self,
        conn: Connection,
        file_path: Path,
        source: str,
        run_date: date,
        file_hash: str,
        batch_id: str,
        start_time: float,
    ) -> PipelineResult:
        # Check repeated file policy in batch_registry
        with conn.cursor() as cur:
            cur.execute(
                "SELECT batch_id, status FROM batch_registry "
                "WHERE file_hash = %s AND status = 'completed';",
                (file_hash,),
            )
            existing = cur.fetchone()

        if existing and self.dataset_config.repeated_file_policy == "skip":
            duration = time.time() - start_time
            return PipelineResult(
                batch_id=batch_id,
                source=source,
                logical_date=run_date,
                file_path=str(file_path),
                file_hash=file_hash,
                status="skipped",
                rows_received=0,
                rows_loaded=0,
                rows_quarantined=0,
                rows_duplicate=0,
                duration_seconds=duration,
                message=(
                    f"Repeated identical file skipped "
                    f"(previously completed in batch {existing[0]})."
                ),
            )

        # Register batch in running status
        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO batch_registry (
                    batch_id, source, logical_date, file_hash, status, started_at
                ) VALUES (%s, %s, %s, %s, 'running', NOW())
                ON CONFLICT (batch_id) DO UPDATE
                SET status = 'running', started_at = NOW(), error_message = NULL;
                """,
                (batch_id, source, run_date, file_hash),
            )
        conn.commit()

        reader = get_reader(self.dataset_config)
        validator = Validator(config_bundle=self.config_bundle, clock=self.clock)
        quarantine_writer = QuarantineWriter(conn)
        loader = IdempotentLoader(
            conn=conn,
            dataset_config=self.dataset_config,
            quarantine_writer=quarantine_writer,
        )

        total_received = 0
        total_loaded = 0
        total_quarantined = 0
        total_duplicate = 0
        schema_checked = False
        latest_event_time: Optional[datetime] = None

        chunk_size = self.dataset_config.chunk_size
        scope_role_col = (
            self.dataset_config.get_primary_column_for_role("scope")
            or self.dataset_config.get_primary_column_for_role("city")
        )
        event_time_col = self.dataset_config.get_primary_column_for_role("pickup_time")
        scope_counts: Dict[str, Dict[str, int]] = {}
        quarantined_records: List[Dict[str, Any]] = []

        try:
            for _chunk_idx, df_chunk in enumerate(
                reader.read_chunks(file_path, chunk_size=chunk_size)
            ):
                # Run schema check once on first chunk
                if not schema_checked:
                    check_schema(df_chunk, self.dataset_config)
                    schema_checked = True

                chunk_len = len(df_chunk)
                if chunk_len == 0:
                    continue

                total_received += chunk_len
                start_row_num = total_received - chunk_len + 1

                if scope_role_col and scope_role_col in df_chunk.columns:
                    for sc, grp in df_chunk.groupby(scope_role_col):
                        sc_str = str(sc)
                        if sc_str not in scope_counts:
                            scope_counts[sc_str] = {
                                "received": 0,
                                "loaded": 0,
                                "quarantined": 0,
                                "duplicate": 0,
                            }
                        scope_counts[sc_str]["received"] += len(grp)

                # Validate rows
                val_result = validator.validate(df_chunk)
                is_valid = val_result.is_valid_mask

                # Separate valid and invalid subsets
                valid_df = df_chunk[is_valid]
                valid_keys = val_result.derived_keys[is_valid]

                invalid_df = df_chunk[~is_valid]
                invalid_keys = val_result.derived_keys[~is_valid]
                invalid_reasons = [
                    reasons
                    for idx, reasons in enumerate(val_result.row_reasons)
                    if not is_valid.iloc[idx]
                ]

                # Quarantine invalid rows
                if not invalid_df.empty:
                    raw_rows: List[Dict[str, Any]] = invalid_df.to_dict(orient="records")
                    scope_val = (
                        str(invalid_df[scope_role_col].iloc[0])
                        if scope_role_col and scope_role_col in invalid_df.columns
                        else None
                    )
                    quarantined_count = quarantine_writer.write_quarantine_rows(
                        batch_id=batch_id,
                        source=source,
                        start_row_number=start_row_num,
                        raw_rows=raw_rows,
                        record_keys=list(invalid_keys),
                        row_reasons=invalid_reasons,
                        scope=scope_val,
                    )
                    total_quarantined += quarantined_count

                    for row_dict, key, reasons in zip(
                        raw_rows,
                        list(invalid_keys),
                        invalid_reasons,
                        strict=True,
                    ):
                        quarantined_records.append({
                            "record_key": key,
                            "raw_row": row_dict,
                            "reasons": reasons,
                            "scope": scope_val,
                        })

                    if scope_role_col and scope_role_col in invalid_df.columns:
                        for sc, grp in invalid_df.groupby(scope_role_col):
                            sc_str = str(sc)
                            if sc_str in scope_counts:
                                scope_counts[sc_str]["quarantined"] += len(grp)

                # Load valid rows
                if not valid_df.empty:
                    scope_val = (
                        str(valid_df[scope_role_col].iloc[0])
                        if scope_role_col and scope_role_col in valid_df.columns
                        else None
                    )
                    load_res = loader.load_chunk(
                        df_chunk=valid_df,
                        derived_keys=valid_keys,
                        batch_id=batch_id,
                        source=source,
                        start_row_number=start_row_num,
                        scope=scope_val,
                    )
                    total_loaded += load_res.rows_loaded
                    total_duplicate += load_res.rows_duplicate
                    total_quarantined += load_res.rows_quarantined

                    if scope_role_col and scope_role_col in valid_df.columns:
                        for sc, grp in valid_df.groupby(scope_role_col):
                            sc_str = str(sc)
                            if sc_str in scope_counts:
                                scope_counts[sc_str]["loaded"] += len(grp)

                # Track latest event timestamp if available
                if event_time_col and event_time_col in df_chunk.columns:
                    try:
                        ts_series = pd.to_datetime(df_chunk[event_time_col], errors="coerce")
                        chunk_max = ts_series.max()
                        if pd.notna(chunk_max):
                            chunk_max_dt = chunk_max.to_pydatetime()
                            if chunk_max_dt.tzinfo is None:
                                chunk_max_dt = chunk_max_dt.replace(tzinfo=timezone.utc)
                            if latest_event_time is None or chunk_max_dt > latest_event_time:
                                latest_event_time = chunk_max_dt
                    except Exception:
                        pass

                # Handle transaction scope commit per chunk
                if self.dataset_config.transaction_scope == "chunk":
                    conn.commit()

            duration = time.time() - start_time

            # Record batch_metrics: overall ('all') and per scope
            with conn.cursor() as cur:
                cur.execute(
                    """
                    INSERT INTO batch_metrics (
                        batch_id, source, scope, rows_received, rows_loaded,
                        rows_quarantined, rows_duplicate, latest_event_time,
                        duration_seconds, created_at
                    ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
                    """,
                    (
                        batch_id,
                        source,
                        "all",
                        total_received,
                        total_loaded,
                        total_quarantined,
                        total_duplicate,
                        latest_event_time,
                        duration,
                    ),
                )

                for sc_name, sc_data in scope_counts.items():
                    cur.execute(
                        """
                        INSERT INTO batch_metrics (
                            batch_id, source, scope, rows_received, rows_loaded,
                            rows_quarantined, rows_duplicate, latest_event_time,
                            duration_seconds, created_at
                        ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, NOW());
                        """,
                        (
                            batch_id,
                            source,
                            sc_name,
                            sc_data["received"],
                            sc_data["loaded"],
                            sc_data["quarantined"],
                            sc_data["duplicate"],
                            latest_event_time,
                            duration,
                        ),
                    )

                cur.execute(
                    """
                    UPDATE batch_registry
                    SET status = 'completed', completed_at = NOW()
                    WHERE batch_id = %s;
                    """,
                    (batch_id,),
                )

            # Final commit for batch transaction scope or metrics
            conn.commit()

            check_report = None
            if self.config_bundle.checks.auto_check_batch:
                engine = BatchCheckEngine(self.config_bundle, self.clock)
                check_report = engine.run_batch_checks_from_db(conn, batch_id=batch_id)

            return PipelineResult(
                batch_id=batch_id,
                source=source,
                logical_date=run_date,
                file_path=str(file_path),
                file_hash=file_hash,
                status="completed",
                rows_received=total_received,
                rows_loaded=total_loaded,
                rows_quarantined=total_quarantined,
                rows_duplicate=total_duplicate,
                duration_seconds=duration,
                message="Batch successfully processed.",
                batch_check_report=check_report,
                quarantined_records=quarantined_records,
            )

        except Exception as e:
            conn.rollback()
            duration = time.time() - start_time
            # Record failed status in batch_registry
            try:
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE batch_registry
                        SET status = 'failed', completed_at = NOW(), error_message = %s
                        WHERE batch_id = %s;
                        """,
                        (str(e), batch_id),
                    )
                conn.commit()
            except Exception:
                pass
            raise
