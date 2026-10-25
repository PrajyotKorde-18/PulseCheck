# PulseCheck: Project Progress & Implementation Tracker

> **Source of Truth Notice**: This document tracks strictly what is built and verified versus what is planned. Only verified items may be cited on a resume or portfolio.

## Phase Overview

| Phase | Description | Status | Verification Summary |
|---|---|---|---|
| Phase 0 | Environment Check, System Specification, Docs Skeleton & Decisions | Complete | Environment verified; docs skeleton created |
| Phase 1 | Config System, Schema Check & Row Rules (No DB) | Complete | 34 unit tests passed, ruff clean, validate-config verified |
| Phase 2 | Docker Compose Postgres, Readers, DDL, Idempotent Loader, Quarantine, `run` | Complete | 42 unit tests passed, Parquet verified, ruff clean (Docker acceptance NOT VERIFIED) |
| Phase 3 | Synthetic Data Generator, Fault Injector & Ground Truth Labels | Complete | 47 unit tests passed, Faker verified, Nominatim compliant, 0 false alarms baseline |
| Phase 4 | Quarantine Replay Subsystem & Diagnostic CLI (`replay`, `diagnose`, `export-rejects`) | Complete | 51 unit tests passed, 9 skipped, ruff clean, CLI & flow verified |
| Phase 5 | Batch-Level Checks (Volume, Freshness, Missing Scopes) & Alert Engine | Complete | 60 unit tests passed, 11 skipped, ruff clean, batch checks & alert deduplication verified |
| Phase 6 | Benchmark Pipeline & Metric Scorecard (`pulsecheck benchmark`) | Complete | 63 unit tests passed, 11 skipped, ruff clean, benchmark reports verified |
| Phase 7 | Real-World Pipeline Profile: NYC TLC Trip Records (Zero-Code Config) | Complete | 2.96M rows, 31 daily batches, 0.1127% quarantine rate, zero-code config, reports/tlc_run.md |

---

## Detailed Component Status

### 1. Configuration & Validation Subsystem
- [x] Project specification and YAML schemas proposed (Phase 0)
- [x] Pydantic base settings and strict schema models (`src/pulsecheck/config/models.py`) (Phase 1)
- [x] Role mapping definitions (dataset roles -> actual columns) (Phase 1)
- [x] CLI subcommand: `pulsecheck validate-config` (Phase 1)
- [x] Auto-generated configuration reference (`scripts/gen_config_docs.py`) (Phase 1)

### 2. File Ingestion & Reader Adapters
- [x] Reader adapter abstraction (`src/pulsecheck/readers/base.py`) (Phase 2)
- [x] CSV reader adapter with chunking support (`src/pulsecheck/readers/csv_reader.py`) (Phase 2)
- [x] Parquet reader adapter with pyarrow engine and chunking support (`src/pulsecheck/readers/parquet_reader.py`) (Phase 2)
- [x] Parquet engine verified with `pip show pyarrow` (v23.0.1) and 3-line chunked read test (`scripts/verify_parquet.py`) (Phase 2)

### 3. Schema Enforcement
- [x] Batch schema comparator (`src/pulsecheck/schema_check.py`) (Phase 1)
- [x] Missing column detection & rejected_schema status (Phase 1)
- [x] Unknown column handling policies (ignore, warn, reject) (Phase 1)

### 4. Row Validation Engine & Rule Registry
- [x] Rule registry with pluggable functions (`src/pulsecheck/rules/registry.py`) (Phase 1)
- [x] 13 built-in rules with role-based dispatch mechanism (`src/pulsecheck/rules/builtin.py`) (Phase 1)
- [x] Adapter for `indpy` (Indian phone, PIN, vehicle registration plate) (Phase 1)
- [x] Adapter for `pandera` schemas (Phase 1)
- [x] Within-batch duplicate key detection (first kept, subsequent quarantined) (Phase 1)
- [x] Multi-failure code accumulation per row (Phase 1)
- [x] Timezone-aware injectable clock protocol (`src/pulsecheck/clock.py`) (Phase 1)

