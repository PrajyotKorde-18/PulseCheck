"""Implementation of synthetic fault injection plugins."""

import random
from datetime import timedelta
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd

from pulsecheck.config.models import FaultConfig
from pulsecheck.faults.base import FaultPlugin, InjectedFaultRecord

DEFAULT_FUTURE_DAYS = 30
DEFAULT_STALE_DAYS = 30
DEFAULT_COORD_OFFSET = 15.0
DEFAULT_DROP_FRACTION = 0.8
INVALID_PHONE_SAMPLE = "12345"
INVALID_PIN_SAMPLE = "999"
INVALID_PLATE_SAMPLE = "NOT_A_PLATE_1"


def select_target_indices(
    df: pd.DataFrame, fault_cfg: FaultConfig, rng: random.Random
) -> List[int]:
    """Select deterministic subset of row indices based on rate or count."""
    total = len(df)
    if total == 0:
        return []

    if fault_cfg.count is not None:
        target_count = min(total, fault_cfg.count)
    elif fault_cfg.rate is not None:
        target_count = max(1, int(total * fault_cfg.rate))
    else:
        target_count = 1

    return rng.sample(range(total), target_count)


def get_row_id(df: pd.DataFrame, idx: int, id_col: Optional[str]) -> str:
    """Retrieve row ID string or fallback to index position."""
    if id_col and id_col in df.columns:
        return str(df.iloc[idx][id_col])
    return str(idx)


class NullIdFault(FaultPlugin):
    """Corrupts primary/composite identity by setting ID column to None or empty."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        id_col = role_to_col.get("id")
        if not id_col or id_col not in df.columns:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]

        for i in targets:
            df.iloc[i, df.columns.get_loc(id_col)] = None

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class SwappedTimesFault(FaultPlugin):
    """Inverts chronological order by swapping pickup and delivery timestamps."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        pickup_col = role_to_col.get("pickup_time")
        delivery_col = role_to_col.get("delivery_time")
        id_col = role_to_col.get("id")

        if not pickup_col or not delivery_col:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]

        pickup_idx = df.columns.get_loc(pickup_col)
        delivery_idx = df.columns.get_loc(delivery_col)

        for i in targets:
            pickup_val = df.iloc[i, pickup_idx]
            delivery_val = df.iloc[i, delivery_idx]
            df.iloc[i, pickup_idx] = delivery_val
            df.iloc[i, delivery_idx] = pickup_val

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class SwappedLatLonFault(FaultPlugin):
    """Inverts latitude and longitude coordinates."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        lat_col = role_to_col.get("lat")
        lon_col = role_to_col.get("lon")
        id_col = role_to_col.get("id")

        if not lat_col or not lon_col:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]

        lat_idx = df.columns.get_loc(lat_col)
        lon_idx = df.columns.get_loc(lon_col)

        for i in targets:
            lat_val = df.iloc[i, lat_idx]
            lon_val = df.iloc[i, lon_idx]
            df.iloc[i, lat_idx] = lon_val
            df.iloc[i, lon_idx] = lat_val

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class OutOfBoxCoordinatesFault(FaultPlugin):
    """Displaces coordinates far beyond the city bounding box."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        lat_col = role_to_col.get("lat")
        lon_col = role_to_col.get("lon")
        id_col = role_to_col.get("id")

        if not lat_col or not lon_col:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]

        lat_idx = df.columns.get_loc(lat_col)
        lon_idx = df.columns.get_loc(lon_col)
        offset = float(fault_cfg.parameters.get("lat_offset", DEFAULT_COORD_OFFSET))

        for i in targets:
            df.iloc[i, lat_idx] = float(df.iloc[i, lat_idx]) + offset
            df.iloc[i, lon_idx] = float(df.iloc[i, lon_idx]) + offset

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class BadMobileFault(FaultPlugin):
    """Injects malformed phone numbers failing Indian mobile format."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        phone_col = role_to_col.get("phone")
        id_col = role_to_col.get("id")

        if not phone_col:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]
        bad_val = fault_cfg.parameters.get("corrupted_value", INVALID_PHONE_SAMPLE)
        col_idx = df.columns.get_loc(phone_col)

        for i in targets:
            df.iloc[i, col_idx] = bad_val

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class BadPinFault(FaultPlugin):
    """Injects malformed postal PIN codes failing Indian postal standards."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        pin_col = role_to_col.get("pincode")
        id_col = role_to_col.get("id")

        if not pin_col:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]
        bad_val = fault_cfg.parameters.get("corrupted_value", INVALID_PIN_SAMPLE)
        col_idx = df.columns.get_loc(pin_col)

        for i in targets:
            df.iloc[i, col_idx] = bad_val

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class BadPlateFault(FaultPlugin):
    """Injects malformed motor vehicle registration plates."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        plate_col = role_to_col.get("plate")
        id_col = role_to_col.get("id")

        if not plate_col:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]
        bad_val = fault_cfg.parameters.get("corrupted_value", INVALID_PLATE_SAMPLE)
        col_idx = df.columns.get_loc(plate_col)

        for i in targets:
            df.iloc[i, col_idx] = bad_val

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class DuplicateIdsFault(FaultPlugin):
    """Duplicates an existing record's ID onto another row within the batch."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        role_to_col: Dict[str, str] = context["role_to_col"]
        id_col = role_to_col.get("id")

        min_rows_needed = 2
        if not id_col or len(df) < min_rows_needed:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        source_id = str(df.iloc[0][id_col])
        target_idx = 1
        affected_ids = [str(df.iloc[target_idx][id_col])]

        # Assign first row's ID to second row
        df.iloc[target_idx, df.columns.get_loc(id_col)] = source_id

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class FutureTimestampsFault(FaultPlugin):
    """Sets event timestamps into the future beyond clock tolerance."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        pickup_col = role_to_col.get("pickup_time")
        id_col = role_to_col.get("id")

        if not pickup_col:
            return df, InjectedFaultRecord(fault_cfg.name, fault_cfg.expected_detector, "rows")

        targets = select_target_indices(df, fault_cfg, rng)
        affected_ids = [get_row_id(df, i, id_col) for i in targets]
        days_ahead = int(fault_cfg.parameters.get("days_ahead", DEFAULT_FUTURE_DAYS))
        col_idx = df.columns.get_loc(pickup_col)

        for i in targets:
            orig_ts = pd.to_datetime(df.iloc[i, col_idx])
            future_ts = orig_ts + timedelta(days=days_ahead)
            df.iloc[i, col_idx] = future_ts.strftime("%Y-%m-%d %H:%M:%S%z")

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="rows",
            affected_row_ids=affected_ids,
        )


class DroppedRequiredColumnFault(FaultPlugin):
    """Removes a mandatory column entirely to trigger rejected_schema."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        role_to_col: Dict[str, str] = context["role_to_col"]
        target_role = fault_cfg.target_roles[0] if fault_cfg.target_roles else "pickup_time"
        col_name = role_to_col.get(target_role)

        if col_name and col_name in df.columns:
            df = df.drop(columns=[col_name])

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="batch",
            affected_scopes=[target_role],
        )


