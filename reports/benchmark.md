# PulseCheck Benchmark Report

> **Methodology Notice**: Notice: Faults and detectors were both designed and implemented by the project author. Consequently, benchmark metrics demonstrate that the validation and batch check engines perform as designed under controlled synthetic defect distributions, rather than measuring generalized accuracy across arbitrary real-world logistics datasets.

## 1. Execution Metadata

- **Run ID**: `bench_20261025_095521_42`
- **Seed**: `42`
- **Config SHA-256**: `deea1eb53fac585ffd54d344214d12bfb8d1475e02ec398d1e84dcd4c011087e`
- **Git Commit**: `workspace_uncommitted`
- **Timestamp**: `2026-10-25T09:55:22.310214+00:00`
- **Runtime**: `0.419s`

### Package Versions

| Package | Version |
|---|---|
| `faker` | `40.43.0` |
| `pandas` | `2.3.3` |
| `psycopg` | `3.3.6` |
| `pyarrow` | `23.0.1` |
| `pydantic` | `2.11.7` |
| `python` | `3.13.3` |

## 2. Overall Pipeline & Data Metrics

| Metric | Value |
|---|---|
| History Rows Generated | 405 |
| Final Day Clean Rows | 150 |
| Final Day Faulty Rows | 150 |
| Rows Successfully Loaded | 104 |
| Rows Quarantined | 46 |
| Clean Rows Wrongly Quarantined | 1 |
| Alerts on Clean Scopes | 0 |
| Rows Recovered by Quarantine Replay | 7 |

## 3. Per-Fault Detection Scorecard

| Fault Name | Scope | Expected Detector | Injected | Caught | Missed | False Alarms | Precision | Recall |
|---|---|---|---|---|---|---|---|---|
| `null_id` | `rows` | `ERR_NULL_VALUE` | 7 | 0 | 7 | 1 | 0.0% | 0.0% |
| `swapped_times` | `rows` | `ERR_INVALID_CHRONOLOGY` | 7 | 7 | 0 | 7 | 50.0% | 100.0% |
| `swapped_lat_lon` | `rows` | `ERR_SWAPPED_COORDINATES` | 7 | 6 | 1 | 0 | 100.0% | 85.7% |
| `out_of_box_coordinates` | `rows` | `ERR_OUT_OF_BOUNDS` | 7 | 6 | 1 | 7 | 46.2% | 85.7% |
| `bad_mobile` | `rows` | `ERR_INVALID_PHONE` | 7 | 6 | 1 | 0 | 100.0% | 85.7% |
| `bad_pin` | `rows` | `ERR_INVALID_PINCODE` | 7 | 7 | 0 | 0 | 100.0% | 100.0% |
| `bad_plate` | `rows` | `ERR_INVALID_PLATE` | 7 | 7 | 0 | 0 | 100.0% | 100.0% |
| `duplicate_ids` | `rows` | `ERR_DUPLICATE_KEY` | 1 | 0 | 1 | 2 | 0.0% | 0.0% |
| `future_timestamps` | `rows` | `ERR_FUTURE_TIMESTAMP` | 7 | 7 | 0 | 0 | 100.0% | 100.0% |

## 4. Missed Fault Analysis

- **`null_id`** (7 missed): 7 row(s) quarantined with ERR_NULL_VALUE, but bear trip_id=null, preventing key correlation to pre-mutation IDs.
- **`swapped_lat_lon`** (1 missed): 1 row(s) masked by compound overlapping fault mutations on the same row index.
- **`out_of_box_coordinates`** (1 missed): 1 row(s) masked by compound overlapping fault mutations on the same row index.
- **`bad_mobile`** (1 missed): 1 row(s) masked by compound overlapping fault mutations on the same row index.
- **`duplicate_ids`** (1 missed): 1 duplicate row quarantined under the copied source ID rather than its original pre-mutation ID.
