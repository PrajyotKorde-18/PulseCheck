"""Pydantic configuration models for PulseCheck with strict schema validation."""

from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class StrictBaseModel(BaseModel):
    """Base model that forbids unexpected extra keys."""

    model_config = ConfigDict(extra="forbid", strict=True)


class DatasetColumn(StrictBaseModel):
    """Definition of a single dataset column and its semantic role."""

    name: str = Field(..., description="Physical column name in the source file")
    role: str = Field(..., description="Semantic role (e.g., id, pickup_time, lat, lon, city)")
    sql_type: str = Field(
        ..., description="Target PostgreSQL SQL data type (e.g., VARCHAR, TIMESTAMPTZ)"
    )
    nullable: bool = Field(
        default=False, description="Whether the column is permitted to contain NULL values"
    )


class CSVOptions(StrictBaseModel):
    """CSV parser configuration."""

    delimiter: str = Field(default=",", description="Field delimiter character")
    has_header: bool = Field(default=True, description="Whether the CSV file includes a header row")


class DatasetConfig(StrictBaseModel):
    """Specification of a dataset, its column-role mappings, and ingestion policies."""

    name: str = Field(..., description="Unique logical dataset name")
    format: Literal["csv", "parquet"] = Field(..., description="File format of incoming data files")
    csv_options: Optional[CSVOptions] = Field(
        default=None, description="Parser options if format is CSV"
    )
    table_name: str = Field(..., description="Target PostgreSQL table name")
    identity_roles: List[str] = Field(
        ...,
        min_length=1,
        description="Role or list of roles that together uniquely identify a record",
    )
    composite_key_hash_algorithm: Literal["sha256"] = Field(
        default="sha256",
        description="Hashing algorithm to derive deterministic surrogate keys for composite roles",
    )
    unknown_columns_policy: Literal["ignore", "warn", "reject"] = Field(
        default="warn",
        description="Behavior when incoming file contains unconfigured columns",
    )
    conflict_policy: Literal["skip", "update", "quarantine"] = Field(
        default="skip",
        description="Action to take when a row conflicts on primary/composite key in PostgreSQL",
    )
    transaction_scope: Literal["batch", "chunk"] = Field(
        default="batch",
        description="Transaction scope for database loads: commit per chunk or across entire batch",
    )
    repeated_file_policy: Literal["skip", "reprocess"] = Field(
        default="skip",
        description="Action when an identical file hash has already been registered in database",
    )
    chunk_size: int = Field(
        default=1000,
        gt=0,
        description="Batch chunk size for streaming file reads and database inserts",
    )
    columns: List[DatasetColumn] = Field(
        ...,
        min_length=1,
        description="List of column definitions",
    )

    def get_role_to_columns_map(self) -> Dict[str, List[str]]:
        """Map each role to the list of physical column names fulfilling it."""
        mapping: Dict[str, List[str]] = {}
        for col in self.columns:
            mapping.setdefault(col.role, []).append(col.name)
        return mapping

    def get_column_to_role_map(self) -> Dict[str, str]:
        """Map each physical column name to its assigned role."""
        return {col.name: col.role for col in self.columns}

    def get_primary_column_for_role(self, role: str) -> Optional[str]:
        """Return the first physical column name fulfilling a given role, or None."""
        for col in self.columns:
            if col.role == role:
                return col.name
        return None

    def get_required_columns(self) -> List[str]:
        """Return physical column names marked as not nullable."""
        return [col.name for col in self.columns if not col.nullable]


class DatasetConfigFile(StrictBaseModel):
    """Root model for dataset.yaml."""

    dataset: DatasetConfig


class RuleConfig(StrictBaseModel):
    """Configuration for an individual row validation rule."""

    rule_id: str = Field(..., description="Unique programmatic rule identifier")
    reason_code: str = Field(
        ..., description="Short diagnostic code attached to quarantined records"
    )
    description: str = Field(
        ..., description="Human-readable explanation of what this rule verifies"
    )
    required_roles: List[str] = Field(
        default_factory=list,
        description="List of semantic roles required for this rule to evaluate",
    )
    severity: Literal["INFO", "LOW", "MEDIUM", "HIGH", "CRITICAL"] = Field(
        default="MEDIUM",
        description="Severity level of this rule violation",
    )
    enabled: bool = Field(default=True, description="Whether this rule is active")
    parameters: Dict[str, Any] = Field(
        default_factory=dict,
        description="Rule-specific thresholds and parameters",
    )


