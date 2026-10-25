# PulseCheck Low-Level Design (LLD) & Class Diagrams

This document details the low-level design, class hierarchies, entity relationships, and execution sequences for PulseCheck.

---

## 1. Subsystem Class Diagrams

### A. Configuration & Schema Models (`src/pulsecheck/config/`)

```mermaid
classDiagram
    class StrictBaseModel {
        +ConfigDict model_config
    }
    class DatasetColumn {
        +str name
        +str role
        +str sql_type
        +bool nullable
    }
    class CSVOptions {
        +str delimiter
        +bool has_header
    }
    class DatasetConfig {
        +str name
        +str format
        +CSVOptions csv_options
        +str table_name
        +List~str~ identity_roles
        +str composite_key_hash_algorithm
        +str unknown_columns_policy
        +str conflict_policy
        +str transaction_scope
        +str repeated_file_policy
        +int chunk_size
        +List~DatasetColumn~ columns
        +get_role_to_columns_map() Dict
        +get_column_to_role_map() Dict
        +get_primary_column_for_role(role) str
        +get_required_columns() List~str~
    }
    class RuleConfig {
        +str rule_id
        +str reason_code
        +str description
        +List~str~ required_roles
        +str severity
        +bool enabled
        +Dict parameters
    }
    class VolumeCheckConfig {
        +bool enabled
        +int history_window_batches
        +int minimum_history_batches
        +float min_volume_ratio
        +float max_volume_ratio
        +int fallback_absolute_min_rows
        +int fallback_absolute_max_rows
    }
    class FreshnessCheckConfig {
        +bool enabled
        +str event_time_role
        +int max_lag_seconds
        +str severity
    }
    class AlertsCheckConfig {
        +int cooldown_seconds
        +int auto_close_healthy_batches
        +Dict~str, bool~ destinations
    }
    class ConfigBundle {
        +DatasetConfig dataset_config
        +List~RuleConfig~ rules
        +BatchChecksConfig checks
        +List~CityBoundingBox~ cities
        +get_city_bounding_box(city_name) tuple
    }

    StrictBaseModel <|-- DatasetColumn
    StrictBaseModel <|-- CSVOptions
    StrictBaseModel <|-- DatasetConfig
    StrictBaseModel <|-- RuleConfig
    StrictBaseModel <|-- VolumeCheckConfig
    StrictBaseModel <|-- FreshnessCheckConfig
    StrictBaseModel <|-- AlertsCheckConfig
    DatasetConfig *-- DatasetColumn
    DatasetConfig *-- CSVOptions
    ConfigBundle o-- DatasetConfig
    ConfigBundle o-- RuleConfig
```

---

### B. Ingestion Readers & Chunking (`src/pulsecheck/readers/`)

```mermaid
classDiagram
    class FileReader {
        <<interface>>
        +read_chunks(path, chunk_size) Iterator~DataFrame~*
        +read_all(path) DataFrame*
    }
    class CSVReader {
        +str delimiter
        +bool has_header
        +read_chunks(path, chunk_size) Iterator~DataFrame~
        +read_all(path) DataFrame
    }
    class ParquetReader {
        +read_chunks(path, chunk_size) Iterator~DataFrame~
        +read_all(path) DataFrame
    }
    class ReaderFactory {
        +get_reader(dataset_config) FileReader
    }

    FileReader <|.. CSVReader
    FileReader <|.. ParquetReader
    ReaderFactory ..> FileReader : instantiates
```

---

### C. Validation Engine, Registry & Rule Protocol (`src/pulsecheck/rules/`, `src/pulsecheck/validator.py`)

