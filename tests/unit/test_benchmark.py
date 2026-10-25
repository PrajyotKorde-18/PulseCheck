"""Unit tests verifying benchmark metric arithmetic, evaluation matching, and report generation."""

from pathlib import Path
from typing import Any, Dict, List, Set

from pulsecheck.benchmark.evaluator import (
    BenchmarkEvaluator,
    compute_precision_recall,
)
from pulsecheck.benchmark.models import (
    AUTHOR_STATEMENT,
    BenchmarkReport,
    BenchmarkRunMetadata,
    FaultBenchmarkMetric,
    OverallBenchmarkMetrics,
)
from pulsecheck.faults.base import InjectedFaultRecord

COUNT_ZERO = 0
COUNT_ONE = 1
COUNT_TWO = 2
COUNT_FOUR = 4
COUNT_FIVE = 5
COUNT_EIGHT = 8
COUNT_TEN = 10
COUNT_TWENTY = 20
RATE_ZERO = 0.0
RATE_EIGHTY_PCT = 0.8
RATE_PERFECT = 1.0


def test_compute_precision_recall_arithmetic() -> None:
    """Verifies that precision and recall formulas match standard definitions and edge cases."""
    # Standard 80% recall and 80% precision
    prec, rec = compute_precision_recall(
        caught=COUNT_EIGHT,
        missed=COUNT_TWO,
        false_alarms=COUNT_TWO,
        injected=COUNT_TEN,
    )
    assert prec == RATE_EIGHTY_PCT
    assert rec == RATE_EIGHTY_PCT

    # 100% precision and recall (perfect detector)
    prec_perf, rec_perf = compute_precision_recall(
        caught=COUNT_TEN,
        missed=COUNT_ZERO,
        false_alarms=COUNT_ZERO,
        injected=COUNT_TEN,
    )
    assert prec_perf == RATE_PERFECT
    assert rec_perf == RATE_PERFECT

    # 0% precision and recall
    prec_zero, rec_zero = compute_precision_recall(
        caught=COUNT_ZERO,
        missed=COUNT_FIVE,
        false_alarms=COUNT_FIVE,
        injected=COUNT_FIVE,
    )
    assert prec_zero == RATE_ZERO
    assert rec_zero == RATE_ZERO

    # Boundary handling: zero injected rows and zero detections
    prec_empty, rec_empty = compute_precision_recall(
        caught=COUNT_ZERO,
        missed=COUNT_ZERO,
        false_alarms=COUNT_ZERO,
        injected=COUNT_ZERO,
    )
    assert prec_empty == RATE_PERFECT
    assert rec_empty == RATE_PERFECT


