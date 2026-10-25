"""Split NYC TLC monthly raw parquet into daily batches by logical date with streaming chunks."""

from collections import defaultdict
from pathlib import Path

import pyarrow as pa
import pyarrow.compute as pc
import pyarrow.parquet as pq

RAW_FILE = Path("data/raw/yellow_tripdata_2024-01.parquet")
OUTPUT_DIR = Path("data/tlc_batches")
CHUNK_SIZE = 250000  # PyArrow batch chunking for low memory footprint


def main() -> None:
    OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
    parquet_file = pq.ParquetFile(RAW_FILE)
    total_file_rows = parquet_file.metadata.num_rows
    print(f"Reading raw file: {RAW_FILE}")
    print(f"Total raw rows: {total_file_rows:,}")
    print(f"Chunk size: {CHUNK_SIZE:,} rows")

    writers: dict[str, pq.ParquetWriter] = {}
    row_counts: dict[str, int] = defaultdict(int)
    schema = parquet_file.schema_arrow

    try:
        for batch_idx, batch in enumerate(parquet_file.iter_batches(batch_size=CHUNK_SIZE)):
            table = pa.Table.from_batches([batch])
            pickup_col = table["tpep_pickup_datetime"]
            pickup_dates = pc.cast(pickup_col, pa.date32())

            unique_dates = pc.unique(pickup_dates).to_pylist()
            for date_val in unique_dates:
                if date_val is None:
                    target_key = "null_date"
                else:
                    date_str = date_val.isoformat()
                    if date_str.startswith("2024-01"):
                        target_key = date_str
                    else:
                        target_key = f"out_of_month_{date_str}"

                mask = pc.equal(pickup_dates, date_val)
                slice_table = table.filter(mask)
                count = slice_table.num_rows
                if count == 0:
                    continue

                row_counts[target_key] += count

                if target_key not in writers:
                    out_path = OUTPUT_DIR / f"{target_key}.parquet"
                    writers[target_key] = pq.ParquetWriter(
                        out_path, schema=schema, compression="SNAPPY"
                    )
                writers[target_key].write_table(slice_table)

            processed = (batch_idx + 1) * CHUNK_SIZE
            print(f"Processed chunk {batch_idx + 1} ({processed:,} rows scanned)...")

    finally:
        for w in writers.values():
            w.close()

    print("\n=== DAILY BATCH PARTITIONING SUMMARY ===")
    in_month_total = 0
    out_of_month_total = 0
    for key in sorted(row_counts.keys()):
        count = row_counts[key]
        if key.startswith("2024-01"):
            in_month_total += count
            print(f"  {key}: {count:,} rows")
        else:
            out_of_month_total += count
            print(f"  [QUIRK / OUT-OF-MONTH] {key}: {count:,} rows")

    print(f"\nIn-month total (2024-01-01 to 2024-01-31): {in_month_total:,}")
    print(f"Out-of-month total: {out_of_month_total:,}")
    print(f"Sum matches raw rows: {in_month_total + out_of_month_total == total_file_rows}")


if __name__ == "__main__":
    main()