```mermaid
classDiagram
    class RuleRegistry {
        -Dict~str, RegisteredRule~ _rules
        +register(rule_id, reason_code, required_roles) Callable
        +get_rule(rule_id) RegisteredRule
        +list_rules() List~RegisteredRule~
    }
    class RegisteredRule {
        +str rule_id
        +str reason_code
        +List~str~ required_roles
        +Callable func
        +str description
        +is_applicable(dataset_config) tuple~bool, str~
    }
    class Validator {
        +ConfigBundle config_bundle
        +Clock clock
        +DatasetConfig dataset_config
        +Dict~str, str~ role_to_col
        +validate(df) ValidationResult
    }
    class RowReason {
        +str code
        +str rule
        +List~str~ fields
        +str message
    }
    class ValidationResult {
        +DataFrame df
        +Series derived_keys
        +List~List~RowReason~~ row_reasons
        +List~str~ skipped_rules
        +Series is_valid_mask
        +get_summary() ValidationSummary
    }
    class ValidationSummary {
        +int total_rows
        +int valid_rows
        +int quarantined_rows
        +List~str~ skipped_rules
    }

    RuleRegistry *-- RegisteredRule
    Validator ..> RuleRegistry : queries rules
    Validator ..> ValidationResult : produces
    ValidationResult *-- RowReason
    ValidationResult ..> ValidationSummary : summarizes
```

---

### D. PostgreSQL Ingestion Loader & Quarantine Writer (`src/pulsecheck/`)

```mermaid
classDiagram
    class IdempotentLoader {
        -Connection conn
        -DatasetConfig dataset_config
        -QuarantineWriter quarantine_writer
        -str table_name
        -List~str~ identity_cols
        +load_batch(df, record_keys, batch_id, source) int
        -_load_chunk(df_chunk, keys_chunk, cur) int
    }
    class QuarantineWriter {
        -Connection conn
        +write_quarantine_rows(batch_id, source, start_row_num, raw_rows, record_keys, row_reasons, scope) int
        +resolve_quarantine_rows(loaded_keys) int
    }
    class PipelineRunner {
        +ConfigBundle config_bundle
        +Clock clock
        +PulseCheckSettings settings
        +run(file_path, source, logical_date, conn, dry_run) PipelineResult
        -_execute_with_conn(...) PipelineResult
        -_execute_dry_run(...) PipelineResult
    }

    PipelineRunner --> IdempotentLoader
    PipelineRunner --> QuarantineWriter
    IdempotentLoader --> QuarantineWriter : resolves valid rows
```

---

### E. Batch Checks, Anomaly Detectors & Alert Deduplication (`src/pulsecheck/checks/`)

```mermaid
classDiagram
    class BatchCheckEngine {
        +ConfigBundle config_bundle
        +Clock clock
        +VolumeChecker volume_checker
        +FreshnessChecker freshness_checker
        +MissingScopesChecker scopes_checker
        +AlertManager alert_manager
        +run_checks(...) BatchCheckReport
    }
    class VolumeChecker {
        +VolumeCheckConfig config
        +evaluate(scope, current_rows, history, current_weekday) CheckFinding
        +evaluate_all_scopes(...) List~CheckFinding~
    }
    class FreshnessChecker {
        +FreshnessCheckConfig config
        +Clock clock
        +evaluate(latest_event_time) CheckFinding
    }
    class MissingScopesChecker {
        +MissingScopesCheckConfig config
        +evaluate(current_scopes, recent_historical_scopes) List~CheckFinding~
    }
    class AlertManager {
        +AlertsCheckConfig config
        +PulseCheckSettings settings
        +Clock clock
        +compute_fingerprint(check_name, scope, severity) str
        +process_findings(findings, conn) tuple
    }
    class CheckFinding {
        +str check_name
        +str scope
        +str severity
        +str message
        +bool passed
        +Dict details
    }
    class BatchCheckReport {
        +str batch_id
        +str source
        +date logical_date
        +bool all_passed
        +List~CheckFinding~ findings
        +List active_alerts
        +List notified_alerts
        +List suppressed_alerts
        +List auto_closed_alerts
    }

    BatchCheckEngine *-- VolumeChecker
    BatchCheckEngine *-- FreshnessChecker
    BatchCheckEngine *-- MissingScopesChecker
    BatchCheckEngine *-- AlertManager
    BatchCheckEngine ..> BatchCheckReport : builds
    BatchCheckReport *-- CheckFinding
```

---

## 2. Entity-Relationship Diagram (PostgreSQL)

