# Configuration Reference

> **Auto-generated Reference**: This document is generated directly from PulseCheck's Pydantic models via `python scripts/gen_config_docs.py`.

## 1. Environment Variables & Runtime Settings (`.env` / OS Environment)

### PulseCheckSettings

Environment variables read by PulseCheck. Documented in `.env.example`.

| Field | Type | Default | Description |
|---|---|---|---|
| `pulsecheck_config` | `Optional[str]` | `null` | Path to custom dataset.yaml configuration file |
| `postgres_host` | `<class 'str'>` | `localhost` | PostgreSQL server hostname |
| `postgres_port` | `<class 'int'>` | `5432` | PostgreSQL server port |
| `postgres_db` | `<class 'str'>` | `pulsecheck_db` | PostgreSQL database name |
| `postgres_user` | `<class 'str'>` | `pulsecheck` | PostgreSQL username |
| `postgres_password` | `<class 'str'>` | `pulsecheck_secret` | PostgreSQL password |
| `alert_webhook_url` | `Optional[str]` | `null` | Webhook endpoint URL for alert notifications |
| `pulsecheck_log_level` | `<class 'str'>` | `INFO` | Log level for PulseCheck |

## 2. Dataset Specification (`config/dataset.yaml`)

### DatasetConfig (`dataset:`)

Top-level configuration for source schema, roles, and load behavior.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `<class 'str'>` | `PydanticUndefined` | Unique logical dataset name |
| `format` | `Literal['csv', 'parquet']` | `PydanticUndefined` | File format of incoming data files |
| `csv_options` | `Optional[CSVOptions]` | `null` | Parser options if format is CSV |
| `table_name` | `<class 'str'>` | `PydanticUndefined` | Target PostgreSQL table name |
| `identity_roles` | `List[str]` | `PydanticUndefined` | Role or list of roles that together uniquely identify a record |
| `composite_key_hash_algorithm` | `Literal['sha256']` | `sha256` | Hashing algorithm to derive deterministic surrogate keys for composite roles |
| `unknown_columns_policy` | `Literal['ignore', 'warn', 'reject']` | `warn` | Behavior when incoming file contains unconfigured columns |
| `conflict_policy` | `Literal['skip', 'update', 'quarantine']` | `skip` | Action to take when a row conflicts on primary/composite key in PostgreSQL |
| `transaction_scope` | `Literal['batch', 'chunk']` | `batch` | Transaction scope for database loads: commit per chunk or across entire batch |
| `repeated_file_policy` | `Literal['skip', 'reprocess']` | `skip` | Action when an identical file hash has already been registered in database |
| `chunk_size` | `<class 'int'>` | `1000` | Batch chunk size for streaming file reads and database inserts |
| `columns` | `List[DatasetColumn]` | `PydanticUndefined` | List of column definitions |

### DatasetColumn (`columns[]`)

Individual column-role mapping and type declaration.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `<class 'str'>` | `PydanticUndefined` | Physical column name in the source file |
| `role` | `<class 'str'>` | `PydanticUndefined` | Semantic role (e.g., id, pickup_time, lat, lon, city) |
| `sql_type` | `<class 'str'>` | `PydanticUndefined` | Target PostgreSQL SQL data type (e.g., VARCHAR, TIMESTAMPTZ) |
| `nullable` | `<class 'bool'>` | `False` | Whether the column is permitted to contain NULL values |

### CSVOptions (`csv_options:`)

Parser settings for CSV data sources.

| Field | Type | Default | Description |
|---|---|---|---|
| `delimiter` | `<class 'str'>` | `,` | Field delimiter character |
| `has_header` | `<class 'bool'>` | `True` | Whether the CSV file includes a header row |

## 3. Validation Rules Specification (`config/rules.yaml`)

### RuleConfig (`rules[]`)

Individual row validation rule settings, required roles, and thresholds.

