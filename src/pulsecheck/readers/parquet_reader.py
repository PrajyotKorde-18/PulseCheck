"""Parquet file reader implementation using pyarrow."""

from pathlib import Path
from typing import Iterator

import pandas as pd
import pyarrow.parquet as pq

from pulsecheck.readers.base import FileReader


class ParquetReader(FileReader):
    """Chunked and full reader for Apache Parquet files using pyarrow."""

    def read_chunks(self, path: Path, chunk_size: int) -> Iterator[pd.DataFrame]:
        """Read Parquet file in batches of chunk_size rows."""
        parquet_file = pq.ParquetFile(path)
        try:
            for batch in parquet_file.iter_batches(batch_size=chunk_size):
                df = batch.to_pandas()
                yield df
        finally:
            parquet_file.close()

    def read_all(self, path: Path) -> pd.DataFrame:
        """Read complete Parquet file into a single DataFrame."""
        table = pq.read_table(path)
        return table.to_pandas()
