# PulseCheck Operational Runbook

This runbook documents tested, step-by-step procedures for development setup, configuration verification, testing, and troubleshooting.

---

## 1. Development Environment Setup

### Prerequisites
- Python 3.11+ (verified with Python 3.13.3 64-bit on Windows)
- Git 2.40+
- Docker and Docker Compose (required for upcoming Phase 4 integration tests)

### Setup Steps
```bash
# 1. Create and activate a local virtual environment
python -m venv --system-site-packages .venv
# On Windows PowerShell:
.\.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

# 2. Install PulseCheck in editable mode
python -m pip install --no-build-isolation -e .

# 3. Verify CLI installation
pulsecheck --help
```

---

## 2. Configuration Validation (`pulsecheck validate-config`)

PulseCheck validates configuration at startup using strict Pydantic schemas. Any unconfigured keys, missing mandatory attributes, or type mismatches produce immediate actionable errors.

### Validate Active Configuration
```bash
# Validate default workspace config (./config)
pulsecheck validate-config

# Validate an explicit configuration directory or file
pulsecheck validate-config --config config/dataset.yaml

# Test with invalid configuration to verify error reporting
pulsecheck validate-config --config nonexistent_dir
```

---

## 3. Running Unit Tests

Unit tests are 100% self-contained and run without external dependencies or database services.

```bash
# Run fast unit test suite
pytest -q tests/unit

# Run tests with verbose output and coverage
pytest -v -m unit
```

---

## 4. Code Quality & Linting (`ruff`)

PulseCheck enforces strict linting, including **Pylint rule PLR2004** which prohibits unnamed magic numbers in comparisons.

```bash
# Run ruff lint checks across the entire codebase
ruff check .

# Automatically apply safe fixes
ruff check . --fix
```

---

## 5. Regenerating Documentation

Configuration documentation is derived directly from Pydantic models so it never drifts from code:

```bash
# Regenerate docs/config-reference.md
python scripts/gen_config_docs.py
```

---

## 6. Docker Compose & Database Operations

PulseCheck uses an official PostgreSQL container (`postgres:17-alpine`) managed through `docker-compose.yml`.

### Start Database Container
```bash
# Start PostgreSQL service in background
docker compose up -d

# Check container status and healthcheck
docker compose ps
```
The healthcheck uses `pg_isready -U pulsecheck -d pulsecheck_db` with a 5-second interval.

### Initialize Database Schema
Runs DDL statements to create `batch_registry`, `batch_metrics`, `quarantine`, `alerts`, and the configured dataset target table. Safe to run multiple times:
```bash
# Initialize schema for default config
pulsecheck init-db

# Initialize schema with specific config
pulsecheck init-db --config config/dataset.yaml
```

### Run Data Ingestion Pipeline
```bash
# Run ingestion on a CSV or Parquet file
pulsecheck run sample_data.csv --source partner_logistics

# Run with explicit logical date
pulsecheck run sample_data.parquet --source partner_logistics --logical-date 2026-03-01
```

### Inspect Database State
```bash
# Query loaded valid records
docker compose exec postgres psql -U pulsecheck -d pulsecheck_db -c "SELECT trip_id, city, fare_inr, record_key FROM trips LIMIT 5;"

# Query quarantined records with reason codes
docker compose exec postgres psql -U pulsecheck -d pulsecheck_db -c "SELECT id, record_key, status, reasons FROM quarantine LIMIT 5;"

# Query batch metrics
docker compose exec postgres psql -U pulsecheck -d pulsecheck_db -c "SELECT batch_id, source, rows_received, rows_loaded, rows_quarantined FROM batch_metrics;"
```

### Reset Environment / Database
```bash
# Stop containers and delete database volume
docker compose down -v

# Restart fresh and re-initialize schema
docker compose up -d
pulsecheck init-db
```

---

## 6. Synthetic Data & Fault Injection Workflows

### Fetch City Bounding Boxes
Fetches geographic bounding boxes from OpenStreetMap Nominatim adhering strictly to its usage policy (1.2s delay, identifiable User-Agent, disk caching in `reports/nominatim_cache.json`):
```bash
# Fetch default cities (Mumbai, Bengaluru, Delhi)
python scripts/fetch_city_boxes.py

# Fetch specific cities and write to custom path
python scripts/fetch_city_boxes.py Chennai Kolkata Pune --output config/cities.yaml
```