| Field | Type | Default | Description |
|---|---|---|---|
| `rule_id` | `<class 'str'>` | `PydanticUndefined` | Unique programmatic rule identifier |
| `reason_code` | `<class 'str'>` | `PydanticUndefined` | Short diagnostic code attached to quarantined records |
| `description` | `<class 'str'>` | `PydanticUndefined` | Human-readable explanation of what this rule verifies |
| `required_roles` | `List[str]` | `PydanticUndefined` | List of semantic roles required for this rule to evaluate |
| `severity` | `Literal['INFO', 'LOW', 'MEDIUM', 'HIGH', 'CRITICAL']` | `MEDIUM` | Severity level of this rule violation |
| `enabled` | `<class 'bool'>` | `True` | Whether this rule is active |
| `parameters` | `Dict[str, Any]` | `PydanticUndefined` | Rule-specific thresholds and parameters |

## 4. Batch-Level Checks Specification (`config/checks.yaml`)

### BatchChecksConfig (`batch_checks:`)

Parent container for volume, missing scopes, freshness, and alerts.

| Field | Type | Default | Description |
|---|---|---|---|
| `auto_check_batch` | `<class 'bool'>` | `True` | Automatically execute batch-level checks at the end of pipeline run |
| `volume` | `<class 'VolumeCheckConfig'>` | `PydanticUndefined` | No description provided. |
| `missing_scopes` | `<class 'MissingScopesCheckConfig'>` | `PydanticUndefined` | No description provided. |
| `freshness` | `<class 'FreshnessCheckConfig'>` | `PydanticUndefined` | No description provided. |
| `alerts` | `<class 'AlertsConfig'>` | `PydanticUndefined` | No description provided. |
| `diagnostics` | `<class 'DiagnosticsCheckConfig'>` | `PydanticUndefined` | No description provided. |

### VolumeCheckConfig (`batch_checks.volume:`)

Volume anomaly check parameters compared against weekday medians.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `<class 'bool'>` | `True` | Enable volume anomaly check |
| `history_window_batches` | `<class 'int'>` | `14` | Lookback window in batches |
| `minimum_history_batches` | `<class 'int'>` | `3` | Minimum historical batches needed for median |
| `min_volume_ratio` | `<class 'float'>` | `0.5` | Minimum acceptable ratio of current to median volume |
| `max_volume_ratio` | `<class 'float'>` | `2.0` | Maximum acceptable ratio of current to median volume |
| `fallback_absolute_min_rows` | `<class 'int'>` | `10` | Fallback floor when insufficient history |
| `fallback_absolute_max_rows` | `<class 'int'>` | `1000000` | Fallback ceiling when insufficient history |

### MissingScopesCheckConfig (`batch_checks.missing_scopes:`)

Missing scope (e.g. absent city) detection parameters.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `<class 'bool'>` | `True` | Enable missing scopes check |
| `lookback_batches` | `<class 'int'>` | `7` | Number of recent batches to inspect for active scopes |
| `severity` | `Literal['INFO', 'WARNING', 'HIGH', 'CRITICAL']` | `CRITICAL` | Severity raised when a recently seen scope is absent |

### FreshnessCheckConfig (`batch_checks.freshness:`)

Data lag and reference clock freshness parameters.

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `<class 'bool'>` | `True` | Enable event freshness check |
| `event_time_role` | `<class 'str'>` | `pickup_time` | Role representing event time |
| `max_lag_seconds` | `<class 'int'>` | `86400` | Maximum allowable lag between event time and reference clock |
| `severity` | `Literal['INFO', 'WARNING', 'HIGH', 'CRITICAL']` | `WARNING` | Severity raised when freshness threshold is exceeded |

### AlertsConfig (`batch_checks.alerts:`)

Alert suppression windows and dispatch channels.

| Field | Type | Default | Description |
|---|---|---|---|
| `cooldown_seconds` | `<class 'int'>` | `3600` | Cooldown suppression window for identical alert fingerprints |
| `auto_close_healthy_batches` | `<class 'int'>` | `3` | Number of consecutive healthy batches after which an active alert auto-closes |
| `destinations` | `<class 'AlertDestinationsConfig'>` | `PydanticUndefined` | No description provided. |

### AlertDestinationsConfig (`batch_checks.alerts.destinations:`)

Delivery channel switches (log, table, webhook).

