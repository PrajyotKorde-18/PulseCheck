"""Synthetic benchmark runner executing pipeline validation, fault injection, and scoring."""

import hashlib
import importlib.metadata
import platform
import subprocess
import time
from copy import deepcopy
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Set

import pandas as pd
from psycopg import Connection

from pulsecheck.benchmark.evaluator import BenchmarkEvaluator
from pulsecheck.benchmark.models import (
    BenchmarkReport,
    BenchmarkRunMetadata,
)
from pulsecheck.clock import Clock, FrozenClock, SystemClock
from pulsecheck.config.loader import ConfigBundle, load_config
from pulsecheck.config.models import BenchConfig, RuleConfig
from pulsecheck.faults.injector import FaultInjector
from pulsecheck.generator.engine import GenerationParams, SyntheticTripGenerator
from pulsecheck.pipeline import PipelineRunner
from pulsecheck.replay import QuarantineReplayer

DEFAULT_START_DATE = date(2026, 3, 1)
INITIAL_OFFSET_ZERO = 0
INDEX_ONE = 1


def get_config_hash(config_dir: Path) -> str:
    """Compute deterministic SHA-256 hash across all configuration YAML files."""
    hasher = hashlib.sha256()
    for filename in sorted(["dataset.yaml", "rules.yaml", "checks.yaml", "faults.yaml"]):
        file_path = config_dir / filename
        if file_path.is_file():
            hasher.update(file_path.read_bytes())
    return hasher.hexdigest()


