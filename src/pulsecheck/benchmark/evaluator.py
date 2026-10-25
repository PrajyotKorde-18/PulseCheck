"""Detection evaluation and ground-truth matching for benchmark analysis."""

from typing import Any, Dict, List, Optional, Set, Tuple

from pulsecheck.benchmark.models import (
    FaultBenchmarkMetric,
    OverallBenchmarkMetrics,
)
from pulsecheck.checks.models import BatchCheckReport
from pulsecheck.faults.base import InjectedFaultRecord

COUNT_ZERO = 0
COUNT_ONE = 1
RATE_PERFECT = 1.0
RATE_ZERO = 0.0

DETECTOR_ALIASES: Dict[str, str] = {
    "CHECK_MISSING_SCOPES": "missing_scope",
    "CHECK_VOLUME_DROP": "volume_anomaly",
    "CHECK_FRESHNESS_STALE": "freshness_anomaly",
}


def normalize_detector(detector_name: str) -> str:
    """Normalize alias detector names to canonical check or rule reason codes."""
    return DETECTOR_ALIASES.get(detector_name, detector_name)


def compute_precision_recall(
    caught: int, missed: int, false_alarms: int, injected: int
) -> Tuple[float, float]:
    """Compute precision and recall safely handling zero-division boundaries."""
    total_detections = caught + false_alarms
    precision = (caught / total_detections) if total_detections > COUNT_ZERO else RATE_PERFECT
    recall = (caught / injected) if injected > COUNT_ZERO else RATE_PERFECT
    return precision, recall


