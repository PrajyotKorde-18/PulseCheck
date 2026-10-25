"""Freshness and future timestamp checks against injectable clock in timezone-aware UTC."""

from datetime import datetime, timezone
from typing import Optional

from pulsecheck.checks.models import CheckFinding
from pulsecheck.clock import Clock
from pulsecheck.config.models import FreshnessCheckConfig

FUTURE_TOLERANCE_SECONDS = 60.0
SECONDS_PER_HOUR = 3600.0


class FreshnessChecker:
    """Verifies batch timeliness and identifies stale data or future timestamps."""

    def __init__(self, config: FreshnessCheckConfig, clock: Clock) -> None:
        self.config = config
        self.clock = clock

    def evaluate(self, latest_event_time: Optional[datetime]) -> CheckFinding:
        """
        Evaluate freshness of the batch based on its most recent event timestamp.

        Args:
            latest_event_time: Maximum event timestamp observed in the batch, or None if empty.
        """
        if not self.config.enabled:
            return CheckFinding(
                check_name="freshness_anomaly",
                scope=None,
                severity="INFO",
                message="Freshness check is disabled.",
                passed=True,
            )

        if latest_event_time is None:
            return CheckFinding(
                check_name="freshness_anomaly",
                scope=None,
                severity="INFO",
                message="No event timestamps found in batch to evaluate freshness.",
                passed=True,
            )

        now = self.clock.now()

        # Ensure timezone awareness (default to UTC if naive)
        event_time_tz = latest_event_time
        if event_time_tz.tzinfo is None:
            event_time_tz = event_time_tz.replace(tzinfo=timezone.utc)

        # 1. Check for Future Timestamps (distinct anomaly check)
        future_delta_seconds = (event_time_tz - now).total_seconds()
        if future_delta_seconds > FUTURE_TOLERANCE_SECONDS:
            return CheckFinding(
                check_name="future_timestamp",
                scope=None,
                severity="CRITICAL",
                message=(
                    f"Batch contains future timestamps: latest event {event_time_tz.isoformat()} "
                    f"is ahead of system clock {now.isoformat()} by {future_delta_seconds:.1f}s."
                ),
                passed=False,
                details={
                    "latest_event_time": event_time_tz.isoformat(),
                    "clock_now": now.isoformat(),
                    "ahead_seconds": future_delta_seconds,
                },
            )

        # 2. Check for Staleness / Maximum allowable lag
        lag_seconds = (now - event_time_tz).total_seconds()
        max_lag = float(self.config.max_lag_seconds)

        if lag_seconds > max_lag:
            lag_hours = lag_seconds / SECONDS_PER_HOUR
            max_hours = max_lag / SECONDS_PER_HOUR
            return CheckFinding(
                check_name="freshness_anomaly",
                scope=None,
                severity=self.config.severity,
                message=(
                    f"Data freshness lag exceeded: latest event is {lag_hours:.1f}h old "
                    f"(max allowed: {max_hours:.1f}h, lag: {lag_seconds:.0f}s)."
                ),
                passed=False,
                details={
                    "latest_event_time": event_time_tz.isoformat(),
                    "clock_now": now.isoformat(),
                    "lag_seconds": lag_seconds,
                    "max_lag_seconds": max_lag,
                },
            )

        return CheckFinding(
            check_name="freshness_anomaly",
            scope=None,
            severity="INFO",
            message=(
                f"Data freshness normal: latest event {event_time_tz.isoformat()} "
                f"is within max allowable lag ({lag_seconds / SECONDS_PER_HOUR:.1f}h lag)."
            ),
            passed=True,
            details={
                "latest_event_time": event_time_tz.isoformat(),
                "clock_now": now.isoformat(),
                "lag_seconds": lag_seconds,
            },
        )
