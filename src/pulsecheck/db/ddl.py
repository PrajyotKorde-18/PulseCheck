"""DDL generation and database schema initialization."""

import re
from typing import List

from psycopg import Connection, sql

from pulsecheck.config.models import DatasetConfig

# Whitelist of allowable PostgreSQL types to ensure safe SQL DDL generation
ALLOWED_SQL_BASE_TYPES = {
    "VARCHAR",
    "CHAR",
    "TEXT",
    "INTEGER",
    "INT",
    "SMALLINT",
    "BIGINT",
    "DECIMAL",
    "NUMERIC",
    "REAL",
    "DOUBLE PRECISION",
    "FLOAT",
    "BOOLEAN",
    "BOOL",
    "DATE",
    "TIME",
    "TIMESTAMP",
    "TIMESTAMPTZ",
    "TIMESTAMP WITH TIME ZONE",
    "TIMESTAMP WITHOUT TIME ZONE",
    "JSON",
    "JSONB",
    "UUID",
}

TYPE_PATTERN = re.compile(r"^([A-Z0-9_\s]+)(?:\((\d+(?:\s*,\s*\d+)?)\))?$", re.IGNORECASE)


def validate_sql_type(raw_type: str) -> str:
    """Validate and normalize SQL data type against allowed PostgreSQL types."""
    cleaned = raw_type.strip().upper()
    match = TYPE_PATTERN.match(cleaned)
    if not match:
        raise ValueError(f"Invalid or unsafe SQL type format: {raw_type}")

    base_type = match.group(1).strip()
    param = match.group(2)

    if base_type not in ALLOWED_SQL_BASE_TYPES:
        raise ValueError(f"Disallowed or unsupported SQL type: {base_type}")

    if param:
        return f"{base_type}({param})"
    return base_type


def generate_dataset_table_ddl(dataset_config: DatasetConfig) -> List[sql.Composable]:
    """Generate composable DDL statements for the dataset target table."""
    col_defs: List[sql.Composable] = []

    # Target table columns from dataset config
    for col in dataset_config.columns:
        validated_type = validate_sql_type(col.sql_type)
        null_clause = sql.SQL("NOT NULL") if not col.nullable else sql.SQL("NULL")
        col_defs.append(
            sql.SQL("{} {} {}").format(
                sql.Identifier(col.name),
                sql.SQL(validated_type),
                null_clause,
            )
        )

    # Add standard PulseCheck tracking columns
    col_defs.append(
        sql.SQL("{} VARCHAR(64) NOT NULL").format(sql.Identifier("record_key"))
    )
    col_defs.append(
        sql.SQL("{} TIMESTAMPTZ NOT NULL DEFAULT NOW()").format(
            sql.Identifier("_loaded_at")
        )
    )

    # Determine unique/primary key constraints
    # If identity role corresponds to a single column, add unique constraint on it
    identity_cols: List[str] = []
    for role in dataset_config.identity_roles:
        matching_cols = dataset_config.get_role_to_columns_map().get(role, [])
        identity_cols.extend(matching_cols)

    table_name = dataset_config.table_name
    constraints: List[sql.Composable] = [
        sql.SQL("CONSTRAINT {} UNIQUE (record_key)").format(
            sql.Identifier(f"{table_name}_record_key_uniq")
        )
    ]

    if identity_cols and len(identity_cols) == 1:
        pk_col = identity_cols[0]
        constraints.append(
            sql.SQL("CONSTRAINT {} PRIMARY KEY ({})").format(
                sql.Identifier(f"{table_name}_pk"),
                sql.Identifier(pk_col),
            )
        )
    elif identity_cols:
        constraints.append(
            sql.SQL("CONSTRAINT {} UNIQUE ({})").format(
                sql.Identifier(f"{table_name}_identity_uniq"),
                sql.SQL(", ").join(sql.Identifier(c) for c in identity_cols),
            )
        )

    create_table_stmt = sql.SQL("CREATE TABLE IF NOT EXISTS {} ({})").format(
        sql.Identifier(table_name),
        sql.SQL(", ").join(col_defs + constraints),
    )

    create_index_stmt = sql.SQL(
        "CREATE INDEX IF NOT EXISTS {} ON {} (record_key)"
    ).format(
        sql.Identifier(f"idx_{table_name}_record_key"),
        sql.Identifier(table_name),
    )

    return [create_table_stmt, create_index_stmt]


