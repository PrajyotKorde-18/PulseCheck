# Architecture Decision Records (ADRs)

This document records the architectural and design decisions made for **PulseCheck**, detailing the context, alternatives considered, chosen solution, and rationale.

---

## ADR-0001: Storage Engine Selection for Quarantine and Metadata (PostgreSQL with JSONB)
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
PulseCheck validates batches of incoming logistics and mobility data. Valid records are loaded into a relational target table, while invalid rows must be quarantined alongside detailed reason codes, original payloads, and diagnostic metadata. Initially, a hybrid PostgreSQL + MongoDB architecture was considered.

### Decision
Use a single local **PostgreSQL** instance for valid data, quarantine records, batch metrics, and alert logs. Store raw unvalidated rows and validation reason arrays inside PostgreSQL `JSONB` columns, indexed with Generalized Inverted Indexes (`GIN`).

### Alternatives Considered
1. **MongoDB for Quarantine**: Would offer natural document storage for arbitrary raw data, but adds operational overhead of running, authenticating, and maintaining two separate database systems in local Docker Compose.
2. **Flat Error Files (CSV/Parquet)**: Cheap to write, but slow and awkward for querying specific reason codes, updating quarantine status during replay, or joining against batch metrics.
3. **Relational EAV (Entity-Attribute-Value) in SQL**: High complexity and poor query ergonomics for unvalidated, malformed rows.

### PostgreSQL vs. MySQL Comparison
| Feature | PostgreSQL | MySQL |
|---|---|---|
| **JSON Storage** | Native `JSONB` (binary, decomposed format) and `JSON` (textual). | Native `JSON` (binary format introduced in 5.7+). |
| **Indexing on JSON Sub-attributes** | **GIN Index** (`CREATE INDEX ... USING GIN (reasons)`). Allows indexing arbitrary keys or arrays directly without modifying table DDL. Contains operator `@>` can search within nested arrays efficiently. | Lacks GIN indexes. Requires creating a **virtual/stored generated column** (e.g., `col AS (data->>'$.code')`) and then placing a B-Tree index on that generated column. |
| **Containment Query** | `WHERE reasons @> '[{"code": "INVALID_PHONE"}]'` | `WHERE JSON_CONTAINS(reasons, '{"code": "INVALID_PHONE"}')` |

### Consequences
- Single database engine to manage in Docker Compose and connect to in Python via `psycopg`.
- High query performance on quarantine searches by error code and status via GIN indexes.

---

## ADR-0002: Zero Hard-coding and Role-Based Column Mapping
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Data pipelines frequently fail when column names vary across different source providers, data versions, or distinct datasets (e.g., Indian logistics trips vs. NYC TLC Taxi data). Hard-coding column names like `trip_id` or `pickup_datetime` inside Python code limits reusability and breaks modularity.

### Decision
Decouple all validation rules, loader logic, and checks from physical column names by introducing **Column Roles** in `config/dataset.yaml`. 
- Physical columns are mapped to semantic roles (e.g., `id`, `pickup_time`, `delivery_time`, `lat`, `lon`, `city`, `phone`, `plate`, `status`).
- Row rules specify the **roles** they require rather than column names.
- If a dataset lacks a required role for a given rule, the rule is safely skipped and logged.

### Alternatives Considered
1. **Hard-coded Column Names in Code**: Rigid, cannot reuse the pipeline for NYC TLC or different schema versions without code edits.
2. **Runtime Code Generation / Dynamic SQL Templates**: Fragile to debug, vulnerable to syntax bugs and SQL injection risks.

### Consequences
- Zero hard-coded column names in Python logic.
- The same validation engine works identically on logistics trip data and NYC TLC taxi trip data purely via YAML configuration.

---

## ADR-0003: Idempotent Ingestion & Row Identity Resolution
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Data pipelines inevitably encounter duplicate file deliveries, backfills, and overlapping time windows. Re-running a batch must never produce duplicate entries or inconsistent table states. Furthermore, datasets may identify rows via a single unique column (e.g., `trip_id`) or through a composite set of attributes (e.g., `vendor_id` + `pickup_datetime` + `pickup_location_id`).

