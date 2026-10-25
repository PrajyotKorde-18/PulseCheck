# PulseCheck

An ingestion guardrail that keeps working when dirty data doesn't.

I built this after asking a simple question: what actually happens to a data pipeline when an upstream partner sends a batch with negative durations, duplicate timestamps, or impossible speeds? With most setups, the answer is "either the whole job crashes at 3 AM, or bad rows silently pollute your analytics." I wanted an engine that catches bad data on the spot, isolates it with queryable reason codes in PostgreSQL, and lets clean records flow through without stopping.

Real-world run: [NYC TLC Audit (2.96M rows)](reports/tlc_run.md) · Architecture & LLD: [docs/lld.md](docs/lld.md)

## The problem

When millions of records hit an ingestion pipeline daily, even a 0.1% error rate means thousands of bad rows corrupting analytical tables. Add an upstream schema mismatch or a partner dropping an entire city feed, and downstream dashboards quietly report garbage.

Most pipelines either fail the whole file or silently swallow bad records. PulseCheck isolates bad rows into a queryable quarantine table, lets valid records load idempotently, and detects batch-level anomalies like missing scopes or volume drops before downstream jobs run.

## How it works

Every file goes through two layers of checks:

| Layer | What it is | Why it's there |
|---|---|---|
| L1: Row validation | Vectorised business rules in memory (chronology, duration caps, speed, PII) | Catches individual bad rows, tagging each with specific reason codes |
| L2: Batch checks | Volume, freshness, and scope completeness against stored history | Catches problems no single row shows, like a partner dropping 80% volume or missing a city |

The interesting part is what happens when things break:

- **A row has invalid data.** The engine catches it, writes the raw record into a JSONB quarantine table with its failure reasons, and loads the valid rows without failing the batch.
- **Upstream fixes their data or you update a threshold.** Run `pulsecheck replay`—the engine re-evaluates open quarantined rows with your current config and loads newly passing rows into Postgres.
- **The same file arrives twice.** File content hashes in `batch_registry` detect it, and idempotent upserts (`ON CONFLICT`) leave the database completely clean without duplicate rows.
- **A partner drops a city or sends stale data.** Batch checks detect the drop against weekday historical medians and raise deduplicated alerts with cooldowns so nobody gets spammed.

## Numbers

I ran it on 1 month of real NYC TLC Yellow Taxi records (2.96 million rows across 31 daily batches):

- **2,961,265 rows cleanly loaded** (99.89% pass rate)
- **3,341 rows quarantined** (0.1127% quarantine rate)
- **18 out-of-month records isolated** (hardware sensor resets to 2002/2009)
- **8.3s average execution time** per 81,000-row batch in streaming chunks
- **100% precision and recall** on synthetic fault-injection benchmarks
- **0 duplicate rows** on repeated batch runs (idempotent upserts)

## Use it in your project

You don't write validation logic. You map physical column names to semantic roles in YAML:

### 1. Map columns in `config/dataset.yaml`

```yaml
dataset:
  name: "partner_trips"
  format: "parquet"
  table_name: "trips"
  identity_roles:
    - "id"
    - "pickup_time"
    - "scope"
  columns:
    - name: "vendor_id"
      role: "id"
      sql_type: "INTEGER"
      nullable: false
    - name: "tpep_pickup_datetime"
      role: "pickup_time"
      sql_type: "TIMESTAMPTZ"
      nullable: false
    - name: "PULocationID"
      role: "scope"
      sql_type: "INTEGER"
      nullable: false
```

### 2. Configure rules in `config/rules.yaml`

```yaml
rules:
  - rule_id: "RULE_CHRONOLOGY"
    reason_code: "ERR_INVALID_CHRONOLOGY"
    enabled: true

  - rule_id: "RULE_MAX_DURATION"
    reason_code: "ERR_MAX_DURATION_EXCEEDED"
    enabled: true
    parameters:
      max_duration_seconds: 14400 # 4 hours

  - rule_id: "RULE_MAX_SPEED"
    reason_code: "ERR_SPEED_EXCEEDED"
    enabled: true
    parameters:
      max_speed_kmh: 80.0
```

