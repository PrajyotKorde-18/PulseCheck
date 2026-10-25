"""Unit tests for DDL generation and SQL type validation."""

import pytest

from pulsecheck.config.models import DatasetColumn, DatasetConfig
from pulsecheck.db.ddl import (
    generate_dataset_table_ddl,
    get_core_tables_ddl,
    validate_sql_type,
)

CORE_TABLE_STATEMENT_COUNT = 8
DATASET_STATEMENT_COUNT = 2


def test_validate_sql_type_valid() -> None:
    """Verify standard PostgreSQL types are accepted and normalized."""
    assert validate_sql_type("VARCHAR(64)") == "VARCHAR(64)"
    assert validate_sql_type("integer") == "INTEGER"
    assert validate_sql_type("TIMESTAMPTZ") == "TIMESTAMPTZ"
    assert validate_sql_type("double precision") == "DOUBLE PRECISION"
    assert validate_sql_type("NUMERIC(12, 4)") == "NUMERIC(12, 4)"
    assert validate_sql_type("BOOLEAN") == "BOOLEAN"


def test_validate_sql_type_invalid() -> None:
    """Verify unsafe or disallowed types raise ValueError."""
    with pytest.raises(ValueError, match="Disallowed or unsupported SQL type"):
        validate_sql_type("UNKNOWN_TYPE")

    with pytest.raises(ValueError, match="Invalid or unsafe SQL type format"):
        validate_sql_type("VARCHAR(64); DROP TABLE trips; --")


def test_generate_dataset_table_ddl() -> None:
    """Verify DDL generated for dataset target table."""
    config = DatasetConfig(
        name="test_logistics",
        format="csv",
        table_name="trips",
        identity_roles=["id"],
        columns=[
            DatasetColumn(name="trip_id", role="id", sql_type="VARCHAR(64)", nullable=False),
            DatasetColumn(name="city", role="city", sql_type="VARCHAR(64)", nullable=False),
            DatasetColumn(
                name="fare", role="fare", sql_type="DOUBLE PRECISION", nullable=True
            ),
        ],
    )

    stmts = generate_dataset_table_ddl(config)
    assert len(stmts) == DATASET_STATEMENT_COUNT

    # Check query string representation
    table_ddl = stmts[0].as_string()
    assert "CREATE TABLE IF NOT EXISTS \"trips\"" in table_ddl
    assert "\"trip_id\" VARCHAR(64) NOT NULL" in table_ddl
    assert "\"record_key\" VARCHAR(64) NOT NULL" in table_ddl
    assert "\"_loaded_at\" TIMESTAMPTZ NOT NULL DEFAULT NOW()" in table_ddl
    assert "CONSTRAINT \"trips_pk\" PRIMARY KEY (\"trip_id\")" in table_ddl


def test_get_core_tables_ddl() -> None:
    """Verify core tables DDL statements include all 4 tables and indexes."""
    stmts = get_core_tables_ddl()
    assert len(stmts) >= CORE_TABLE_STATEMENT_COUNT

    all_ddl = " ".join(s.as_string() for s in stmts)
    assert "batch_registry" in all_ddl
    assert "batch_metrics" in all_ddl
    assert "quarantine" in all_ddl
    assert "alerts" in all_ddl
    assert "USING GIN (reasons)" in all_ddl
