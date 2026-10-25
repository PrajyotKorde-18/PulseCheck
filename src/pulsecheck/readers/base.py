"""File reader interface protocol."""

from pathlib import Path
from typing import Iterator, Protocol

import pandas as pd


class FileReader(Protocol):
    """Protocol for reading dataset files in batches or all at once."""

    def read_chunks(self, path: Path, chunk_size: int) -> Iterator[pd.DataFrame]:
        """Yield DataFrames of up to chunk_size rows."""
        ...

    def read_all(self, path: Path) -> pd.DataFrame:
        """Read the entire file into a single DataFrame."""
        ...
