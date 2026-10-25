"""PulseCheck Command Line Interface (CLI)."""

import argparse
import sys
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd

from pulsecheck.benchmark.runner import BenchmarkRunner
from pulsecheck.checks.engine import BatchCheckEngine
from pulsecheck.clock import SystemClock
from pulsecheck.config.loader import ConfigBundle, ConfigError, load_config
from pulsecheck.config.models import PulseCheckSettings
from pulsecheck.db.connection import get_db_connection
from pulsecheck.db.ddl import init_db
from pulsecheck.diagnostics import QuarantineDiagnostics
from pulsecheck.export import DEFAULT_EXPORT_PATH, QuarantineExporter
from pulsecheck.faults.injector import FaultInjector
from pulsecheck.generator.engine import GenerationParams, SyntheticTripGenerator
from pulsecheck.pipeline import PipelineRunner
from pulsecheck.readers.factory import get_reader
from pulsecheck.replay import QuarantineReplayer
from pulsecheck.validator import Validator


def create_parser() -> argparse.ArgumentParser:
    """Construct CLI argument parser with subcommands."""
    parser = argparse.ArgumentParser(
        prog="pulsecheck",
        description="PulseCheck: Ingestion Validation, Schema Enforcement & Quarantine Framework",
    )
    parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory or dataset.yaml file",
    )

    subparsers = parser.add_subparsers(dest="command", required=True)

    # validate-config
    validate_parser = subparsers.add_parser(
        "validate-config",
        help="Validate all configuration files and environment settings against strict schemas",
    )
    validate_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory or dataset.yaml file",
    )

    # init-db
    init_db_parser = subparsers.add_parser(
        "init-db",
        help="Initialize PostgreSQL core tables, indexes, and dataset target table",
    )
    init_db_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory or dataset.yaml file",
    )

    # run
    run_parser = subparsers.add_parser(
        "run",
        help="Enforce schema, validate rows, load valid data, and quarantine invalid rows",
    )
    run_parser.add_argument(
        "file",
        help="Path to the data file to process (CSV or Parquet)",
    )
    run_parser.add_argument(
        "--source",
        "-s",
        required=True,
        help="Name of the ingestion source (e.g. partner_logistics, nyc_taxi)",
    )
    run_parser.add_argument(
        "--logical-date",
        "-d",
        dest="logical_date",
        default=None,
        help="Logical batch date in YYYY-MM-DD format (defaults to current date)",
    )
    run_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory or dataset.yaml file",
    )
    run_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Validate schema and rules without writing to database",
    )

    # generate
    gen_parser = subparsers.add_parser(
        "generate",
        help="Generate synthetic multi-day logistics trip files",
    )
    gen_parser.add_argument(
        "--days",
        "-d",
        type=int,
        default=3,
        help="Number of days to generate (default: 3)",
    )
    gen_parser.add_argument(
        "--trips-per-day",
        "-t",
        type=int,
        default=50,
        help="Base trips per city per day (default: 50)",
    )
    gen_parser.add_argument(
        "--start-date",
        default="2026-03-01",
        help="Start date in YYYY-MM-DD format (default: 2026-03-01)",
    )
    gen_parser.add_argument(
        "--seed",
        "-s",
        type=int,
        default=42,
        help="Random seed for reproducible generation (default: 42)",
    )
    gen_parser.add_argument(
        "--output-dir",
        "-o",
        default="data/generated",
        help="Directory to output generated files (default: data/generated)",
    )
    gen_parser.add_argument(
        "--format",
        "-f",
        choices=["csv", "parquet"],
        default="csv",
        help="Output file format (csv or parquet)",
    )
    gen_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory",
    )

    # inject
    inject_parser = subparsers.add_parser(
        "inject",
        help="Inject configured synthetic faults and produce ground-truth labels",
    )
    inject_parser.add_argument(
        "file",
        help="Source data file to inject faults into",
    )
    inject_parser.add_argument(
        "--output",
        "-o",
        default=None,
        help="Path to output mutated faulty file",
    )
    inject_parser.add_argument(
        "--labels",
        "-l",
        default=None,
        help="Path to output ground-truth labels JSON file",
    )
    inject_parser.add_argument(
        "--seed",
        "-s",
        type=int,
        default=12345,
        help="Random seed for fault sampling (default: 12345)",
    )
    inject_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory",
    )

    # replay
    replay_parser = subparsers.add_parser(
        "replay",
        help="Replay open quarantine records through current configuration",
    )
    replay_parser.add_argument(
        "--batch",
        default=None,
        help="Filter quarantine records by batch ID",
    )
    replay_parser.add_argument(
        "--reason",
        default=None,
        help="Filter quarantine records by reason code (e.g. ERR_INVALID_PHONE)",
    )
    replay_parser.add_argument(
        "--scope",
        default=None,
        help="Filter quarantine records by scope (e.g. Mumbai)",
    )
    replay_parser.add_argument(
        "--status",
        default="open",
        help="Quarantine record status filter (default: open)",
    )
    replay_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Test replay and preview results without modifying database tables",
    )
    replay_parser.add_argument(
        "--file",
        "-f",
        default=None,
        help="Optional local data file to replay from (for offline evaluation)",
    )
    replay_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory",
    )

    # diagnose
    diag_parser = subparsers.add_parser(
        "diagnose",
        help="Group quarantined records by cause and flag statistical concentrations",
    )
    diag_parser.add_argument(
        "--batch",
        default=None,
        help="Filter quarantine records by batch ID",
    )
    diag_parser.add_argument(
        "--source",
        default=None,
        help="Filter quarantine records by source feed",
    )
    diag_parser.add_argument(
        "--scope",
        default=None,
        help="Filter quarantine records by scope",
    )
    diag_parser.add_argument(
        "--status",
        default="open",
        help="Quarantine record status filter (default: open)",
    )
    diag_parser.add_argument(
        "--file",
        "-f",
        default=None,
        help="Optional local data file to diagnose (for offline evaluation)",
    )
    diag_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory",
    )

    # export-rejects
    export_parser = subparsers.add_parser(
        "export-rejects",
        help="Export quarantined records to CSV with human-readable reason column",
    )
    export_parser.add_argument(
        "--output",
        "-o",
        default=DEFAULT_EXPORT_PATH,
        help=f"Path to output CSV file (default: {DEFAULT_EXPORT_PATH})",
    )
    export_parser.add_argument(
        "--batch",
        default=None,
        help="Filter quarantine records by batch ID",
    )
    export_parser.add_argument(
        "--reason",
        default=None,
        help="Filter quarantine records by reason code",
    )
    export_parser.add_argument(
        "--scope",
        default=None,
        help="Filter quarantine records by scope",
    )
    export_parser.add_argument(
        "--status",
        default="open",
        help="Quarantine record status filter (default: open)",
    )
    export_parser.add_argument(
        "--file",
        "-f",
        default=None,
        help="Optional local data file to export rejects from (for offline evaluation)",
    )
    export_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory",
    )

    # check-batch
    check_batch_parser = subparsers.add_parser(
        "check-batch",
        help="Run batch-level volume, freshness, and missing scopes checks",
    )
    check_batch_parser.add_argument(
        "batch_id",
        nargs="?",
        default=None,
        help="Batch ID to evaluate",
    )
    check_batch_parser.add_argument(
        "--batch",
        "-b",
        dest="batch_flag",
        default=None,
        help="Batch ID to evaluate (alternative to positional argument)",
    )
    check_batch_parser.add_argument(
        "--file",
        "-f",
        default=None,
        help="Optional local data file to evaluate batch checks offline",
    )
    check_batch_parser.add_argument(
        "--source",
        default=None,
        help="Source feed identifier",
    )
    check_batch_parser.add_argument(
        "--date",
        default=None,
        help="Logical date (YYYY-MM-DD)",
    )
    check_batch_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory",
    )

    # benchmark
    benchmark_parser = subparsers.add_parser(
        "benchmark",
        help="Run synthetic baseline generation, fault injection, and detection evaluation",
    )
    benchmark_parser.add_argument(
        "--seed",
        type=int,
        default=None,
        help="Deterministic random seed override",
    )
    benchmark_parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Run benchmark in dry-run mode (without committing to PostgreSQL)",
    )
    benchmark_parser.add_argument(
        "--json-output",
        default=None,
        help="Custom file path for benchmark.json report",
    )
    benchmark_parser.add_argument(
        "--md-output",
        default=None,
        help="Custom file path for benchmark.md report",
    )
    benchmark_parser.add_argument(
        "--config",
        "-c",
        dest="config_path",
        default=None,
        help="Path to configuration directory",
    )

    return parser


