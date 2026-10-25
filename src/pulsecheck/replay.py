"""Quarantine replay engine re-evaluating rejected records against current rules."""

import json
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import pandas as pd
from psycopg import Connection

from pulsecheck.clock import Clock, SystemClock
from pulsecheck.config.loader import ConfigBundle
from pulsecheck.loader import IdempotentLoader
from pulsecheck.quarantine import QuarantineWriter
from pulsecheck.validator import RowReason, Validator


@dataclass
class ReplaySummary:
    """Summary of quarantine replay execution."""

    total_examined: int
    replayed_loaded: int
    still_open: int
    dry_run: bool
    replayed_ids: List[int] = field(default_factory=list)
    still_open_ids: List[int] = field(default_factory=list)
    refreshed_reasons: Dict[int, List[Dict[str, Any]]] = field(default_factory=dict)

    def format_text(self) -> str:
        """Render formatted summary of replay results."""
        mode_str = " (DRY-RUN - NO CHANGES COMMITTED)" if self.dry_run else ""
        lines = [
            "=" * 60,
            f"PulseCheck Quarantine Replay Summary{mode_str}",
            "=" * 60,
            f"Total Examined Records:  {self.total_examined}",
            f"Successfully Replayed:   {self.replayed_loaded}",
            f"Remaining in Quarantine: {self.still_open}",
            f"Dry Run Mode:            {self.dry_run}",
            "=" * 60,
        ]
        return "\n".join(lines)


class QuarantineReplayer:
    """Orchestrates re-validation and idempotent loading of quarantined records."""

    def __init__(
        self,
        config_bundle: ConfigBundle,
        clock: Optional[Clock] = None,
    ) -> None:
        self.config_bundle = config_bundle
        self.clock = clock or SystemClock()
        self.dataset_config = config_bundle.dataset_config
        self.validator = Validator(config_bundle=self.config_bundle, clock=self.clock)

    def replay_records(
        self,
        records: List[Dict[str, Any]],
        dry_run: bool = False,
        conn: Optional[Connection] = None,
    ) -> ReplaySummary:
        """
        Re-validate quarantine records against current configuration.
        Each record dict has: 'id', 'record_key', 'raw_row', 'reasons',
        'scope', 'attempts', 'status'.
        """
        if not records:
            return ReplaySummary(
                total_examined=0,
                replayed_loaded=0,
                still_open=0,
                dry_run=dry_run,
            )

        # Build DataFrame from raw_row dictionaries
        df_rows: List[Dict[str, Any]] = []
        for r in records:
            raw = r.get("raw_row", {})
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}
            df_rows.append(raw)

        df = pd.DataFrame(df_rows)
        val_result = self.validator.validate(df)
        is_valid_mask = val_result.is_valid_mask.tolist()
        row_reasons = val_result.row_reasons

        now_valid_records: List[Dict[str, Any]] = []
        now_valid_indices: List[int] = []
        still_open_records: List[Dict[str, Any]] = []
        still_open_indices: List[int] = []
        refreshed_reasons_map: Dict[int, List[Dict[str, Any]]] = {}

        for idx, (rec, is_valid) in enumerate(zip(records, is_valid_mask, strict=True)):
            rec_id = rec.get("id", idx)
            if is_valid:
                now_valid_records.append(rec)
                now_valid_indices.append(rec_id)
            else:
                reasons_for_row: List[RowReason] = (
                    row_reasons[idx] if idx < len(row_reasons) else []
                )
                serialized_reasons = [
                    {
                        "code": r.code,
                        "rule": r.rule,
                        "fields": r.fields,
                        "message": r.message,
                    }
                    for r in reasons_for_row
                ]
                refreshed_reasons_map[rec_id] = serialized_reasons
                still_open_records.append(rec)
                still_open_indices.append(rec_id)

        # Apply mutations if not dry-run and connection provided
        if not dry_run and conn is not None:
            quarantine_writer = QuarantineWriter(conn)
            loader = IdempotentLoader(
                conn=conn,
                dataset_config=self.dataset_config,
                quarantine_writer=quarantine_writer,
            )

            # 1. Load now-valid rows into target table
            if now_valid_records:
                valid_df = df.iloc[[i for i, v in enumerate(is_valid_mask) if v]].copy()
                loader.load(valid_df, start_row_num=1)

                # Mark these quarantine entries as replayed
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE quarantine
                        SET status = 'replayed', updated_at = NOW()
                        WHERE id = ANY(%s);
                        """,
                        (now_valid_indices,),
                    )

            # 2. Update still-invalid rows: attempts + 1 and refreshed reasons
            for rec in still_open_records:
                rec_id = rec["id"]
                reasons_json = json.dumps(refreshed_reasons_map[rec_id])
                with conn.cursor() as cur:
                    cur.execute(
                        """
                        UPDATE quarantine
                        SET attempts = attempts + 1,
                            reasons = %s::jsonb,
                            updated_at = NOW()
                        WHERE id = %s;
                        """,
                        (reasons_json, rec_id),
                    )

            conn.commit()

        return ReplaySummary(
            total_examined=len(records),
            replayed_loaded=len(now_valid_records),
            still_open=len(still_open_records),
            dry_run=dry_run,
            replayed_ids=now_valid_indices,
            still_open_ids=still_open_indices,
            refreshed_reasons=refreshed_reasons_map,
        )

    def replay_from_db(
        self,
        conn: Connection,
        batch_id: Optional[str] = None,
        reason: Optional[str] = None,
        scope: Optional[str] = None,
        status: str = "open",
        dry_run: bool = False,
    ) -> ReplaySummary:
        """Fetch matching records from database quarantine table and replay them."""
        query = """
            SELECT
                id, batch_id, source, row_number, record_key,
                raw_row, reasons, scope, status, attempts
            FROM quarantine
            WHERE status = %s
        """
        params: List[Any] = [status]

        if batch_id:
            query += " AND batch_id = %s"
            params.append(batch_id)
        if scope:
            query += " AND scope = %s"
            params.append(scope)
        if reason:
            query += " AND reasons @> %s::jsonb"
            params.append(json.dumps([{"code": reason}]))

        query += " ORDER BY id ASC;"

        with conn.cursor() as cur:
            cur.execute(query, params)
            rows = cur.fetchall()

        records = [
            {
                "id": r[0],
                "batch_id": r[1],
                "source": r[2],
                "row_number": r[3],
                "record_key": r[4],
                "raw_row": r[5],
                "reasons": r[6],
                "scope": r[7],
                "status": r[8],
                "attempts": r[9],
            }
            for r in rows
        ]

        return self.replay_records(records, dry_run=dry_run, conn=conn)
