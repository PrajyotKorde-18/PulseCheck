# NYC TLC Yellow Taxi Run Report (January 2024)

## 1. Executive Summary & Methodology
- **Dataset**: NYC TLC Yellow Taxi Trip Records (`yellow_tripdata_2024-01.parquet`)
- **Official Source URL**: `https://www.nyc.gov/site/tlc/about/tlc-trip-record-data.page`
- **Data Dictionary**: `https://www.nyc.gov/assets/tlc/downloads/pdf/data_dictionary_trip_records_yellow.pdf`
- **Terms of Use**: City of New York Open Data Terms of Use (`nyc.gov/home/terms-of-use.page`). Free public access provided for research, analysis, and application development without FOIL requests. Data is captured by third-party technology service providers (TPEP) and published by NYC TLC without warranty as to accuracy or completeness.
- **Zero-Code Execution**: Executed via configuration only (`config/tlc/dataset.yaml`, `rules.yaml`, `checks.yaml`). No rules were hard-coded.
- **Execution Mode**: In-memory chunked streaming validation (`chunk_size: 50,000`), daily chronological partition replays (`2024-01-01` to `2024-01-31`).

> [!IMPORTANT]
> **Quarantine Rate Disclaimer**: There is no ground truth for real-world taxi operations. A quarantine rate is **NOT** an error rate. Quarantined records represent rows that violate configured contractual data quality limits or operational bounds (e.g. meter left running for >4 hours, implied speed >80 mph, negative trip durations).

## 2. Ingestion Statistics
- **Total Rows Received (In-Month)**: 2,964,606
- **Total Rows Loaded (Valid)**: 2,961,265
- **Total Rows Quarantined**: 3,341
- **Overall Quarantine Rate**: **0.1127%**
- **Total Out-of-Month Anomaly Rows**: 18

### Overall Quarantine Reasons Breakdown
| Reason Code | Rule Description | Violations | Share of Quarantined |
|---|---|---|---|
| `ERR_MAX_DURATION_EXCEEDED` | ERR_MAX_DURATION_EXCEEDED | 1,878 | 56.21% |
| `ERR_SPEED_EXCEEDED` | ERR_SPEED_EXCEEDED | 1,107 | 33.13% |
| `ERR_DUPLICATE_KEY` | ERR_DUPLICATE_KEY | 301 | 9.01% |
| `ERR_INVALID_CHRONOLOGY` | ERR_INVALID_CHRONOLOGY | 56 | 1.68% |

## 3. Real-Data Quirks & Out-of-Month Anomalies
The raw TLC download contained rows with pickup timestamps falling outside January 2024. These were isolated rather than silently dropped:
| Anomaly Partition | Row Count | Sample Pickup Timestamp | Probable Root Cause |
|---|---|---|---|
| `out_of_month_2002-12-31.parquet` | 2 | `2002-12-31 22:59:39` | Taximeter clock reset to epoch / corrupt hardware clock |
| `out_of_month_2009-01-01.parquet` | 3 | `2009-01-01 23:58:40` | Taximeter clock reset to epoch / corrupt hardware clock |
| `out_of_month_2023-12-31.parquet` | 10 | `2023-12-31 23:56:46` | Trip spanning midnight across month boundary |
| `out_of_month_2024-02-01.parquet` | 3 | `2024-02-01 00:00:39` | Trip spanning midnight across month boundary |

## 4. Daily Batch Execution History (31 Days)
| Logical Date | Received | Loaded | Quarantined | Quarantine Rate | Top Reason Code |
|---|---|---|---|---|---|
| `2024-01-01` | 81,013 | 80,869 | 144 | 0.178% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-02` | 75,519 | 75,426 | 93 | 0.123% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-03` | 82,427 | 82,328 | 99 | 0.120% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-04` | 102,901 | 102,783 | 118 | 0.115% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-05` | 103,178 | 103,072 | 106 | 0.103% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-06` | 97,117 | 96,994 | 123 | 0.127% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-07` | 67,543 | 67,445 | 98 | 0.145% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-08` | 80,034 | 79,933 | 101 | 0.126% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-09` | 93,962 | 93,887 | 75 | 0.080% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-10` | 95,000 | 94,914 | 86 | 0.091% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-11` | 105,010 | 104,897 | 113 | 0.108% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-12` | 103,655 | 103,546 | 109 | 0.105% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-13` | 104,758 | 104,633 | 125 | 0.119% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-14` | 94,420 | 94,285 | 135 | 0.143% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-15` | 77,033 | 76,936 | 97 | 0.126% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-16` | 93,057 | 92,944 | 113 | 0.121% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-17` | 110,365 | 110,255 | 110 | 0.100% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-18` | 110,358 | 110,255 | 103 | 0.093% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-19` | 95,951 | 95,855 | 96 | 0.100% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-20` | 108,768 | 108,649 | 119 | 0.109% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-21` | 84,502 | 84,379 | 123 | 0.146% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-22` | 85,650 | 85,544 | 106 | 0.124% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-23` | 99,891 | 99,793 | 98 | 0.098% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-24` | 105,120 | 105,010 | 110 | 0.105% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-25` | 110,318 | 110,216 | 102 | 0.092% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-26` | 105,804 | 105,695 | 109 | 0.103% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-27` | 110,515 | 110,393 | 122 | 0.110% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-28` | 92,837 | 92,732 | 105 | 0.113% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-29` | 84,547 | 84,441 | 106 | 0.125% | `ERR_SPEED_EXCEEDED` |
| `2024-01-30` | 101,233 | 101,135 | 98 | 0.097% | `ERR_MAX_DURATION_EXCEEDED` |
| `2024-01-31` | 102,120 | 102,021 | 99 | 0.097% | `ERR_MAX_DURATION_EXCEEDED` |

