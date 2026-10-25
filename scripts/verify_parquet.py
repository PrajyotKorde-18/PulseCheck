"""Verification script for Parquet reading using pyarrow."""

from pathlib import Path

import pyarrow as pa
import pyarrow.parquet as pq


def main() -> None:
    data = {
        "trip_id": ["T1", "T2", "T3"],
        "city": ["Mumbai", "Bengaluru", "Delhi"],
        "fare": [100.5, 250.0, 75.25],
    }
    table = pa.Table.from_pydict(data)
    test_file = Path("reports/test_3line.parquet")
    test_file.parent.mkdir(parents=True, exist_ok=True)
    pq.write_table(table, test_file)

    # Read back in batches using pyarrow ParquetFile
    parquet_file = pq.ParquetFile(test_file)
    batch_count = 0
    total_rows = 0
    batch_size = 2
    for batch in parquet_file.iter_batches(batch_size=batch_size):
        df = batch.to_pandas()
        batch_count += 1
        total_rows += len(df)
        print(f"Batch {batch_count} rows: {len(df)}")
        print(df)

    print(
        f"Successfully read {total_rows} rows across {batch_count} "
        f"batches using pyarrow {pa.__version__}"
    )
    parquet_file.close()
    if test_file.exists():
        test_file.unlink()

if __name__ == "__main__":
    main()
