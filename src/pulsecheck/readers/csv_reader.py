"""CSV file reader implementation."""

from pathlib import Path
from typing import Iterator, Optional

import pandas as pd

from pulsecheck.config.models import CSVOptions
from pulsecheck.readers.base import FileReader

DEFAULT_DELIMITER = ","
DEFAULT_HEADER_ROW = 0


class CSVReader(FileReader):
    """Chunked and full reader for delimited CSV files."""

    def __init__(self, options: Optional[CSVOptions] = None) -> None:
        self.delimiter = options.delimiter if options else DEFAULT_DELIMITER
        self.header = DEFAULT_HEADER_ROW if (not options or options.has_header) else None

    def read_chunks(self, path: Path, chunk_size: int) -> Iterator[pd.DataFrame]:
        """Read CSV in chunks of chunk_size rows."""
        chunks = pd.read_csv(
            path,
            sep=self.delimiter,
            header=self.header,
            chunksize=chunk_size,
            dtype=object,
            keep_default_na=False,
        )
        for chunk in chunks:
            yield chunk

    def read_all(self, path: Path) -> pd.DataFrame:
        """Read full CSV into DataFrame."""
        return pd.read_csv(
            path,
            sep=self.delimiter,
            header=self.header,
            dtype=object,
            keep_default_na=False,
        )