### 5. PostgreSQL Idempotent Loader
- [x] DDL generation from dataset schema with documented SQL type mappings (`src/pulsecheck/db/ddl.py`) (Phase 2)
- [x] Idempotent `INSERT ... ON CONFLICT` loader (`src/pulsecheck/loader.py`) (Phase 2)
- [x] Conflict policies: `skip`, `update`, `quarantine` (Phase 2)
- [x] Deterministic composite key derivation (`src/pulsecheck/validator.py`) (Phase 1)
- [x] Transaction scope support: `batch` (all-or-nothing atomicity) and `chunk` (progressive commit) (Phase 2)

### 6. PostgreSQL Quarantine (JSONB)
- [x] Quarantine table schema with `JSONB` columns and `GIN` indexing on `reasons` (`src/pulsecheck/db/ddl.py`) (Phase 2)
- [x] Quarantine writer with full raw payload and structured reason codes (`src/pulsecheck/quarantine.py`) (Phase 2)
- [x] Lifecycle management (`open`, `resolved_by_load`, `replayed`, `dismissed`) (Phase 2)
- [x] Automatic quarantine resolution on subsequent successful load (`resolved_by_load`) (Phase 2)

### 7. Batch Identity & Pipeline CLI
- [x] Batch identity calculation: `SHA256(source | logical_date | file_hash)[:32]` (Phase 2)
- [x] Repeated identical file handling per config (`skip` or `reprocess`) (Phase 2)
- [x] CLI command: `pulsecheck init-db` (idempotent, safe to run twice) (Phase 2)
- [x] CLI command: `pulsecheck run <file> --source <name>` with summary metrics report (Phase 2)

### 8. Batch Checks & Alert Subsystem
- [x] Batch registry and metrics tracking (`batch_registry`, `batch_metrics`) tables initialized (Phase 2)
- [x] Median-volume check with weekday matching and missing-scope zero baseline (`src/pulsecheck/checks/volume.py`) (Phase 5)
- [x] Freshness check and future timestamp detector with injectable clock (`src/pulsecheck/checks/freshness.py`) (Phase 5)
- [x] Missing operational scopes detection against recent scope history (`src/pulsecheck/checks/scopes.py`) (Phase 5)
- [x] Alert deduplication engine with fingerprinting, cooldown windows, severity escalation, and auto-close (`src/pulsecheck/checks/alerts.py`) (Phase 5)
- [x] Multi-channel notification dispatch: log, DB `alerts` table, and optional webhook (`ALERT_WEBHOOK_URL`) (Phase 5)
- [x] CLI command: `pulsecheck check-batch <batch_id>` (with `--file` fallback) and automatic execution in `run` (`auto_check_batch`) (Phase 5)

### 9. Quarantine Replay, Diagnostics & Remediation
- [x] Safe replay pipeline using current configuration (`src/pulsecheck/replay.py`) (Phase 4)
- [x] Filterable replay (`--batch`, `--reason`, `--scope`, `--status`, `--dry-run`) (Phase 4)
- [x] Root cause diagnostics with concentration detection (`src/pulsecheck/diagnostics.py`) (Phase 4)
- [x] Human-readable rejections export to CSV (`src/pulsecheck/export.py`) (Phase 4)
- [x] CLI subcommands: `pulsecheck replay`, `pulsecheck diagnose`, `pulsecheck export-rejects` (Phase 4)
- [x] Configurable concentration limits in `config/checks.yaml` (`max_source_share`, `max_day_share`) (Phase 4)
- [x] Full unit & integration test coverage (`tests/unit/test_replay_diagnose.py`, `tests/integration/test_replay_diagnose.py`) (Phase 4)