### Decision
1. **Row Identity**: Configured in `config/dataset.yaml` as either a single role or a list of roles. For composite keys, derive a deterministic unique key (SHA-256 hash of the normalized, concatenated key values).
2. **Idempotent Loading**: Use PostgreSQL `INSERT ... ON CONFLICT (...) DO UPDATE` or `DO NOTHING` based on a configurable conflict policy (`skip`, `update`, or `quarantine_diff`).
3. **Safe Parameterized SQL**: Construct SQL identifiers strictly using `psycopg.sql.Identifier`, and bind all values as positional parameters. String concatenation for SQL generation is strictly prohibited.

### PostgreSQL vs. MySQL Comparison
| Concept | PostgreSQL Syntax | MySQL Syntax |
|---|---|---|
| **Upsert Logic** | `INSERT INTO tbl (id, val) VALUES (%s, %s) ON CONFLICT (id) DO UPDATE SET val = EXCLUDED.val;` | `INSERT INTO tbl (id, val) VALUES (%s, %s) ON DUPLICATE KEY UPDATE val = VALUES(val);` |
| **Referencing Proposed Value** | `EXCLUDED.column_name` refers to the row that was proposed for insertion. | `VALUES(column_name)` (or alias in MySQL 8.0.20+ `AS new ON DUPLICATE KEY UPDATE val = new.val`). |
| **Skip Existing Conflicts** | `INSERT INTO tbl (...) VALUES (...) ON CONFLICT (id) DO NOTHING;` | `INSERT IGNORE INTO tbl (...) VALUES (...);` |

### Known Limitation
When multiple natural columns form a composite key, any rows with duplicate composite values within the same batch will collide by design: the first occurrence proceeds, and subsequent occurrences are quarantined as duplicates.

---

## ADR-0004: Injectable Clock Adapter for Pipeline Time Determinism
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Time-sensitive batch checks (e.g., freshness checks comparing maximum event time against reference time) and timestamp generation fail unit tests or yield erratic results during historical replays if `datetime.now()` or `datetime.utcnow()` is called directly inside business logic.

### Decision
Implement an injectable `Clock` protocol (`src/pulsecheck/clock.py`). 
- Production runs default to a `SystemClock` returning the current timezone-aware UTC time.
- Unit tests, simulations, and historical backfills inject a `FrozenClock` or `SimulatedClock` initialized to an explicit timestamp.

### Alternatives Considered
1. **`freezegun` or monkeypatching `datetime`**: Adds an external library and monkeypatches C-extensions, which can introduce subtle bugs in multi-threaded runtimes or third-party libraries.
2. **Passing `current_time` as an argument to every function**: Verbose and creates leaky abstractions across deep call stacks.

### Consequences
- 100% reproducible test suites and deterministic benchmark verification without relying on system clock state.

---

## ADR-0005: Dual-Profile Configuration Architecture (Synthetic Logistics vs. NYC TLC)
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
The user requires demonstrating the framework on both an Indian logistics trip domain (synthetic data with intentional fault injection) and an open-source real-world dataset (NYC TLC Trip Records in Parquet format) without modifying source code.

### Decision
Provide two configuration profiles:
1. `config/`: The primary synthetic profile with Indian logistics domains (`dataset.yaml`, `rules.yaml`, `checks.yaml`, `faults.yaml`, `cities.yaml`).
2. `config/tlc/`: The real-world profile mapping NYC TLC yellow taxi records (`dataset.yaml`, `rules.yaml`, `checks.yaml`).

The CLI allows selecting the active configuration via `--config <path>` or the environment variable `PULSECHECK_CONFIG`.

---

## ADR-0006: Third-Party Adapter Pattern for indpy and Pandera
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Third-party domain libraries (such as `indpy` for Indian postal/telecom formats and `pandera` for DataFrame schemas) are subject to upstream API deprecations or replacement. Calling third-party functions directly throughout business logic tightly couples the codebase to specific library APIs.

