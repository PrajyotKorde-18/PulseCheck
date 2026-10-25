"""Built-in row validation rules for logistics trips and tabular datasets."""

from typing import Any, Dict

import haversine
import pandas as pd

from pulsecheck.adapters.indpy_adapter import indpy_adapter
from pulsecheck.clock import Clock
from pulsecheck.config.models import RuleConfig
from pulsecheck.rules.registry import registry

SECONDS_PER_HOUR = 3600.0


@registry.register(
    rule_id="RULE_REQUIRED_VALUES",
    reason_code="ERR_NULL_VALUE",
    required_roles=[],
    description="Required columns must not be null, empty, or whitespace",
)
def check_required_values(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows having null, empty, or whitespace-only values in non-nullable columns."""
    dataset_config = context.get("dataset_config")
    if not dataset_config:
        return pd.Series(False, index=df.index)

    required_cols = dataset_config.get_required_columns()
    cols_to_check = [c for c in required_cols if c in df.columns]
    if not cols_to_check:
        return pd.Series(False, index=df.index)

    is_failed = pd.Series(False, index=df.index)
    for col in cols_to_check:
        series = df[col]
        is_null = series.isna()
        if pd.api.types.is_string_dtype(series) or pd.api.types.is_object_dtype(series):
            is_empty = series.astype(str).str.strip() == ""
            is_failed = is_failed | is_null | is_empty
        else:
            is_failed = is_failed | is_null
    return is_failed


@registry.register(
    rule_id="RULE_TYPE_PARSING",
    reason_code="ERR_TYPE_PARSING",
    required_roles=[],
    description="Numeric and timestamp fields must parse cleanly into declared types",
)
def check_type_parsing(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows where values cannot be cast to expected numeric or timestamp formats."""
    dataset_config = context.get("dataset_config")
    if not dataset_config:
        return pd.Series(False, index=df.index)

    is_failed = pd.Series(False, index=df.index)
    for col in dataset_config.columns:
        if col.name not in df.columns:
            continue
        sql_type_upper = col.sql_type.upper()
        series = df[col.name]
        non_null_mask = series.notna()

        if "INT" in sql_type_upper or "DOUBLE" in sql_type_upper or "NUMERIC" in sql_type_upper:
            # Test numeric conversion
            numeric_parsed = pd.to_numeric(series, errors="coerce")
            is_failed = is_failed | (non_null_mask & numeric_parsed.isna())
        elif "TIMESTAMP" in sql_type_upper or "DATE" in sql_type_upper:
            dt_parsed = pd.to_datetime(series, errors="coerce", utc=True)
            is_failed = is_failed | (non_null_mask & dt_parsed.isna())
    return is_failed


@registry.register(
    rule_id="RULE_CHRONOLOGY",
    reason_code="ERR_INVALID_CHRONOLOGY",
    required_roles=["pickup_time", "delivery_time"],
    description="Delivery timestamp must not precede pickup timestamp",
)
def check_chronology(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows where delivery timestamp occurs before pickup timestamp."""
    pickup_col = role_to_col.get("pickup_time")
    delivery_col = role_to_col.get("delivery_time")
    if not pickup_col or not delivery_col or pickup_col not in df or delivery_col not in df:
        return pd.Series(False, index=df.index)

    t_pickup = pd.to_datetime(df[pickup_col], errors="coerce", utc=True)
    t_delivery = pd.to_datetime(df[delivery_col], errors="coerce", utc=True)

    mask = t_pickup.notna() & t_delivery.notna()
    return mask & (t_delivery < t_pickup)


@registry.register(
    rule_id="RULE_MAX_DURATION",
    reason_code="ERR_MAX_DURATION_EXCEEDED",
    required_roles=["pickup_time", "delivery_time"],
    description="Trip duration must not exceed configured max_duration_seconds",
)
def check_max_duration(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows where duration between pickup and delivery exceeds parameter limit."""
    pickup_col = role_to_col.get("pickup_time")
    delivery_col = role_to_col.get("delivery_time")
    max_duration_seconds = rule_config.parameters.get("max_duration_seconds")
    if (
        not pickup_col
        or not delivery_col
        or pickup_col not in df
        or delivery_col not in df
        or max_duration_seconds is None
    ):
        return pd.Series(False, index=df.index)

    t_pickup = pd.to_datetime(df[pickup_col], errors="coerce", utc=True)
    t_delivery = pd.to_datetime(df[delivery_col], errors="coerce", utc=True)

    mask = t_pickup.notna() & t_delivery.notna()
    durations = (t_delivery - t_pickup).dt.total_seconds()
    return mask & (durations > max_duration_seconds)


@registry.register(
    rule_id="RULE_MAX_SPEED",
    reason_code="ERR_SPEED_EXCEEDED",
    required_roles=["pickup_time", "delivery_time"],
    description="Calculated trip speed must not exceed max_speed_kmh",
)
def check_max_speed(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """
    Identify rows where implied speed exceeds max_speed_kmh.
    Uses 'distance' role if present in dataset; otherwise computes distance via haversine.
    """
    pickup_col = role_to_col.get("pickup_time")
    delivery_col = role_to_col.get("delivery_time")
    max_speed_kmh = rule_config.parameters.get("max_speed_kmh")
    if (
        not pickup_col
        or not delivery_col
        or pickup_col not in df
        or delivery_col not in df
        or max_speed_kmh is None
    ):
        return pd.Series(False, index=df.index)

    t_pickup = pd.to_datetime(df[pickup_col], errors="coerce", utc=True)
    t_delivery = pd.to_datetime(df[delivery_col], errors="coerce", utc=True)
    valid_times = t_pickup.notna() & t_delivery.notna()
    duration_hours = (t_delivery - t_pickup).dt.total_seconds() / SECONDS_PER_HOUR
    positive_duration = valid_times & (duration_hours > 0.0)

    distance_col = role_to_col.get("distance")
    if distance_col and distance_col in df:
        distances_km = pd.to_numeric(df[distance_col], errors="coerce")
        valid_distance = distances_km.notna() & (distances_km > 0.0)
        speed = distances_km / duration_hours
        return positive_duration & valid_distance & (speed > max_speed_kmh)

    # Fallback to coordinate-based haversine distance
    lat_col = role_to_col.get("lat")
    lon_col = role_to_col.get("lon")
    dest_lat_col = role_to_col.get("dest_lat")
    dest_lon_col = role_to_col.get("dest_lon")

    if (
        lat_col
        and lon_col
        and dest_lat_col
        and dest_lon_col
        and all(c in df for c in [lat_col, lon_col, dest_lat_col, dest_lon_col])
    ):
        coords_valid = (
            df[lat_col].notna()
            & df[lon_col].notna()
            & df[dest_lat_col].notna()
            & df[dest_lon_col].notna()
        )
        mask = positive_duration & coords_valid

        def _calc_dist(row: pd.Series) -> float:
            p1 = (row[lat_col], row[lon_col])
            p2 = (row[dest_lat_col], row[dest_lon_col])
            return haversine.haversine(p1, p2, unit=haversine.Unit.KILOMETERS)

        if mask.any():
            dist_computed = df[mask].apply(_calc_dist, axis=1)
            speeds = dist_computed / duration_hours[mask]
            result = pd.Series(False, index=df.index)
            result[mask] = speeds > max_speed_kmh
            return result

    return pd.Series(False, index=df.index)


@registry.register(
    rule_id="RULE_GEO_BOUNDS",
    reason_code="ERR_OUT_OF_BOUNDS",
    required_roles=["lat", "lon", "city"],
    description="Coordinates must lie within the bounding box of the specified city",
)
def check_geo_bounds(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows where (lat, lon) coordinates fall outside the bounding box for the city."""
    lat_col = role_to_col.get("lat")
    lon_col = role_to_col.get("lon")
    city_col = role_to_col.get("city")
    if (
        not lat_col
        or not lon_col
        or not city_col
        or lat_col not in df
        or lon_col not in df
        or city_col not in df
    ):
        return pd.Series(False, index=df.index)

    lats = pd.to_numeric(df[lat_col], errors="coerce")
    lons = pd.to_numeric(df[lon_col], errors="coerce")
    cities = df[city_col].astype(str)

    has_coords = lats.notna() & lons.notna() & cities.notna()
    is_failed = pd.Series(False, index=df.index)

    config_bundle = context.get("config_bundle")
    if not config_bundle:
        return is_failed

    for city_name in cities.unique():
        box = config_bundle.get_city_bounding_box(city_name)
        if not box:
            continue
        min_lat, max_lat, min_lon, max_lon = box
        city_mask = has_coords & (cities == city_name)
        out_of_box = (
            (lats < min_lat) | (lats > max_lat) | (lons < min_lon) | (lons > max_lon)
        )
        is_failed = is_failed | (city_mask & out_of_box)

    return is_failed


@registry.register(
    rule_id="RULE_SWAPPED_COORDINATES",
    reason_code="ERR_SWAPPED_COORDINATES",
    required_roles=["lat", "lon", "city"],
    description="Coordinates inverted (lat in lon range and lon in lat range)",
)
def check_swapped_coordinates(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows where lat is in the lon range and lon is in the lat range."""
    lat_col = role_to_col.get("lat")
    lon_col = role_to_col.get("lon")
    city_col = role_to_col.get("city")
    if (
        not lat_col
        or not lon_col
        or not city_col
        or lat_col not in df
        or lon_col not in df
        or city_col not in df
    ):
        return pd.Series(False, index=df.index)

    lats = pd.to_numeric(df[lat_col], errors="coerce")
    lons = pd.to_numeric(df[lon_col], errors="coerce")
    cities = df[city_col].astype(str)

    has_coords = lats.notna() & lons.notna()
    is_failed = pd.Series(False, index=df.index)

    config_bundle = context.get("config_bundle")
    if not config_bundle:
        return is_failed

    for city_name in cities.unique():
        box = config_bundle.get_city_bounding_box(city_name)
        if not box:
            continue
        min_lat, max_lat, min_lon, max_lon = box
        city_mask = has_coords & (cities == city_name)
        # Swapped check: lat is inside lon box and lon is inside lat box
        swapped = (
            (lats >= min_lon) & (lats <= max_lon) & (lons >= min_lat) & (lons <= max_lat)
        )
        is_failed = is_failed | (city_mask & swapped)

    return is_failed


@registry.register(
    rule_id="RULE_CANCELLED_DELIVERY",
    reason_code="ERR_CANCELLED_WITH_DELIVERY",
    required_roles=["status", "delivery_time"],
    description="Cancelled or aborted trips cannot have an associated delivery timestamp",
)
def check_cancelled_with_delivery(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows marked as cancelled/aborted that contain non-null delivery timestamps."""
    status_col = role_to_col.get("status")
    delivery_col = role_to_col.get("delivery_time")
    if not status_col or not delivery_col or status_col not in df or delivery_col not in df:
        return pd.Series(False, index=df.index)

    cancelled_statuses = rule_config.parameters.get(
        "cancelled_statuses", ["cancelled", "aborted", "canceled"]
    )
    norm_statuses = [s.lower() for s in cancelled_statuses]

    is_cancelled = df[status_col].astype(str).str.lower().isin(norm_statuses)
    delivery_present = df[delivery_col].notna() & (df[delivery_col].astype(str).str.strip() != "")
    return is_cancelled & delivery_present


@registry.register(
    rule_id="RULE_PHONE_FORMAT",
    reason_code="ERR_INVALID_PHONE",
    required_roles=["phone"],
    description="Mobile numbers must match national Indian mobile phone format",
)
def check_phone_format(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Validate phone format using indpy adapter."""
    phone_col = role_to_col.get("phone")
    if not phone_col or phone_col not in df:
        return pd.Series(False, index=df.index)

    series = df[phone_col]
    non_null_mask = series.notna() & (series.astype(str).str.strip() != "")
    if not non_null_mask.any():
        return pd.Series(False, index=df.index)

    def _is_invalid(val: Any) -> bool:
        return not indpy_adapter.is_valid_mobile(val)

    is_failed = pd.Series(False, index=df.index)
    is_failed[non_null_mask] = series[non_null_mask].apply(_is_invalid)
    return is_failed


@registry.register(
    rule_id="RULE_PINCODE_FORMAT",
    reason_code="ERR_INVALID_PINCODE",
    required_roles=["pincode"],
    description="Postal PIN codes must match national Indian postal PIN format",
)
def check_pincode_format(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Validate postal PIN code format using indpy adapter."""
    pincode_col = role_to_col.get("pincode")
    if not pincode_col or pincode_col not in df:
        return pd.Series(False, index=df.index)

    series = df[pincode_col]
    non_null_mask = series.notna() & (series.astype(str).str.strip() != "")
    if not non_null_mask.any():
        return pd.Series(False, index=df.index)

    def _is_invalid(val: Any) -> bool:
        return not indpy_adapter.is_valid_pincode(val)

    is_failed = pd.Series(False, index=df.index)
    is_failed[non_null_mask] = series[non_null_mask].apply(_is_invalid)
    return is_failed


@registry.register(
    rule_id="RULE_PLATE_FORMAT",
    reason_code="ERR_INVALID_PLATE",
    required_roles=["plate"],
    description="Vehicle registration plate must match Indian motor vehicle format",
)
def check_plate_format(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Validate vehicle registration plate using indpy adapter."""
    plate_col = role_to_col.get("plate")
    if not plate_col or plate_col not in df:
        return pd.Series(False, index=df.index)

    series = df[plate_col]
    non_null_mask = series.notna() & (series.astype(str).str.strip() != "")
    if not non_null_mask.any():
        return pd.Series(False, index=df.index)

    def _is_invalid(val: Any) -> bool:
        return not indpy_adapter.is_valid_plate(val)

    is_failed = pd.Series(False, index=df.index)
    is_failed[non_null_mask] = series[non_null_mask].apply(_is_invalid)
    return is_failed


@registry.register(
    rule_id="RULE_DUPLICATE_KEY",
    reason_code="ERR_DUPLICATE_KEY",
    required_roles=["id"],
    description="Identifies duplicate primary or composite keys within the current batch",
)
def check_duplicate_key(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """
    First occurrence of a key proceeds; subsequent duplicates are marked as failed.
    Keys are retrieved from the pre-computed 'derived_key' series if available.
    """
    derived_keys = context.get("derived_keys")
    if derived_keys is not None:
        return derived_keys.duplicated(keep="first")

    id_col = role_to_col.get("id")
    if id_col and id_col in df:
        return df[id_col].duplicated(keep="first")
    return pd.Series(False, index=df.index)


@registry.register(
    rule_id="RULE_FUTURE_TIMESTAMP",
    reason_code="ERR_FUTURE_TIMESTAMP",
    required_roles=["pickup_time"],
    description="Event timestamp must not be in the future relative to injectable reference clock",
)
def check_future_timestamp(
    df: pd.DataFrame,
    role_to_col: Dict[str, str],
    rule_config: RuleConfig,
    clock: Clock,
    context: Dict[str, Any],
) -> pd.Series:
    """Identify rows where pickup timestamp exceeds clock.now() + tolerance_seconds."""
    pickup_col = role_to_col.get("pickup_time")
    if not pickup_col or pickup_col not in df:
        return pd.Series(False, index=df.index)

    tolerance_seconds = rule_config.parameters.get("tolerance_seconds", 0)
    current_time = clock.now()
    cutoff_time = current_time.timestamp() + tolerance_seconds

    parsed_dates = pd.to_datetime(df[pickup_col], errors="coerce", utc=True)
    mask = parsed_dates.notna()

    timestamps = parsed_dates[mask].apply(lambda dt: dt.timestamp())
    is_failed = pd.Series(False, index=df.index)
    is_failed[mask] = timestamps > cutoff_time
    return is_failed
