"""Batch verification check engine executing volume, freshness, missing scopes, and alerts."""

import logging
from datetime import date, datetime
from typing import Dict, List, Optional, Set

from psycopg import Connection

from pulsecheck.checks.alerts import AlertManager
from pulsecheck.checks.freshness import FreshnessChecker
from pulsecheck.checks.models import (
    BatchCheckReport,
    BatchMetricRecord,
    CheckFinding,
)
from pulsecheck.checks.scopes import MissingScopesChecker
from pulsecheck.checks.volume import VolumeChecker
from pulsecheck.clock import Clock
from pulsecheck.config.loader import ConfigBundle

logger = logging.getLogger("pulsecheck.checks")


class BatchCheckEngine:
    """Orchestrates all batch-level verification checks and alert updates."""

    def __init__(self, config_bundle: ConfigBundle, clock: Clock) -> None:
        self.config_bundle = config_bundle
        self.clock = clock
        self.checks_config = config_bundle.checks
        self.settings = config_bundle.settings

        self.volume_checker = VolumeChecker(self.checks_config.volume)
        self.freshness_checker = FreshnessChecker(self.checks_config.freshness, clock=self.clock)
        self.scopes_checker = MissingScopesChecker(self.checks_config.missing_scopes)
        self.alert_manager = AlertManager(
            config=self.checks_config.alerts,
            settings=self.settings,
            clock=self.clock,
        )

    def run_checks(
        self,
        batch_id: str,
        source: str,
        logical_date: date,
        current_scope_volumes: Dict[Optional[str], int],
        latest_event_time: Optional[datetime],
        history_by_scope: Dict[Optional[str], List[BatchMetricRecord]],
        recent_historical_scopes: Set[str],
        conn: Optional[Connection] = None,
    ) -> BatchCheckReport:
        """
        Execute volume, missing scope, and freshness checks given metrics data.

        Args:
            batch_id: Unique batch identifier.
            source: Source feed identifier.
            logical_date: Business date of the batch.
            current_scope_volumes: Map of scope name -> rows loaded in current batch.
            latest_event_time: Maximum event timestamp in the current batch.
            history_by_scope: Historical batch metrics mapped per scope.
            recent_historical_scopes: All scope names seen in lookback window.
            conn: Optional database connection for alert persistence.
        """
        findings: List[CheckFinding] = []
        current_weekday = logical_date.weekday()

        # 1. Missing Scopes Check
        current_scopes_set: Set[str] = {s for s in current_scope_volumes.keys() if s}
        missing_findings = self.scopes_checker.evaluate(
            current_scopes=current_scopes_set,
            recent_historical_scopes=recent_historical_scopes,
        )
        findings.extend(missing_findings)

        # 2. Volume Anomaly Check (evaluates current scopes + any missing scopes with rows=0)
        expected_scopes_list = sorted(recent_historical_scopes)
        volume_findings = self.volume_checker.evaluate_all_scopes(
            current_scope_volumes=current_scope_volumes,
            expected_scopes=expected_scopes_list,
            history_by_scope=history_by_scope,
            current_weekday=current_weekday,
        )
        findings.extend(volume_findings)

        # 3. Freshness & Future Timestamp Check
        freshness_finding = self.freshness_checker.evaluate(latest_event_time=latest_event_time)
        findings.append(freshness_finding)

        # 4. Alert Lifecycle & Deduplication
        active, notified, suppressed, auto_closed = self.alert_manager.process_findings(
            findings=findings,
            conn=conn,
        )

        all_passed = all(f.passed for f in findings)

        return BatchCheckReport(
            batch_id=batch_id,
            source=source,
            logical_date=logical_date,
            all_passed=all_passed,
            findings=findings,
            active_alerts=active,
            notified_alerts=notified,
            suppressed_alerts=suppressed,
            auto_closed_alerts=auto_closed,
        )

    def run_batch_checks_from_db(self, conn: Connection, batch_id: str) -> BatchCheckReport:
        """Query batch and history from database tables and execute checks."""
        # Fetch batch metadata
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT source, logical_date
                FROM batch_registry
                WHERE batch_id = %s;
                """,
                (batch_id,),
            )
            reg_row = cur.fetchone()
            if not reg_row:
                raise ValueError(f"Batch '{batch_id}' not found in batch_registry.")
            source, logical_date = reg_row[0], reg_row[1]

            # Fetch current batch metrics
            cur.execute(
                """
                SELECT scope, rows_loaded, latest_event_time
                FROM batch_metrics
                WHERE batch_id = %s;
                """,
                (batch_id,),
            )
            metric_rows = cur.fetchall()

            current_scope_volumes: Dict[Optional[str], int] = {}
            latest_event_time: Optional[datetime] = None
            for row in metric_rows:
                scope_val = row[0]
                rows_loaded = row[1]
                event_time = row[2]
                if scope_val != "all":
                    current_scope_volumes[scope_val] = rows_loaded
                if event_time and (latest_event_time is None or event_time > latest_event_time):
                    latest_event_time = event_time

            # If no scoped rows exist, use 'all' scope
            if not current_scope_volumes:
                for row in metric_rows:
                    if row[0] == "all":
                        current_scope_volumes["all"] = row[1]

            # Fetch recent historical scopes
            lookback_batches = self.checks_config.missing_scopes.lookback_batches
            cur.execute(
                """
                SELECT DISTINCT m.scope
                FROM batch_metrics m
                JOIN batch_registry r ON m.batch_id = r.batch_id
                WHERE m.source = %s
                  AND r.status = 'completed'
                  AND r.logical_date < %s
                  AND m.scope IS NOT NULL
                  AND m.scope != 'all'
                ORDER BY m.scope
                LIMIT %s;
                """,
                (source, logical_date, lookback_batches * 5),
            )
            recent_scopes = {r[0] for r in cur.fetchall()}

            # Fetch history per scope
            window = self.checks_config.volume.history_window_batches
            history_by_scope: Dict[Optional[str], List[BatchMetricRecord]] = {}
            all_scopes_to_query = list(current_scope_volumes.keys()) + list(recent_scopes)
            for sc in set(all_scopes_to_query):
                cur.execute(
                    """
                    SELECT m.batch_id, m.source, m.scope, r.logical_date,
                           m.rows_received, m.rows_loaded, m.rows_quarantined,
                           m.rows_duplicate, m.latest_event_time, m.created_at
                    FROM batch_metrics m
                    JOIN batch_registry r ON m.batch_id = r.batch_id
                    WHERE m.source = %s
                      AND (m.scope = %s OR (%s IS NULL AND m.scope IS NULL))
                      AND r.status = 'completed'
                      AND r.logical_date < %s
                    ORDER BY r.logical_date DESC
                    LIMIT %s;
                    """,
                    (source, sc, sc, logical_date, window * 7),
                )
                h_rows = cur.fetchall()
                history_by_scope[sc] = [
                    BatchMetricRecord(
                        batch_id=hr[0],
                        source=hr[1],
                        scope=hr[2],
                        logical_date=hr[3],
                        rows_received=hr[4],
                        rows_loaded=hr[5],
                        rows_quarantined=hr[6],
                        rows_duplicate=hr[7],
                        latest_event_time=hr[8],
                        created_at=hr[9],
                    )
                    for hr in h_rows
                ]

        return self.run_checks(
            batch_id=batch_id,
            source=source,
            logical_date=logical_date,
            current_scope_volumes=current_scope_volumes,
            latest_event_time=latest_event_time,
            history_by_scope=history_by_scope,
            recent_historical_scopes=recent_scopes,
            conn=conn,
        )