## 5. Seeded Random Samples for Manual Audit (Seed: 42)
### Sample Quarantined Records
```json
[
  {
    "record_key": "95ed4e012e5a02ba...",
    "scope_PULocationID": "229",
    "reasons": [
      "ERR_SPEED_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-28 17:22:27",
      "tpep_dropoff_datetime": "2024-01-28 17:23:07",
      "trip_distance": 15.8,
      "fare_amount": 53.0
    }
  },
  {
    "record_key": "23434f78480067a3...",
    "scope_PULocationID": "100",
    "reasons": [
      "ERR_SPEED_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-05 16:29:52",
      "tpep_dropoff_datetime": "2024-01-05 16:30:39",
      "trip_distance": 2.3,
      "fare_amount": 3.0
    }
  },
  {
    "record_key": "80b54a107c22959c...",
    "scope_PULocationID": "43",
    "reasons": [
      "ERR_MAX_DURATION_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-02 12:04:59",
      "tpep_dropoff_datetime": "2024-01-03 11:42:32",
      "trip_distance": 1.96,
      "fare_amount": 15.6
    }
  },
  {
    "record_key": "df69b940eb39a767...",
    "scope_PULocationID": "238",
    "reasons": [
      "ERR_SPEED_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-12 05:16:00",
      "tpep_dropoff_datetime": "2024-01-12 05:26:00",
      "trip_distance": 51619.36,
      "fare_amount": 16.17
    }
  },
  {
    "record_key": "01b99c0c19d92886...",
    "scope_PULocationID": "170",
    "reasons": [
      "ERR_SPEED_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-11 23:30:35",
      "tpep_dropoff_datetime": "2024-01-11 23:31:05",
      "trip_distance": 3.8,
      "fare_amount": 87.0
    }
  },
  {
    "record_key": "70687197e3ec1958...",
    "scope_PULocationID": "68",
    "reasons": [
      "ERR_MAX_DURATION_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-10 20:50:50",
      "tpep_dropoff_datetime": "2024-01-11 19:28:16",
      "trip_distance": 2.64,
      "fare_amount": 21.2
    }
  },
  {
    "record_key": "3c50c3d0688f3bce...",
    "scope_PULocationID": "148",
    "reasons": [
      "ERR_MAX_DURATION_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-06 01:34:45",
      "tpep_dropoff_datetime": "2024-01-07 01:25:16",
      "trip_distance": 0.73,
      "fare_amount": 7.9
    }
  },
  {
    "record_key": "babf6d1248f08c26...",
    "scope_PULocationID": "264",
    "reasons": [
      "ERR_SPEED_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-05 19:01:09",
      "tpep_dropoff_datetime": "2024-01-05 19:01:12",
      "trip_distance": 3.0,
      "fare_amount": 21.2
    }
  },
  {
    "record_key": "80deb1ce99dd9197...",
    "scope_PULocationID": "145",
    "reasons": [
      "ERR_SPEED_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-29 12:14:55",
      "tpep_dropoff_datetime": "2024-01-29 12:15:05",
      "trip_distance": 10.2,
      "fare_amount": 3.0
    }
  },
  {
    "record_key": "257e8982cfd732d8...",
    "scope_PULocationID": "230",
    "reasons": [
      "ERR_SPEED_EXCEEDED"
    ],
    "raw_summary": {
      "tpep_pickup_datetime": "2024-01-24 02:28:43",
      "tpep_dropoff_datetime": "2024-01-24 02:29:20",
      "trip_distance": 4.0,
      "fare_amount": 3.0
    }
  }
]
```

