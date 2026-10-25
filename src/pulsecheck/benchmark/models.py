"""Data models for benchmark results, evaluation metrics, and reports."""

import json
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

ROUND_PRECISION = 4
PERCENT_MULTIPLIER = 100.0
AUTHOR_STATEMENT = (
    "Notice: Faults and detectors were both designed and implemented by the project author. "
    "Consequently, benchmark metrics demonstrate that the validation and batch check engines "
    "perform as designed under controlled synthetic defect distributions, rather than measuring "
    "generalized accuracy across arbitrary real-world logistics datasets."
)


@dataclass
class FaultBenchmarkMetric:
    """Detection evaluation metrics for a single injected fault plugin."""

    fault_name: str
    scope: str
    expected_detector: str
    injected: int
    caught: int
    missed: int
    false_alarms: int
    precision: float
    recall: float
    explanation: Optional[str] = None

    def to_dict(self) -> Dict[str, Any]:
        """Convert metric to dictionary."""
        return {
            "fault_name": self.fault_name,
            "scope": self.scope,
            "expected_detector": self.expected_detector,
            "injected": self.injected,
            "caught": self.caught,
            "missed": self.missed,
            "false_alarms": self.false_alarms,
            "precision": round(self.precision, ROUND_PRECISION),
            "recall": round(self.recall, ROUND_PRECISION),
            "explanation": self.explanation,
        }


@dataclass
class OverallBenchmarkMetrics:
    """Aggregate volume and pipeline health metrics across the benchmark run."""

    total_history_rows: int
    total_final_day_clean_rows: int
    total_final_day_faulty_rows: int
    total_loaded_rows: int
    total_quarantined_rows: int
    clean_rows_wrongly_quarantined: int
    alerts_on_clean_scopes: int
    rows_recovered_by_replay: int

    def to_dict(self) -> Dict[str, Any]:
        """Convert metrics to dictionary."""
        return {
            "total_history_rows": self.total_history_rows,
            "total_final_day_clean_rows": self.total_final_day_clean_rows,
            "total_final_day_faulty_rows": self.total_final_day_faulty_rows,
            "total_loaded_rows": self.total_loaded_rows,
            "total_quarantined_rows": self.total_quarantined_rows,
            "clean_rows_wrongly_quarantined": self.clean_rows_wrongly_quarantined,
            "alerts_on_clean_scopes": self.alerts_on_clean_scopes,
            "rows_recovered_by_replay": self.rows_recovered_by_replay,
        }


@dataclass
class BenchmarkRunMetadata:
    """Execution metadata and environment provenance for the benchmark run."""

    run_id: str
    seed: int
    config_hash: str
    git_commit_hash: str
    package_versions: Dict[str, str]
    runtime_seconds: float
    timestamp: str = field(
        default_factory=lambda: datetime.now(timezone.utc).isoformat()
    )

    def to_dict(self) -> Dict[str, Any]:
        """Convert metadata to dictionary."""
        return {
            "run_id": self.run_id,
            "seed": self.seed,
            "config_hash": self.config_hash,
            "git_commit_hash": self.git_commit_hash,
            "package_versions": self.package_versions,
            "runtime_seconds": round(self.runtime_seconds, ROUND_PRECISION),
            "timestamp": self.timestamp,
        }


@dataclass
class BenchmarkReport:
    """Complete benchmark report containing metadata, metrics, and author statement."""

    metadata: BenchmarkRunMetadata
    overall_metrics: OverallBenchmarkMetrics
    per_fault_metrics: List[FaultBenchmarkMetric]
    author_statement: str = AUTHOR_STATEMENT

    def to_dict(self) -> Dict[str, Any]:
        """Serialize complete report to dictionary."""
        return {
            "metadata": self.metadata.to_dict(),
            "overall_metrics": self.overall_metrics.to_dict(),
            "per_fault_metrics": [m.to_dict() for m in self.per_fault_metrics],
            "author_statement": self.author_statement,
        }

    def write_json(self, destination: Path) -> None:
        """Write report as indented JSON to the target path."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as f:
            json.dump(self.to_dict(), f, indent=2)

    def to_markdown(self) -> str:
        """Render report as GitHub-flavored Markdown document."""
        lines = [
            "# PulseCheck Benchmark Report",
            "",
            "> **Methodology Notice**: " + self.author_statement,
            "",
            "## 1. Execution Metadata",
            "",
            f"- **Run ID**: `{self.metadata.run_id}`",
            f"- **Seed**: `{self.metadata.seed}`",
            f"- **Config SHA-256**: `{self.metadata.config_hash}`",
            f"- **Git Commit**: `{self.metadata.git_commit_hash}`",
            f"- **Timestamp**: `{self.metadata.timestamp}`",
            f"- **Runtime**: `{self.metadata.runtime_seconds:.3f}s`",
            "",
            "### Package Versions",
            "",
            "| Package | Version |",
            "|---|---|",
        ]

        for pkg, ver in sorted(self.metadata.package_versions.items()):
            lines.append(f"| `{pkg}` | `{ver}` |")

        m = self.overall_metrics
        lines.extend([
            "",
            "## 2. Overall Pipeline & Data Metrics",
            "",
            "| Metric | Value |",
            "|---|---|",
            f"| History Rows Generated | {m.total_history_rows} |",
            f"| Final Day Clean Rows | {m.total_final_day_clean_rows} |",
            f"| Final Day Faulty Rows | {m.total_final_day_faulty_rows} |",
            f"| Rows Successfully Loaded | {m.total_loaded_rows} |",
            f"| Rows Quarantined | {m.total_quarantined_rows} |",
            f"| Clean Rows Wrongly Quarantined | {m.clean_rows_wrongly_quarantined} |",
            f"| Alerts on Clean Scopes | {m.alerts_on_clean_scopes} |",
            f"| Rows Recovered by Quarantine Replay | {m.rows_recovered_by_replay} |",
            "",
            "## 3. Per-Fault Detection Scorecard",
            "",
            "| Fault Name | Scope | Expected Detector | Injected | Caught | "
            "Missed | False Alarms | Precision | Recall |",
            "|---|---|---|---|---|---|---|---|---|",
        ])

        for fm in self.per_fault_metrics:
            prec_pct = f"{fm.precision * PERCENT_MULTIPLIER:.1f}%"
            rec_pct = f"{fm.recall * PERCENT_MULTIPLIER:.1f}%"
            lines.append(
                f"| `{fm.fault_name}` | `{fm.scope}` | `{fm.expected_detector}` | "
                f"{fm.injected} | {fm.caught} | {fm.missed} | {fm.false_alarms} | "
                f"{prec_pct} | {rec_pct} |"
            )

        # Add explanations for any missed faults
        missed_faults = [f for f in self.per_fault_metrics if f.missed > 0]
        if missed_faults:
            lines.extend([
                "",
                "## 4. Missed Fault Analysis",
                "",
            ])
            for mf in missed_faults:
                exp = mf.explanation or "No specific explanation recorded."
                lines.append(f"- **`{mf.fault_name}`** ({mf.missed} missed): {exp}")
        else:
            lines.extend([
                "",
                "## 4. Missed Fault Analysis",
                "",
                "All injected faults were caught by their designated detectors (0 missed).",
            ])

        lines.append("")
        return "\n".join(lines)

    def write_markdown(self, destination: Path) -> None:
        """Write report as Markdown to the target path."""
        destination.parent.mkdir(parents=True, exist_ok=True)
        with open(destination, "w", encoding="utf-8") as f:
            f.write(self.to_markdown())
