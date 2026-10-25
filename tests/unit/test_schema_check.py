"""Unit tests for batch-level schema checking and unknown column policies."""

import pytest

from pulsecheck.config.models import DatasetColumn, DatasetConfig
from pulsecheck.schema_check import check_batch_schema


@pytest.fixture
def sample_dataset_config() -> DatasetConfig:
    return DatasetConfig(
        name="test_trips",
        format="csv",
        table_name="trips",
        identity_roles=["id"],
        unknown_columns_policy="warn",
        conflict_policy="skip",
        chunk_size=1000,
        columns=[
            DatasetColumn(name="trip_id", role="id", sql_type="VARCHAR", nullable=False),
            DatasetColumn(name="city", role="city", sql_type="VARCHAR", nullable=False),
            DatasetColumn(name="notes", role="notes", sql_type="TEXT", nullable=True),
        ],
    )


@pytest.mark.unit
def test_schema_passes_when_all_required_columns_present(
    sample_dataset_config: DatasetConfig,
) -> None:
    cols = ["trip_id", "city", "notes"]
    res = check_batch_schema(cols, sample_dataset_config)
    assert res.is_valid is True
    assert res.status == "passed"
    assert len(res.missing_required_columns) == 0


@pytest.mark.unit
def test_schema_rejects_when_required_column_is_missing(
    sample_dataset_config: DatasetConfig,
) -> None:
    cols = ["city", "notes"]  # trip_id is missing and required
    res = check_batch_schema(cols, sample_dataset_config)
    assert res.is_valid is False
    assert res.status == "rejected_schema"
    assert "trip_id" in res.missing_required_columns


@pytest.mark.unit
def test_schema_passes_when_optional_column_is_missing(
    sample_dataset_config: DatasetConfig,
) -> None:
    cols = ["trip_id", "city"]  # notes is missing but optional
    res = check_batch_schema(cols, sample_dataset_config)
    assert res.is_valid is True
    assert res.status == "passed"
    assert "notes" in res.missing_optional_columns


@pytest.mark.unit
def test_schema_unknown_columns_policy_reject(sample_dataset_config: DatasetConfig) -> None:
    config = sample_dataset_config.model_copy(update={"unknown_columns_policy": "reject"})
    cols = ["trip_id", "city", "notes", "SURPRISE_COL"]
    res = check_batch_schema(cols, config)
    assert res.is_valid is False
    assert res.status == "rejected_schema"
    assert "SURPRISE_COL" in res.unknown_columns


@pytest.mark.unit
def test_schema_unknown_columns_policy_warn(sample_dataset_config: DatasetConfig) -> None:
    config = sample_dataset_config.model_copy(update={"unknown_columns_policy": "warn"})
    cols = ["trip_id", "city", "notes", "SURPRISE_COL"]
    res = check_batch_schema(cols, config)
    assert res.is_valid is True
    assert res.status == "has_warnings"
    assert "SURPRISE_COL" in res.unknown_columns


@pytest.mark.unit
def test_schema_unknown_columns_policy_ignore(sample_dataset_config: DatasetConfig) -> None:
    config = sample_dataset_config.model_copy(update={"unknown_columns_policy": "ignore"})
    cols = ["trip_id", "city", "notes", "SURPRISE_COL"]
    res = check_batch_schema(cols, config)
    assert res.is_valid is True
    assert res.status == "passed"
    assert "SURPRISE_COL" in res.unknown_columns