### Decision
Encapsulate all external library calls behind small, dedicated adapter modules:
- `src/pulsecheck/adapters/indpy_adapter.py`
- `src/pulsecheck/adapters/pandera_adapter.py`

### Consequences
- Business rules depend only on adapter interfaces (`indpy_adapter.is_valid_mobile()`), not raw library internals.
- If an alternative validation engine or national format library is adopted, only the adapter requires modification.

---

## ADR-0007: Distance Calculation Methodology and Implied Speed Evaluation
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Calculating trip speed requires measuring distance. Incoming datasets may already provide a precomputed distance column (e.g. `trip_distance_km` in Indian logistics, or `trip_distance` in NYC TLC taxi data). Alternatively, datasets may only provide pickup and delivery geographic coordinates `(lat, lon)`.

### Decision
1. If the dataset schema includes the `distance` role, use the pre-measured physical distance value.
2. If the `distance` role is absent but both pickup and dropoff coordinates exist, calculate great-circle distance using the **Haversine formula** (`haversine` library with `Unit.KILOMETERS`).
3. Compute implied speed: $\text{Speed (km/h)} = \frac{\text{Distance (km)}}{\Delta t / 3600.0}$. If $\text{Speed} > \text{max\_speed\_kmh}$, trigger `ERR_SPEED_EXCEEDED`.

### MySQL vs. PostgreSQL Comparison
| Feature | PostgreSQL | MySQL |
|---|---|---|
| **Geographic Distance** | Built-in point types and operator `<@>` (earthdistance extension) or PostGIS `ST_Distance()`. | MySQL 5.7+ supports spatial functions such as `ST_Distance_Sphere(point1, point2)` natively in metres. |
| **Python-Side Vectorization** | Computed in pandas using vectorized arithmetic and Haversine functions before database insertion. | Identical. |

---

## ADR-0008: Deterministic and Order-Independent Composite Key Derivation
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
When datasets lack a natural surrogate ID (e.g. NYC TLC records lacking unique trip IDs), uniqueness is defined across a composite set of attributes (e.g. `vendor_id`, `pickup_datetime`, `pickup_location_id`). If the order of roles in YAML configuration changes between deployments, key derivation must remain 100% deterministic and invariant.

### Decision
1. For single-role keys, use the trimmed string representation of the column.
2. For composite keys:
   - Sort the configured identity role names lexicographically.
   - Extract string values for each role (mapping null to empty string).
   - Format canonical key-value pairs: `role1=val1|role2=val2|...`
   - Hash the canonical string using SHA-256 to produce a uniform 64-character hexadecimal surrogate ID.

### Consequences
- Changing the declaration order of identity roles in `config/dataset.yaml` (e.g. `[vendor, pickup_time]` vs `[pickup_time, vendor]`) produces identical surrogate keys.
- Known limitation: identical composite rows within the same file will collide by design (first occurrence retained, subsequent occurrences flagged as duplicate keys).

---

## ADR-0009: PostgreSQL JSONB Quarantine Architecture vs MySQL Virtual Columns
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
A core requirement of PulseCheck is to quarantine malformed rows alongside structured diagnostic reasons (rule IDs, error codes, failed column names, error messages) without schema migrations when new validation rules are added.

### Decision
Store raw unvalidated records in a `raw_row JSONB` column and all failure reasons in a `reasons JSONB` array of objects:
`[{"code": "ERR_NEGATIVE_FARE", "rule": "fare_positive", "fields": ["fare_inr"], "message": "..."}]`
Index the `reasons` column with a Generalized Inverted Index:
`CREATE INDEX idx_quarantine_reasons_gin ON quarantine USING GIN (reasons);`

