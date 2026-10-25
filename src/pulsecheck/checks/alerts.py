import hashlib
import json
import logging
import urllib.request
from typing import Dict, List, Optional, Tuple

from psycopg import Connection

from pulsecheck.checks.models import AlertRecord, CheckFinding
from pulsecheck.clock import Clock
from pulsecheck.config.models import AlertsConfig, PulseCheckSettings

logger = logging.getLogger("pulsecheck.alerts")

SEVERITY_ORDER: Dict[str, int] = {
    "INFO": 1,
    "LOW": 2,
    "MEDIUM": 3,
    "WARNING": 4,
    "HIGH": 5,
    "CRITICAL": 6,
}
INITIAL_COUNT = 1
RESET_HEALTHY_COUNT = 0
HTTP_TIMEOUT_SECONDS = 5.0
MAX_FINGERPRINT_LEN = 64
PREFIX_LEN_20 = 20
HASH_DIGEST_LEN_16 = 16


def compute_fingerprint(check_name: str, scope: Optional[str], severity: str) -> str:
    """Derive deterministic fingerprint: check + scope + severity."""
    scope_str = scope if scope else "all"
    raw_str = f"{check_name}:{scope_str}:{severity}"
    if len(raw_str) > MAX_FINGERPRINT_LEN:
        check_p = check_name[:PREFIX_LEN_20]
        scope_p = scope_str[:PREFIX_LEN_20]
        hash_p = hashlib.sha256(raw_str.encode()).hexdigest()[:HASH_DIGEST_LEN_16]
        return f"{check_p}:{scope_p}:{severity}:{hash_p}"
    return raw_str


