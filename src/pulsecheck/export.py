"""Exporter for quarantine rejected rows into CSV for operational remediation."""

import csv
import json
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
from psycopg import Connection

from pulsecheck.config.loader import ConfigBundle

DEFAULT_EXPORT_PATH = "reports/quarantine_rejects.csv"


def format_human_reasons(reasons_data: Any) -> str:
    """Format raw JSON reasons array into human-readable semicolon-delimited string."""
    if isinstance(reasons_data, str):
        try:
            reasons_data = json.loads(reasons_data)
        except Exception:
            return str(reasons_data)

    if not isinstance(reasons_data, list):
        return "UNKNOWN_ERROR"

    parts: List[str] = []
    for r in reasons_data:
        if isinstance(r, dict):
            code = r.get("code", "ERROR")
            msg = r.get("message", "Validation failed")
            parts.append(f"{code}: {msg}")
        elif isinstance(r, str):
            parts.append(r)

    return "; ".join(parts) if parts else "UNKNOWN_ERROR"


class QuarantineExporter:
    """Exports open quarantine entries to CSV for data correction."""

    def __init__(self, config_bundle: ConfigBundle) -> None:
        self.config_bundle = config_bundle
        self.dataset_config = config_bundle.dataset_config

    def export_records_to_csv(
        self,
        records: List[Dict[str, Any]],
        output_path: Path,
    ) -> Path:
        """
        Convert quarantine records into a CSV containing original values + reason column.
        Each record has: 'id', 'batch_id', 'source', 'scope', 'raw_row', 'reasons'.
        """
        output_path = Path(output_path).resolve()
        output_path.parent.mkdir(parents=True, exist_ok=True)

        if not records:
            # Write empty dataframe with dataset columns + metadata columns
            cols = [col.name for col in self.dataset_config.columns]
            cols.extend(["quarantine_id", "quarantine_reason", "quarantine_scope", "batch_id"])
            empty_df = pd.DataFrame(columns=cols)
            empty_df.to_csv(output_path, index=False)
            return output_path

        rows_to_export: List[Dict[str, Any]] = []
        for r in records:
            raw = r.get("raw_row", {})
            if isinstance(raw, str):
                try:
                    raw = json.loads(raw)
                except Exception:
                    raw = {}

            export_row = dict(raw)
            export_row["quarantine_id"] = r.get("id")
            export_row["quarantine_reason"] = format_human_reasons(r.get("reasons"))
            export_row["quarantine_scope"] = r.get("scope")
            export_row["quarantine_batch_id"] = r.get("batch_id")
            rows_to_export.append(export_row)

        df = pd.DataFrame(rows_to_export)
        df.to_csv(output_path, index=False, quoting=csv.QUOTE_MINIMAL)
        return output_path

    def export_from_db(
        self,
        conn: Connection,
        output_path: Path,
        batch_id: Optional[str] = None,
        reason: Optional[str] = None,
        scope: Optional[str] = None,
        status: str = "open",
    ) -> Path:
        """Fetch open quarantine records from database and export to CSV."""
        query = """
            SELECT id, batch_id, source, row_number, record_key, raw_row, reasons, scope
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
            }
            for r in rows
        ]

        return self.export_records_to_csv(records, output_path)