def handle_validate_config(config_path: Optional[str]) -> int:
    """Execute config validation and print diagnostic report."""
    try:
        bundle = load_config(config_path)
        print(f"SUCCESS: Configuration validated successfully from '{bundle.config_dir}'.")
        print(f" - Dataset Name: {bundle.dataset_config.name}")
        print(f" - Table Name: {bundle.dataset_config.table_name}")
        print(f" - Configured Columns: {len(bundle.dataset_config.columns)}")
        print(f" - Identity Roles: {bundle.dataset_config.identity_roles}")
        print(f" - Active Rules: {len(bundle.rules)}")
        print(f" - Configured Cities: {len(bundle.cities)}")
        return 0
    except ConfigError as e:
        print(f"ERROR: Configuration validation failed:\n{e}", file=sys.stderr)
        return 1
    except Exception as e:
        print(f"UNEXPECTED ERROR: {e}", file=sys.stderr)
        return 1


def handle_init_db(config_path: Optional[str]) -> int:
    """Initialize PostgreSQL database schema idempotently."""
    try:
        bundle = load_config(config_path)
        settings = PulseCheckSettings()
        with get_db_connection(settings) as conn:
            init_db(conn, bundle.dataset_config)

        print("SUCCESS: Database initialized successfully.")
        print("Core tables and indexes verified:")
        print(" - batch_registry")
        print(" - batch_metrics")
        print(" - quarantine (with GIN index on reasons)")
        print(" - alerts")
        print(f"Dataset table initialized: {bundle.dataset_config.table_name}")
        return 0
    except Exception as e:
        print(f"ERROR: Database initialization failed:\n{e}", file=sys.stderr)
        return 1