def get_git_commit_hash() -> str:
    """Retrieve current Git commit SHA-256 hash or fallback provenance."""
    try:
        proc = subprocess.run(
            ["git", "rev-parse", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        )
        if proc.returncode == INITIAL_OFFSET_ZERO and proc.stdout.strip():
            return proc.stdout.strip()
    except Exception:
        pass
    return "workspace_uncommitted"


def get_package_versions() -> Dict[str, str]:
    """Inspect environment package versions for provenance tracking."""
    packages = ["pandas", "pydantic", "pyarrow", "faker", "psycopg"]
    versions: Dict[str, str] = {"python": platform.python_version()}
    for pkg in packages:
        try:
            versions[pkg] = importlib.metadata.version(pkg)
        except Exception:
            versions[pkg] = "not_installed"
    return versions


class BenchmarkRunner:
    """Orchestrates end-to-end benchmark execution and evaluation."""

    def __init__(
        self,
        config_bundle: ConfigBundle,
        clock: Optional[Clock] = None,
        seed_override: Optional[int] = None,
    ) -> None:
        self.config_bundle = config_bundle
        self.clock = clock or SystemClock()
        self.bench_config: BenchConfig = config_bundle.bench
        self.seed = seed_override if seed_override is not None else self.bench_config.seed
        self.output_dir = Path(self.bench_config.output_dir)
        self.evaluator = BenchmarkEvaluator(id_column="trip_id")

    def run(
        self,
        conn: Optional[Connection] = None,
        dry_run: bool = False,
    ) -> BenchmarkReport:
        """Execute full benchmark pipeline and produce report."""
        start_time = time.perf_counter()
        run_id = f"bench_{datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')}_{self.seed}"

        # 1. Setup paths and generation parameters
        self.output_dir.mkdir(parents=True, exist_ok=True)
        total_days = self.bench_config.history_days + INDEX_ONE
        gen_params = GenerationParams(
            start_date=DEFAULT_START_DATE,
            days=total_days,
            trips_per_city_day=self.bench_config.trips_per_city_day,
            seed=self.seed,
            output_dir=self.output_dir,
            file_format="csv",
        )

        # 2. Generate clean multi-day data
        generator = SyntheticTripGenerator(
            config_bundle=self.config_bundle, params=gen_params
        )
        generated_files = generator.generate_all()

        # 3. Load baseline history days
        history_files = generated_files[: self.bench_config.history_days]
        final_clean_file = generated_files[self.bench_config.history_days]

        total_history_rows = 0
        pipeline_runner = PipelineRunner(config_bundle=self.config_bundle, clock=self.clock)

        for day_idx, h_file in enumerate(history_files):
            day_dt = DEFAULT_START_DATE + timedelta(days=day_idx)
            res = pipeline_runner.run(
                file_path=h_file,
                source=self.bench_config.source,
                logical_date=day_dt,
                conn=conn,
                dry_run=dry_run,
            )
            total_history_rows += res.rows_received

        # 4. Inject configured faults into the final day
        final_dt = DEFAULT_START_DATE + timedelta(days=self.bench_config.history_days)
        clean_final_df = pd.read_csv(final_clean_file)
        clean_row_ids: Set[str] = set(clean_final_df["trip_id"].astype(str))
        total_final_clean_rows = len(clean_final_df)

        fault_injector = FaultInjector(
            config_bundle=self.config_bundle,
            seed=self.seed,
        )
        faulty_file_path = self.output_dir / f"trips_{final_dt.strftime('%Y%m%d')}_faulty.csv"
        labels_file_path = self.output_dir / f"trips_{final_dt.strftime('%Y%m%d')}_labels.json"

        inj_result = fault_injector.inject_file(
            input_path=final_clean_file,
            output_path=faulty_file_path,
            labels_path=labels_file_path,
        )

        # 5. Execute pipeline and batch checks on final faulty file
        # In dry-run or when conn is None, frozen reference clock aligns with final day 23:59:59
        eval_clock = FrozenClock(
            datetime(final_dt.year, final_dt.month, final_dt.day, 23, 59, 59, tzinfo=timezone.utc)
        )
        eval_pipeline_runner = PipelineRunner(
            config_bundle=self.config_bundle, clock=eval_clock
        )
        faulty_res = eval_pipeline_runner.run(
            file_path=faulty_file_path,
            source=self.bench_config.source,
            logical_date=final_dt,
            conn=conn,
            dry_run=dry_run,
        )

        # 6. Execute quarantine replay recovery test
        recovered_rows = self._evaluate_replay_recovery(
            quarantined_records=faulty_res.quarantined_records,
            conn=conn,
        )

        # 7. Evaluate detections against ground truth
        overall_metrics, per_fault_metrics = self.evaluator.evaluate(
            injected_records=inj_result.fault_records,
            quarantined_records=faulty_res.quarantined_records,
            batch_check_report=faulty_res.batch_check_report,
            total_history_rows=total_history_rows,
            total_final_day_clean_rows=total_final_clean_rows,
            total_final_day_faulty_rows=inj_result.total_output_rows,
            total_loaded_rows=faulty_res.rows_loaded,
            clean_row_ids=clean_row_ids,
            rows_recovered_by_replay=recovered_rows,
        )

        # 8. Assemble metadata and report
        runtime_seconds = time.perf_counter() - start_time
        metadata = BenchmarkRunMetadata(
            run_id=run_id,
            seed=self.seed,
            config_hash=get_config_hash(self.config_bundle.config_dir),
            git_commit_hash=get_git_commit_hash(),
            package_versions=get_package_versions(),
            runtime_seconds=runtime_seconds,
        )

        report = BenchmarkReport(
            metadata=metadata,
            overall_metrics=overall_metrics,
            per_fault_metrics=per_fault_metrics,
        )

        # 9. Write outputs
        report.write_json(Path(self.bench_config.reports_json))
        report.write_markdown(Path(self.bench_config.reports_md))

        return report

    def _evaluate_replay_recovery(
        self,
        quarantined_records: List[Dict[str, Any]],
        conn: Optional[Connection] = None,
    ) -> int:
        """Assess replay recovery count following configured remediation fix."""
        if not quarantined_records:
            return INITIAL_OFFSET_ZERO

        recovery_cfg = self.bench_config.replay_recovery
        if recovery_cfg.fix_type == "config":
            # Clone configuration and loosen or disable target rule
            modified_rules: List[RuleConfig] = []
            for rule in self.config_bundle.rules:
                cloned = deepcopy(rule)
                if cloned.rule_id == recovery_cfg.loosened_rule_id:
                    cloned.enabled = False
                modified_rules.append(cloned)

            replayer_bundle = deepcopy(self.config_bundle)
            replayer_bundle.rules = modified_rules

            replayer = QuarantineReplayer(config_bundle=replayer_bundle, clock=self.clock)
            summary = replayer.replay_records(
                records=quarantined_records,
                dry_run=True,
                conn=conn,
            )
            return summary.replayed_loaded

        return INITIAL_OFFSET_ZERO


def run_benchmark(
    config_path_or_dir: Optional[str] = None,
    seed: Optional[int] = None,
    conn: Optional[Connection] = None,
    dry_run: bool = False,
) -> BenchmarkReport:
    """Helper entry point to load config and execute benchmark."""
    bundle = load_config(config_path_or_dir)
    runner = BenchmarkRunner(config_bundle=bundle, seed_override=seed)
    return runner.run(conn=conn, dry_run=dry_run)
