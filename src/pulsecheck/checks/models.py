"""Data models and structures for batch-level checks and alert management."""

from datetime import date, datetime
from typing import Any, Dict, List, Literal, Optional

from pydantic import BaseModel, ConfigDict, Field


class AlertRecord(BaseModel):
    """Represents an active or historical alert in the system."""

    model_config = ConfigDict(strict=True)

    fingerprint: str = Field(
        ..., description="Deterministic unique identifier: check:scope:severity"
    )
    check_name: str = Field(..., description="Name of the batch check raising this alert")
    scope: Optional[str] = Field(default=None, description="Operational scope (e.g. city) or None")
    severity: Literal["INFO", "LOW", "MEDIUM", "WARNING", "HIGH", "CRITICAL"] = Field(
        ..., description="Severity level"
    )
    status: Literal["active", "auto_closed", "resolved", "superseded"] = Field(
        default="active", description="Alert lifecycle state"
    )
    message: str = Field(..., description="Human-readable explanation of anomaly")
    occurrence_count: int = Field(default=1, ge=1, description="Number of times condition occurred")
    healthy_batch_count: int = Field(
        default=0, ge=0, description="Consecutive healthy batches observed without this alert"
    )
    first_seen_at: datetime = Field(..., description="Timestamp of first occurrence")
    last_seen_at: datetime = Field(..., description="Timestamp of most recent occurrence")
    last_notified_at: datetime = Field(..., description="Timestamp when notification was last sent")
    resolved_at: Optional[datetime] = Field(default=None, description="Timestamp when alert closed")
    details: Dict[str, Any] = Field(default_factory=dict, description="Metadata and metrics")


class BatchMetricRecord(BaseModel):
    """Represents a row from batch_metrics for historical comparison."""

    model_config = ConfigDict(strict=True)

    batch_id: str
    source: str
    scope: Optional[str]
    logical_date: date
    rows_received: int
    rows_loaded: int
    rows_quarantined: int
    rows_duplicate: int
    latest_event_time: Optional[datetime]
    created_at: datetime


class CheckFinding(BaseModel):
    """Internal representation of a check's evaluation before alert manager dispatch."""

    model_config = ConfigDict(strict=True)

    check_name: str
    scope: Optional[str]
    severity: Literal["INFO", "LOW", "MEDIUM", "WARNING", "HIGH", "CRITICAL"]
    message: str
    passed: bool
    details: Dict[str, Any] = Field(default_factory=dict)


class BatchCheckReport(BaseModel):
    """Comprehensive summary of all batch-level checks executed for a batch."""

    model_config = ConfigDict(strict=True)

    batch_id: str
    source: str
    logical_date: date
    all_passed: bool
    findings: List[CheckFinding]
    active_alerts: List[AlertRecord]
    notified_alerts: List[AlertRecord]
    suppressed_alerts: List[AlertRecord]
    auto_closed_alerts: List[AlertRecord]