### 10. Synthetic Data, Fault Injection & Benchmark Suite
- [x] OpenStreetMap Nominatim geocoder with disk caching & rate limiting (`scripts/fetch_city_boxes.py`) (Phase 3)
- [x] Faker API verification and reproducibility testing (`scripts/verify_faker.py`) (Phase 3)
- [x] Parameterized synthetic multi-day trip generator (`src/pulsecheck/generator/`) (Phase 3)
- [x] Zero false-alarm validation baseline verification (Phase 3)
- [x] Parameterized fault injector with 13 plugins (`src/pulsecheck/faults/`) (Phase 3)
- [x] Ground-truth labels generator producing `<filename>.labels.json` (Phase 3)
- [x] CLI commands: `pulsecheck generate` and `pulsecheck inject` (Phase 3)
- [x] Benchmark runner measuring detection rates (`reports/benchmark.json`, `reports/benchmark.md`) (`src/pulsecheck/benchmark/`) (Phase 6)
- [x] CLI subcommand: `pulsecheck benchmark` with `--seed`, `--dry-run`, custom report paths (Phase 6)
- [x] Real-world NYC TLC trip data profile (`config/tlc/`) (Phase 7)

---

## Known Limitations & Intentional Design Constraints
1. **Composite Key Collision Assumption**: When multiple roles define a composite key, deterministic hashing maps them to a unique surrogate ID. Natural collisions or duplicate keys within the same batch are intentionally tagged as duplicates by design.
2. **Postgres GIN Index vs MySQL JSON**: PostgreSQL native JSONB supports generalized inverted indexes (GIN) for sub-field containment queries (`@>`), which MySQL lacks without explicit virtual generated columns.
3. **PostgreSQL Single Statement Cardinality**: PostgreSQL `INSERT ... ON CONFLICT` rejects duplicate keys inside a single statement. PulseCheck enforces intra-batch deduplication in the validation layer prior to database insertion.
4. **OSM Nominatim Usage Policy**: Geocoding respects OSM policies by enforcing $\ge 1.2\text{s}$ between calls, local caching in `reports/nominatim_cache.json`, and identifiable User-Agent.

---

## Generality Gaps Identified (Phase 7: NYC TLC Real Data Ingestion)

During Phase 7 execution against real NYC TLC trip records (`yellow_tripdata_2024-01.parquet`, 2.96M rows, ~80k-110k rows/day), the following two implementation issues in `src/` were identified:

### Gap 1: $O(N^2)$ Property Evaluation in In-Memory Dry-Run Quarantine Extraction (`src/pulsecheck/pipeline.py:191`)
- **Location**: `src/pulsecheck/pipeline.py`, line 191 in `_execute_dry_run()`.
- **Issue**:
  ```python
  invalid_reasons = [
      reasons
      for idx, reasons in enumerate(val_result.row_reasons)
      if not val_result.is_valid_mask.iloc[idx]
  ]
  ```
  `ValidationResult.is_valid_mask` is a Python `@property` that dynamically constructs a new `pd.Series([len(reasons) == 0 for reasons in self.row_reasons])` on every attribute access. Inside the list comprehension over a 50,000-row chunk, accessing `val_result.is_valid_mask.iloc[idx]` calls this property 50,000 times, resulting in $50,000 \times 50,000 = 2,500,000,000$ operations. This freezes the process on large batches. (Note: in `_execute_with_conn` line 375, `is_valid` is correctly hoisted outside the loop).
- **Proposed Patch**: Hoist `is_valid = val_result.is_valid_mask` once before the comprehension, or directly extract invalid rows via `[reasons for reasons in val_result.row_reasons if len(reasons) > 0]`.

### Gap 2: Hardcoded Role Name for Scope Aggregations (`src/pulsecheck/pipeline.py:160, 340`)
- **Location**: `src/pulsecheck/pipeline.py`, line 160 (`_execute_dry_run`) and line 340 (`_execute_with_conn`).
- **Issue**:
  ```python
  scope_role_col = self.dataset_config.get_primary_column_for_role("city")
  ```
  The scope role is hardcoded to look only for role `"city"`. In the NYC TLC dataset, taxi trip data is partitioned by Taxi Zone ID (`PULocationID`), mapped to role `"scope"`. As a result, `scope_role_col` evaluates to `None`, bypassing scope volume tracking and missing scope checks for non-city scope models.
- **Proposed Patch**: Update to look for `"scope"` first, then fall back to `"city"`:
  ```python
  scope_role_col = (
      self.dataset_config.get_primary_column_for_role("scope")
      or self.dataset_config.get_primary_column_for_role("city")
  )
  ```

---

## Verification History

