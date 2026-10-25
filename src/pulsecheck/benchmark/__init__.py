"""Benchmark module for evaluating detection capabilities against synthetic faults."""

from pulsecheck.benchmark.evaluator import BenchmarkEvaluator, compute_precision_recall
from pulsecheck.benchmark.models import (
    BenchmarkReport,
    BenchmarkRunMetadata,
    FaultBenchmarkMetric,
    OverallBenchmarkMetrics,
)
from pulsecheck.benchmark.runner import BenchmarkRunner, run_benchmark

__all__ = [
    "BenchmarkEvaluator",
    "compute_precision_recall",
    "BenchmarkReport",
    "BenchmarkRunMetadata",
    "FaultBenchmarkMetric",
    "OverallBenchmarkMetrics",
    "BenchmarkRunner",
    "run_benchmark",
]