### PostgreSQL vs. MySQL Comparison
| Feature | PostgreSQL | MySQL |
|---|---|---|
| **JSON Indexing** | Native **GIN Index** on entire JSONB structure. Indexes all keys, values, and array elements automatically. | No GIN index. To index an attribute inside JSON, MySQL requires creating a **Virtual Generated Column** and building a standard B-Tree index on it: `ALTER TABLE q ADD code VARCHAR(32) AS (reasons->>'$[0].code') STORED, ADD INDEX (code);` |
| **Array Containment** | Containment operator `@>`: `WHERE reasons @> '[{"code": "ERR_NEGATIVE_FARE"}]'` uses the GIN index directly. | `JSON_CONTAINS(reasons, '{"code": "ERR_NEGATIVE_FARE"}')` (full table scan unless specific generated column exists). |
| **Resolution Linking** | Array containment check `= ANY(%s)` efficiently resolves open quarantine records when valid rows load: `WHERE record_key = ANY(%s) AND status = 'open'`. | Requires dynamic `IN (?, ?, ...)` prepared statement parameter lists. |

---

## ADR-0010: Upsert Conflict Resolution Policies vs MySQL ON DUPLICATE KEY UPDATE
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
When loading data, rows may conflict with existing primary keys or surrogate `record_key` values in the target table due to backfills, duplicate files, or updated records.

### Decision
Support three configurable conflict policies in `config/dataset.yaml`:
1. `skip`: `ON CONFLICT (record_key) DO NOTHING RETURNING record_key`. Unreturned rows are counted as `rows_duplicate`.
2. `update`: `ON CONFLICT (record_key) DO UPDATE SET col = EXCLUDED.col, ..., _loaded_at = NOW() RETURNING record_key`.
3. `quarantine`: `ON CONFLICT (record_key) DO NOTHING RETURNING record_key`. Unreturned rows represent database collisions and are written directly to `quarantine` with `ERR_DUPLICATE_KEY`.

### PostgreSQL vs. MySQL Comparison
| Concept | PostgreSQL Syntax | MySQL Syntax |
|---|---|---|
| **Conflict Target** | Explicit target required: `ON CONFLICT (record_key)` | Implicitly targets any UNIQUE or PRIMARY KEY: `ON DUPLICATE KEY UPDATE` |
| **Proposed Value Reference** | `EXCLUDED.column_name` | `VALUES(column_name)` or table alias `AS new ... new.column_name` |
| **Intra-batch Duplicates** | Cardinality violation error if duplicate keys exist in a single `INSERT` statement. PulseCheck prevents this by deduplicating rows during row validation before load. | Silently executes updates sequentially on duplicates within the same statement. |
| **Atomic Returning** | `RETURNING record_key` allows immediate discovery of which rows were actually inserted vs skipped. | MySQL requires inspecting `ROW_COUNT()` (1 = inserted, 2 = updated, 0 = unchanged), which cannot distinguish row-by-row identity in batch inserts. |

---

## ADR-0011: Transaction Scope (Batch vs. Chunk)
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Streaming large data files requires deciding when to commit database transactions.

### Decision
Support configurable `transaction_scope` in `config/dataset.yaml`:
- `batch` (default): Entire ingestion run (data inserts, quarantine inserts, batch metrics, registry updates) executes inside a single transaction. If any chunk or check fails, everything rolls back atomically.
- `chunk`: Each chunk commits its valid and quarantined rows independently. Useful for multi-gigabyte files to minimize memory pressure, avoid lock escalation, and prevent long-running transaction overhead.

---

## ADR-0012: Documented SQL Type Map & Safe Identifier Parameterization
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Generating DDL from `config/dataset.yaml` presents SQL injection hazards if arbitrary strings are interpolated into table creation statements.

### Decision
1. Whitelist allowable PostgreSQL base types (`VARCHAR`, `TEXT`, `INTEGER`, `BIGINT`, `DOUBLE PRECISION`, `NUMERIC`, `BOOLEAN`, `TIMESTAMPTZ`, `DATE`, `JSONB`).
2. Strictly reject invalid characters or chained statements via regex validation (`validate_sql_type`).
3. Wrap all table and column names in `psycopg.sql.Identifier(col.name)`.

---

## ADR-0013: Parquet Engine Selection (pyarrow Streaming)
- **Date**: 2026-10-24
- **Status**: Accepted

### Context
Reading multi-gigabyte Parquet files without out-of-memory errors requires true chunked/batched reading.

