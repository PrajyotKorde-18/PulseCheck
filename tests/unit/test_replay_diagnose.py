"""Unit tests for quarantine replay, diagnostics, and rejects export."""

from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional

import pandas as pd
import pytest

from pulsecheck.adapters.indpy_adapter import indpy_adapter
from pulsecheck.config.loader import ConfigBundle, load_config
from pulsecheck.config.models import (
    ChecksConfigFile,
    CitiesConfigFile,
    DatasetConfigFile,
    RuleConfig,
    RulesConfigFile,
)
from pulsecheck.diagnostics import QuarantineDiagnostics
from pulsecheck.export import QuarantineExporter, format_human_reasons
from pulsecheck.replay import QuarantineReplayer

TEST_BASE_TIME = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
CONCENTRATION_THRESHOLD = 0.80
COUNT_EIGHT = 8
COUNT_TWO = 2
COUNT_ONE = 1
COUNT_TEN = 10
FULL_PERCENTAGE = 100.0
RECORD_ID_ONE = 1
RECORD_ID_TWO = 2
EXPECTED_ZERO = 0


@pytest.fixture
def config_bundle() -> ConfigBundle:
    return load_config("config")


def make_test_quarantine_record(
    record_id: int,
    trip_id: str,
    source: str = "partner_a",
    scope: str = "Mumbai",
    logical_date: str = "2026-03-01",
    phone: str = "12345",
    pincode: str = "400001",
    status: str = "open",
    attempts: int = 0,
    reasons: Optional[List[Dict[str, Any]]] = None,
) -> Dict[str, Any]:
    """Helper creating a test quarantine record dictionary."""
    t_pickup = TEST_BASE_TIME
    t_delivery = t_pickup + timedelta(minutes=30)
    raw = {
        "trip_id": trip_id,
        "pickup_time": t_pickup.strftime("%Y-%m-%d %H:%M:%S%z"),
        "delivery_time": t_delivery.strftime("%Y-%m-%d %H:%M:%S%z"),
        "pickup_lat": 19.0760,
        "pickup_lon": 72.8777,
        "city": scope,
        "driver_phone": phone,
        "postal_code": pincode,
        "vehicle_plate": indpy_adapter.generate_plate(),
        "trip_status": "completed",
        "trip_distance_km": 10.0,
    }
    reasons_list = reasons or [
        {
            "code": "ERR_INVALID_PHONE",
            "rule": "RULE_PHONE_FORMAT",
            "fields": ["driver_phone"],
            "message": "Invalid mobile number",
        }
    ]
    return {
        "id": record_id,
        "batch_id": "BATCH_001",
        "source": source,
        "scope": scope,
        "row_number": record_id,
        "record_key": trip_id,
        "raw_row": raw,
        "reasons": reasons_list,
        "status": status,
        "attempts": attempts,
        "logical_date": logical_date,
    }


@pytest.mark.unit
def test_diagnostics_top_causes_and_concentration(config_bundle: ConfigBundle) -> None:
    """Verifies top cause sorting, percentage calculation, and concentration detection."""
    diagnostics = QuarantineDiagnostics(config_bundle)

    records: List[Dict[str, Any]] = []
    # 8 records from partner_a on 2026-03-01 with ERR_INVALID_PHONE
    for i in range(1, COUNT_EIGHT + 1):
        records.append(
            make_test_quarantine_record(
                record_id=i,
                trip_id=f"T-PHONE-{i:03d}",
                source="partner_a",
                logical_date="2026-03-01",
                phone="12345",
            )
        )
    # 2 records from partner_b on 2026-03-02 with ERR_INVALID_PHONE
    for i in range(COUNT_EIGHT + 1, COUNT_TEN + 1):
        records.append(
            make_test_quarantine_record(
                record_id=i,
                trip_id=f"T-PHONE-{i:03d}",
                source="partner_b",
                logical_date="2026-03-02",
                phone="12345",
            )
        )

    report = diagnostics.analyze_records(records)
    assert report.total_quarantined_records == COUNT_TEN
    assert len(report.reasons_summary) == 1
    assert report.reasons_summary[0].code == "ERR_INVALID_PHONE"
    assert report.reasons_summary[0].count == COUNT_TEN
    assert report.reasons_summary[0].share_percentage == FULL_PERCENTAGE

    # Source 'partner_a' has 8/10 = 80.0%, exactly at threshold
    # Add 1 more row for partner_a to strictly exceed 80%
    records.append(
        make_test_quarantine_record(
            record_id=11,
            trip_id="T-PHONE-011",
            source="partner_a",
            logical_date="2026-03-01",
            phone="12345",
        )
    )
    report_flagged = diagnostics.analyze_records(records)
    # 9/11 = 81.8% from partner_a -> triggers concentration alert
    flags = report_flagged.concentration_flags
    assert len(flags) >= 1
    source_flag = next((f for f in flags if f.dimension == "source"), None)
    assert source_flag is not None
    assert source_flag.dimension_value == "partner_a"
    assert source_flag.share_percentage > CONCENTRATION_THRESHOLD * 100.0


