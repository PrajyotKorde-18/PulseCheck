# PulseCheck

PulseCheck is a declarative data ingestion validation, schema enforcement, and quarantine engine for batch data pipelines. It intercepts incoming tabular feeds (CSV, Parquet), validates rows against configurable business rules, loads valid records idempotently into PostgreSQL, and preserves malformed rows in a queryable JSONB quarantine table alongside structured reason codes.

Instead of hardcoding column names in application code, PulseCheck decouples source schemas from validation logic using semantic column roles. The same validation engine runs across freight logistics data and public mobility feeds (such as NYC TLC Taxi records) purely through configuration edits.

---

## Architecture Overview

```
Incoming Feed (CSV / Parquet)
       │
       ▼
┌─────────────────────────────────┐
│     Batch Schema Validator      │ ──[ Missing Required Columns ]──> Abort (rejected_schema)
└─────────────────────────────────┘
       │ Schema Valid
       ▼
┌─────────────────────────────────┐
│   Row Validation & Key Engine   │
│  • Vectorized business rules    │
│  • Deterministic composite keys │
│  • Multi-reason accumulation    │
└─────────────────────────────────┘
       │
       ├─── Valid Rows ──────> PostgreSQL Target Table (Idempotent ON CONFLICT)
       │
       └─── Quarantined Rows ─> PostgreSQL Quarantine Table (JSONB + GIN index)
       │
       ▼
┌─────────────────────────────────┐
│     Batch Verification Checks   │
│  • Weekday volume median        │ ──[ Out of Bounds / Missing Scope ]──> Alerts Table / Webhook
│  • Freshness lag & clock skew   │                                        (Deduplicated with Cooldown)
└─────────────────────────────────┘
```

Detailed design documents, class diagrams, and sequence diagrams:
- [docs/lld.md](docs/lld.md) - Low-level class diagrams, ERD, and execution sequences
- [docs/architecture.md](docs/architecture.md) - System architecture and component interactions
- [docs/decisions.md](docs/decisions.md) - 24 Architecture Decision Records (ADRs)
- [docs/runbook.md](docs/runbook.md) - Step-by-step operational and deployment procedures
- [docs/config-reference.md](docs/config-reference.md) - Complete YAML configuration schema reference

---

## Core Capabilities

- **Zero-Code Column Role Mapping**: Maps physical source headers to semantic roles (`id`, `pickup_time`, `delivery_time`, `scope`, `distance`, `lat`, `lon`, `phone`, `plate`). Rules declare required roles; rules with missing roles are skipped automatically with logged explanations.
- **Deterministic Composite Keys**: If a dataset lacks a single natural primary key, PulseCheck constructs an order-independent surrogate key using lexicographically sorted role values and SHA-256 hashing.
- **Idempotent Ingestion**: PostgreSQL upserts using parameterized `ON CONFLICT` clauses with configurable conflict policies (`skip`, `update`, `quarantine`). Repeated file deliveries are tracked in `batch_registry` via file content hashes.
- **JSONB Quarantine Subsystem**: Failed rows are saved with their full original row payload, 1-indexed row number, and a list of diagnostic failure objects. Indexed via PostgreSQL `GIN` on `reasons` for sub-second containment queries (`@>`).
- **Closed-Loop Quarantine Replay**: The `pulsecheck replay` command re-evaluates open quarantine records against updated configurations, loads newly valid records into the target table, and resolves quarantine entries without touching production code.
- **Statistical Batch Anomaly Detection**:
  - **Volume Check**: Historical lookback comparing current batch count against the median of the last $N$ batches matching the same day of the week.
  - **Missing Scopes**: Tracks active partitions (cities or taxi zones) over recent history and flags disappearing scopes with volume 0.
  - **Freshness Check**: Measures max event timestamp against an injectable UTC reference clock.
- **Alert Deduplication & Auto-Closure**: Fingerprinted alerts (`check_name:scope:severity`) prevent alert fatigue via configurable cooldown windows and auto-close when a scope passes consecutive healthy runs.

---

## Quickstart

### Prerequisites
- Python 3.11+
- PostgreSQL 15+ (optional for dry-run/in-memory evaluation; local Docker Compose file provided)

### 1. Setup Virtual Environment
```bash
python -m venv .venv
# On Windows:
.\.venv\Scripts\Activate.ps1
# On Linux/macOS:
source .venv/bin/activate

pip install -e .
```

### 2. Validate Configuration
```bash
pulsecheck validate-config
```

### 3. Initialize Database Tables
```bash
# Optional: start local Postgres via Docker
docker compose up -d

# Create core tracking tables, target table, and GIN indexes
pulsecheck init-db
```

### 4. Process an Ingestion File
```bash
# Live run with database writes
pulsecheck run data/daily_trips.csv --source partner_feed --logical-date 2024-01-01

# Dry-run mode (validates schema, rows, and batch checks in-memory without database writes)
pulsecheck run data/daily_trips.csv --source partner_feed --dry-run
```