### Generate Clean Multi-Day Synthetic Data
Generates clean data strictly compliant with row rules and limits (false-alarm baseline):
```bash
# Generate 3 days of trips (50 trips/city/day) in CSV format
pulsecheck generate --days 3 --trips-per-day 50 --output-dir data/synthetic

# Generate 7 days in Parquet format with custom seed
pulsecheck generate --days 7 --trips-per-day 100 --format parquet --seed 999 --output-dir data/synthetic
```

### Inject Faults & Produce Ground-Truth Labels
Injects configured mutations from `config/faults.yaml` (bad phones, swapped timestamps, missing city, etc.) and writes an accompanying `<file>.labels.json`:
```bash
# Inject faults into a clean dataset
pulsecheck inject data/synthetic/trips_20260301.csv --output data/synthetic/trips_20260301_faulty.csv

# Inspect generated ground-truth labels
cat data/synthetic/trips_20260301_faulty_labels.json
```

---

## 4. Quarantine Management & Remediation

When rows fail row validation or conflict checks, they are stored in the `quarantine` table with their original JSON payload and failure reason codes.

### Step 1: Diagnose Root Causes & Concentration Anomalies
Aggregate quarantined rows to understand top causes and detect supplier concentration:

```bash
# Diagnose all open quarantine rows in database
pulsecheck diagnose

# Filter diagnosis by source or batch
pulsecheck diagnose --source vendor_alpha --batch BATCH_20260301

# Diagnose directly from an offline file (pre-flight check)
pulsecheck diagnose --file data/trips_faulty.csv
```

**Interpreting Concentration Alerts**:
If a reason shows `[CONCENTRATION ALERT: Source 'vendor_beta' accounts for 92.5% of failures]`, check whether an upstream API release introduced formatting bugs in that specific feed.

### Step 2: Export Rejections for Supplier Remediation
Export open quarantine records to a CSV file containing original values and human-readable error descriptions:

```bash
# Export all open rejections to CSV
pulsecheck export-rejects --output rejected_records.csv

# Filter export by source and reason code
pulsecheck export-rejects --source vendor_alpha --reason ERR_INVALID_PHONE --output vendor_alpha_bad_phones.csv

# Offline export from a faulty file
pulsecheck export-rejects --file data/trips_faulty.csv --output rejects.csv
```

The data owner reviews the `quarantine_reason` column, corrects the faulty data fields in Excel/scripts, and saves the corrected file.

### Step 3: Re-Submit Corrected Data
The data owner re-submits the corrected file through standard ingestion:

```bash
pulsecheck run data/corrected_trips.csv --source vendor_alpha
```
`IdempotentLoader` loads valid records and automatically resolves corresponding open quarantine records to `status = 'resolved_by_load'`.

### Step 4: Replay Quarantine After Configuration or Rule Changes
If an ingestion failure was caused by an overly strict rule or incorrect boundary in `config/rules.yaml`:

```bash
# 1. Preview changes safely without modifying database state
pulsecheck replay --dry-run

# 2. Preview replay for a specific batch or error code
pulsecheck replay --batch BATCH_20260301 --reason ERR_OUT_OF_BOUNDS --dry-run

# 3. Execute replay against the CURRENT configuration
pulsecheck replay --batch BATCH_20260301

# 4. Verify idempotency by running replay again (reports 0 changes)
pulsecheck replay --batch BATCH_20260301
```

Records that pass under the current rules are idempotently inserted into the target data table and updated to `status = 'replayed'`. Records that still violate rules remain `status = 'open'` with `attempts` incremented by 1.

---

## 5. Batch Verification Checks & Alert Management

PulseCheck automatically executes batch checks (volume anomalies, missing operational scopes, and data freshness) at the end of every `run` when `batch_checks.auto_check_batch: true` is configured in `config/checks.yaml`. Operators can also manually execute checks.

### Run Manual Batch Checks (`pulsecheck check-batch`)

```bash
# Check an existing batch in the database by batch ID
pulsecheck check-batch 06e4452cedf97a1cc9a5779415544180

# Check an offline data file directly
pulsecheck check-batch --file data/synthetic/trips_20260301.csv --source vendor_alpha --date 2026-03-01
```