@pytest.mark.unit
def test_replay_with_loosened_rule_and_dry_run(config_bundle: ConfigBundle) -> None:
    """Verifies that loosening a rule allows affected rows to replay and dry-run mutates nothing."""
    replayer = QuarantineReplayer(config_bundle)

    # Record 1: Bad phone only
    rec1 = make_test_quarantine_record(
        record_id=RECORD_ID_ONE,
        trip_id="T-PHONE-ONLY",
        phone="12345",  # Invalid phone
        pincode="400001",  # Valid PIN
    )
    # Record 2: Bad phone AND bad PIN
    rec2 = make_test_quarantine_record(
        record_id=RECORD_ID_TWO,
        trip_id="T-MULTI-FAIL",
        phone="12345",  # Invalid phone
        pincode="99",  # Invalid PIN
        reasons=[
            {"code": "ERR_INVALID_PHONE", "rule": "RULE_PHONE_FORMAT", "message": "Bad phone"},
            {"code": "ERR_INVALID_PINCODE", "rule": "RULE_PINCODE_FORMAT", "message": "Bad pin"},
        ],
    )
    records = [rec1, rec2]

    # Under current strict config: 0 can replay, 2 still open
    summary_strict = replayer.replay_records(records, dry_run=True)
    assert summary_strict.total_examined == COUNT_TWO
    assert summary_strict.replayed_loaded == EXPECTED_ZERO
    assert summary_strict.still_open == COUNT_TWO

    # Loosen phone rule by disabling it in a modified config bundle
    modified_rules: List[RuleConfig] = []
    for r in config_bundle.rules:
        if r.rule_id == "RULE_PHONE_FORMAT":
            modified_rules.append(r.model_copy(update={"enabled": False}))
        else:
            modified_rules.append(r)

    loosened_bundle = ConfigBundle(
        dataset_file=DatasetConfigFile(dataset=config_bundle.dataset_config),
        rules_file=RulesConfigFile(rules=modified_rules),
        checks_file=ChecksConfigFile(batch_checks=config_bundle.checks),
        cities_file=CitiesConfigFile(cities=config_bundle.cities),
        settings=config_bundle.settings,
        config_dir=config_bundle.config_dir,
    )

    replayer_loosened = QuarantineReplayer(loosened_bundle)
    summary_loosened = replayer_loosened.replay_records(records, dry_run=True)
    # rec1 had only bad phone -> now valid!
    # rec2 had bad phone and bad PIN -> still open due to bad PIN!
    assert summary_loosened.total_examined == COUNT_TWO
    assert summary_loosened.replayed_loaded == COUNT_ONE
    assert summary_loosened.still_open == COUNT_ONE
    assert RECORD_ID_ONE in summary_loosened.replayed_ids
    assert RECORD_ID_TWO in summary_loosened.still_open_ids

    # Check refreshed reasons for rec2: bad phone is gone, bad PIN remains
    rec2_refreshed = summary_loosened.refreshed_reasons[RECORD_ID_TWO]
    codes = [r["code"] for r in rec2_refreshed]
    assert "ERR_INVALID_PINCODE" in codes
    assert "ERR_INVALID_PHONE" not in codes


@pytest.mark.unit
def test_export_rejects_to_csv(config_bundle: ConfigBundle, tmp_path: Path) -> None:
    """Verifies that export-rejects writes CSV with original values and human reasons."""
    exporter = QuarantineExporter(config_bundle)
    out_file = tmp_path / "rejects.csv"

    records = [
        make_test_quarantine_record(
            record_id=1,
            trip_id="T-EXPORT-1",
            phone="12345",
            reasons=[
                {
                    "code": "ERR_INVALID_PHONE",
                    "rule": "RULE_PHONE_FORMAT",
                    "message": "Invalid mobile number",
                }
            ],
        ),
        make_test_quarantine_record(
            record_id=2,
            trip_id="T-EXPORT-2",
            phone="99999",
            reasons=[
                {
                    "code": "ERR_INVALID_PHONE",
                    "rule": "RULE_PHONE_FORMAT",
                    "message": "Invalid mobile number",
                },
                {
                    "code": "ERR_INVALID_CHRONOLOGY",
                    "rule": "RULE_CHRONOLOGY",
                    "message": "Delivery before pickup",
                },
            ],
        ),
    ]

    saved_path = exporter.export_records_to_csv(records, out_file)
    assert saved_path.exists()

    df = pd.read_csv(saved_path)
    assert len(df) == COUNT_TWO
    assert "trip_id" in df.columns
    assert "quarantine_id" in df.columns
    assert "quarantine_reason" in df.columns

    row1 = df[df["trip_id"] == "T-EXPORT-1"].iloc[0]
    assert "ERR_INVALID_PHONE" in str(row1["quarantine_reason"])

    row2 = df[df["trip_id"] == "T-EXPORT-2"].iloc[0]
    reason_str = str(row2["quarantine_reason"])
    assert "ERR_INVALID_PHONE" in reason_str
    assert "ERR_INVALID_CHRONOLOGY" in reason_str


@pytest.mark.unit
def test_format_human_reasons_handles_edge_cases() -> None:
    """Verifies format_human_reasons helper for various inputs."""
    assert format_human_reasons([]) == "UNKNOWN_ERROR"
    assert format_human_reasons(None) == "UNKNOWN_ERROR"
    single = [{"code": "ERR_01", "message": "Failed"}]
    assert format_human_reasons(single) == "ERR_01: Failed"
    as_json = '[{"code": "ERR_02", "message": "Malformed"}]'
    assert format_human_reasons(as_json) == "ERR_02: Malformed"
