"""Unit tests verifying all row rules, boundaries from config, and multi-failure behavior."""

from datetime import datetime, timedelta, timezone
from pathlib import Path

import pandas as pd
import pytest

from pulsecheck.adapters.indpy_adapter import indpy_adapter
from pulsecheck.clock import FrozenClock
from pulsecheck.config.loader import ConfigBundle, load_config
from pulsecheck.config.models import (
    ChecksConfigFile,
    CitiesConfigFile,
    CityBoundingBox,
    DatasetConfigFile,
    RulesConfigFile,
)
from pulsecheck.rules.builtin import (
    check_cancelled_with_delivery,
    check_chronology,
    check_duplicate_key,
    check_future_timestamp,
    check_geo_bounds,
    check_max_duration,
    check_max_speed,
    check_phone_format,
    check_pincode_format,
    check_plate_format,
    check_required_values,
    check_swapped_coordinates,
    check_type_parsing,
)
from pulsecheck.validator import Validator

BASE_TIME = datetime(2026, 5, 10, 12, 0, 0, tzinfo=timezone.utc)


@pytest.fixture
def config_bundle() -> ConfigBundle:
    return load_config("config")


@pytest.fixture
def test_clock() -> FrozenClock:
    return FrozenClock(BASE_TIME)


@pytest.mark.unit
def test_rule_required_values(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    dataset_cfg = config_bundle.dataset_config
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_REQUIRED_VALUES")
    role_to_col = {col.role: col.name for col in dataset_cfg.columns}

    df = pd.DataFrame(
        {
            "trip_id": ["T-01", "", None],
            "pickup_time": [BASE_TIME.isoformat(), BASE_TIME.isoformat(), BASE_TIME.isoformat()],
            "pickup_lat": [19.0, 19.0, 19.0],
            "pickup_lon": [72.8, 72.8, 72.8],
            "city": ["Mumbai", "Mumbai", "Mumbai"],
            "trip_status": ["completed", "completed", "completed"],
        }
    )
    context = {"dataset_config": dataset_cfg}
    failed = check_required_values(df, role_to_col, rule_cfg, test_clock, context)

    assert bool(failed.iloc[0]) is False  # Valid
    assert bool(failed.iloc[1]) is True   # Empty string in required trip_id
    assert bool(failed.iloc[2]) is True   # Null in required trip_id


@pytest.mark.unit
def test_rule_type_parsing(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    dataset_cfg = config_bundle.dataset_config
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_TYPE_PARSING")
    role_to_col = {col.role: col.name for col in dataset_cfg.columns}

    df = pd.DataFrame(
        {
            "trip_id": ["T-01", "T-02", "T-03"],
            "pickup_lat": [19.05, "NOT_A_FLOAT", 19.10],
            "pickup_time": [BASE_TIME.isoformat(), BASE_TIME.isoformat(), "INVALID_DATE"],
        }
    )
    context = {"dataset_config": dataset_cfg}
    failed = check_type_parsing(df, role_to_col, rule_cfg, test_clock, context)

    assert bool(failed.iloc[0]) is False  # Valid
    assert bool(failed.iloc[1]) is True   # Malformed float
    assert bool(failed.iloc[2]) is True   # Malformed timestamp


@pytest.mark.unit
def test_rule_chronology(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_CHRONOLOGY")
    role_to_col = {"pickup_time": "pickup_time", "delivery_time": "delivery_time"}

    # Passing case: delivery is after pickup
    t_pickup = BASE_TIME
    t_after = t_pickup + timedelta(seconds=1)
    # Exact boundary: delivery == pickup (not before pickup -> passes)
    t_equal = t_pickup
    # Failing case: delivery 1 step before pickup (-1 sec)
    t_before = t_pickup - timedelta(seconds=1)

    df = pd.DataFrame(
        {
            "pickup_time": [t_pickup, t_pickup, t_pickup],
            "delivery_time": [t_after, t_equal, t_before],
        }
    )
    failed = check_chronology(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # 1 step after -> passes
    assert bool(failed.iloc[1]) is False  # Exact boundary (equal) -> passes
    assert bool(failed.iloc[2]) is True   # 1 step before -> fails


@pytest.mark.unit
def test_rule_max_duration_with_boundary_read_from_config(
    config_bundle: ConfigBundle, test_clock: FrozenClock
) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_MAX_DURATION")
    limit = rule_cfg.parameters["max_duration_seconds"]  # READ FROM CONFIG, NOT TYPED

    role_to_col = {"pickup_time": "pickup_time", "delivery_time": "delivery_time"}
    t_pickup = BASE_TIME

    step = 1
    t_pass = t_pickup + timedelta(seconds=limit - step)
    t_exact = t_pickup + timedelta(seconds=limit)
    t_fail = t_pickup + timedelta(seconds=limit + step)

    df = pd.DataFrame(
        {
            "pickup_time": [t_pickup, t_pickup, t_pickup],
            "delivery_time": [t_pass, t_exact, t_fail],
        }
    )
    failed = check_max_duration(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # limit - 1 step -> passes
    assert bool(failed.iloc[1]) is False  # exact limit -> passes (duration not above max)
    assert bool(failed.iloc[2]) is True   # limit + 1 step -> fails


@pytest.mark.unit
def test_rule_max_speed_with_boundary_read_from_config(
    config_bundle: ConfigBundle, test_clock: FrozenClock
) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_MAX_SPEED")
    limit = float(rule_cfg.parameters["max_speed_kmh"])  # READ FROM CONFIG, NOT TYPED

    role_to_col = {
        "pickup_time": "pickup_time",
        "delivery_time": "delivery_time",
        "distance": "trip_distance_km",
    }
    t_pickup = BASE_TIME
    t_delivery = t_pickup + timedelta(hours=1)  # 1 hour duration

    step = 1.0
    dist_pass = limit - step
    dist_exact = limit
    dist_fail = limit + step

    df = pd.DataFrame(
        {
            "pickup_time": [t_pickup, t_pickup, t_pickup],
            "delivery_time": [t_delivery, t_delivery, t_delivery],
            "trip_distance_km": [dist_pass, dist_exact, dist_fail],
        }
    )
    failed = check_max_speed(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # speed = limit - 1.0 -> passes
    assert bool(failed.iloc[1]) is False  # speed = limit -> passes (not above max)
    assert bool(failed.iloc[2]) is True   # speed = limit + 1.0 -> fails


@pytest.mark.unit
def test_rule_geo_bounds_with_test_fixtures(
    config_bundle: ConfigBundle, test_clock: FrozenClock
) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_GEO_BOUNDS")
    role_to_col = {"lat": "pickup_lat", "lon": "pickup_lon", "city": "city"}

    # Define city boundary box strictly in test fixture (do not invent facts)
    test_box = CityBoundingBox(
        name="TestCity",
        min_lat=10.0,
        max_lat=20.0,
        min_lon=70.0,
        max_lon=80.0,
    )
    custom_bundle = ConfigBundle(
        dataset_file=DatasetConfigFile(dataset=config_bundle.dataset_config),
        rules_file=RulesConfigFile(rules=config_bundle.rules),
        checks_file=ChecksConfigFile(batch_checks=config_bundle.checks),
        cities_file=CitiesConfigFile(cities=[test_box]),
        settings=config_bundle.settings,
        config_dir=Path("config"),
    )

    step = 0.01
    df = pd.DataFrame(
        {
            "pickup_lat": [15.0, 10.0, 20.0, 10.0 - step, 20.0 + step],
            "pickup_lon": [75.0, 75.0, 75.0, 75.0, 75.0],
            "city": ["TestCity"] * 5,
        }
    )
    context = {"config_bundle": custom_bundle}
    failed = check_geo_bounds(df, role_to_col, rule_cfg, test_clock, context)

    assert bool(failed.iloc[0]) is False  # Center -> passes
    assert bool(failed.iloc[1]) is False  # Exact min_lat boundary -> passes
    assert bool(failed.iloc[2]) is False  # Exact max_lat boundary -> passes
    assert bool(failed.iloc[3]) is True   # min_lat - step -> fails
    assert bool(failed.iloc[4]) is True   # max_lat + step -> fails


@pytest.mark.unit
def test_rule_swapped_coordinates(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_SWAPPED_COORDINATES")
    role_to_col = {"lat": "pickup_lat", "lon": "pickup_lon", "city": "city"}

    test_box = CityBoundingBox(
        name="TestCity",
        min_lat=10.0,
        max_lat=20.0,
        min_lon=70.0,
        max_lon=80.0,
    )
    custom_bundle = ConfigBundle(
        dataset_file=DatasetConfigFile(dataset=config_bundle.dataset_config),
        rules_file=RulesConfigFile(rules=config_bundle.rules),
        checks_file=ChecksConfigFile(batch_checks=config_bundle.checks),
        cities_file=CitiesConfigFile(cities=[test_box]),
        settings=config_bundle.settings,
        config_dir=Path("config"),
    )

    df = pd.DataFrame(
        {
            # Row 0: Normal coordinates (lat ~ 15, lon ~ 75)
            # Row 1: Swapped coordinates (lat ~ 75, lon ~ 15)
            "pickup_lat": [15.0, 75.0],
            "pickup_lon": [75.0, 15.0],
            "city": ["TestCity", "TestCity"],
        }
    )
    context = {"config_bundle": custom_bundle}
    failed = check_swapped_coordinates(df, role_to_col, rule_cfg, test_clock, context)
    assert bool(failed.iloc[0]) is False
    assert bool(failed.iloc[1]) is True


@pytest.mark.unit
def test_rule_cancelled_with_delivery(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_CANCELLED_DELIVERY")
    role_to_col = {"status": "trip_status", "delivery_time": "delivery_time"}

    df = pd.DataFrame(
        {
            "trip_status": ["completed", "cancelled", "cancelled"],
            "delivery_time": [BASE_TIME.isoformat(), None, BASE_TIME.isoformat()],
        }
    )
    failed = check_cancelled_with_delivery(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # Completed with delivery -> passes
    assert bool(failed.iloc[1]) is False  # Cancelled with no delivery -> passes
    assert bool(failed.iloc[2]) is True   # Cancelled with delivery -> fails


@pytest.mark.unit
def test_rule_phone_format(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_PHONE_FORMAT")
    role_to_col = {"phone": "driver_phone"}

    valid_phone = indpy_adapter.generate_mobile()
    df = pd.DataFrame({"driver_phone": [valid_phone, "12345"]})
    failed = check_phone_format(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # Generated by indpy -> passes
    assert bool(failed.iloc[1]) is True   # Truncated digits -> fails


@pytest.mark.unit
def test_rule_pincode_format(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_PINCODE_FORMAT")
    role_to_col = {"pincode": "postal_code"}

    valid_pincode = indpy_adapter.generate_pincode()
    df = pd.DataFrame({"postal_code": [valid_pincode, "000000"]})
    failed = check_pincode_format(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # Generated by indpy -> passes
    assert bool(failed.iloc[1]) is True   # Zero prefix invalid -> fails


@pytest.mark.unit
def test_rule_plate_format(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_PLATE_FORMAT")
    role_to_col = {"plate": "vehicle_plate"}

    valid_plate = indpy_adapter.generate_plate()
    df = pd.DataFrame({"vehicle_plate": [valid_plate, "INVALID_PLATE"]})
    failed = check_plate_format(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # Generated by indpy -> passes
    assert bool(failed.iloc[1]) is True   # Invalid pattern -> fails


@pytest.mark.unit
def test_rule_duplicate_key(config_bundle: ConfigBundle, test_clock: FrozenClock) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_DUPLICATE_KEY")
    role_to_col = {"id": "trip_id"}

    df = pd.DataFrame({"trip_id": ["T-01", "T-02", "T-01"]})
    failed = check_duplicate_key(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # First occurrence of T-01 passes
    assert bool(failed.iloc[1]) is False  # T-02 passes
    assert bool(failed.iloc[2]) is True   # Second occurrence of T-01 fails


@pytest.mark.unit
def test_rule_future_timestamp_with_boundary_read_from_config(
    config_bundle: ConfigBundle, test_clock: FrozenClock
) -> None:
    rule_cfg = next(r for r in config_bundle.rules if r.rule_id == "RULE_FUTURE_TIMESTAMP")
    tol = rule_cfg.parameters["tolerance_seconds"]  # READ FROM CONFIG, NOT TYPED

    role_to_col = {"pickup_time": "pickup_time"}
    clock_now = test_clock.now()

    step = 1
    t_pass = clock_now + timedelta(seconds=tol - step)
    t_exact = clock_now + timedelta(seconds=tol)
    t_fail = clock_now + timedelta(seconds=tol + step)

    df = pd.DataFrame({"pickup_time": [t_pass, t_exact, t_fail]})
    failed = check_future_timestamp(df, role_to_col, rule_cfg, test_clock, {})
    assert bool(failed.iloc[0]) is False  # tolerance - 1 step -> passes
    assert bool(failed.iloc[1]) is False  # exact boundary -> passes
    assert bool(failed.iloc[2]) is True   # tolerance + 1 step -> fails


@pytest.mark.unit
def test_row_failing_two_rules_returns_both_codes(
    config_bundle: ConfigBundle, test_clock: FrozenClock
) -> None:
    validator = Validator(config_bundle, test_clock)

    # A row that violates both Chronology (delivery < pickup) AND Phone format
    t_pickup = BASE_TIME
    t_delivery = t_pickup - timedelta(hours=1)

    df = pd.DataFrame(
        {
            "trip_id": ["T-100"],
            "pickup_time": [t_pickup.isoformat()],
            "delivery_time": [t_delivery.isoformat()],
            "pickup_lat": [19.0],
            "pickup_lon": [72.8],
            "city": ["Mumbai"],
            "driver_phone": ["12345"],  # Invalid phone
            "postal_code": [indpy_adapter.generate_pincode()],
            "vehicle_plate": [indpy_adapter.generate_plate()],
            "trip_status": ["completed"],
            "trip_distance_km": [10.0],
        }
    )

    result = validator.validate(df)
    codes = result.reason_codes_per_row[0]

    assert "ERR_INVALID_CHRONOLOGY" in codes
    assert "ERR_INVALID_PHONE" in codes
    min_expected_codes = 2
    assert len(codes) >= min_expected_codes


@pytest.mark.unit
def test_rule_with_missing_role_is_skipped_and_logged(
    config_bundle: ConfigBundle, test_clock: FrozenClock
) -> None:
    # Build dataset config missing the 'plate' role entirely
    reduced_columns = [col for col in config_bundle.dataset_config.columns if col.role != "plate"]
    reduced_dataset = config_bundle.dataset_config.model_copy(update={"columns": reduced_columns})

    reduced_bundle = ConfigBundle(
        dataset_file=DatasetConfigFile(dataset=reduced_dataset),
        rules_file=RulesConfigFile(rules=config_bundle.rules),
        checks_file=ChecksConfigFile(batch_checks=config_bundle.checks),
        cities_file=CitiesConfigFile(cities=config_bundle.cities),
        settings=config_bundle.settings,
        config_dir=Path("config"),
    )

    validator = Validator(reduced_bundle, test_clock)
    assert "RULE_PLATE_FORMAT" in validator.validate(pd.DataFrame()).skipped_rules