| Field | Type | Default | Description |
|---|---|---|---|
| `log` | `<class 'bool'>` | `True` | Write alerts to standard logger |
| `table` | `<class 'bool'>` | `True` | Persist alerts into the PostgreSQL alerts table |
| `webhook` | `<class 'bool'>` | `False` | Send alert payload to HTTP webhook (requires ALERT_WEBHOOK_URL) |

### DiagnosticsCheckConfig (`batch_checks.diagnostics:`)

Thresholds for quarantine root cause concentration detection (source and day share limits).

| Field | Type | Default | Description |
|---|---|---|---|
| `enabled` | `<class 'bool'>` | `True` | Enable concentration diagnostics |
| `max_source_share` | `<class 'float'>` | `0.8` | Concentration threshold for single source share of a reason |
| `max_day_share` | `<class 'float'>` | `0.8` | Concentration threshold for single logical date share of a reason |

## 5. Scope Geometries (`config/cities.yaml`)

### CityBoundingBox (`cities[]`)

Geographic boundaries for city scopes.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `<class 'str'>` | `PydanticUndefined` | Unique name of the city or scope |
| `min_lat` | `<class 'float'>` | `PydanticUndefined` | Minimum latitude boundary |
| `max_lat` | `<class 'float'>` | `PydanticUndefined` | Maximum latitude boundary |
| `min_lon` | `<class 'float'>` | `PydanticUndefined` | Minimum longitude boundary |
| `max_lon` | `<class 'float'>` | `PydanticUndefined` | Maximum longitude boundary |

## 6. Fault Injections (`config/faults.yaml`)

### FaultConfig (`faults[]`)

Synthetic defect and anomaly injection specifications.

| Field | Type | Default | Description |
|---|---|---|---|
| `name` | `<class 'str'>` | `PydanticUndefined` | Unique fault identifier |
| `description` | `<class 'str'>` | `PydanticUndefined` | Explanation of injected defect |
| `target_roles` | `List[str]` | `PydanticUndefined` | Column roles targeted by this fault |
| `scope` | `Literal['rows', 'batch']` | `PydanticUndefined` | Scope of the fault injection |
| `rate` | `Optional[float]` | `null` | Fraction of rows to corrupt |
| `count` | `Optional[int]` | `null` | Absolute count of rows or occurrences |
| `parameters` | `Dict[str, Any]` | `PydanticUndefined` | Custom fault parameters |
| `expected_detector` | `<class 'str'>` | `PydanticUndefined` | Rule code or batch check expected to catch this fault |
| `enabled` | `<class 'bool'>` | `True` | Whether this fault is currently active |

## 7. Synthetic Benchmark Specification (`config/checks.yaml` / `bench:`)

### BenchConfig (`bench:`)

Configuration parameters for the synthetic benchmark runner and evaluation metrics.

| Field | Type | Default | Description |
|---|---|---|---|
| `history_days` | `<class 'int'>` | `3` | Number of clean baseline history days to generate and load |
| `trips_per_city_day` | `<class 'int'>` | `50` | Base trips per city per day for synthetic generation |
| `seed` | `<class 'int'>` | `42` | Deterministic random seed for generation and injection |
| `source` | `<class 'str'>` | `logistics_feed` | Data source name for benchmark runs |
| `output_dir` | `<class 'str'>` | `data/benchmark` | Directory to store generated benchmark batches |
| `reports_json` | `<class 'str'>` | `reports/benchmark.json` | Target path for benchmark JSON report |
| `reports_md` | `<class 'str'>` | `reports/benchmark.md` | Target path for benchmark Markdown report |
| `replay_recovery` | `<class 'ReplayRecoveryConfig'>` | `PydanticUndefined` | Settings for replay recovery assessment |

### ReplayRecoveryConfig (`bench.replay_recovery:` / `faults.yaml:replay_recovery:`)

Configuration for validating quarantine recovery via rule or data remediation.

| Field | Type | Default | Description |
|---|---|---|---|
| `fault_name` | `<class 'str'>` | `swapped_times` | Target fault to remediate via quarantine replay |
| `fix_type` | `Literal['config', 'corrected_file']` | `config` | Remediation strategy: 'config' (rule modification) or 'corrected_file' |
| `loosened_rule_id` | `<class 'str'>` | `RULE_CHRONOLOGY` | Rule ID to loosen or disable during config-based replay |