def handle_run(args: argparse.Namespace) -> int:
    """Execute end-to-end ingestion run on data file."""
    try:
        bundle = load_config(args.config_path)
        file_path = Path(args.file)

        logical_date = None
        if args.logical_date:
            logical_date = datetime.strptime(args.logical_date, "%Y-%m-%d").date()

        runner = PipelineRunner(config_bundle=bundle)
        res = runner.run(
            file_path=file_path,
            source=args.source,
            logical_date=logical_date,
            dry_run=getattr(args, "dry_run", False),
        )

        print("=" * 60)
        print("PulseCheck Batch Execution Summary")
        print("=" * 60)
        print(f"Batch ID:         {res.batch_id}")
        print(f"Status:           {res.status.upper()}")
        print(f"Source:           {res.source}")
        print(f"Logical Date:     {res.logical_date}")
        print(f"File Path:        {res.file_path}")
        print(f"File Hash:        {res.file_hash[:16]}...")
        print(f"Rows Received:    {res.rows_received}")
        print(f"Rows Loaded:      {res.rows_loaded}")
        print(f"Rows Quarantined: {res.rows_quarantined}")
        print(f"Rows Duplicate:   {res.rows_duplicate}")
        print(f"Duration:         {res.duration_seconds:.2f}s")
        if res.message:
            print(f"Message:          {res.message}")

        if res.batch_check_report:
            print("-" * 60)
            print("Batch-Level Checks:")
            report = res.batch_check_report
            status_str = "PASSED" if report.all_passed else "ALERTS TRIGGERED"
            print(f"Check Status:     {status_str}")
            for finding in report.findings:
                status_icon = "[PASS]" if finding.passed else f"[{finding.severity}]"
                scope_str = f" ({finding.scope})" if finding.scope else ""
                print(f"  {status_icon} {finding.check_name}{scope_str}: {finding.message}")

            if report.notified_alerts:
                print(f"Dispatched Alerts: {len(report.notified_alerts)}")
                for alert in report.notified_alerts:
                    print(f"  - [{alert.severity}] {alert.check_name}: {alert.message}")
            if report.suppressed_alerts:
                print(f"Suppressed Alerts: {len(report.suppressed_alerts)}")
            if report.auto_closed_alerts:
                print(f"Auto-Closed Alerts: {len(report.auto_closed_alerts)}")

        print("=" * 60)
        return 0
    except Exception as e:
        print(f"ERROR: Batch execution failed:\n{e}", file=sys.stderr)
        return 1