---

## Operational CLI Commands

| Command | Purpose |
|---|---|
| `pulsecheck validate-config` | Strict schema validation of YAML configs and environment variables |
| `pulsecheck init-db` | Idempotent PostgreSQL DDL generation and index creation |
| `pulsecheck run <file> --source <s>` | Full ingestion pipeline: schema check, row validation, loading, quarantine, metrics |
| `pulsecheck check-batch <batch_id>` | Evaluate volume, freshness, and scope checks on an existing batch |
| `pulsecheck replay --status open` | Re-validate open quarantine rows with current config and load valid rows |
| `pulsecheck diagnose` | Aggregate quarantine reason frequencies and flag supplier/temporal concentration |
| `pulsecheck export-rejects` | Export quarantined records to CSV with human-readable diagnostic reasons |
| `pulsecheck benchmark --seed 42` | Run end-to-end synthetic baseline, fault injection, precision/recall scorecard |

---

## Real-World Profile: NYC TLC Taxi Data (2.96 Million Rows)

To prove portability without modifying application code, PulseCheck was evaluated on one month of real NYC TLC Yellow Taxi parquet trip records (`yellow_tripdata_2024-01.parquet`):

- **Data Volume**: 2,964,624 raw records partitioned into 31 daily batches.
- **Configuration**: Dedicated config bundle in `config/tlc/` mapping 19 parquet columns to semantic roles, zone ID (`PULocationID`) to scope, and establishing a composite key `(VendorID, tpep_pickup_datetime, PULocationID, DOLocationID, fare_amount)`.
- **Empirical Calibration**: Limits calibrated directly from empirical percentiles (duration cap at 4 hours / 14,400s; speed cap at 80 mph).
- **Execution Results**:
  - Received: 2,964,606 in-month records
  - Valid & Loaded: 2,961,265 rows
  - Quarantined: 3,341 rows (**0.1127% quarantine rate**)
  - Isolated Out-of-Month Anomalies: 18 records (hardware clock resets to 2002/2009 and month-boundary trips)
  - Top Detected Quirks: Taximeters left running for 20+ hours, odometer sensor spikes (>1,000 mph implied speed), and negative trip durations.

Full report: [reports/tlc_run.md](reports/tlc_run.md)

---

## Project Structure

```
PulseCheck/
├── config/                  # Default logistics configuration
│   ├── dataset.yaml         # Column-to-role mappings & conflict policies
│   ├── rules.yaml           # Active row validation rules & thresholds
│   ├── checks.yaml          # Batch anomaly checks, alert cooldowns & bench settings
│   ├── cities.yaml          # Reference bounding boxes for geo rules
│   └── tlc/                 # Production NYC TLC taxi configuration profile
├── docs/                    # Technical documentation
│   ├── architecture.md      # System architecture & component flows
│   ├── lld.md               # Low-level class diagrams, ERD, and sequence diagrams
│   ├── decisions.md         # 24 Architecture Decision Records (ADRs)
│   ├── runbook.md           # Operational and maintenance runbook
│   └── config-reference.md  # Generated YAML schema reference
├── reports/                 # Execution summaries & audit reports
│   ├── benchmark.json       # Machine-readable benchmark run
│   ├── benchmark.md         # Benchmark scorecard & author statement
│   └── tlc_run.md           # NYC TLC 31-day batch run report
├── scripts/                 # Utility scripts (batching, docs generator, geocoder)
├── src/pulsecheck/          # Core Python application package
│   ├── adapters/            # External library wrappers (indpy, pandera)
│   ├── benchmark/           # Automated benchmark evaluation harness
│   ├── checks/              # Batch checks (volume, freshness, scopes, alerts)
│   ├── config/              # Pydantic v2 strict schema models & loaders
│   ├── db/                  # Connection handling & DDL generation
│   ├── faults/              # Fault injection plugins
│   ├── generator/           # Synthetic trip data generator
│   ├── readers/             # CSV & Parquet chunked file readers
│   ├── rules/               # Pluggable row validation rule registry
│   ├── clock.py             # Injectable clock abstraction
│   ├── loader.py            # Idempotent PostgreSQL loader
│   ├── pipeline.py          # End-to-end ingestion pipeline runner
│   ├── quarantine.py        # PostgreSQL JSONB quarantine writer
│   ├── replay.py            # Quarantine replayer & remediation
│   ├── schema_check.py      # Batch-level schema verifier
│   └── validator.py         # Row validation & composite key engine
└── tests/                   # Unit and integration test suites
    ├── unit/                # 63 standalone unit tests (no external services needed)
    └── integration/         # PostgreSQL container integration tests
```

---

## License

This project is licensed under the Apache License, Version 2.0.
