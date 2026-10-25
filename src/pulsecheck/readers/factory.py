"""Reader factory for dataset file formats."""

from pulsecheck.config.models import DatasetConfig
from pulsecheck.readers.base import FileReader
from pulsecheck.readers.csv_reader import CSVReader
from pulsecheck.readers.parquet_reader import ParquetReader


def get_reader(dataset_config: DatasetConfig) -> FileReader:
    """Return reader adapter appropriate for configured format."""
    if dataset_config.format == "csv":
        return CSVReader(options=dataset_config.csv_options)
    elif dataset_config.format == "parquet":
        return ParquetReader()
    raise ValueError(f"Unsupported dataset format: {dataset_config.format}")