def handle_generate(args: argparse.Namespace) -> int:
    """Generate synthetic multi-day trip dataset files."""
    try:
        bundle = load_config(args.config_path)
        start_date = datetime.strptime(args.start_date, "%Y-%m-%d").date()
        params = GenerationParams(
            start_date=start_date,
            days=args.days,
            trips_per_city_day=args.trips_per_day,
            seed=args.seed,
            output_dir=Path(args.output_dir),
            file_format=args.format,
        )
        generator = SyntheticTripGenerator(config_bundle=bundle, params=params)
        created_files = generator.generate_all()

        print("=" * 60)
        print("PulseCheck Synthetic Data Generation Complete")
        print("=" * 60)
        print(f"Days Generated:       {args.days}")
        print(f"Base Trips/City/Day:  {args.trips_per_day}")
        print(f"Random Seed:          {args.seed}")
        print(f"Output Directory:     {args.output_dir}")
        print(f"Format:               {args.format.upper()}")
        print(f"Files Created ({len(created_files)}):")
        for f in created_files:
            print(f"  - {f}")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"ERROR: Generation failed:\n{e}", file=sys.stderr)
        return 1


def handle_inject(args: argparse.Namespace) -> int:
    """Inject synthetic faults into clean dataset and write labels."""
    try:
        bundle = load_config(args.config_path)
        injector = FaultInjector(
            config_bundle=bundle,
            seed=args.seed,
        )
        result = injector.inject_file(
            input_path=Path(args.file),
            output_path=Path(args.output) if args.output else None,
            labels_path=Path(args.labels) if args.labels else None,
        )

        print("=" * 60)
        print("PulseCheck Fault Injection Complete")
        print("=" * 60)
        print(f"Source File:         {result.input_file}")
        print(f"Faulty File:         {result.output_file}")
        print(f"Ground Truth Labels: {result.labels_file}")
        print(f"Initial Rows:        {result.total_input_rows}")
        print(f"Output Rows:         {result.total_output_rows}")
        print(f"Faults Applied:      {result.faults_applied}")
        for rec in result.fault_records:
            count = len(rec.affected_row_ids) if rec.scope == "rows" else len(rec.affected_scopes)
            print(f"  - {rec.fault_name} ({rec.expected_detector}): {count} affected {rec.scope}")
        print("=" * 60)
        return 0
    except Exception as e:
        print(f"ERROR: Fault injection failed:\n{e}", file=sys.stderr)
        return 1


def _extract_quarantined_from_file(
    bundle: ConfigBundle, file_path: Path, source_name: str = "offline_file"
) -> List[Dict[str, Any]]:
    """Helper to extract quarantined records and reasons from a local file without DB."""
    reader = get_reader(bundle.dataset_config)
    validator = Validator(config_bundle=bundle, clock=SystemClock())
    df = reader.read_all(file_path)
    val_res = validator.validate(df)
    is_valid = val_res.is_valid_mask.tolist()
    reasons_list = val_res.row_reasons

    records: List[Dict[str, Any]] = []
    scope_col = bundle.dataset_config.get_primary_column_for_role("city")
    for idx, (passed, reasons) in enumerate(zip(is_valid, reasons_list, strict=True)):
        if not passed:
            raw_row = df.iloc[idx].to_dict()
            scope_val = raw_row.get(scope_col) if scope_col else None
            serialized_reasons = [
                {
                    "code": r.code,
                    "rule": r.rule,
                    "fields": r.fields,
                    "message": r.message,
                }
                for r in reasons
            ]
            records.append(
                {
                    "id": idx + 1,
                    "batch_id": "offline_batch",
                    "source": source_name,
                    "scope": scope_val,
                    "raw_row": raw_row,
                    "reasons": serialized_reasons,
                    "status": "open",
                    "attempts": 0,
                }
            )
    return records


def handle_replay(args: argparse.Namespace) -> int:
    """Replay quarantined records against current configuration."""
    try:
        bundle = load_config(args.config_path)
        replayer = QuarantineReplayer(bundle)

        if getattr(args, "file", None):
            file_path = Path(args.file)
            records = _extract_quarantined_from_file(bundle, file_path, source_name=file_path.stem)
            summary = replayer.replay_records(records, dry_run=args.dry_run)
            print(summary.format_text())
            return 0

        settings = PulseCheckSettings()
        with get_db_connection(settings) as conn:
            summary = replayer.replay_from_db(
                conn=conn,
                batch_id=args.batch,
                reason=args.reason,
                scope=args.scope,
                status=args.status,
                dry_run=args.dry_run,
            )
            print(summary.format_text())
            return 0
    except Exception as e:
        print(f"ERROR: Quarantine replay failed:\n{e}", file=sys.stderr)
        return 1