class BenchmarkEvaluator:
    """Evaluates pipeline detections against injected ground-truth fault records."""

    def __init__(
        self,
        id_column: str = "trip_id",
    ) -> None:
        self.id_column = id_column

    def evaluate(
        self,
        injected_records: List[InjectedFaultRecord],
        quarantined_records: List[Dict[str, Any]],
        batch_check_report: Optional[BatchCheckReport],
        total_history_rows: int,
        total_final_day_clean_rows: int,
        total_final_day_faulty_rows: int,
        total_loaded_rows: int,
        clean_row_ids: Set[str],
        rows_recovered_by_replay: int,
    ) -> Tuple[OverallBenchmarkMetrics, List[FaultBenchmarkMetric]]:
        """Match detections to ground truth and compute per-fault and overall metrics."""
        # 1. Index quarantined rows by row ID and record_key
        quarantined_row_reasons: Dict[str, Set[str]] = {}
        for q in quarantined_records:
            reasons = q.get("reasons", [])
            reason_codes: Set[str] = set()
            for r in reasons:
                if isinstance(r, dict):
                    code = r.get("code") or r.get("reason_code")
                else:
                    code = getattr(r, "code", None) or getattr(r, "reason_code", None)
                if code:
                    reason_codes.add(normalize_detector(str(code)))

            # Register by primary ID and record_key
            raw_row = q.get("raw_row", {})
            if isinstance(raw_row, dict) and self.id_column in raw_row:
                row_id = str(raw_row[self.id_column])
                quarantined_row_reasons[row_id] = reason_codes

            rec_key = q.get("record_key")
            if rec_key:
                quarantined_row_reasons[str(rec_key)] = reason_codes

        # 2. Collect all mutated row IDs across all row faults
        all_mutated_ids: Set[str] = set()
        for fault in injected_records:
            if fault.scope == "rows":
                all_mutated_ids.update(fault.affected_row_ids)

        # 3. Collect active alerts from batch check report
        batch_alerts: Dict[str, Set[Optional[str]]] = {}
        if batch_check_report:
            for alert in batch_check_report.active_alerts:
                canonical_check = normalize_detector(alert.check_name)
                if canonical_check not in batch_alerts:
                    batch_alerts[canonical_check] = set()
                batch_alerts[canonical_check].add(alert.scope)

            for finding in batch_check_report.findings:
                if not finding.passed:
                    canonical_check = normalize_detector(finding.check_name)
                    if canonical_check not in batch_alerts:
                        batch_alerts[canonical_check] = set()
                    batch_alerts[canonical_check].add(finding.scope)

        # 4. Evaluate each injected fault
        per_fault_metrics: List[FaultBenchmarkMetric] = []
        all_affected_batch_scopes: Set[str] = set()

        for fault in injected_records:
            expected_detector = normalize_detector(fault.expected_detector)

            if fault.scope == "rows":
                injected = len(fault.affected_row_ids)
                caught_ids: Set[str] = set()

                for row_id in fault.affected_row_ids:
                    row_reasons = quarantined_row_reasons.get(str(row_id), set())
                    if expected_detector in row_reasons:
                        caught_ids.add(str(row_id))

                caught = len(caught_ids)
                missed = injected - caught

                # False alarms: rows triggering this detector that were NOT in affected_row_ids
                false_alarms = COUNT_ZERO
                for q_id, q_codes in quarantined_row_reasons.items():
                    if expected_detector in q_codes and q_id not in fault.affected_row_ids:
                        false_alarms += COUNT_ONE

                precision, recall = compute_precision_recall(
                    caught=caught,
                    missed=missed,
                    false_alarms=false_alarms,
                    injected=injected,
                )

                explanation = None
                if missed > COUNT_ZERO:
                    if fault.fault_name == "null_id":
                        explanation = (
                            f"{missed} row(s) quarantined with {expected_detector}, but bear "
                            f"trip_id=null, preventing key correlation to pre-mutation IDs."
                        )
                    elif fault.fault_name == "duplicate_ids":
                        explanation = (
                            f"{missed} duplicate row quarantined under the copied source ID "
                            f"rather than its original pre-mutation ID."
                        )
                    else:
                        explanation = (
                            f"{missed} row(s) masked by compound overlapping fault mutations "
                            f"on the same row index."
                        )

                per_fault_metrics.append(
                    FaultBenchmarkMetric(
                        fault_name=fault.fault_name,
                        scope="rows",
                        expected_detector=fault.expected_detector,
                        injected=injected,
                        caught=caught,
                        missed=missed,
                        false_alarms=false_alarms,
                        precision=precision,
                        recall=recall,
                        explanation=explanation,
                    )
                )

            elif fault.scope == "batch":
                injected = COUNT_ONE
                affected_scopes = set(fault.affected_scopes)
                all_affected_batch_scopes.update(affected_scopes)

                triggered_scopes = batch_alerts.get(expected_detector, set())
                matched_scopes = affected_scopes.intersection(triggered_scopes)
                has_matched = bool(matched_scopes or (not affected_scopes and triggered_scopes))
                caught = COUNT_ONE if has_matched else COUNT_ZERO
                missed = injected - caught

                # False alarms: alert on scopes not targeted by this fault
                clean_triggered = triggered_scopes.difference(affected_scopes)
                false_alarms = len(clean_triggered)

                precision, recall = compute_precision_recall(
                    caught=caught,
                    missed=missed,
                    false_alarms=false_alarms,
                    injected=injected,
                )

                explanation = None
                if missed > COUNT_ZERO:
                    exp_msg = (
                        f"Batch check '{expected_detector}' was not triggered "
                        f"for scopes {fault.affected_scopes}."
                    )
                    explanation = exp_msg

                per_fault_metrics.append(
                    FaultBenchmarkMetric(
                        fault_name=fault.fault_name,
                        scope="batch",
                        expected_detector=fault.expected_detector,
                        injected=injected,
                        caught=caught,
                        missed=missed,
                        false_alarms=false_alarms,
                        precision=precision,
                        recall=recall,
                        explanation=explanation,
                    )
                )

        # 5. Overall metrics
        # Clean rows wrongly quarantined: clean rows not mutated by ANY fault that got quarantined
        clean_quarantined = COUNT_ZERO
        for r_id in clean_row_ids:
            if r_id not in all_mutated_ids and r_id in quarantined_row_reasons:
                clean_quarantined += COUNT_ONE

        # Alerts on clean scopes: batch alerts on scopes not affected by any batch fault
        clean_scope_alerts = COUNT_ZERO
        for _detector, scopes in batch_alerts.items():
            for sc in scopes:
                if sc and sc not in all_affected_batch_scopes:
                    clean_scope_alerts += COUNT_ONE

        overall = OverallBenchmarkMetrics(
            total_history_rows=total_history_rows,
            total_final_day_clean_rows=total_final_day_clean_rows,
            total_final_day_faulty_rows=total_final_day_faulty_rows,
            total_loaded_rows=total_loaded_rows,
            total_quarantined_rows=len(quarantined_records),
            clean_rows_wrongly_quarantined=clean_quarantined,
            alerts_on_clean_scopes=clean_scope_alerts,
            rows_recovered_by_replay=rows_recovered_by_replay,
        )

        return overall, per_fault_metrics