class RulesConfigFile(StrictBaseModel):
    """Root model for rules.yaml."""

    rules: List[RuleConfig]


class VolumeCheckConfig(StrictBaseModel):
    """Volume anomaly check settings."""

    enabled: bool = Field(default=True, description="Enable volume anomaly check")
    history_window_batches: int = Field(
        default=14, gt=0, description="Lookback window in batches"
    )
    minimum_history_batches: int = Field(
        default=3, gt=0, description="Minimum historical batches needed for median"
    )
    min_volume_ratio: float = Field(
        default=0.50, gt=0.0, description="Minimum acceptable ratio of current to median volume"
    )
    max_volume_ratio: float = Field(
        default=2.00, gt=0.0, description="Maximum acceptable ratio of current to median volume"
    )
    fallback_absolute_min_rows: int = Field(
        default=10, ge=0, description="Fallback floor when insufficient history"
    )
    fallback_absolute_max_rows: int = Field(
        default=1000000, gt=0, description="Fallback ceiling when insufficient history"
    )


class MissingScopesCheckConfig(StrictBaseModel):
    """Missing scope (e.g. city) check settings."""

    enabled: bool = Field(default=True, description="Enable missing scopes check")
    lookback_batches: int = Field(
        default=7, gt=0, description="Number of recent batches to inspect for active scopes"
    )
    severity: Literal["INFO", "WARNING", "HIGH", "CRITICAL"] = Field(
        default="CRITICAL",
        description="Severity raised when a recently seen scope is absent",
    )


class FreshnessCheckConfig(StrictBaseModel):
    """Data freshness check settings."""

    enabled: bool = Field(default=True, description="Enable event freshness check")
    event_time_role: str = Field(default="pickup_time", description="Role representing event time")
    max_lag_seconds: int = Field(
        default=86400,
        gt=0,
        description="Maximum allowable lag between event time and reference clock",
    )
    severity: Literal["INFO", "WARNING", "HIGH", "CRITICAL"] = Field(
        default="WARNING",
        description="Severity raised when freshness threshold is exceeded",
    )


class AlertDestinationsConfig(StrictBaseModel):
    """Channels for alert delivery."""

    log: bool = Field(default=True, description="Write alerts to standard logger")
    table: bool = Field(
        default=True, description="Persist alerts into the PostgreSQL alerts table"
    )
    webhook: bool = Field(
        default=False,
        description="Send alert payload to HTTP webhook (requires ALERT_WEBHOOK_URL)",
    )


class AlertsConfig(StrictBaseModel):
    """Alert deduplication and destination settings."""

    cooldown_seconds: int = Field(
        default=3600,
        ge=0,
        description="Cooldown suppression window for identical alert fingerprints",
    )
    auto_close_healthy_batches: int = Field(
        default=3,
        ge=1,
        description="Number of consecutive healthy batches after which an active alert auto-closes",
    )
    destinations: AlertDestinationsConfig = Field(default_factory=AlertDestinationsConfig)


class DiagnosticsCheckConfig(StrictBaseModel):
    """Quarantine diagnostic concentration threshold settings."""

    enabled: bool = Field(default=True, description="Enable concentration diagnostics")
    max_source_share: float = Field(
        default=0.80,
        gt=0.0,
        le=1.0,
        description="Concentration threshold for single source share of a reason",
    )
    max_day_share: float = Field(
        default=0.80,
        gt=0.0,
        le=1.0,
        description="Concentration threshold for single logical date share of a reason",
    )


class BatchChecksConfig(StrictBaseModel):
    """Composite configuration for all batch-level verification checks."""

    auto_check_batch: bool = Field(
        default=True,
        description="Automatically execute batch-level checks at the end of pipeline run",
    )
    volume: VolumeCheckConfig = Field(default_factory=VolumeCheckConfig)
    missing_scopes: MissingScopesCheckConfig = Field(default_factory=MissingScopesCheckConfig)
    freshness: FreshnessCheckConfig = Field(default_factory=FreshnessCheckConfig)
    alerts: AlertsConfig = Field(default_factory=AlertsConfig)
    diagnostics: DiagnosticsCheckConfig = Field(default_factory=DiagnosticsCheckConfig)


