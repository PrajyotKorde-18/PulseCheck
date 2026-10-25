"""Quarantine management for invalid data records."""

from typing import Any, Dict, List, Optional

from psycopg import Connection
from psycopg.types.json import Jsonb

from pulsecheck.validator import RowReason


class QuarantineWriter:
    """Writes failed validation rows to the PostgreSQL quarantine table."""

    def __init__(self, conn: Connection) -> None:
        self.conn = conn

    def write_quarantine_rows(
        self,
        batch_id: str,
        source: str,
        start_row_number: int,
        raw_rows: List[Dict[str, Any]],
        record_keys: List[str],
        row_reasons: List[List[RowReason]],
        scope: Optional[str] = None,
    ) -> int:
        """
        Insert invalid records into the quarantine table.

        Returns number of quarantined records inserted.
        """
        if not raw_rows:
            return 0

        insert_stmt = """
        INSERT INTO quarantine (
            batch_id,
            source,
            row_number,
            record_key,
            raw_row,
            reasons,
            scope,
            status,
            attempts,
            created_at,
            updated_at
        ) VALUES (
            %s, %s, %s, %s, %s, %s, %s, 'open', 0, NOW(), NOW()
        );
        """

        records_to_insert = []
        for i, (raw_row, record_key, reasons) in enumerate(
            zip(raw_rows, record_keys, row_reasons, strict=False)
        ):
            row_num = start_row_number + i
            reasons_payload = [
                {
                    "code": r.code,
                    "rule": r.rule,
                    "fields": r.fields,
                    "message": r.message,
                }
                for r in reasons
            ]
            records_to_insert.append(
                (
                    batch_id,
                    source,
                    row_num,
                    record_key,
                    Jsonb(raw_row),
                    Jsonb(reasons_payload),
                    scope,
                )
            )

        with self.conn.cursor() as cur:
            cur.executemany(insert_stmt, records_to_insert)

        return len(records_to_insert)

    def resolve_by_keys(self, record_keys: List[str]) -> int:
        """
        Mark open quarantine entries as resolved_by_load for successfully loaded keys.

        Uses PostgreSQL ANY(%s) syntax for efficient array containment.
        """
        if not record_keys:
            return 0

        update_stmt = """
        UPDATE quarantine
        SET status = 'resolved_by_load', updated_at = NOW()
        WHERE record_key = ANY(%s) AND status = 'open';
        """
        with self.conn.cursor() as cur:
            cur.execute(update_stmt, (record_keys,))
            return cur.rowcount