class AlertManager:
    """Manages alert lifecycle, deduplication, cooldown windows, escalation, and dispatch."""

    def __init__(
        self,
        config: AlertsConfig,
        settings: PulseCheckSettings,
        clock: Clock,
    ) -> None:
        self.config = config
        self.settings = settings
        self.clock = clock
        # In-memory registry for offline simulation and tests
        self._in_memory_alerts: Dict[str, AlertRecord] = {}

    def process_findings(
        self,
        findings: List[CheckFinding],
        conn: Optional[Connection] = None,
    ) -> Tuple[List[AlertRecord], List[AlertRecord], List[AlertRecord], List[AlertRecord]]:
        """
        Evaluate check findings, updating active alerts, sending notifications, and auto-closing.

        Returns:
            Tuple of (all_active_alerts, notified_alerts, suppressed_alerts, auto_closed_alerts)
        """
        now = self.clock.now()
        failing_findings = [f for f in findings if not f.passed]
        triggered_keys = {(f.check_name, f.scope) for f in failing_findings}

        active_alerts: List[AlertRecord] = []
        notified_alerts: List[AlertRecord] = []
        suppressed_alerts: List[AlertRecord] = []
        auto_closed_alerts: List[AlertRecord] = []

        # 1. Fetch current active alerts from DB or memory
        current_active = self._get_active_alerts(conn)
        active_by_check_scope: Dict[Tuple[str, Optional[str]], AlertRecord] = {
            (a.check_name, a.scope): a for a in current_active
        }

        # 2. Process each failed finding
        for finding in failing_findings:
            check_key = (finding.check_name, finding.scope)
            existing = active_by_check_scope.get(check_key)
            fingerprint = compute_fingerprint(finding.check_name, finding.scope, finding.severity)

            if existing is None:
                # Case A: Brand new alert
                new_alert = AlertRecord(
                    fingerprint=fingerprint,
                    check_name=finding.check_name,
                    scope=finding.scope,
                    severity=finding.severity,
                    status="active",
                    message=finding.message,
                    occurrence_count=INITIAL_COUNT,
                    healthy_batch_count=RESET_HEALTHY_COUNT,
                    first_seen_at=now,
                    last_seen_at=now,
                    last_notified_at=now,
                    resolved_at=None,
                    details=finding.details,
                )
                self._persist_alert(new_alert, conn)
                self._dispatch_channels(new_alert, is_escalation=False)
                active_alerts.append(new_alert)
                notified_alerts.append(new_alert)
                active_by_check_scope[check_key] = new_alert

            else:
                existing_rank = SEVERITY_ORDER.get(existing.severity, 0)
                new_rank = SEVERITY_ORDER.get(finding.severity, 0)

                # Case B: Severity Escalation (higher severity re-notifies immediately)
                if new_rank > existing_rank:
                    escalated = AlertRecord(
                        fingerprint=fingerprint,
                        check_name=finding.check_name,
                        scope=finding.scope,
                        severity=finding.severity,
                        status="active",
                        message=finding.message,
                        occurrence_count=existing.occurrence_count + 1,
                        healthy_batch_count=RESET_HEALTHY_COUNT,
                        first_seen_at=existing.first_seen_at,
                        last_seen_at=now,
                        last_notified_at=now,
                        resolved_at=None,
                        details=finding.details,
                    )
                    # Mark previous alert superseded
                    self._supersede_alert(existing, conn)
                    self._persist_alert(escalated, conn)
                    self._dispatch_channels(escalated, is_escalation=True)
                    active_alerts.append(escalated)
                    notified_alerts.append(escalated)
                    active_by_check_scope[check_key] = escalated

                else:
                    # Case C: Same or lower severity - check cooldown window
                    elapsed_seconds = (now - existing.last_notified_at).total_seconds()
                    cooldown = float(self.config.cooldown_seconds)

                    notified_at = (
                        now if elapsed_seconds >= cooldown else existing.last_notified_at
                    )
                    updated = AlertRecord(
                        fingerprint=existing.fingerprint,
                        check_name=existing.check_name,
                        scope=existing.scope,
                        severity=existing.severity,
                        status="active",
                        message=finding.message,
                        occurrence_count=existing.occurrence_count + 1,
                        healthy_batch_count=RESET_HEALTHY_COUNT,
                        first_seen_at=existing.first_seen_at,
                        last_seen_at=now,
                        last_notified_at=notified_at,
                        resolved_at=None,
                        details=finding.details,
                    )

                    if elapsed_seconds >= cooldown:
                        # Cooldown expired -> re-notify
                        self._persist_alert(updated, conn)
                        self._dispatch_channels(updated, is_escalation=False)
                        active_alerts.append(updated)
                        notified_alerts.append(updated)
                    else:
                        # Inside cooldown -> increment count without re-notifying
                        self._persist_alert(updated, conn)
                        active_alerts.append(updated)
                        suppressed_alerts.append(updated)

                    active_by_check_scope[check_key] = updated

        # 3. Check for auto-closing healthy batches
        for check_key, alert in active_by_check_scope.items():
            if check_key not in triggered_keys:
                new_healthy_count = alert.healthy_batch_count + 1
                if new_healthy_count >= self.config.auto_close_healthy_batches:
                    # Auto-close alert
                    closed = AlertRecord(
                        fingerprint=alert.fingerprint,
                        check_name=alert.check_name,
                        scope=alert.scope,
                        severity=alert.severity,
                        status="auto_closed",
                        message=alert.message,
                        occurrence_count=alert.occurrence_count,
                        healthy_batch_count=new_healthy_count,
                        first_seen_at=alert.first_seen_at,
                        last_seen_at=alert.last_seen_at,
                        last_notified_at=alert.last_notified_at,
                        resolved_at=now,
                        details=alert.details,
                    )
                    self._persist_alert(closed, conn)
                    auto_closed_alerts.append(closed)
                    logger.info(
                        "Alert auto-closed after %d healthy batches: %s",
                        new_healthy_count,
                        closed.fingerprint,
                    )
                else:
                    # Increment healthy batch count while keeping active
                    incremented = AlertRecord(
                        fingerprint=alert.fingerprint,
                        check_name=alert.check_name,
                        scope=alert.scope,
                        severity=alert.severity,
                        status="active",
                        message=alert.message,
                        occurrence_count=alert.occurrence_count,
                        healthy_batch_count=new_healthy_count,
                        first_seen_at=alert.first_seen_at,
                        last_seen_at=alert.last_seen_at,
                        last_notified_at=alert.last_notified_at,
                        resolved_at=None,
                        details=alert.details,
                    )
                    self._persist_alert(incremented, conn)
                    active_alerts.append(incremented)

        return active_alerts, notified_alerts, suppressed_alerts, auto_closed_alerts

    def _get_active_alerts(self, conn: Optional[Connection]) -> List[AlertRecord]:
        """Fetch all currently active alerts from database or in-memory store."""
        if conn is None:
            return [a for a in self._in_memory_alerts.values() if a.status == "active"]

        alerts: List[AlertRecord] = []
        with conn.cursor() as cur:
            cur.execute(
                """
                SELECT fingerprint, check_name, scope, severity, status, message,
                       occurrence_count, healthy_batch_count, first_seen_at,
                       last_seen_at, last_notified_at, resolved_at
                FROM alerts
                WHERE status = 'active';
                """
            )
            rows = cur.fetchall()
            for r in rows:
                alerts.append(
                    AlertRecord(
                        fingerprint=r[0],
                        check_name=r[1],
                        scope=r[2],
                        severity=r[3],
                        status=r[4],
                        message=r[5],
                        occurrence_count=r[6],
                        healthy_batch_count=r[7],
                        first_seen_at=r[8],
                        last_seen_at=r[9],
                        last_notified_at=r[10],
                        resolved_at=r[11],
                    )
                )
        return alerts

    def _persist_alert(self, alert: AlertRecord, conn: Optional[Connection]) -> None:
        """Upsert alert into PostgreSQL table or in-memory dictionary."""
        self._in_memory_alerts[alert.fingerprint] = alert

        if conn is None or not self.config.destinations.table:
            return

        with conn.cursor() as cur:
            cur.execute(
                """
                INSERT INTO alerts (
                    fingerprint, check_name, scope, severity, status, message,
                    occurrence_count, healthy_batch_count, first_seen_at,
                    last_seen_at, last_notified_at, resolved_at
                ) VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s, %s)
                ON CONFLICT (fingerprint) DO UPDATE SET
                    status = EXCLUDED.status,
                    message = EXCLUDED.message,
                    occurrence_count = EXCLUDED.occurrence_count,
                    healthy_batch_count = EXCLUDED.healthy_batch_count,
                    last_seen_at = EXCLUDED.last_seen_at,
                    last_notified_at = EXCLUDED.last_notified_at,
                    resolved_at = EXCLUDED.resolved_at;
                """,
                (
                    alert.fingerprint,
                    alert.check_name,
                    alert.scope,
                    alert.severity,
                    alert.status,
                    alert.message,
                    alert.occurrence_count,
                    alert.healthy_batch_count,
                    alert.first_seen_at,
                    alert.last_seen_at,
                    alert.last_notified_at,
                    alert.resolved_at,
                ),
            )
        conn.commit()

    def _supersede_alert(self, alert: AlertRecord, conn: Optional[Connection]) -> None:
        """Mark an existing alert superseded due to severity escalation."""
        superseded = AlertRecord(
            fingerprint=alert.fingerprint,
            check_name=alert.check_name,
            scope=alert.scope,
            severity=alert.severity,
            status="superseded",
            message=alert.message,
            occurrence_count=alert.occurrence_count,
            healthy_batch_count=alert.healthy_batch_count,
            first_seen_at=alert.first_seen_at,
            last_seen_at=alert.last_seen_at,
            last_notified_at=alert.last_notified_at,
            resolved_at=self.clock.now(),
            details=alert.details,
        )
        self._persist_alert(superseded, conn)

    def _dispatch_channels(self, alert: AlertRecord, is_escalation: bool) -> None:
        """Deliver alert to configured channels: log, table, and webhook."""
        prefix = "[ALERT ESCALATION]" if is_escalation else "[ALERT]"

        # 1. Log destination
        if self.config.destinations.log:
            logger.warning(
                "%s %s [%s] Scope: %s - %s (Count: %d)",
                prefix,
                alert.check_name,
                alert.severity,
                alert.scope or "all",
                alert.message,
                alert.occurrence_count,
            )

        # 2. Webhook destination (only if ALERT_WEBHOOK_URL env var is provided)
        webhook_url = self.settings.alert_webhook_url
        if self.config.destinations.webhook or webhook_url:
            if not webhook_url:
                logger.debug("Webhook alert skipped: ALERT_WEBHOOK_URL is not set.")
                return

            payload = {
                "fingerprint": alert.fingerprint,
                "check_name": alert.check_name,
                "scope": alert.scope,
                "severity": alert.severity,
                "message": alert.message,
                "occurrence_count": alert.occurrence_count,
                "is_escalation": is_escalation,
                "timestamp": alert.last_notified_at.isoformat(),
            }
            try:
                data = json.dumps(payload).encode("utf-8")
                req = urllib.request.Request(
                    webhook_url,
                    data=data,
                    headers={"Content-Type": "application/json"},
                    method="POST",
                )
                with urllib.request.urlopen(req, timeout=HTTP_TIMEOUT_SECONDS):
                    pass
            except Exception as e:
                logger.error("Failed to deliver alert to webhook %s: %s", webhook_url, e)
