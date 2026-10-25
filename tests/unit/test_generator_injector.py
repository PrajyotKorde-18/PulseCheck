"""Unit tests for synthetic data generator and fault injector."""

import json
import random
from datetime import date, datetime, timezone
from pathlib import Path

import pandas as pd
import pytest

from pulsecheck.adapters.indpy_adapter import indpy_adapter
from pulsecheck.clock import FrozenClock
from pulsecheck.config.loader import ConfigBundle, load_config
from pulsecheck.config.models import FaultConfig
from pulsecheck.faults.injector import FaultInjector
from pulsecheck.faults.registry import fault_registry
from pulsecheck.generator.engine import GenerationParams, SyntheticTripGenerator
from pulsecheck.validator import Validator

TEST_SEED_PRIMARY = 42
TEST_SEED_SECONDARY = 999
TEST_DAYS_COUNT = 2
TEST_TRIPS_PER_CITY = 30
EXPECTED_CLEAN_QUARANTINE_COUNT = 0
ROW_RATE_TOLERANCE_MARGIN = 0.05
EXPECTED_INJECTED_SAMPLE_SIZE = 500
SAMPLE_RATE = 0.10
TEST_CLOCK_FROZEN_TIME = datetime(2026, 3, 10, 12, 0, 0, tzinfo=timezone.utc)
DAY_ONE_INDEX = 0


@pytest.fixture
def config_bundle() -> ConfigBundle:
    return load_config("config")


@pytest.mark.unit
def test_generator_deterministic_seed_reproducibility(config_bundle: ConfigBundle) -> None:
    """Verifies that identical seed produces identical output and different seeds differ."""
    params_seed1 = GenerationParams(
        days=TEST_DAYS_COUNT,
        trips_per_city_day=TEST_TRIPS_PER_CITY,
        seed=TEST_SEED_PRIMARY,
        start_date=date(2026, 3, 1),
    )
    gen1 = SyntheticTripGenerator(config_bundle, params_seed1)
    dataset1 = gen1.generate()

    params_seed1_repeat = GenerationParams(
        days=TEST_DAYS_COUNT,
        trips_per_city_day=TEST_TRIPS_PER_CITY,
        seed=TEST_SEED_PRIMARY,
        start_date=date(2026, 3, 1),
    )
    gen1_repeat = SyntheticTripGenerator(config_bundle, params_seed1_repeat)
    dataset1_repeat = gen1_repeat.generate()

    params_seed2 = GenerationParams(
        days=TEST_DAYS_COUNT,
        trips_per_city_day=TEST_TRIPS_PER_CITY,
        seed=TEST_SEED_SECONDARY,
        start_date=date(2026, 3, 1),
    )
    gen2 = SyntheticTripGenerator(config_bundle, params_seed2)
    dataset2 = gen2.generate()

    # Identical seed produces identical frames
    assert len(dataset1) == len(dataset1_repeat)
    for (d1, df1), (d2, df2) in zip(dataset1, dataset1_repeat, strict=True):
        assert d1 == d2
        pd.testing.assert_frame_equal(df1, df2)

    # Different seeds produce different coordinates/values
    _, df_s1 = dataset1[DAY_ONE_INDEX]
    _, df_s2 = dataset2[DAY_ONE_INDEX]
    assert len(df_s1) == len(df_s2)
    assert not df_s1["pickup_lat"].equals(df_s2["pickup_lat"])


@pytest.mark.unit
def test_clean_generator_output_has_zero_false_alarms(config_bundle: ConfigBundle) -> None:
    """Verifies that clean generated data produces zero quarantined rows (false alarm baseline)."""
    params = GenerationParams(
        days=TEST_DAYS_COUNT,
        trips_per_city_day=TEST_TRIPS_PER_CITY,
        seed=TEST_SEED_PRIMARY,
        start_date=date(2026, 3, 1),
    )
    generator = SyntheticTripGenerator(config_bundle, params)
    dataset = generator.generate()

    clock = FrozenClock(TEST_CLOCK_FROZEN_TIME)
    validator = Validator(config_bundle, clock=clock)

    total_quarantined = 0
    total_valid = 0

    for _target_date, df in dataset:
        result = validator.validate(df)
        summary = result.get_summary()
        total_quarantined += summary.quarantined_rows
        total_valid += summary.valid_rows

    assert total_quarantined == EXPECTED_CLEAN_QUARANTINE_COUNT
    assert total_valid > 0