def test_benchmark_evaluator_tiny_fixed_scenario() -> None:
    """Tests evaluator matching logic on a tiny, hand-computable synthetic scenario."""
    evaluator = BenchmarkEvaluator(id_column="trip_id")

    # Injected faults:
    # Fault A (null_id): 5 rows injected (r1, r2, r3, r4, r5)
    # Fault B (swapped_times): 2 rows injected (r10, r11)
    injected: List[InjectedFaultRecord] = [
        InjectedFaultRecord(
            fault_name="null_id",
            expected_detector="ERR_NULL_VALUE",
            scope="rows",
            affected_row_ids=["r1", "r2", "r3", "r4", "r5"],
        ),
        InjectedFaultRecord(
            fault_name="swapped_times",
            expected_detector="ERR_INVALID_CHRONOLOGY",
            scope="rows",
            affected_row_ids=["r10", "r11"],
        ),
    ]

    # Quarantined rows:
    # r1, r2, r3, r4 caught with ERR_NULL_VALUE (r5 missed)
    # r99 false alarm for ERR_NULL_VALUE
    # r10, r11 caught with ERR_INVALID_CHRONOLOGY
    # r50 clean row wrongly quarantined with ERR_OUT_OF_BOUNDS
    quarantined: List[Dict[str, Any]] = [
        {"raw_row": {"trip_id": "r1"}, "reasons": [{"code": "ERR_NULL_VALUE"}]},
        {"raw_row": {"trip_id": "r2"}, "reasons": [{"code": "ERR_NULL_VALUE"}]},
        {"raw_row": {"trip_id": "r3"}, "reasons": [{"code": "ERR_NULL_VALUE"}]},
        {"raw_row": {"trip_id": "r4"}, "reasons": [{"code": "ERR_NULL_VALUE"}]},
        {"raw_row": {"trip_id": "r99"}, "reasons": [{"code": "ERR_NULL_VALUE"}]},
        {"raw_row": {"trip_id": "r10"}, "reasons": [{"code": "ERR_INVALID_CHRONOLOGY"}]},
        {"raw_row": {"trip_id": "r11"}, "reasons": [{"code": "ERR_INVALID_CHRONOLOGY"}]},
        {"raw_row": {"trip_id": "r50"}, "reasons": [{"code": "ERR_OUT_OF_BOUNDS"}]},
    ]

    clean_row_ids: Set[str] = {
        "r1", "r2", "r3", "r4", "r5", "r10", "r11", "r50", "r51", "r52"
    }

    overall, per_fault = evaluator.evaluate(
        injected_records=injected,
        quarantined_records=quarantined,
        batch_check_report=None,
        total_history_rows=100,
        total_final_day_clean_rows=10,
        total_final_day_faulty_rows=10,
        total_loaded_rows=2,
        clean_row_ids=clean_row_ids,
        rows_recovered_by_replay=2,
    )

    # Check Fault A (null_id):
    # Injected = 5, Caught = 4, Missed = 1, False Alarms = 1 (r99)
    # Precision = 4 / (4 + 1) = 0.8
    # Recall = 4 / 5 = 0.8
    fault_a = per_fault[0]
    assert fault_a.fault_name == "null_id"
    assert fault_a.injected == COUNT_FIVE
    assert fault_a.caught == COUNT_FOUR
    assert fault_a.missed == COUNT_ONE
    assert fault_a.false_alarms == COUNT_ONE
    assert fault_a.precision == RATE_EIGHTY_PCT
    assert fault_a.recall == RATE_EIGHTY_PCT
    assert fault_a.explanation is not None

    # Check Fault B (swapped_times):
    # Injected = 2, Caught = 2, Missed = 0, False Alarms = 0
    # Precision = 1.0, Recall = 1.0
    fault_b = per_fault[1]
    assert fault_b.fault_name == "swapped_times"
    assert fault_b.injected == COUNT_TWO
    assert fault_b.caught == COUNT_TWO
    assert fault_b.missed == COUNT_ZERO
    assert fault_b.false_alarms == COUNT_ZERO
    assert fault_b.precision == RATE_PERFECT
    assert fault_b.recall == RATE_PERFECT
    assert fault_b.explanation is None

    # Overall:
    # r50 was in clean_row_ids and not in injected affected_row_ids, and was quarantined
    assert overall.clean_rows_wrongly_quarantined == COUNT_ONE
    assert overall.rows_recovered_by_replay == COUNT_TWO
    assert overall.total_quarantined_rows == COUNT_EIGHT


def test_benchmark_report_markdown_and_json_serialization(tmp_path: Path) -> None:
    """Verifies report structure, markdown formatting, and JSON persistence."""
    metadata = BenchmarkRunMetadata(
        run_id="bench_test_run",
        seed=42,
        config_hash="abc123hash",
        git_commit_hash="commit789",
        package_versions={"python": "3.13.0", "pandas": "2.2.0"},
        runtime_seconds=1.2345,
    )
    overall = OverallBenchmarkMetrics(
        total_history_rows=150,
        total_final_day_clean_rows=50,
        total_final_day_faulty_rows=50,
        total_loaded_rows=40,
        total_quarantined_rows=10,
        clean_rows_wrongly_quarantined=0,
        alerts_on_clean_scopes=0,
        rows_recovered_by_replay=5,
    )
    per_fault = [
        FaultBenchmarkMetric(
            fault_name="swapped_times",
            scope="rows",
            expected_detector="ERR_INVALID_CHRONOLOGY",
            injected=5,
            caught=5,
            missed=0,
            false_alarms=0,
            precision=1.0,
            recall=1.0,
        )
    ]
    report = BenchmarkReport(
        metadata=metadata,
        overall_metrics=overall,
        per_fault_metrics=per_fault,
    )

    # Markdown generation checks
    md_content = report.to_markdown()
    assert "# PulseCheck Benchmark Report" in md_content
    assert AUTHOR_STATEMENT in md_content
    assert "bench_test_run" in md_content
    assert "`swapped_times`" in md_content
    assert "100.0%" in md_content

    # File serialization checks
    json_path = tmp_path / "bench.json"
    md_path = tmp_path / "bench.md"

    report.write_json(json_path)
    report.write_markdown(md_path)

    assert json_path.is_file()
    assert md_path.is_file()
    assert "abc123hash" in json_path.read_text(encoding="utf-8")
    assert "commit789" in md_path.read_text(encoding="utf-8")
