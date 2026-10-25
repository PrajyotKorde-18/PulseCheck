"""Batch-level schema check enforcing required column presence and unknown column policies."""

import logging
from typing import Any, List, Literal, Sequence

from pydantic import BaseModel, ConfigDict

from pulsecheck.config.models import DatasetConfig

logger = logging.getLogger(__name__)


class SchemaValidationError(Exception):
    """Raised when an incoming batch violates required schema rules."""

    pass


class SchemaCheckResult(BaseModel):
    """Outcome of checking an incoming batch's column structure."""

    model_config = ConfigDict(strict=True)

    is_valid: bool
    status: Literal["passed", "rejected_schema", "has_warnings"]
    missing_required_columns: List[str]
    missing_optional_columns: List[str]
    unknown_columns: List[str]
    messages: List[str]


def check_batch_schema(
    actual_columns: Sequence[str],
    dataset_config: DatasetConfig,
) -> SchemaCheckResult:
    """
    Compare incoming file columns against configured dataset schema.

    Rules:
    1. If any required (non-nullable) column is missing -> rejected_schema, is_valid=False.
    2. Unknown columns are handled according to dataset_config.unknown_columns_policy:
       - 'reject': batch rejected_schema, is_valid=False.
       - 'warn': logged with warning, is_valid=True (unless required columns also missing).
       - 'ignore': ignored, is_valid=True.
    """
    actual_set = set(actual_columns)
    configured_cols = {col.name: col for col in dataset_config.columns}

    missing_required: List[str] = []
    missing_optional: List[str] = []

    for name, col in configured_cols.items():
        if name not in actual_set:
            if not col.nullable:
                missing_required.append(name)
            else:
                missing_optional.append(name)

    unknown_columns: List[str] = [c for c in actual_columns if c not in configured_cols]
    messages: List[str] = []
    policy = dataset_config.unknown_columns_policy

    if missing_required:
        msg = f"Batch rejected: missing required column(s): {sorted(missing_required)}"
        logger.error(msg)
        messages.append(msg)
        return SchemaCheckResult(
            is_valid=False,
            status="rejected_schema",
            missing_required_columns=sorted(missing_required),
            missing_optional_columns=sorted(missing_optional),
            unknown_columns=sorted(unknown_columns),
            messages=messages,
        )

    if unknown_columns:
        if policy == "reject":
            msg = (
                f"Batch rejected: unknown column(s) detected with 'reject' policy: "
                f"{sorted(unknown_columns)}"
            )
            logger.error(msg)
            messages.append(msg)
            return SchemaCheckResult(
                is_valid=False,
                status="rejected_schema",
                missing_required_columns=[],
                missing_optional_columns=sorted(missing_optional),
                unknown_columns=sorted(unknown_columns),
                messages=messages,
            )
        elif policy == "warn":
            msg = f"Unknown column(s) encountered (policy=warn): {sorted(unknown_columns)}"
            logger.warning(msg)
            messages.append(msg)
            return SchemaCheckResult(
                is_valid=True,
                status="has_warnings",
                missing_required_columns=[],
                missing_optional_columns=sorted(missing_optional),
                unknown_columns=sorted(unknown_columns),
                messages=messages,
            )
        else:  # policy == "ignore"
            msg = f"Unknown column(s) ignored (policy=ignore): {sorted(unknown_columns)}"
            logger.info(msg)
            messages.append(msg)

    if missing_optional:
        msg = f"Optional column(s) missing from file: {sorted(missing_optional)}"
        logger.info(msg)
        messages.append(msg)

    return SchemaCheckResult(
        is_valid=True,
        status="passed",
        missing_required_columns=[],
        missing_optional_columns=sorted(missing_optional),
        unknown_columns=sorted(unknown_columns),
        messages=messages,
    )


def check_schema(df: Sequence[str] | Any, dataset_config: DatasetConfig) -> SchemaCheckResult:
    """Validate DataFrame or column sequence against dataset schema, raising on error."""
    columns = list(df.columns) if hasattr(df, "columns") else list(df)
    result = check_batch_schema(columns, dataset_config)
    if not result.is_valid:
        raise SchemaValidationError("; ".join(result.messages))
    return result