@pytest.mark.unit
def test_fault_injector_row_level_tolerance_and_labels(
    config_bundle: ConfigBundle, tmp_path: Path
) -> None:
    """Verifies fault rate produces counts within tolerance and all rows appear in labels."""
    # Generate a large enough batch to evaluate statistical rate tolerance
    params = GenerationParams(
        days=1,
        trips_per_city_day=EXPECTED_INJECTED_SAMPLE_SIZE,
        seed=TEST_SEED_PRIMARY,
        start_date=date(2026, 3, 1),
    )
    gen = SyntheticTripGenerator(config_bundle, params)
    dataset = gen.generate()
    _clean_date, clean_df = dataset[DAY_ONE_INDEX]

    clean_file = tmp_path / "clean_trips.csv"
    faulty_file = tmp_path / "faulty_trips.csv"
    labels_file = tmp_path / "faulty_trips.labels.json"
    clean_df.to_csv(clean_file, index=False)

    # Configure single fault to test rate tolerance
    test_fault = FaultConfig(
        name="bad_mobile",
        description="Test injected bad mobile rate",
        scope="rows",
        target_roles=["mobile"],
        rate=SAMPLE_RATE,
        expected_detector="RULE_PHONE_FORMAT",
    )
    injector = FaultInjector(
        config_bundle=config_bundle,
        fault_configs=[test_fault],
        seed=TEST_SEED_PRIMARY,
    )

    _summary = injector.inject_file(
        input_path=clean_file,
        output_path=faulty_file,
        labels_path=labels_file,
    )

    # 1. Labels file exists and is valid JSON
    assert labels_file.exists()
    labels_data = json.loads(labels_file.read_text(encoding="utf-8"))
    assert "injected_faults" in labels_data
    assert "bad_mobile" in labels_data["injected_faults"]

    fault_record = labels_data["injected_faults"]["bad_mobile"]
    assert fault_record["expected_detector"] == "RULE_PHONE_FORMAT"

    injected_row_ids = set(fault_record["affected_row_ids"])
    injected_count = len(injected_row_ids)

    # 2. Rate tolerance check: expected is N * SAMPLE_RATE
    total_rows = len(clean_df)
    expected_count = total_rows * SAMPLE_RATE
    tolerance = total_rows * ROW_RATE_TOLERANCE_MARGIN
    assert abs(injected_count - expected_count) <= tolerance

    # 3. Every injected row appears in the labels file
    faulty_df = pd.read_csv(faulty_file)
    assert len(faulty_df) == total_rows
    for row_id in injected_row_ids:
        matching_rows = faulty_df[faulty_df["trip_id"] == row_id]
        assert not matching_rows.empty
        phone_val = str(matching_rows.iloc[0]["driver_phone"])
        assert not indpy_adapter.is_valid_mobile(phone_val)


@pytest.mark.unit
def test_all_row_fault_plugins_execution(config_bundle: ConfigBundle) -> None:
    """Verifies that every row-level fault plugin mutates records as expected."""
    params = GenerationParams(
        days=1,
        trips_per_city_day=20,
        seed=TEST_SEED_PRIMARY,
        start_date=date(2026, 3, 1),
    )
    gen = SyntheticTripGenerator(config_bundle, params)
    _, clean_df = gen.generate()[DAY_ONE_INDEX]

    context = {
        "rng": random.Random(TEST_SEED_PRIMARY),
        "role_to_col": {col.role: col.name for col in config_bundle.dataset_config.columns},
        "dataset_config": config_bundle.dataset_config,
    }

    row_fault_types = [
        "null_id",
        "swapped_times",
        "swapped_lat_lon",
        "out_of_box_coordinates",
        "bad_mobile",
        "bad_pin",
        "bad_plate",
        "duplicate_ids",
        "future_timestamps",
        "dropped_required_column",
    ]

    for f_type in row_fault_types:
        plugin = fault_registry.get(f_type)
        assert plugin is not None

        cfg = FaultConfig(
            name=f_type,
            description=f"Test {f_type}",
            scope="rows",
            count=5,
            expected_detector="TEST_DETECTOR",
        )
        mutated_df, record = plugin.apply(clean_df.copy(), cfg, context)
        assert record.fault_name == f_type
        assert record.expected_detector == "TEST_DETECTOR"
        if f_type == "dropped_required_column":
            assert record.details.get("dropped_column") not in mutated_df.columns
        else:
            assert len(record.affected_row_ids) > 0


@pytest.mark.unit
def test_batch_fault_plugins_execution(config_bundle: ConfigBundle) -> None:
    """Verifies that batch-level fault plugins mutate the dataset appropriately."""
    params = GenerationParams(
        days=1,
        trips_per_city_day=30,
        seed=TEST_SEED_PRIMARY,
        start_date=date(2026, 3, 1),
    )
    gen = SyntheticTripGenerator(config_bundle, params)
    _, clean_df = gen.generate()[DAY_ONE_INDEX]

    context = {
        "rng": random.Random(TEST_SEED_PRIMARY),
        "role_to_col": {col.role: col.name for col in config_bundle.dataset_config.columns},
        "dataset_config": config_bundle.dataset_config,
    }

    # Test missing_city
    missing_plugin = fault_registry.get("missing_city")
    assert missing_plugin is not None
    missing_cfg = FaultConfig(
        name="missing_city",
        description="Test missing city",
        scope="batch",
        expected_detector="volume_anomaly",
    )
    df_missing, record_missing = missing_plugin.apply(clean_df.copy(), missing_cfg, context)
    assert len(record_missing.affected_scopes) > 0
    removed_city = record_missing.affected_scopes[DAY_ONE_INDEX]
    assert removed_city not in df_missing["city"].unique()

    # Test volume_drop
    volume_plugin = fault_registry.get("volume_drop")
    assert volume_plugin is not None
    volume_cfg = FaultConfig(
        name="volume_drop",
        description="Test volume drop",
        scope="batch",
        rate=0.5,
        expected_detector="volume_anomaly",
    )
    df_vol, record_vol = volume_plugin.apply(clean_df.copy(), volume_cfg, context)
    assert len(df_vol) < len(clean_df)

    # Test stale_batch
    stale_plugin = fault_registry.get("stale_batch")
    assert stale_plugin is not None
    stale_cfg = FaultConfig(
        name="stale_batch",
        description="Test stale batch",
        scope="batch",
        expected_detector="freshness_anomaly",
    )
    df_stale, record_stale = stale_plugin.apply(clean_df.copy(), stale_cfg, context)
    assert len(df_stale) == len(clean_df)
    assert record_stale.details.get("days_behind") is not None