### Decision
Use `pyarrow.parquet.ParquetFile(path).iter_batches(batch_size=chunk_size)`:
- Reads metadata without loading entire tables into memory.
- Streams record batches directly into pandas DataFrames.
- Closes file handles deterministically to prevent file lock issues on Windows environments.

---

## ADR-0014: Fault Plugin Architecture & Ground-Truth Labels
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Simulating defects (bad phone, swapped coordinates, dropped columns, missing cities, stale batches) requires a decoupled, testable mechanism that produces both the corrupted data and exact ground-truth labels for precision/recall benchmarking.

### Decision
1. Implement a pluggable `FaultPlugin` registry. Each plugin implements:
   `apply(df, fault_cfg, context) -> Tuple[pd.DataFrame, InjectedFaultRecord]`
2. Separate row-level mutations (`scope: rows`) from batch-level anomalies (`scope: batch`).
3. For row-level faults, plugins track exact `affected_row_ids`. For batch-level faults, plugins track `affected_scopes` and anomaly parameters.
4. Serialize ground truth to `<filename>.labels.json` documenting `fault_type`, `expected_detector`, `scope`, `affected_row_ids`, and `count`.

---

## ADR-0015: Deterministic RNG Seeding for Synthetic Generation & Fault Injection
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Testing and data pipelines require 100% reproducible synthetic generation. Different libraries (`random`, `Faker`, `indpy`) maintain separate random states.

### Decision
1. Pass an explicit integer seed (`--seed`, default `42`).
2. Coordinate seeding across all generators simultaneously:
   - Call `random.seed(seed)` to seed Python's global generator (used internally by `indpy`).
   - Create an isolated `random.Random(seed)` instance for local jitter and permutation logic.
   - Call `Faker.seed(seed)` to lock third-party text/name generators.
3. Validate in unit tests that identical seeds yield bit-for-bit identical DataFrames, and differing seeds produce distinct distributions.

---

## ADR-0016: OpenStreetMap Nominatim Usage Policy Compliance
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Fetching city bounding boxes for coordinate sampling requires external geocoding. OpenStreetMap Nominatim enforces strict operational guidelines.