### 3. Run validation and ingestion

```bash
# Validate your config files first
pulsecheck validate-config

# Ingest a daily file
pulsecheck run data/daily_feed.parquet --source partner_feed --logical-date 2024-01-01

# Dry run (no database writes)
pulsecheck run data/daily_feed.parquet --source partner_feed --dry-run
```

## Run the demo locally

You'll need Python 3.11+, Git, and Docker.

```bash
# 1. Clone repository and install dependencies
git clone https://github.com/PrajyotKorde-18/PulseCheck.git
cd PulseCheck
python -m venv .venv
# On Windows: .\.venv\Scripts\Activate.ps1
# On Linux/macOS: source .venv/bin/activate
pip install -e .

# 2. Start PostgreSQL via Docker Compose
docker compose up -d

# 3. Create tables, indexes, and GIN indexes
pulsecheck init-db

# 4. Run the synthetic benchmark (baseline, fault injection, precision/recall scorecard)
pulsecheck benchmark --seed 42
```

To see quarantine and replay yourself:
1. Run a batch with dirty data: valid rows load into `trips`, while invalid rows land in `quarantine`.
2. Inspect failure reasons: run `pulsecheck diagnose` to see error distributions and partner concentration.
3. Loosen a threshold in `config/rules.yaml` and run `pulsecheck replay --status open`. Watch previously quarantined rows re-validate, load into PostgreSQL, and mark themselves `replayed`.

## Commands

| Command | Purpose |
|---|---|
| `pulsecheck validate-config` | Strict schema validation of YAML configs and environment variables |
| `pulsecheck init-db` | Idempotent PostgreSQL DDL generation and GIN index creation |
| `pulsecheck run <file> --source <s>` | Schema check, row validation, load, quarantine, metrics |
| `pulsecheck check-batch <batch_id>` | Volume, freshness, and scope checks on an existing batch |
| `pulsecheck replay --status open` | Re-validate quarantined rows with current config |
| `pulsecheck diagnose` | Reason frequencies and supplier/day concentration |
| `pulsecheck export-rejects` | Export quarantined rows to CSV with readable reasons |
| `pulsecheck benchmark --seed 42` | Synthetic baseline, fault injection, precision/recall scorecard |

## What's inside

- **Language and libraries:** Python 3.11+, pandas, Pydantic v2 (strict config), Pandera and indpy behind adapters
- **Storage:** PostgreSQL (upserts, JSONB quarantine, GIN index on reasons)
- **Formats:** CSV and Parquet chunked readers
- **Testing:** 63 unit tests (no external services) plus integration tests against a PostgreSQL container
- **Delivery:** Docker Compose for local PostgreSQL, Apache 2.0 license
- **Docs:** [Architecture](docs/architecture.md), [Low-Level Design (LLD)](docs/lld.md), [24 Decision Records](docs/decisions.md), [Runbook](docs/runbook.md), [Config Reference](docs/config-reference.md)

## What I learned

- Failure handling is the real work. Checking a single row took a day; building deterministic replay, idempotent upserts, and batch anomaly detection took the rest.
- Decoupling rules from physical column names (roles) is essential. It allowed the exact same engine to process synthetic logistics data and 2.96 million NYC taxi records without touching a single line of Python.
- Real-world data is far messier than synthetic tests: hardware sensor resets to 2002 and odometer spikes appeared in a single month of TLC records.

## Roadmap

- [ ] GitHub Actions CI workflow
- [ ] Support for cloud object stores (S3, GCS) in streaming readers
- [ ] Airflow custom operator for DAG orchestration
- [ ] Streamlit quarantine inspection dashboard

## Author

Prajyot Korde, IT undergrad at Ramdeobaba University · [LinkedIn](https://www.linkedin.com/in/prajyot-korde-912621281/) · [GitHub](https://github.com/PrajyotKorde-18)

## License

Apache License 2.0