| Date | Phase | Verified Command | Exit Code | Result Summary |
|---|---|---|---|---|
| 2026-10-24 | Phase 0 | `python --version`, `pip --version`, `docker --version`, `docker compose version`, `git --version`, `git remote -v`, `git status` | 0 | Environment checked; git initialized; remote configured |
| 2026-10-24 | Phase 1 | `ruff check .` | 0 | Code quality verified clean with PLR2004 enabled |
| 2026-10-24 | Phase 1 | `pytest -q tests/unit` | 0 | 34 unit tests passed (clock, schemas, rules, boundaries) |
| 2026-10-24 | Phase 1 | `pulsecheck validate-config` | 0 | Configuration verified across dataset, rules, checks, cities |
| 2026-10-24 | Phase 1 | `python scripts/gen_config_docs.py` | 0 | Generated docs/config-reference.md from Pydantic models |
| 2026-10-24 | Phase 2 | `pip show pyarrow` | 0 | pyarrow 23.0.1 verified |
| 2026-10-24 | Phase 2 | `python scripts/verify_parquet.py` | 0 | Verified 3-line Parquet generation and 2-row chunked reading |
| 2026-10-24 | Phase 2 | `ruff check .` | 0 | Code quality verified clean across all new modules and tests |
| 2026-10-24 | Phase 2 | `pytest -q` | 0 | 42 passed, 7 skipped (integration tests require live Docker container) |
| 2026-10-25 | Phase 3 | `python scripts/verify_faker.py` | 0 | Verified Faker 40.43.0 deterministic seeding |
| 2026-10-25 | Phase 3 | `python scripts/fetch_city_boxes.py` | 0 | Fetched & disk-cached bounding boxes for Mumbai, Bengaluru, Delhi |
| 2026-10-25 | Phase 3 | `ruff check .` | 0 | All checks passed cleanly with PLR2004 |
| 2026-10-25 | Phase 3 | `pytest -q` | 0 | 47 passed, 7 skipped (all 5 Phase 3 unit tests passed) |
| 2026-10-25 | Phase 4 | `ruff check .` | 0 | All checks passed cleanly with PLR2004 on replay/diagnose/export |
| 2026-10-25 | Phase 4 | `python scripts/gen_config_docs.py` | 0 | Regenerated docs/config-reference.md with DiagnosticsCheckConfig |
| 2026-10-25 | Phase 4 | `pytest -q` | 0 | 51 passed, 9 skipped (unit & integration tests for Phase 4) |
| 2026-10-25 | Phase 5 | `python scripts/gen_config_docs.py` | 0 | Regenerated docs/config-reference.md with Phase 5 check models |
| 2026-10-25 | Phase 5 | `ruff check .` | 0 | All checks passed cleanly with PLR2004 on batch checks & alerts |
| 2026-10-25 | Phase 5 | `pytest -q` | 0 | 60 passed, 11 skipped (unit tests & integration suite for Phase 5) |
| 2026-10-25 | Phase 6 | `pulsecheck benchmark --seed 42 --dry-run` | 0 | Verified reproducible benchmark run with precision, recall, and replay recovery |
| 2026-10-25 | Phase 6 | `ruff check .` | 0 | All checks passed cleanly with PLR2004 across benchmark suite |
| 2026-10-25 | Phase 6 | `pytest -q` | 0 | 63 passed, 11 skipped (including report arithmetic and evaluation tests) |
| 2026-10-25 | Phase 7 | `python scripts/prepare_tlc_batches.py` | 0 | Stream-partitioned 2.96M rows into 31 daily batches; isolated 18 out-of-month records |
| 2026-10-25 | Phase 7 | `python scripts/run_all_tlc_batches.py` | 0 | Chronological replay of 31 days (2.96M rows), 0.1127% quarantine rate, reports/tlc_run.md generated |
| 2026-10-25 | Phase 7 | `ruff check .` | 0 | All checks passed cleanly across codebase and scripts |
| 2026-10-25 | Phase 7 | `pytest -q` | 0 | 63 passed, 11 skipped in 51.97s |
| 2026-10-25 | Phase 7 | `git status --short` | 0 | Verified untracked data directory; no data files tracked |