### Decision
Strictly adhere to [Nominatim Usage Policy](https://operations.osmfoundation.org/policies/nominatim/):
1. **User-Agent**: Provide a descriptive, identifiable User-Agent string (`PulseCheck-DataEngineering/1.0 (contact: admin@pulsecheck.internal)`).
2. **Rate Limit**: Enforce an explicit delay of $\ge 1.2\text{ seconds}$ between successive requests (policy requires $< 1$ req/sec).
3. **Persistent Disk Caching**: Cache all geocoded responses in `reports/nominatim_cache.json`. Repeated runs read from disk without contacting OSM servers.
4. **Offline Safety**: If the network is unavailable or rate-limited, do not guess coordinates from memory. Write placeholders (`null`) to `config/cities.yaml` and instruct the user on exact manual bounding box values.

---

## ADR-0017: Quarantine Replay Semantics & Idempotency
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
When business rules or configuration parameters are corrected (e.g. relaxing an overly strict speed threshold or correcting a geographic bounding box), previously quarantined records must be safely re-evaluated and ingested without creating duplicates or losing audit history.

### Decision
1. **Re-validation with Current Config**: `pulsecheck replay` reads open quarantine entries and subjects their raw payloads (`raw_row`) to the currently active `ConfigBundle` rules.
2. **Idempotent Ingestion**: Records that now pass are loaded into the target table using `IdempotentLoader`, applying the dataset's configured conflict resolution policy. Replaying twice is guaranteed to produce zero additional rows or duplicate keys.
3. **State Updates**:
   - Successfully loaded records transition from `status = 'open'` to `status = 'replayed'`, recording `attempts = attempts + 1` and `replayed_at = NOW()`.
   - Records that still violate rules remain `status = 'open'`, have their diagnostic `reasons` refreshed, and have their `attempts` counter incremented by 1.
4. **Dry-Run Non-Destructive Invariant**: The `--dry-run` flag evaluates transitions and prints predicted counts without performing any database writes or state mutations.
5. **Auto-Resolution Contract**: When data suppliers fix rejected rows out-of-band and re-submit them via `pulsecheck run`, `IdempotentLoader` updates open quarantine records with matching keys to `status = 'resolved_by_load'`.

---

## ADR-0018: Diagnostic Root-Cause Concentration Flags
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Quarantined records often stem from upstream supplier system failures (e.g., an app update corrupting driver phone formats) or localized event disruptions. Operators need immediate root-cause visibility to distinguish widespread uniform drift from concentrated source- or day-specific anomalies.

### Decision
1. **Multi-Dimensional Aggregation**: Group quarantine entries by `reason_code`, `source`, `scope`, and `logical_date`, computing percentage shares of each reason.
2. **Configurable Thresholds**: Define concentration limits in `config/checks.yaml` under `batch_checks.diagnostics`:
   - `max_source_share` (default: `0.80`): Flags any single source accounting for $\ge 80\%$ of all failures for a specific reason code.
   - `max_day_share` (default: `0.80`): Flags any single logical date accounting for $\ge 80\%$ of all failures for a specific reason code.
3. **Actionable Output**: Highlight concentrated anomalies prominently in CLI output and diagnostic reports with clear attribution (`source` or `logical_date`).

---

## ADR-0019: Historical Weekday-Matched Median vs. Mean for Volume Anomaly Detection
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Daily ingestion volumes fluctuate significantly between workdays and weekends, and occasional upstream pipeline outages or marketing promotions introduce extreme volume outliers. Using a simple arithmetic mean across all calendar days causes high false-alarm rates on weekends and skews baselines after extreme events.

### Decision
1. **Weekday Matching**: When evaluating volume for a batch on day $D$, compare only against past batches where $\text{weekday}(H) = \text{weekday}(D)$ (e.g., comparing Monday to prior Mondays).
2. **Median over Mean**: Use the statistical median of the historical window rather than the mean. The median has a breakdown point of 50%, making it immune to isolated outage spikes or zero-volume anomalies in the lookback history.
3. **Configurable Ratio Envelope**: Trigger alerts when current volume falls outside $[\text{min\_volume\_ratio} \times \text{median}, \text{max\_volume\_ratio} \times \text{median}]$.

---

## ADR-0020: Short-History Fallback Rules & Zero-Baseline Behavior
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
When onboarding a new data feed or launching operations in a new city, insufficient historical batches exist to compute a statistically meaningful weekday median. Furthermore, certain scopes may legitimately have zero volume on specific days (e.g., non-operational holidays).

### Decision
1. **Fallback Thresholds**: If fewer than `minimum_history_batches` matching weekday batches exist:
   - Do not compute ratio against median.
   - Evaluate volume against absolute bounds `[fallback_absolute_min_rows, fallback_absolute_max_rows]`.
   - Volumes within bounds pass with diagnostic status `fallback_passed`.
2. **Zero-Baseline Handling**:
   - If historical median is exactly 0:
     - 0 rows loaded passes (expected absence).
     - $>0$ rows loaded triggers a `WARNING` alert for unexpected volume against a zero baseline, preventing division-by-zero errors.

---

## ADR-0021: Alert Fingerprinting, Cooldown Windows & Escalation Semantics
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Repeated executions (backfills, continuous streaming batches, retries) can cause alert notification storms if every failure sends external notifications. Conversely, critical escalations must not be suppressed by prior lower-severity warnings.

### Decision
1. **Deterministic Fingerprint**: Form fingerprint as `check_name:scope:severity` (truncated or hashed to fit within 64 chars).
2. **Cooldown Suppression**: Repeated occurrences of the exact same condition within `cooldown_seconds` update `last_seen_at` and increment `occurrence_count` without re-dispatching to external channels (log/webhook).
3. **Severity Escalation Bypass**: If a check escalates from a lower severity (e.g. `WARNING` volume spike) to a higher severity (e.g. `CRITICAL` volume drop to 0), the cooldown is bypassed, the previous alert is superseded, and notification is dispatched immediately.
4. **Auto-Closure Lifecycle**: Active alerts track consecutive healthy batches for that `(check_name, scope)`. When `healthy_batch_count >= auto_close_healthy_batches`, the alert transitions to `status = 'auto_closed'` and records `resolved_at = NOW()`.

---

## ADR-0022: Ground-Truth Detection Matching, Compound Fault Overlaps & Threshold Policy
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Automated evaluation of pipeline performance requires matching detector outputs to injected ground-truth labels. In synthetic defect generation, compound overlapping defects (e.g. both a null ID and inverted timestamps applied to the same row) and key-nullifying mutations introduce non-trivial detection semantics. Furthermore, benchmark systems often suffer from "threshold gaming" where rules are tuned specifically to maximize synthetic precision/recall scores.

### Decision
1. **Matching Semantics**:
   - For row faults: A defect is `caught` if the quarantined row's ID matches `affected_row_ids` and its reasons include `expected_detector`.
   - `missed = injected - caught`.
   - `false_alarms = detections_not_in_affected_ids`.
   - Formulae: $\text{Precision} = \frac{\text{caught}}{\text{caught} + \text{false\_alarms}}$, $\text{Recall} = \frac{\text{caught}}{\text{injected}}$.
2. **Key-Nullifying Mutations & Overlapping Explanations**:
   - In `null_id`, the row's `trip_id` is replaced with `None`. When quarantined, the row lacks its original ID, causing ID-based correlation to fail.
   - In `duplicate_ids`, the target row takes the copied `source_id`, so it is quarantined under the source ID rather than its pre-mutation key.
   - For overlapping mutations, earlier defects take precedence or mask secondary rules.
   - **Factual Explanations**: Rather than hiding these realities, the benchmark records explicit, factual explanations in Section 4 of the report.
3. **No Threshold Gaming Policy**:
   - Detection thresholds in `rules.yaml` and `checks.yaml` must reflect production operational requirements, never synthetic test convenience.
   - No threshold shall be modified solely to reach artificial 100% scores. Any necessary threshold changes must preserve both runs in `reports/` and explain the rationale.

---

## ADR-0023: Closed-Loop Quarantine Recovery Evaluation via Replay
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Validating quarantine functionality requires proving not only that bad data is quarantined, but that data can be remediated and recovered after corrective action (closed-loop lifecycle).

### Decision
1. Configure `replay_recovery` in `bench` config or `faults.yaml` specifying target fault (e.g., `swapped_times`), remediation type (`config` or `corrected_file`), and parameter modifications.
2. In `config` remediation:
   - Benchmark dynamically clones configuration and disables or loosens the target rule (e.g., `RULE_CHRONOLOGY`).
   - Runs `QuarantineReplayer.replay_records(quarantined_records)`.
   - Counts `replayed_loaded` to verify how many rows are recovered into the target table.
3. This verifies that quarantine entries are neither dead-ends nor permanently trapped, confirming the self-healing and reprocessing capacity of PulseCheck.

---

## ADR-0024: NYC TLC Yellow Taxi Schema Mapping, Composite Key Resolution & Empirical Thresholds
- **Date**: 2026-10-25
- **Status**: Accepted

### Context
Phase 7 evaluates PulseCheck on real-world NYC TLC Yellow Taxi trip data (`yellow_tripdata_2024-01.parquet`, 2,964,624 rows, 49.96 MB). The real TLC schema contains 19 physical columns but lacks a natural single-column unique identifier (no `trip_id`). Furthermore, urban taxi operations differ from freight/courier logistics: zone IDs (`PULocationID`, `DOLocationID`) replace raw GPS coordinates, and thresholds must reflect empirical distributions from real TLC operations rather than synthetic assumptions.

### Real Printed Schema (from `pyarrow.parquet.read_schema`)
1. `VendorID` (`int32`)
2. `tpep_pickup_datetime` (`timestamp[us]`)
3. `tpep_dropoff_datetime` (`timestamp[us]`)
4. `passenger_count` (`int64`)
5. `trip_distance` (`double`)
6. `RatecodeID` (`int64`)
7. `store_and_fwd_flag` (`large_string`)
8. `PULocationID` (`int32`)
9. `DOLocationID` (`int32`)
10. `payment_type` (`int64`)
11. `fare_amount` (`double`)
12. `extra` (`double`)
13. `mta_tax` (`double`)
14. `tip_amount` (`double`)
15. `tolls_amount` (`double`)
16. `improvement_surcharge` (`double`)
17. `total_amount` (`double`)
18. `congestion_surcharge` (`double`)
19. `Airport_fee` (`double`)

### Column Role Mapping (`config/tlc/dataset.yaml`)
- `VendorID` $\rightarrow$ role `vendor` (participates in composite identity)
- `tpep_pickup_datetime` $\rightarrow$ role `pickup_time` (chronology, duration, freshness)
- `tpep_dropoff_datetime` $\rightarrow$ role `delivery_time` (chronology, duration)
- `PULocationID` $\rightarrow$ role `scope` (zone ID mapped to scope for volume/anomalies)
- `DOLocationID` $\rightarrow$ role `dropoff_scope` (participates in composite identity)
- `fare_amount` $\rightarrow$ role `fare` (participates in composite identity)
- `trip_distance` $\rightarrow$ role `distance` (speed checks)
- Unmapped columns (`passenger_count`, `RatecodeID`, `store_and_fwd_flag`, `payment_type`, `extra`, `mta_tax`, `tip_amount`, `tolls_amount`, `improvement_surcharge`, `total_amount`, `congestion_surcharge`, `Airport_fee`): left unmapped, retained in target schema / raw payloads.
- Missing roles (`lat`, `lon`, `city`, `phone`, `pincode`, `plate`, `status`): omitted. Rules requiring these roles (`RULE_GEO_BOUNDS`, `RULE_SWAPPED_COORDINATES`, `RULE_PHONE_FORMAT`, `RULE_PINCODE_FORMAT`, `RULE_PLATE_FORMAT`, `RULE_CANCELLED_DELIVERY`) are skipped cleanly with logged reasons.

### Proposed Composite Key & Collision Risk Analysis
- **Proposed Identity Roles**: `[vendor, pickup_time, scope, dropoff_scope, fare]`
- **Underlying Columns**: `(VendorID, tpep_pickup_datetime, PULocationID, DOLocationID, fare_amount)`
- **Empirical Collision Rate**: Across all 2,964,624 rows of January 2024, exactly 615 rows (0.0207%) share a collision.
- **Risk Assessment**:
  - TPEP providers (Vendor 1: CMT, Vendor 2: VeriFone) transmit trip records with microsecond timestamps and discrete zone IDs.
  - Two taxis from the same vendor picking up in the exact same zone at the exact same microsecond, dropping off in the same zone, with the identical metered fare is statistically near-zero for independent trips.
  - The 615 duplicates predominantly represent meter re-transmissions or double submissions.
  - PulseCheck's `RULE_DUPLICATE_KEY` will accept the first occurrence and quarantine duplicates under `ERR_DUPLICATE_KEY`, which is the intended safe pipeline behavior.

### Empirical Thresholds (Computed from 2.96M Rows)
1. **Duration (`max_duration_seconds`)**:
   - Data percentiles: p50 = 11.6 min, p90 = 28.9 min, p99 = 1.01 h, p99.9 = 1.92 h.
   - Negative durations: 56 rows (`ERR_INVALID_CHRONOLOGY`).
   - Proposed candidate: **14,400 seconds** (4.0 hours), covering >99.95% of legitimate trips while catching runaway meters and multi-day errors.
2. **Speed (`max_speed_kmh`)**:
   - In TLC data, `trip_distance` is measured in statute miles.
   - Data percentiles (mph): p50 = 9.67 mph, p90 = 20.20 mph, p99 = 37.12 mph, p99.9 = 49.13 mph, p99.99 = 1,605 mph (impossible GPS spikes).
   - Legal highway limit in NYC is 50–55 mph.
   - Proposed candidate: **80.0** (or **130.0** if km/h scale), covering >99.95% of traffic while quarantining sensor teleportation.