### Inspecting Alerts in PostgreSQL

Active and historical alerts are persisted in the `alerts` table:

```sql
-- View all currently active alerts
SELECT fingerprint, check_name, scope, severity, occurrence_count,
       last_seen_at, last_notified_at
FROM alerts
WHERE status = 'active'
ORDER BY last_seen_at DESC;

-- Inspect suppressed alert counts (deduplicated during cooldown)
SELECT fingerprint, occurrence_count, healthy_batch_count, status
FROM alerts
WHERE occurrence_count > 1;

-- Check auto-closed alerts
SELECT fingerprint, healthy_batch_count, resolved_at
FROM alerts
WHERE status = 'auto_closed';
```

### Alert Webhook Integration
To enable outbound webhook alerts (e.g. to Slack, PagerDuty, or incident response webhook):
1. Set the environment variable in `.env`:
   ```bash
   ALERT_WEBHOOK_URL="https://hooks.slack.com/services/xxx/yyy/zzz"
   ```
2. By default, webhook delivery is disabled (`ALERT_WEBHOOK_URL=null`).

---

## 6. Running the Synthetic Benchmark (`pulsecheck benchmark`)

The benchmark harness tests PulseCheck's entire lifecycle—generating baseline history, injecting synthetic faults, executing batch checks, evaluating precision/recall, and testing quarantine replay recovery.

### Running the Benchmark
```bash
# Standard benchmark using settings from config/checks.yaml (bench:) and config/faults.yaml
pulsecheck benchmark

# Run with an explicit deterministic seed
pulsecheck benchmark --seed 42

# Run in dry-run mode (offline evaluation without connecting to PostgreSQL)
pulsecheck benchmark --seed 42 --dry-run

# Custom output paths for JSON and Markdown artifacts
pulsecheck benchmark --json-output reports/my_bench.json --md-output reports/my_bench.md
```

### Interpreting Benchmark Outputs
1. **Per-Fault Scorecard**:
   - `Injected`: Number of mutated rows or batch faults introduced.
   - `Caught`: Number of injected defects caught by the expected rule/check code.
   - `Missed`: Number of injected defects that escaped the detector.
   - `False Alarms`: Detections triggered on rows/scopes not targeted by that fault.
   - `Precision` & `Recall`: Computed per standard statistical formulae.
2. **Overall Pipeline Metrics**:
   - `Clean Rows Wrongly Quarantined`: Must be 0 on clean baseline feeds.
   - `Alerts on Clean Scopes`: Must be 0 on unaffected cities/scopes.
   - `Rows Recovered by Replay`: Number of quarantined rows successfully recovered when the configured fix is applied.
3. **Artifacts Produced**:
   - `reports/benchmark.json`: Machine-readable run metadata, git commit hash, configuration SHA-256, package versions, and complete scorecard.
   - `reports/benchmark.md`: Formatted GitHub Markdown scorecard.

---

## 7. NYC TLC Real Data Ingestion & Batch Replay

To execute PulseCheck on real NYC TLC Yellow Taxi parquet trip data:

### 1. Download Raw Parquet Dataset
```bash
# Download official Yellow Taxi data for January 2024 into data/raw/ (gitignored)
curl -o data/raw/yellow_tripdata_2024-01.parquet https://d37ci6vzurychx.cloudfront.net/trip-data/yellow_tripdata_2024-01.parquet
```

### 2. Partition into Daily Batches
```bash
# Stream-partition into daily files (chunked streaming, low memory)
python scripts/prepare_tlc_batches.py
```

### 3. Run Pipeline via TLC Configuration
```bash
# Validate TLC configuration
pulsecheck validate-config --config config/tlc/

# Run a single daily batch in dry-run mode
pulsecheck run data/tlc_batches/2024-01-01.parquet --source nyc_tlc_yellow --config config/tlc/ --dry-run

# Run full 31-day chronological replay and generate report
python scripts/run_all_tlc_batches.py
```

### 4. Review Execution Report
Inspect `reports/tlc_run.md` for daily breakdown, empirical quarantine rates, top reason codes, and sampled rows.