def get_core_tables_ddl() -> List[sql.Composable]:
    """Generate DDL statements for batch_registry, batch_metrics, quarantine, and alerts."""
    statements: List[str] = [
        # Batch Registry
        """
        CREATE TABLE IF NOT EXISTS batch_registry (
            batch_id VARCHAR(64) PRIMARY KEY,
            source VARCHAR(64) NOT NULL,
            logical_date DATE NOT NULL,
            file_hash VARCHAR(64) NOT NULL,
            status VARCHAR(32) NOT NULL,
            started_at TIMESTAMPTZ NOT NULL,
            completed_at TIMESTAMPTZ,
            error_message TEXT
        );
        """,
        (
            "CREATE INDEX IF NOT EXISTS idx_batch_registry_source_date "
            "ON batch_registry (source, logical_date);"
        ),
        "CREATE INDEX IF NOT EXISTS idx_batch_registry_file_hash ON batch_registry (file_hash);",
        # Batch Metrics
        """
        CREATE TABLE IF NOT EXISTS batch_metrics (
            id BIGSERIAL PRIMARY KEY,
            batch_id VARCHAR(64) NOT NULL REFERENCES batch_registry(batch_id),
            source VARCHAR(64) NOT NULL,
            scope VARCHAR(64),
            rows_received INT NOT NULL,
            rows_loaded INT NOT NULL,
            rows_quarantined INT NOT NULL,
            rows_duplicate INT NOT NULL,
            latest_event_time TIMESTAMPTZ,
            duration_seconds DOUBLE PRECISION,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_batch_metrics_batch_id ON batch_metrics (batch_id);",
        (
            "CREATE INDEX IF NOT EXISTS idx_batch_metrics_source_scope "
            "ON batch_metrics (source, scope);"
        ),
        # Quarantine
        """
        CREATE TABLE IF NOT EXISTS quarantine (
            id BIGSERIAL PRIMARY KEY,
            batch_id VARCHAR(64) NOT NULL,
            source VARCHAR(64) NOT NULL,
            row_number INT NOT NULL,
            record_key VARCHAR(64) NOT NULL,
            raw_row JSONB NOT NULL,
            reasons JSONB NOT NULL,
            scope VARCHAR(64),
            status VARCHAR(32) NOT NULL DEFAULT 'open',
            attempts INT NOT NULL DEFAULT 0,
            created_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            updated_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_quarantine_reasons_gin ON quarantine USING GIN (reasons);",
        "CREATE INDEX IF NOT EXISTS idx_quarantine_record_key ON quarantine (record_key);",
        "CREATE INDEX IF NOT EXISTS idx_quarantine_batch_id ON quarantine (batch_id);",
        "CREATE INDEX IF NOT EXISTS idx_quarantine_status ON quarantine (status);",
        # Alerts
        """
        CREATE TABLE IF NOT EXISTS alerts (
            id BIGSERIAL PRIMARY KEY,
            fingerprint VARCHAR(64) UNIQUE NOT NULL,
            check_name VARCHAR(64) NOT NULL,
            scope VARCHAR(64),
            severity VARCHAR(32) NOT NULL,
            status VARCHAR(32) NOT NULL DEFAULT 'active',
            message TEXT NOT NULL,
            occurrence_count INT NOT NULL DEFAULT 1,
            healthy_batch_count INT NOT NULL DEFAULT 0,
            first_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_seen_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            last_notified_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
            resolved_at TIMESTAMPTZ
        );
        """,
        "CREATE INDEX IF NOT EXISTS idx_alerts_check_scope ON alerts (check_name, scope);",
        "CREATE INDEX IF NOT EXISTS idx_alerts_status ON alerts (status);",
        "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS status VARCHAR(32) NOT NULL DEFAULT 'active';",
        (
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS "
            "healthy_batch_count INT NOT NULL DEFAULT 0;"
        ),
        (
            "ALTER TABLE alerts ADD COLUMN IF NOT EXISTS "
            "last_notified_at TIMESTAMPTZ NOT NULL DEFAULT NOW();"
        ),
    ]
    return [sql.SQL(stmt.strip()) for stmt in statements]


def init_db(conn: Connection, dataset_config: DatasetConfig) -> None:
    """Initialize all core tables, indexes, and dataset table. Safe to run multiple times."""
    with conn.cursor() as cur:
        # Create core tables
        for stmt in get_core_tables_ddl():
            cur.execute(stmt)

        # Create dataset table
        for stmt in generate_dataset_table_ddl(dataset_config):
            cur.execute(stmt)

    conn.commit()
