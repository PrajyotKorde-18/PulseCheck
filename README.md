# PulseCheck

**A data validation engine that keeps bad rows out of your database without stopping the pipeline.**

I built this after asking a simple question: what actually happens when a messy daily file lands in a pipeline? With most setups, the answer is "the bad rows get loaded silently, or the whole batch fails." I wanted something that checks every row, loads the good ones, keeps the bad ones with a reason, and can retry them later.

Real-data proof: [NYC TLC run report](reports/tlc_run.md) · Design docs: [docs/](docs/)

## The problem

Daily data files arrive with broken rows: dropoff before pickup, impossible speeds, duplicate ids, missing columns. Row checks alone also miss batch-level problems, like a city that sent no data today or a file that is a day old.

## How it works

Every file goes through two layers of checks:

| Layer | What it is | Why it's there |
|---|---|---|
| Row validation | Vectorised business rules, one reason code per rule | Catches individual bad rows, and a row can fail several rules at once |
| Batch checks | Volume, freshness and missing-scope checks against stored history | Catches problems no single row shows |

The interesting part is what happens when things break:

- **A row is bad.** It is kept in a PostgreSQL quarantine table with its full original values and a list of reasons. The valid rows still load.
- **A required column is missing.** The whole batch is rejected as `rejected_schema` and nothing is loaded.
- **The same file arrives twice.** File content hashes in `batch_registry` recognise it, and upserts (`ON CONFLICT`) leave the table identical.
- **Rules change.** `pulsecheck replay` re-checks open quarantine rows with the current config and loads the ones that now pass.
- **A city or zone vanishes, or data goes stale.** Batch checks raise an alert. Fingerprinted alerts with a cooldown prevent alert fatigue and auto-close after healthy runs.

## Real data

To check it isn't tied to my own synthetic data, I ran it on one month of real NYC TLC Yellow Taxi records (`yellow_tripdata_2024-01.parquet`) using configuration only:

- 2,964,624 raw records split into 31 daily batches
- 2,964,606 in-month records received, 2,961,265 loaded, 3,341 quarantined (0.1127%)
- 18 out-of-month records isolated (clock resets to 2002/2009 and month-boundary trips)
- Top findings: taximeters left running 20+ hours, implied speeds over 1,000 mph, negative trip durations
- Limits (4-hour duration cap, 80 mph speed cap) were calibrated from the data's own percentiles

Real data has no ground truth, so I report what was flagged, not recall. Full details are in [reports/tlc_run.md](reports/tlc_run.md).

## Benchmark

On synthetic data, PulseCheck injects known faults and measures detection per fault type.

```
pulsecheck benchmark --seed 42
```

Results: see [reports/benchmark.md](reports/benchmark.md). 

## Use it in your project

You don't write validation code. You map your columns to roles in a config file:

```yaml
# config/dataset.yaml (shape only, see docs/config-reference.md for the real schema)
# map your physical column names to semantic roles:
# id, pickup_time, delivery_time, scope, distance, lat, lon, phone, plate
```

Rules declare the roles they need. If your data lacks a role, that rule is skipped and the reason is logged. If your data has no single key, PulseCheck builds a deterministic composite key from sorted role values with SHA-256.

### 1. Set up

```bash
python -m venv .venv
# Windows: .\.venv\Scripts\Activate.ps1
# Linux/macOS: source .venv/bin/activate
pip install -e .
```

### 2. Validate the config

```bash
pulsecheck validate-config
```

### 3. Start Postgres and create tables

```bash
docker compose up -d
pulsecheck init-db
```

### 4. Process a file

```bash
# Live run
pulsecheck run data/daily_trips.csv --source partner_feed --logical-date 2024-01-01

# Dry run (no database writes)
pulsecheck run data/daily_trips.csv --source partner_feed --dry-run
```

## Commands

| Command | Purpose |
|---|---|
| `pulsecheck validate-config` | Strict validation of YAML configs and environment variables |
| `pulsecheck init-db` | Idempotent table and index creation |
| `pulsecheck run <file> --source <s>` | Schema check, row validation, load, quarantine, metrics |
| `pulsecheck check-batch <batch_id>` | Volume, freshness and scope checks on an existing batch |
| `pulsecheck replay --status open` | Re-validate quarantined rows with current config |
| `pulsecheck diagnose` | Reason frequencies and supplier/day concentration |
| `pulsecheck export-rejects` | Export quarantined rows to CSV with readable reasons |
| `pulsecheck benchmark --seed 42` | Synthetic baseline, fault injection, precision/recall scorecard |

## What's inside

- **Language and libraries:** Python 3.11+, pandas, Pydantic v2 (strict config), Pandera and indpy behind adapters
- **Storage:** PostgreSQL (upserts, JSONB quarantine, GIN index on reasons)
- **Formats:** CSV and Parquet chunked readers
- **Testing:** 63 unit tests (no external services) plus integration tests against a PostgreSQL container
- **Delivery:** Docker Compose for local Postgres, Apache 2.0 licence
- **Docs:** [architecture](docs/architecture.md), [low-level design](docs/lld.md), [24 decision records](docs/decisions.md), [runbook](docs/runbook.md), [config reference](docs/config-reference.md)

## What I learned

- Handling bad data is the real work. Checking one row is easy; replay, idempotency and batch-level checks took most of the effort.
- Decoupling rules from column names (roles) is what let the same engine run on logistics data and NYC taxi data with config edits only.
- Real data is messier than synthetic data: clock resets to 2002 and odometer spikes appeared in a single month of TLC records.

## Roadmap

- [ ] GitHub Actions CI
- [ ] Run a second real dataset through config only
- [ ] Optional Airflow DAG and Streamlit dashboard

## Author

Prajyot Korde, IT undergrad at Ramdeobaba University · [LinkedIn](https://www.linkedin.com/in/prajyot-korde-912621281/) · [GitHub](https://github.com/PrajyotKorde-18)

## License

Apache License 2.0
