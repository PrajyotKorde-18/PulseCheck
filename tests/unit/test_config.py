"""Unit tests for configuration loading, Pydantic schemas, and error reporting."""

from pathlib import Path

import pytest
import yaml
from pydantic import ValidationError

from pulsecheck.config.loader import ConfigError, load_config
from pulsecheck.config.models import DatasetConfigFile


@pytest.mark.unit
def test_valid_config_loads_successfully() -> None:
    bundle = load_config("config")
    assert bundle.dataset_config.name == "logistics_trips"
    assert len(bundle.dataset_config.columns) > 0
    assert len(bundle.rules) > 0
    assert len(bundle.cities) > 0


@pytest.mark.unit
def test_config_error_on_missing_file(tmp_path: Path) -> None:
    with pytest.raises(ConfigError) as exc_info:
        load_config(str(tmp_path))
    assert "Configuration file not found" in str(exc_info.value)


@pytest.mark.unit
def test_config_error_on_unknown_key(tmp_path: Path) -> None:
    invalid_data = {
        "dataset": {
            "name": "test_dataset",
            "format": "csv",
            "table_name": "test_table",
            "identity_roles": ["id"],
            "columns": [
                {"name": "col1", "role": "id", "sql_type": "VARCHAR", "nullable": False}
            ],
            "UNEXPECTED_EXTRA_KEY": 12345,  # Forbidden by extra="forbid"
        }
    }
    with pytest.raises(ValidationError) as exc_info:
        DatasetConfigFile.model_validate(invalid_data)
    err_msg = str(exc_info.value).lower()
    assert "extra_forbidden" in err_msg or "extra" in err_msg


@pytest.mark.unit
def test_config_error_on_wrong_type() -> None:
    invalid_data = {
        "dataset": {
            "name": "test_dataset",
            "format": "csv",
            "table_name": "test_table",
            "identity_roles": ["id"],
            "chunk_size": "NOT_AN_INTEGER",  # Wrong type
            "columns": [
                {"name": "col1", "role": "id", "sql_type": "VARCHAR", "nullable": False}
            ],
        }
    }
    with pytest.raises(ValidationError) as exc_info:
        DatasetConfigFile.model_validate(invalid_data)
    assert "int" in str(exc_info.value).lower()


@pytest.mark.unit
def test_config_error_on_missing_mandatory_key() -> None:
    invalid_data = {
        "dataset": {
            # Missing "name" and "format"
            "table_name": "test_table",
            "identity_roles": ["id"],
            "columns": [
                {"name": "col1", "role": "id", "sql_type": "VARCHAR", "nullable": False}
            ],
        }
    }
    with pytest.raises(ValidationError) as exc_info:
        DatasetConfigFile.model_validate(invalid_data)
    assert "missing" in str(exc_info.value).lower()


@pytest.mark.unit
def test_config_error_when_identity_role_not_in_columns(tmp_path: Path) -> None:
    # Setup temporary config where identity role 'unmatched_role' is not in columns
    dataset_content = {
        "dataset": {
            "name": "test_dataset",
            "format": "csv",
            "table_name": "test_table",
            "identity_roles": ["non_existent_role"],
            "columns": [
                {"name": "col1", "role": "id", "sql_type": "VARCHAR", "nullable": False}
            ],
        }
    }
    rules_content = {"rules": []}
    checks_content = {"batch_checks": {}}

    with open(tmp_path / "dataset.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(dataset_content, f)
    with open(tmp_path / "rules.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(rules_content, f)
    with open(tmp_path / "checks.yaml", "w", encoding="utf-8") as f:
        yaml.safe_dump(checks_content, f)

    with pytest.raises(ConfigError) as exc_info:
        load_config(str(tmp_path))
    assert "non_existent_role" in str(exc_info.value)