def handle_diagnose(args: argparse.Namespace) -> int:
    """Execute diagnostic analysis on quarantined records."""
    try:
        bundle = load_config(args.config_path)
        diagnostics = QuarantineDiagnostics(bundle)

        if getattr(args, "file", None):
            file_path = Path(args.file)
            records = _extract_quarantined_from_file(bundle, file_path, source_name=file_path.stem)
            report = diagnostics.analyze_records(records)
            print(report.format_text())
            return 0

        settings = PulseCheckSettings()
        with get_db_connection(settings) as conn:
            report = diagnostics.analyze_from_db(
                conn=conn,
                batch_id=args.batch,
                source=getattr(args, "source", None),
                scope=args.scope,
                status=args.status,
            )
            print(report.format_text())
            return 0
    except Exception as e:
        print(f"ERROR: Diagnostics execution failed:\n{e}", file=sys.stderr)
        return 1


def handle_export_rejects(args: argparse.Namespace) -> int:
    """Export quarantined rejected records to CSV for operational fixes."""
    try:
        bundle = load_config(args.config_path)
        exporter = QuarantineExporter(bundle)
        out_path = Path(args.output)

        if getattr(args, "file", None):
            file_path = Path(args.file)
            records = _extract_quarantined_from_file(bundle, file_path, source_name=file_path.stem)
            saved_path = exporter.export_records_to_csv(records, out_path)
            print(f"SUCCESS: Exported {len(records)} quarantined reject rows to '{saved_path}'.")
            return 0

        settings = PulseCheckSettings()
        with get_db_connection(settings) as conn:
            saved_path = exporter.export_from_db(
                conn=conn,
                output_path=out_path,
                batch_id=args.batch,
                reason=args.reason,
                scope=args.scope,
                status=args.status,
            )
            print(f"SUCCESS: Exported open quarantine records to '{saved_path}'.")
            return 0
    except Exception as e:
        print(f"ERROR: Export rejects failed:\n{e}", file=sys.stderr)
        return 1