### Sample Loaded Records
```json
[
  {
    "VendorID": 1,
    "tpep_pickup_datetime": "2024-01-03 00:12:37",
    "tpep_dropoff_datetime": "2024-01-03 00:33:34",
    "PULocationID": 138,
    "DOLocationID": 262,
    "trip_distance": 7.5,
    "fare_amount": 30.3
  },
  {
    "VendorID": 2,
    "tpep_pickup_datetime": "2024-01-19 00:02:27",
    "tpep_dropoff_datetime": "2024-01-19 00:04:59",
    "PULocationID": 158,
    "DOLocationID": 90,
    "trip_distance": 0.71,
    "fare_amount": 5.1
  },
  {
    "VendorID": 2,
    "tpep_pickup_datetime": "2024-01-14 00:01:39",
    "tpep_dropoff_datetime": "2024-01-14 00:10:58",
    "PULocationID": 237,
    "DOLocationID": 145,
    "trip_distance": 2.08,
    "fare_amount": 12.1
  },
  {
    "VendorID": 1,
    "tpep_pickup_datetime": "2024-01-02 00:00:15",
    "tpep_dropoff_datetime": "2024-01-02 00:17:36",
    "PULocationID": 138,
    "DOLocationID": 170,
    "trip_distance": 8.3,
    "fare_amount": 32.4
  },
  {
    "VendorID": 1,
    "tpep_pickup_datetime": "2024-01-01 00:03:00",
    "tpep_dropoff_datetime": "2024-01-01 00:09:36",
    "PULocationID": 140,
    "DOLocationID": 236,
    "trip_distance": 1.8,
    "fare_amount": 10.0
  },
  {
    "VendorID": 2,
    "tpep_pickup_datetime": "2024-01-31 00:01:39",
    "tpep_dropoff_datetime": "2024-01-31 00:17:57",
    "PULocationID": 148,
    "DOLocationID": 262,
    "trip_distance": 4.92,
    "fare_amount": 21.9
  },
  {
    "VendorID": 2,
    "tpep_pickup_datetime": "2024-01-07 00:01:28",
    "tpep_dropoff_datetime": "2024-01-07 00:12:50",
    "PULocationID": 68,
    "DOLocationID": 229,
    "trip_distance": 2.17,
    "fare_amount": 13.5
  },
  {
    "VendorID": 2,
    "tpep_pickup_datetime": "2024-01-08 00:01:06",
    "tpep_dropoff_datetime": "2024-01-08 00:06:03",
    "PULocationID": 132,
    "DOLocationID": 132,
    "trip_distance": 2.28,
    "fare_amount": 10.7
  },
  {
    "VendorID": 2,
    "tpep_pickup_datetime": "2024-01-17 00:25:47",
    "tpep_dropoff_datetime": "2024-01-17 00:49:32",
    "PULocationID": 138,
    "DOLocationID": 261,
    "trip_distance": 12.39,
    "fare_amount": 49.2
  },
  {
    "VendorID": 2,
    "tpep_pickup_datetime": "2024-01-20 00:01:17",
    "tpep_dropoff_datetime": "2024-01-20 00:15:02",
    "PULocationID": 237,
    "DOLocationID": 145,
    "trip_distance": 2.09,
    "fare_amount": 14.9
  }
]
```

## 6. Applied Rules & Skipped Rules Explanation
- **Rules Evaluated**:
  - `RULE_REQUIRED_VALUES`: Enforces non-nullability on physical non-nullable columns.
  - `RULE_TYPE_PARSING`: Casts timestamps and numeric types cleanly.
  - `RULE_CHRONOLOGY`: Drops trips where `tpep_dropoff_datetime < tpep_pickup_datetime`.
  - `RULE_MAX_DURATION`: Quarantines trips exceeding 14,400s (4 hours).
  - `RULE_MAX_SPEED`: Quarantines trips exceeding 80 mph (128.7 km/h) implied speed.
  - `RULE_DUPLICATE_KEY`: Flags duplicate composite records within each batch.
- **Rules Skipped (Logged Reasons)**:
  - `RULE_GEO_BOUNDS` & `RULE_SWAPPED_COORDINATES`: Skipped because NYC TLC taxi data uses discrete Taxi Zone IDs (`PULocationID`, `DOLocationID`) rather than continuous GPS latitude/longitude coordinates (`lat`, `lon`, `city` roles).
  - `RULE_PHONE_FORMAT`, `RULE_PINCODE_FORMAT`, `RULE_PLATE_FORMAT`: Skipped because PII and driver vehicle plates are stripped from public TLC trip records.
  - `RULE_CANCELLED_DELIVERY`: Skipped because trip cancellation is encoded via `payment_type` or rate code rather than a discrete `status` column.