```mermaid
erDiagram
    batch_registry ||--o{ batch_metrics : "records metrics"
    batch_registry ||--o{ quarantine : "contains invalid records"
    batch_registry {
        VARCHAR(64) batch_id PK "SHA256(source|date|file_hash)"
        VARCHAR(64) source "Feed identifier"
        DATE logical_date "Business batch date"
        VARCHAR(64) file_hash "SHA256 of raw file"
        VARCHAR(32) status "'running' | 'completed' | 'failed' | 'rejected_schema'"
        TIMESTAMPTZ started_at "Run start timestamp"
        TIMESTAMPTZ completed_at "Run completion timestamp"
        TEXT error_message "Failure explanation if any"
    }

    batch_metrics {
        BIGSERIAL id PK
        VARCHAR(64) batch_id FK "References batch_registry"
        VARCHAR(64) source
        VARCHAR(64) scope "Partition (e.g., city, zone ID)"
        INT rows_received
        INT rows_loaded
        INT rows_quarantined
        INT rows_duplicate
        DOUBLE_PRECISION duration_seconds
        TIMESTAMPTZ created_at
    }

    quarantine {
        BIGSERIAL id PK
        VARCHAR(64) batch_id FK "References batch_registry"
        VARCHAR(64) source
        INT row_number "1-indexed row number in file"
        VARCHAR(64) record_key "Natural or SHA-256 composite key"
        VARCHAR(64) scope "City or Taxi Zone ID"
        JSONB raw_row "Full unvalidated row payload"
        JSONB reasons "GIN indexed array of failure reason codes"
        VARCHAR(32) status "'open' | 'resolved_by_load' | 'replayed' | 'dismissed'"
        INT replay_attempts "Number of replay re-validations"
        TIMESTAMPTZ created_at
        TIMESTAMPTZ resolved_at
    }

    alerts {
        BIGSERIAL id PK
        VARCHAR(64) fingerprint UK "check_name:scope:severity"
        VARCHAR(64) check_name "volume_drop | freshness_anomaly | missing_scope"
        VARCHAR(64) scope "Affected scope partition"
        VARCHAR(16) severity "'INFO' | 'WARNING' | 'CRITICAL'"
        VARCHAR(32) status "'active' | 'auto_closed' | 'superseded'"
        TEXT message "Human-readable diagnostic description"
        JSONB payload "Raw metric details"
        INT occurrence_count "Incremented on duplicate events"
        INT healthy_batch_count "Consecutive healthy runs"
        TIMESTAMPTZ first_seen_at
        TIMESTAMPTZ last_seen_at
        TIMESTAMPTZ resolved_at
    }

    target_table_trips {
        VARCHAR(64) trip_id PK "Natural or composite primary key"
        TIMESTAMPTZ pickup_time
        TIMESTAMPTZ delivery_time
        DOUBLE_PRECISION pickup_lat
        DOUBLE_PRECISION pickup_lon
        VARCHAR(64) city
        VARCHAR(20) driver_phone
        VARCHAR(10) postal_code
        VARCHAR(20) vehicle_plate
        VARCHAR(32) trip_status
        DOUBLE_PRECISION trip_distance_km
    }
```

---

## 3. Core Execution Sequences

### A. End-to-End Ingestion Pipeline (`pulsecheck run`)

