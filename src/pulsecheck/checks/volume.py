"""Volume anomaly check evaluating current scope volume against weekday historical median."""

import statistics
from typing import Dict, List, Optional

from pulsecheck.checks.models import BatchMetricRecord, CheckFinding
from pulsecheck.config.models import VolumeCheckConfig

ZERO_ROWS = 0
PERCENT_MULTIPLIER = 100.0


class VolumeChecker:
    """Evaluates whether current batch volume conforms to historical weekday median."""

    def __init__(self, config: VolumeCheckConfig) -> None:
        self.config = config

    def evaluate_scope_volume(
        self,
        scope: Optional[str],
        current_rows: int,
        history: List[BatchMetricRecord],
        current_weekday: int,
    ) -> CheckFinding:
        """
        Evaluate a single scope's loaded volume against weekday-matched history.

        Args:
            scope: Operational scope (e.g. city name) or None for overall.
            current_rows: Number of rows loaded in current batch for this scope.
            history: Historical BatchMetricRecord rows for this scope, ordered newest to oldest.
            current_weekday: Integer weekday (0=Monday to 6=Sunday).
        """
        scope_label = scope if scope else "overall"

        if not self.config.enabled:
            return CheckFinding(
                check_name="volume_anomaly",
                scope=scope,
                severity="INFO",
                message=f"Volume check disabled for scope '{scope_label}'.",
                passed=True,
            )

        # Filter history to batches matching the exact weekday of current batch
        weekday_history: List[int] = [
            m.rows_loaded
            for m in history
            if m.logical_date.weekday() == current_weekday
        ][: self.config.history_window_batches]

        # 1. Fallback Rule when historical volume count < minimum_history_batches
        if len(weekday_history) < self.config.minimum_history_batches:
            if current_rows < self.config.fallback_absolute_min_rows:
                return CheckFinding(
                    check_name="volume_anomaly",
                    scope=scope,
                    severity="CRITICAL" if current_rows == ZERO_ROWS else "HIGH",
                    message=(
                        f"Volume {current_rows} for scope '{scope_label}' is below "
                        f"fallback min of {self.config.fallback_absolute_min_rows} "
                        f"(history: {len(weekday_history)}/"
                        f"{self.config.minimum_history_batches} batches)."
                    ),
                    passed=False,
                    details={
                        "rule": "fallback_floor",
                        "current_rows": current_rows,
                        "fallback_min": self.config.fallback_absolute_min_rows,
                        "history_count": len(weekday_history),
                    },
                )
            if current_rows > self.config.fallback_absolute_max_rows:
                return CheckFinding(
                    check_name="volume_anomaly",
                    scope=scope,
                    severity="HIGH",
                    message=(
                        f"Volume {current_rows} for scope '{scope_label}' exceeds "
                        f"fallback max of {self.config.fallback_absolute_max_rows} "
                        f"(history: {len(weekday_history)}/"
                        f"{self.config.minimum_history_batches} batches)."
                    ),
                    passed=False,
                    details={
                        "rule": "fallback_ceiling",
                        "current_rows": current_rows,
                        "fallback_max": self.config.fallback_absolute_max_rows,
                        "history_count": len(weekday_history),
                    },
                )

            return CheckFinding(
                check_name="volume_anomaly",
                scope=scope,
                severity="INFO",
                message=(
                    f"Volume {current_rows} for scope '{scope_label}' satisfies limits "
                    f"[{self.config.fallback_absolute_min_rows}, "
                    f"{self.config.fallback_absolute_max_rows}] "
                    f"({len(weekday_history)}/{self.config.minimum_history_batches} seen)."
                ),
                passed=True,
                details={
                    "rule": "fallback_passed",
                    "current_rows": current_rows,
                    "history_count": len(weekday_history),
                },
            )

        # 2. Compute historical median from matching weekday batches
        median_rows = float(statistics.median(weekday_history))

        # 3. Explicit zero-baseline handling
        if median_rows == 0.0:
            if current_rows == ZERO_ROWS:
                return CheckFinding(
                    check_name="volume_anomaly",
                    scope=scope,
                    severity="INFO",
                    message=f"Scope '{scope_label}' volume is 0 matching zero-baseline history.",
                    passed=True,
                    details={"median_rows": 0.0, "current_rows": ZERO_ROWS},
                )
            return CheckFinding(
                check_name="volume_anomaly",
                scope=scope,
                severity="WARNING",
                message=(
                    f"Unexpected volume for scope '{scope_label}': {current_rows} rows received "
                    f"against zero baseline (median = 0 across {len(weekday_history)} batches)."
                ),
                passed=False,
                details={"median_rows": 0.0, "current_rows": current_rows, "rule": "zero_baseline"},
            )

        # 4. Standard median-ratio comparison
        ratio = current_rows / median_rows
        pct = ratio * PERCENT_MULTIPLIER
        min_pct = self.config.min_volume_ratio * PERCENT_MULTIPLIER
        max_pct = self.config.max_volume_ratio * PERCENT_MULTIPLIER

        if ratio < self.config.min_volume_ratio:
            severity = "CRITICAL" if current_rows == ZERO_ROWS else "HIGH"
            return CheckFinding(
                check_name="volume_anomaly",
                scope=scope,
                severity=severity,
                message=(
                    f"Volume drop detected for scope '{scope_label}': {current_rows} rows vs "
                    f"weekday median {median_rows:.1f} ({pct:.1f}%, threshold: {min_pct:.1f}%)."
                ),
                passed=False,
                details={
                    "current_rows": current_rows,
                    "median_rows": median_rows,
                    "ratio": ratio,
                    "threshold_ratio": self.config.min_volume_ratio,
                    "history_samples": len(weekday_history),
                },
            )

        if ratio > self.config.max_volume_ratio:
            return CheckFinding(
                check_name="volume_anomaly",
                scope=scope,
                severity="WARNING",
                message=(
                    f"Volume spike detected for scope '{scope_label}': {current_rows} rows vs "
                    f"weekday median {median_rows:.1f} ({pct:.1f}%, threshold: {max_pct:.1f}%)."
                ),
                passed=False,
                details={
                    "current_rows": current_rows,
                    "median_rows": median_rows,
                    "ratio": ratio,
                    "threshold_ratio": self.config.max_volume_ratio,
                    "history_samples": len(weekday_history),
                },
            )

        return CheckFinding(
            check_name="volume_anomaly",
            scope=scope,
            severity="INFO",
            message=(
                f"Volume normal for scope '{scope_label}': {current_rows} rows "
                f"({pct:.1f}% of weekday median {median_rows:.1f})."
            ),
            passed=True,
            details={
                "current_rows": current_rows,
                "median_rows": median_rows,
                "ratio": ratio,
                "history_samples": len(weekday_history),
            },
        )

    def evaluate_all_scopes(
        self,
        current_scope_volumes: Dict[Optional[str], int],
        expected_scopes: List[str],
        history_by_scope: Dict[Optional[str], List[BatchMetricRecord]],
        current_weekday: int,
    ) -> List[CheckFinding]:
        """Evaluate volume for all current scopes plus any missing expected scopes."""
        findings: List[CheckFinding] = []

        all_scopes_to_check = set(current_scope_volumes.keys())
        for exp in expected_scopes:
            all_scopes_to_check.add(exp)

        for scope in sorted(all_scopes_to_check, key=lambda s: s or ""):
            cur_rows = current_scope_volumes.get(scope, ZERO_ROWS)
            scope_history = history_by_scope.get(scope, [])
            finding = self.evaluate_scope_volume(
                scope=scope,
                current_rows=cur_rows,
                history=scope_history,
                current_weekday=current_weekday,
            )
            findings.append(finding)

        return findings