def handle_check_batch(args: argparse.Namespace) -> int:
    """Execute batch-level volume, freshness, and missing scopes checks."""
    try:
        bundle = load_config(args.config_path)
        batch_id = args.batch_id or getattr(args, "batch_flag", None)

        if getattr(args, "file", None):
            file_path = Path(args.file)
            reader = get_reader(bundle.dataset_config)
            validator = Validator(config_bundle=bundle, clock=SystemClock())
            df = reader.read_all(file_path)
            val_res = validator.validate(df)
            scope_col = bundle.dataset_config.get_primary_column_for_role("city")
            event_time_col = bundle.dataset_config.get_primary_column_for_role("pickup_time")

            scope_volumes: Dict[Optional[str], int] = {}
            if scope_col and scope_col in df.columns:
                valid_df = df[val_res.is_valid_mask]
                for sc, grp in valid_df.groupby(scope_col):
                    scope_volumes[str(sc)] = len(grp)

            latest_event_time = None
            if event_time_col and event_time_col in df.columns:
                ts = pd.to_datetime(df[event_time_col], errors="coerce").max()
                if pd.notna(ts):
                    latest_event_time = ts.to_pydatetime()
                    if latest_event_time.tzinfo is None:
                        latest_event_time = latest_event_time.replace(tzinfo=timezone.utc)

            run_date = (
                datetime.strptime(args.date, "%Y-%m-%d").date()
                if args.date
                else datetime.now(timezone.utc).date()
            )
            source = args.source or file_path.stem
            effective_batch_id = batch_id or f"BATCH_{file_path.stem}"

            engine = BatchCheckEngine(config_bundle=bundle, clock=SystemClock())
            report = engine.run_checks(
                batch_id=effective_batch_id,
                source=source,
                logical_date=run_date,
                current_scope_volumes=scope_volumes,
                latest_event_time=latest_event_time,
                history_by_scope={},
                recent_historical_scopes=set(scope_volumes.keys()),
                conn=None,
            )
        else:
            if not batch_id:
                print("ERROR: Must specify batch ID or use --file <path>", file=sys.stderr)
                return 1

            settings = PulseCheckSettings()
            with get_db_connection(settings) as conn:
                engine = BatchCheckEngine(config_bundle=bundle, clock=SystemClock())
                report = engine.run_batch_checks_from_db(conn, batch_id=batch_id)

        print("=" * 60)
        print("PulseCheck Batch Verification Report")
        print("=" * 60)
        print(f"Batch ID:         {report.batch_id}")
        print(f"Source:           {report.source}")
        print(f"Logical Date:     {report.logical_date}")
        status_str = "PASSED" if report.all_passed else "ALERTS TRIGGERED"
        print(f"Overall Status:   {status_str}")
        print("-" * 60)
        print("Findings:")
        for finding in report.findings:
            status_icon = "[PASS]" if finding.passed else f"[{finding.severity}]"
            scope_str = f" ({finding.scope})" if finding.scope else ""
            print(f"  {status_icon} {finding.check_name}{scope_str}: {finding.message}")

        if report.notified_alerts:
            print(f"\nDispatched Alerts ({len(report.notified_alerts)}):")
            for alert in report.notified_alerts:
                print(f"  - [{alert.severity}] {alert.check_name}: {alert.message}")
        if report.suppressed_alerts:
            print(f"\nSuppressed Alerts (Cooldown Active: {len(report.suppressed_alerts)}):")
            for alert in report.suppressed_alerts:
                print(
                    f"  - [{alert.severity}] {alert.check_name} "
                    f"(Count: {alert.occurrence_count})"
                )
        if report.auto_closed_alerts:
            print(f"\nAuto-Closed Alerts ({len(report.auto_closed_alerts)}):")
            for alert in report.auto_closed_alerts:
                print(f"  - {alert.fingerprint} (Healthy batches: {alert.healthy_batch_count})")
        print("=" * 60)
        return 0 if report.all_passed else 1

    except Exception as e:
        print(f"ERROR: Batch checks failed:\n{e}", file=sys.stderr)
        return 1


def handle_benchmark(args: argparse.Namespace) -> int:
    """Execute end-to-end benchmark run and output scorecard."""
    try:
        config_override = getattr(args, "config_path", None)
        bundle = load_config(config_override)

        if getattr(args, "json_output", None):
            bundle.bench.reports_json = args.json_output
        if getattr(args, "md_output", None):
            bundle.bench.reports_md = args.md_output

        settings = PulseCheckSettings()
        conn = None
        is_dry_run = getattr(args, "dry_run", False)

        if not is_dry_run:
            try:
                conn = get_db_connection(settings)
            except Exception:
                is_dry_run = True

        runner = BenchmarkRunner(config_bundle=bundle, seed_override=getattr(args, "seed", None))
        report = runner.run(conn=conn, dry_run=is_dry_run)
        if conn:
            conn.close()

        print(report.to_markdown())
        print("=" * 60)
        print("PulseCheck Benchmark Completed Successfully")
        print(f"JSON Report:     {bundle.bench.reports_json}")
        print(f"Markdown Report: {bundle.bench.reports_md}")
        print("=" * 60)
        return 0

    except Exception as e:
        print(f"ERROR: Benchmark run failed:\n{e}", file=sys.stderr)
        return 1


def main(argv: Optional[List[str]] = None) -> int:
    """Main CLI entrypoint."""
    parser = create_parser()
    args = parser.parse_args(argv)

    config_override = getattr(args, "config_path", None)

    if args.command == "validate-config":
        return handle_validate_config(config_override)
    elif args.command == "init-db":
        return handle_init_db(config_override)
    elif args.command == "run":
        return handle_run(args)
    elif args.command == "generate":
        return handle_generate(args)
    elif args.command == "inject":
        return handle_inject(args)
    elif args.command == "replay":
        return handle_replay(args)
    elif args.command == "diagnose":
        return handle_diagnose(args)
    elif args.command == "export-rejects":
        return handle_export_rejects(args)
    elif args.command == "check-batch":
        return handle_check_batch(args)
    elif args.command == "benchmark":
        return handle_benchmark(args)

    print(f"Subcommand '{args.command}' is scheduled for an upcoming phase.", file=sys.stderr)
    return 1


if __name__ == "__main__":
    sys.exit(main())
