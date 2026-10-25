# Changelog

All notable changes to the PulseCheck project will be documented in this file.
The format is based on [Keep a Changelog](https://keepachangelog.com/en/1.0.0/).

## [Unreleased]

### [Phase 7] - 2026-10-25
### Added
- Real-world production pipeline profile on NYC TLC Yellow Taxi parquet trip records (`yellow_tripdata_2024-01.parquet`, 2,964,624 rows, 49.96 MB):
  - Ingested using **zero-code configuration only** via `config/tlc/dataset.yaml`, `rules.yaml`, and `checks.yaml`.
  - Schema mapping: real parquet columns mapped to semantic roles (`VendorID` $\rightarrow$ `id`, `tpep_pickup_datetime` $\rightarrow$ `pickup_time`, `tpep_dropoff_datetime` $\rightarrow$ `delivery_time`, `PULocationID` $\rightarrow$ `scope`, `DOLocationID` $\rightarrow$ `dropoff_scope`, `fare_amount` $\rightarrow$ `fare`, `trip_distance` $\rightarrow$ `distance`).
  - Automatic rule omission: rules requiring unmapped roles (`RULE_GEO_BOUNDS`, `RULE_SWAPPED_COORDINATES`, `RULE_CANCELLED_DELIVERY`, `RULE_PHONE_FORMAT`, `RULE_PINCODE_FORMAT`, `RULE_PLATE_FORMAT`) are skipped cleanly with logged explanations.
  - Composite identity resolution: deterministic composite key `(VendorID, tpep_pickup_datetime, PULocationID, DOLocationID, fare_amount)` with empirical collision risk analysis (0.0207% collision rate).
  - Empirical rule calibration: candidate thresholds computed directly from data distributions (duration cap at 14,400s / 4.0h; speed cap at 80 mph / 128.7 km/h).
  - Chronological daily batch replay: stream-partitioned into 31 daily batches (`2024-01-01` to `2024-01-31`), executed in date order with multi-day scope volume and freshness history.
  - Data anomaly isolation: isolated 18 out-of-month records (spanning 2002, 2009, 2023, and February 2024) with root-cause documentation.
  - Audit report (`reports/tlc_run.md`): includes daily batch breakdown, empirical quarantine rates (~0.12%), reason code distributions, seeded audit samples (seed 42), and official NYC TLC data citations & Terms of Use.
- Architecture Decision Record ADR-0024 in `docs/decisions.md`.
- Generality gap documentation in `PROGRESS.md` and performance optimization hoisting `is_valid` in dry-run mode.
### Added
- Synthetic benchmark harness (`src/pulsecheck/benchmark/`):
  - `BenchmarkRunner`: End-to-end evaluation executing clean multi-day history generation, baseline loading, fault injection, pipeline validation, batch checks, and quarantine replay remediation.
  - `BenchmarkEvaluator`: Ground-truth matching logic computing precision, recall, false alarms, and clean-row quarantine tracking.
  - Closed-loop quarantine recovery assessment evaluating remediation via rule loosening or data resubmission.
  - Automated reporting: outputs machine-readable `reports/benchmark.json` and GitHub Markdown `reports/benchmark.md` with environment provenance, git commit hash, configuration SHA-256, package versions, and author statement.
- Configuration models: `BenchConfig` and `ReplayRecoveryConfig` under `ChecksConfigFile` (`bench:`) and `FaultsConfigFile`.
- CLI subcommand `pulsecheck benchmark` with `--seed`, `--dry-run`, `--json-output`, `--md-output` flags.
- Comprehensive unit test suite (`tests/unit/test_benchmark.py`) verifying precision/recall arithmetic, edge cases, and deterministic evaluation.
- Architecture Decision Records ADR-0022 and ADR-0023 in `docs/decisions.md`.

### [Phase 5] - 2026-10-25
### Added
- Batch verification check engine (`src/pulsecheck/checks/`):
  - `VolumeChecker`: Historical weekday-median comparison per `(source, scope)` with configurable ratio envelope, short-history fallback rule, and explicit zero-baseline handling.
  - `MissingScopesChecker`: Expected operational scope lookback tracker detecting when historically active scopes disappear (raising `CRITICAL` alerts with `rows = 0`).
  - `FreshnessChecker`: Data lag verification and future timestamp detector against injectable `Clock` in timezone-aware UTC.
- Alert deduplication and lifecycle manager (`src/pulsecheck/checks/alerts.py`):
  - Deterministic fingerprinting (`check_name:scope:severity`).
  - Cooldown window deduplication incrementing `occurrence_count` while suppressing repeated notifications.
  - Severity escalation bypassing cooldown and re-notifying immediately.
  - Auto-closure of active alerts after configurable consecutive healthy batches (`auto_close_healthy_batches`).
  - Multi-channel delivery: standard log, PostgreSQL `alerts` table, and HTTP webhook (if `ALERT_WEBHOOK_URL` is set).
- CLI command `pulsecheck check-batch` and automatic batch check execution at the end of `pulsecheck run`.
- Unit tests (`tests/unit/test_batch_checks.py`) and PostgreSQL integration tests (`tests/integration/test_batch_checks_integration.py`).
- Architecture Decision Records ADR-0019, ADR-0020, and ADR-0021 in `docs/decisions.md`.

### [Phase 4] - 2026-10-25
### Added
- Quarantine replay engine (`src/pulsecheck/replay.py`) with filters (`--batch`, `--reason`, `--scope`, `--status`) and `--dry-run`:
  - Re-validates open quarantine entries against CURRENT configuration.
  - Idempotently loads now-valid entries via `IdempotentLoader` and marks them `replayed`.
  - Keeps remaining invalid records `open` with incremented `attempts` counter and refreshed diagnostic reasons.
  - Computes and prints exact before/after transition counts.
- Quarantine diagnostics engine (`src/pulsecheck/diagnostics.py`):
  - Multi-dimensional aggregation grouping quarantined rows by reason code, source, scope, and logical date.
  - Computes exact defect frequencies and percentage shares.
  - Detects and flags supplier/temporal concentration when a single source or date exceeds configured limits in `config/checks.yaml` (`max_source_share`, `max_day_share`).
- Rejections exporter (`src/pulsecheck/export.py`):
  - Exports open quarantine records to CSV with original column values.
  - Appends human-readable diagnostic explanations (`quarantine_reason`) and quarantine tracking metadata (`quarantine_id`, `quarantine_scope`, `quarantine_batch_id`).
- CLI subcommands: `pulsecheck replay`, `pulsecheck diagnose`, and `pulsecheck export-rejects`.
- Automated test coverage: unit tests (`tests/unit/test_replay_diagnose.py`) and integration tests (`tests/integration/test_replay_diagnose.py`) covering replay idempotency, dry-run immutability, rule loosening, attempts incrementing, diagnostics matching, and export-correct-run loop.
- Architecture Decision Records ADR-0017 (Replay Semantics & Idempotency) and ADR-0018 (Diagnostic Concentration Thresholds) in `docs/decisions.md`.

### [Phase 3] - 2026-10-25
### Added
- OpenStreetMap Nominatim geocoding script (`scripts/fetch_city_boxes.py`) adhering strictly to usage policy with User-Agent, $\ge 1.2\text{s}$ rate limit, and local disk cache in `reports/nominatim_cache.json`.
- Synthetic trip generator engine (`src/pulsecheck/generator/`) producing pristine multi-day datasets with city-specific bounding boxes, realistic speeds, durations, and Indian identifiers via `indpy_adapter`.
- Zero false-alarm validation baseline guaranteeing clean generated data produces 0 quarantined rows.
- Parameterized fault injection engine (`src/pulsecheck/faults/`) supporting 13 pluggable fault types (both row-level and batch-level).
- Ground-truth labels generator outputting `<filename>.labels.json` capturing affected row IDs, affected scopes, fault categories, and expected detectors.
- CLI commands `pulsecheck generate` and `pulsecheck inject`.
- Unit tests (`tests/unit/test_generator_injector.py`) testing seed reproducibility, false-alarm baseline, fault rate tolerances, and ground-truth label tracking.
- Architecture Decision Records ADR-0014, ADR-0015, and ADR-0016 in `docs/decisions.md`.

### [Phase 2] - 2026-10-24
### Added
- Docker Compose configuration (`docker-compose.yml`) pinning `postgres:17-alpine` with healthcheck (`pg_isready`) and `.env` credentials.
- File reader adapters (`src/pulsecheck/readers/`) for delimited CSV and streaming Parquet with chunked reading support.
- Database DDL generation and initialization module (`src/pulsecheck/db/ddl.py`) supporting safe type mapping and safe `sql.Identifier` parameterization. Safe to run multiple times.
- Database connectivity helper (`src/pulsecheck/db/connection.py`) using `psycopg 3`.
- Quarantine writer (`src/pulsecheck/quarantine.py`) storing invalid records in `raw_row JSONB` and `reasons JSONB` with GIN indexing.
- Automatic quarantine resolution: successful load of a key updates open quarantine entries to `resolved_by_load`.
- Idempotent database loader (`src/pulsecheck/loader.py`) supporting `skip`, `update`, and `quarantine` conflict policies with chunking.
- Batch identity calculation (`file_hash`, `logical_date`, `batch_id`) and `batch_registry` deduplication with configurable `repeated_file_policy`.
- Batch metrics recording (`batch_metrics`) tracking received, loaded, quarantined, and duplicate counts.
- Full pipeline runner (`src/pulsecheck/pipeline.py`) orchestrating schema enforcement, validation, loading, quarantining, and metrics.
- CLI subcommands `pulsecheck init-db` and `pulsecheck run <file> --source <name>`.
- Unit tests for readers and DDL generation; integration tests covering duplicate file loads, quarantine resolution, multi-rule failures, conflict policies, and missing required columns.
- Architecture Decision Records ADR-0009 through ADR-0013 in `docs/decisions.md`.

### [Phase 1] - 2026-10-24
### Added
- Pydantic v2 strict configuration models in `src/pulsecheck/config/models.py` (`extra='forbid'`).
- Configuration bundle loader with environment variable overrides in `src/pulsecheck/config/loader.py`.
- Documentation generator script `scripts/gen_config_docs.py` producing `docs/config-reference.md`.
- Timezone-aware injectable clock abstraction (`src/pulsecheck/clock.py`).
- Third-party adapter modules for `indpy` and `pandera` (`src/pulsecheck/adapters/`).
- Batch-level schema verification with unknown-column policy handling (`src/pulsecheck/schema_check.py`).
- Row-level rule registry with 13 built-in rules, parameterization, and role checking (`src/pulsecheck/rules/`).
- Validation engine with multi-failure retention and deterministic composite key derivation (`src/pulsecheck/validator.py`).
- CLI tool `pulsecheck` with `validate-config` subcommand.
- Comprehensive unit test suite (34 tests) covering clock, configuration, schema checking, adapters, composite keys, and rule boundaries.
- Architecture Decision Records ADR-0006, ADR-0007, and ADR-0008 in `docs/decisions.md`.
- Operational guide in `docs/runbook.md` and system architecture in `docs/architecture.md`.

## [Phase 0] - 2026-10-24
#### Added
- Environment check for Python runtime, Docker, Docker Compose, Git, and Pip.
- Repository initialization with `main` branch and remote link to `https://github.com/PrajyotKorde-18/PulseCheck.git`.
- Root `.gitignore` configured to exclude credentials, data artifacts, caches, and environments.
- Initial documentation skeleton (`PROGRESS.md`, `CHANGELOG.md`, `docs/decisions.md`).
- System architecture specification, YAML configuration schema, SQL table designs, and row/batch rule blueprints.
