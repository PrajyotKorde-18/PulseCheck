"""Idempotent database loader with configurable conflict resolution and transaction scopes."""

from dataclasses import dataclass
from typing import Any, Dict, List, Optional, Set

import numpy as np
import pandas as pd
from psycopg import Connection, sql

from pulsecheck.config.models import DatasetConfig
from pulsecheck.quarantine import QuarantineWriter
from pulsecheck.validator import RowReason


@dataclass
class LoadResult:
    """Summary metrics of a chunk load operation."""

    rows_loaded: int = 0
    rows_duplicate: int = 0
    rows_quarantined: int = 0
    loaded_keys: List[str] = None

    def __post_init__(self) -> None:
        if self.loaded_keys is None:
            self.loaded_keys = []


def sanitize_value(val: Any) -> Any:
    """Sanitize pandas / numpy types for psycopg 3 binding."""
    if pd.isna(val) or val is pd.NA:
        return None
    if isinstance(val, (np.integer, int)):
        return int(val)
    if isinstance(val, (np.floating, float)):
        return float(val)
    if isinstance(val, (np.bool_, bool)):
        return bool(val)
    if isinstance(val, pd.Timestamp):
        return val.to_pydatetime()
    return val


class IdempotentLoader:
    """
    Loads validated rows into the PostgreSQL target table with idempotent upsert.

    Supports three conflict policies when a record_key already exists:
    - 'skip': ON CONFLICT DO NOTHING (counts as duplicate)
    - 'update': ON CONFLICT DO UPDATE SET ... (updates non-key attributes)
    - 'quarantine': Existing DB key conflicts are routed to quarantine table
    """

    def __init__(
        self,
        conn: Connection,
        dataset_config: DatasetConfig,
        quarantine_writer: QuarantineWriter,
    ) -> None:
        self.conn = conn
        self.dataset_config = dataset_config
        self.quarantine_writer = quarantine_writer
        self.table_name = dataset_config.table_name
        self.conflict_policy = dataset_config.conflict_policy

    def load_chunk(
        self,
        df_chunk: pd.DataFrame,
        derived_keys: pd.Series,
        batch_id: str,
        source: str,
        start_row_number: int,
        scope: Optional[str] = None,
    ) -> LoadResult:
        """
        Insert or upsert a chunk of validated rows into the database.

        Returns LoadResult containing counts and list of loaded keys.
        """
        if df_chunk.empty:
            return LoadResult()

        # Identify physical columns defined in dataset config present in df
        target_columns = [
            col.name for col in self.dataset_config.columns if col.name in df_chunk.columns
        ]

        # Prepare records with sanitized values and record_key
        records: List[Dict[str, Any]] = []
        for idx, (_, row) in enumerate(df_chunk.iterrows()):
            key = str(derived_keys.iloc[idx])
            record_dict = {col: sanitize_value(row[col]) for col in target_columns}
            record_dict["record_key"] = key
            record_dict["_row_number"] = start_row_number + idx
            records.append(record_dict)

        # Build column identifiers
        all_cols = target_columns + ["record_key"]
        cols_sql = sql.SQL(", ").join(sql.Identifier(c) for c in all_cols)

        # Build row placeholders and flat parameter list
        row_placeholders = []
        flat_params: List[Any] = []
        for rec in records:
            placeholders = sql.SQL(", ").join([sql.Placeholder()] * len(all_cols))
            row_placeholders.append(sql.SQL("({})").format(placeholders))
            for c in all_cols:
                flat_params.append(rec[c])

        values_sql = sql.SQL(", ").join(row_placeholders)

        # Build conflict clause based on policy
        if self.conflict_policy == "skip":
            conflict_sql = sql.SQL(
                "ON CONFLICT (record_key) DO NOTHING RETURNING record_key"
            )
        elif self.conflict_policy == "update":
            update_clauses = [
                sql.SQL("{} = EXCLUDED.{}").format(sql.Identifier(c), sql.Identifier(c))
                for c in target_columns
            ]
            update_clauses.append(sql.SQL("_loaded_at = NOW()"))
            conflict_sql = sql.SQL(
                "ON CONFLICT (record_key) DO UPDATE SET {} RETURNING record_key"
            ).format(sql.SQL(", ").join(update_clauses))
        elif self.conflict_policy == "quarantine":
            # With quarantine policy, do not overwrite; return inserted keys to detect collisions
            conflict_sql = sql.SQL(
                "ON CONFLICT (record_key) DO NOTHING RETURNING record_key"
            )
        else:
            raise ValueError(f"Unknown conflict policy: {self.conflict_policy}")

        query = sql.SQL(
            "INSERT INTO {} ({}) VALUES {} {}"
        ).format(
            sql.Identifier(self.table_name),
            cols_sql,
            values_sql,
            conflict_sql,
        )

        with self.conn.cursor() as cur:
            cur.execute(query, flat_params)
            rows = cur.fetchall()
            returned_keys: Set[str] = {r[0] for r in rows}

        loaded_keys = list(returned_keys)
        rows_loaded = len(loaded_keys)
        rows_duplicate = 0
        rows_quarantined = 0

        # Mark any previously open quarantine records for these keys as resolved
        if loaded_keys:
            self.quarantine_writer.resolve_by_keys(loaded_keys)

        if self.conflict_policy == "skip":
            rows_duplicate = len(records) - rows_loaded
        elif self.conflict_policy == "update":
            rows_duplicate = 0  # All incoming rows are loaded / updated
        elif self.conflict_policy == "quarantine":
            # For quarantine policy, records not returned collided with existing DB keys
            collided_records = [
                rec for rec in records if rec["record_key"] not in returned_keys
            ]
            if collided_records:
                collided_raw_rows = [
                    {k: v for k, v in rec.items() if not k.startswith("_")}
                    for rec in collided_records
                ]
                collided_keys = [rec["record_key"] for rec in collided_records]
                collided_row_nums = [rec["_row_number"] for rec in collided_records]
                collided_reasons = [
                    [
                        RowReason(
                            code="ERR_DUPLICATE_KEY",
                            rule="conflict_policy",
                            fields=["record_key"],
                            message="Record key already exists in target database table",
                        )
                    ]
                    for _ in collided_records
                ]

                # Write collided rows to quarantine table
                self.quarantine_writer.write_quarantine_rows(
                    batch_id=batch_id,
                    source=source,
                    start_row_number=collided_row_nums[0],
                    raw_rows=collided_raw_rows,
                    record_keys=collided_keys,
                    row_reasons=collided_reasons,
                    scope=scope,
                )
                rows_quarantined = len(collided_records)

        return LoadResult(
            rows_loaded=rows_loaded,
            rows_duplicate=rows_duplicate,
            rows_quarantined=rows_quarantined,
            loaded_keys=loaded_keys,
        )
