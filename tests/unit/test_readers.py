"""Unit tests for CSV and Parquet file reader adapters."""

from pathlib import Path

import pandas as pd
import pytest
from pandas.testing import assert_frame_equal

from pulsecheck.config.models import CSVOptions, DatasetColumn, DatasetConfig
from pulsecheck.readers.csv_reader import CSVReader
from pulsecheck.readers.factory import get_reader
from pulsecheck.readers.parquet_reader import ParquetReader

SAMPLE_ROW_COUNT = 5
CHUNK_SIZE_TWO = 2
CHUNK_SIZE_ONE = 1
EXPECTED_BATCH_COUNT = 3


@pytest.fixture
def sample_data() -> pd.DataFrame:
    """Sample dataframe for reader verification."""
    return pd.DataFrame(
        {
            "trip_id": ["T1", "T2", "T3", "T4", "T5"],
            "city": ["Mumbai", "Bengaluru", "Delhi", "Mumbai", "Pune"],
            "fare": [150.0, 320.5, 95.0, 410.0, 220.75],
        }
    )


@pytest.fixture
def sample_csv(tmp_path: Path, sample_data: pd.DataFrame) -> Path:
    """Write sample dataframe to CSV file."""
    csv_file = tmp_path / "trips.csv"
    sample_data.to_csv(csv_file, index=False)
    return csv_file


@pytest.fixture
def sample_parquet(tmp_path: Path, sample_data: pd.DataFrame) -> Path:
    """Write sample dataframe to Parquet file."""
    parquet_file = tmp_path / "trips.parquet"
    sample_data.to_parquet(parquet_file, index=False)
    return parquet_file


def test_csv_reader_chunked(sample_csv: Path) -> None:
    """Verify CSVReader yields correct chunk sizes and complete rows."""
    reader = CSVReader(CSVOptions(delimiter=",", has_header=True))
    chunks = list(reader.read_chunks(sample_csv, chunk_size=CHUNK_SIZE_TWO))

    assert len(chunks) == EXPECTED_BATCH_COUNT
    assert len(chunks[0]) == CHUNK_SIZE_TWO
    assert len(chunks[1]) == CHUNK_SIZE_TWO
    assert len(chunks[2]) == CHUNK_SIZE_ONE

    combined = pd.concat(chunks, ignore_index=True)
    all_rows = reader.read_all(sample_csv)
    assert len(combined) == SAMPLE_ROW_COUNT
    assert len(all_rows) == SAMPLE_ROW_COUNT


def test_parquet_reader_chunked(sample_parquet: Path) -> None:
    """Verify ParquetReader yields correct chunk sizes using pyarrow batches."""
    reader = ParquetReader()
    chunks = list(reader.read_chunks(sample_parquet, chunk_size=CHUNK_SIZE_TWO))

    assert len(chunks) == EXPECTED_BATCH_COUNT
    assert len(chunks[0]) == CHUNK_SIZE_TWO
    assert len(chunks[1]) == CHUNK_SIZE_TWO
    assert len(chunks[2]) == CHUNK_SIZE_ONE

    all_rows = reader.read_all(sample_parquet)
    assert len(all_rows) == SAMPLE_ROW_COUNT


def test_csv_and_parquet_give_identical_results(
    sample_csv: Path, sample_parquet: Path, sample_data: pd.DataFrame
) -> None:
    """Verify CSV and Parquet readers produce equivalent dataframes."""
    csv_reader = CSVReader()
    parquet_reader = ParquetReader()

    df_csv = csv_reader.read_all(sample_csv)
    df_parquet = parquet_reader.read_all(sample_parquet)

    # Cast CSV string column types to match parquet types for value equality check
    df_csv["fare"] = df_csv["fare"].astype(float)
    assert_frame_equal(df_csv, df_parquet)


def test_reader_factory(tmp_path: Path) -> None:
    """Verify get_reader returns proper reader instance based on config."""
    cols = [
        DatasetColumn(name="id", role="id", sql_type="VARCHAR", nullable=False),
    ]
    csv_cfg = DatasetConfig(
        name="test_csv",
        format="csv",
        table_name="test_table",
        identity_roles=["id"],
        columns=cols,
    )
    parquet_cfg = DatasetConfig(
        name="test_pq",
        format="parquet",
        table_name="test_table",
        identity_roles=["id"],
        columns=cols,
    )

    assert isinstance(get_reader(csv_cfg), CSVReader)
    assert isinstance(get_reader(parquet_cfg), ParquetReader)