class MissingCityFault(FaultPlugin):
    """Drops all records for an entire city scope."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        role_to_col: Dict[str, str] = context["role_to_col"]
        city_col = role_to_col.get("city")
        target_city = fault_cfg.parameters.get("target_city", "Delhi")

        if city_col and city_col in df.columns:
            df = df[df[city_col] != target_city].reset_index(drop=True)

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="batch",
            affected_scopes=[target_city],
        )


class VolumeDropFault(FaultPlugin):
    """Drops a significant fraction of records for a city scope."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        rng: random.Random = context["rng"]
        role_to_col: Dict[str, str] = context["role_to_col"]
        city_col = role_to_col.get("city")
        target_city = fault_cfg.parameters.get("target_city", "Bengaluru")
        drop_fraction = float(fault_cfg.parameters.get("drop_fraction", DEFAULT_DROP_FRACTION))

        if city_col and city_col in df.columns:
            city_indices = df.index[df[city_col] == target_city].tolist()
            num_to_drop = int(len(city_indices) * drop_fraction)
            if num_to_drop > 0:
                drop_indices = set(rng.sample(city_indices, num_to_drop))
                df = df[~df.index.isin(drop_indices)].reset_index(drop=True)

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="batch",
            affected_scopes=[target_city],
        )


class StaleBatchFault(FaultPlugin):
    """Shifts all timestamps backward in time to simulate stale, delayed delivery."""

    def apply(
        self, df: pd.DataFrame, fault_cfg: FaultConfig, context: Dict[str, Any]
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        df = df.copy()
        role_to_col: Dict[str, str] = context["role_to_col"]
        days_behind = int(fault_cfg.parameters.get("days_behind", DEFAULT_STALE_DAYS))

        for role in ["pickup_time", "delivery_time"]:
            col = role_to_col.get(role)
            if col and col in df.columns:
                ts_series = pd.to_datetime(df[col])
                shifted = ts_series - timedelta(days=days_behind)
                df[col] = shifted.dt.strftime("%Y-%m-%d %H:%M:%S%z")

        return df, InjectedFaultRecord(
            fault_name=fault_cfg.name,
            expected_detector=fault_cfg.expected_detector,
            scope="batch",
            details={"days_behind": days_behind},
        )