```mermaid
sequenceDiagram
    autonumber
    participant CLI as CLI (pulsecheck run)
    participant Runner as PipelineRunner
    participant Reader as FileReader (CSV/Parquet)
    participant Schema as check_schema
    participant Val as Validator
    participant DB as PostgreSQL
    participant Loader as IdempotentLoader
    participant Quar as QuarantineWriter
    participant Engine as BatchCheckEngine

    CLI->>Runner: run(file, source, logical_date)
    Runner->>Runner: compute_file_hash & batch_id
    Runner->>DB: INSERT INTO batch_registry ('running')
    loop For each chunk (default 1,000 - 50,000 rows)
        Runner->>Reader: read_chunk(file_path, chunk_size)
        Reader-->>Runner: df_chunk
        opt First chunk only
            Runner->>Schema: check_schema(df_chunk, dataset_config)
            Schema-->>Runner: Schema OK
        end
        Runner->>Val: validate(df_chunk)
        Val->>Val: derive_composite_key (SHA-256)
        Val->>Val: vectorized rule evaluations
        Val-->>Runner: ValidationResult (valid_mask, reasons)
        Runner->>Loader: load_batch(valid_df, valid_keys)
        Loader->>DB: INSERT INTO target ON CONFLICT DO NOTHING/UPDATE
        Loader->>Quar: resolve_quarantine_rows(valid_keys)
        Quar->>DB: UPDATE quarantine SET status='resolved_by_load'
        Runner->>Quar: write_quarantine_rows(invalid_df, invalid_keys, reasons)
        Quar->>DB: INSERT INTO quarantine (JSONB raw_row, reasons)
    end
    Runner->>DB: INSERT INTO batch_metrics (received, loaded, quarantined)
    Runner->>DB: UPDATE batch_registry SET status='completed'
    opt auto_check_batch enabled
        Runner->>Engine: run_checks(batch_id, scope_volumes, latest_event_time)
        Engine->>DB: Record alerts & check findings
    end
    Runner-->>CLI: PipelineResult summary
```

---

### B. Alert Lifecycle, Cooldown & Escalation

```mermaid
sequenceDiagram
    autonumber
    participant Engine as BatchCheckEngine
    participant Checker as Volume / Freshness Checker
    participant Mgr as AlertManager
    participant DB as PostgreSQL (alerts table)
    participant Channel as Dispatcher (Log / Webhook)

    Engine->>Checker: evaluate(scope, metrics, history)
    Checker-->>Engine: CheckFinding(check_name, scope, severity, passed)
    Engine->>Mgr: process_findings([findings], conn)
    loop For each finding
        Mgr->>Mgr: compute_fingerprint(check_name, scope, severity)
        Mgr->>DB: SELECT * FROM alerts WHERE fingerprint=%s
        alt Finding FAILED and No active alert
            Mgr->>DB: INSERT INTO alerts (fingerprint, status='active', occurrences=1)
            Mgr->>Channel: Dispatch alert notification
        else Finding FAILED and Within Cooldown
            Mgr->>DB: UPDATE alerts SET occurrence_count += 1, last_seen_at=NOW()
            Note over Mgr,Channel: Notification suppressed (cooldown)
        else Finding FAILED and Cooldown Expired
            Mgr->>DB: UPDATE alerts SET occurrence_count += 1, last_seen_at=NOW()
            Mgr->>Channel: Re-dispatch notification
        else Finding PASSED and Active alert exists
            Mgr->>DB: UPDATE alerts SET healthy_batch_count += 1
            opt healthy_batch_count >= auto_close_healthy_batches
                Mgr->>DB: UPDATE alerts SET status='auto_closed', resolved_at=NOW()
                Mgr->>Channel: Log auto-closure notification
            end
        end
    end
```

---

### C. Quarantine Replay & Self-Healing Loop (`pulsecheck replay`)

```mermaid
sequenceDiagram
    autonumber
    participant CLI as CLI (pulsecheck replay)
    participant Replayer as QuarantineReplayer
    participant DB as PostgreSQL
    participant Val as Validator
    participant Loader as IdempotentLoader

    CLI->>Replayer: replay(filters, dry_run=False)
    Replayer->>DB: SELECT * FROM quarantine WHERE status='open' AND (filters)
    DB-->>Replayer: Open quarantine rows
    Replayer->>Replayer: Reconstruct DataFrame from raw_row JSONB
    Replayer->>Val: validate(reconstructed_df) with CURRENT config
    Val-->>Replayer: ValidationResult
    alt Rows Now Valid (Passed Loosened/Updated Rules)
        Replayer->>Loader: load_batch(valid_rows, keys)
        Loader->>DB: INSERT INTO target ON CONFLICT DO UPDATE
        Replayer->>DB: UPDATE quarantine SET status='replayed', resolved_at=NOW()
    end
    alt Rows Still Invalid
        Replayer->>DB: UPDATE quarantine SET replay_attempts += 1, reasons=(refreshed)
    end
    Replayer-->>CLI: Before/after transition scorecard
```