class ReplayRecoveryConfig(StrictBaseModel):
    """Configuration for benchmark replay remediation assessment."""

    fault_name: str = Field(
        default="swapped_times", description="Target fault to remediate via quarantine replay"
    )
    fix_type: Literal["config", "corrected_file"] = Field(
        default="config",
        description="Remediation strategy: 'config' (rule modification) or 'corrected_file'",
    )
    loosened_rule_id: str = Field(
        default="RULE_CHRONOLOGY",
        description="Rule ID to loosen or disable during config-based replay",
    )


class BenchConfig(StrictBaseModel):
    """Configuration settings governing synthetic benchmark execution."""

    history_days: int = Field(
        default=3, ge=1, description="Number of clean baseline history days to generate and load"
    )
    trips_per_city_day: int = Field(
        default=50, ge=1, description="Base trips per city per day for synthetic generation"
    )
    seed: int = Field(
        default=42, description="Deterministic random seed for generation and injection"
    )
    source: str = Field(default="logistics_feed", description="Data source name for benchmark runs")
    output_dir: str = Field(
        default="data/benchmark", description="Directory to store generated benchmark batches"
    )
    reports_json: str = Field(
        default="reports/benchmark.json", description="Target path for benchmark JSON report"
    )
    reports_md: str = Field(
        default="reports/benchmark.md", description="Target path for benchmark Markdown report"
    )
    replay_recovery: ReplayRecoveryConfig = Field(
        default_factory=ReplayRecoveryConfig,
        description="Settings for replay recovery assessment",
    )


class ChecksConfigFile(StrictBaseModel):
    """Root model for checks.yaml."""

    batch_checks: BatchChecksConfig
    bench: BenchConfig = Field(default_factory=BenchConfig)


class CityBoundingBox(StrictBaseModel):
    """Geographic bounding box for a reference city or operational scope."""

    name: str = Field(..., description="Unique name of the city or scope")
    min_lat: float = Field(..., description="Minimum latitude boundary")
    max_lat: float = Field(..., description="Maximum latitude boundary")
    min_lon: float = Field(..., description="Minimum longitude boundary")
    max_lon: float = Field(..., description="Maximum longitude boundary")


class CitiesConfigFile(StrictBaseModel):
    """Root model for cities.yaml."""

    cities: List[CityBoundingBox]


class FaultConfig(StrictBaseModel):
    """Configuration for a single synthetic fault injection plugin."""

    name: str = Field(..., description="Unique fault identifier")
    description: str = Field(..., description="Explanation of injected defect")
    target_roles: List[str] = Field(
        default_factory=list, description="Column roles targeted by this fault"
    )
    scope: Literal["rows", "batch"] = Field(..., description="Scope of the fault injection")
    rate: Optional[float] = Field(
        default=None, ge=0.0, le=1.0, description="Fraction of rows to corrupt"
    )
    count: Optional[int] = Field(
        default=None, ge=1, description="Absolute count of rows or occurrences"
    )
    parameters: Dict[str, Any] = Field(
        default_factory=dict, description="Custom fault parameters"
    )
    expected_detector: str = Field(
        ..., description="Rule code or batch check expected to catch this fault"
    )
    enabled: bool = Field(default=True, description="Whether this fault is currently active")


class FaultsConfigFile(StrictBaseModel):
    """Root model for faults.yaml."""

    faults: List[FaultConfig]
    replay_recovery: Optional[ReplayRecoveryConfig] = Field(
        default=None, description="Optional replay remediation settings defined in faults.yaml"
    )


class PulseCheckSettings(BaseSettings):
    """Application runtime settings backed by environment variables."""

    pulsecheck_config: Optional[str] = Field(
        default=None,
        description="Path to custom dataset.yaml configuration file",
    )
    postgres_host: str = Field(default="localhost", description="PostgreSQL server hostname")
    postgres_port: int = Field(default=5432, description="PostgreSQL server port")
    postgres_db: str = Field(default="pulsecheck_db", description="PostgreSQL database name")
    postgres_user: str = Field(default="pulsecheck", description="PostgreSQL username")
    postgres_password: str = Field(default="pulsecheck_secret", description="PostgreSQL password")
    alert_webhook_url: Optional[str] = Field(
        default=None, description="Webhook endpoint URL for alert notifications"
    )
    pulsecheck_log_level: str = Field(default="INFO", description="Log level for PulseCheck")

    model_config = SettingsConfigDict(
        env_prefix="",
        case_sensitive=False,
        extra="ignore",
    )
