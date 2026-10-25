"""Synthetic data generator producing clean, schema-compliant logistics trips."""

import random
from dataclasses import dataclass, field
from datetime import date, datetime, time, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

import pandas as pd
from faker import Faker

from pulsecheck.adapters.indpy_adapter import indpy_adapter
from pulsecheck.config.loader import ConfigBundle
from pulsecheck.config.models import CityBoundingBox

DEFAULT_SEED = 42
DEFAULT_DAYS = 3
DEFAULT_TRIPS_PER_CITY = 50
DEFAULT_START_DATE = date(2026, 3, 1)

# Default weekday multipliers: Monday(0) through Sunday(6)
DEFAULT_WEEKDAY_MULTIPLIERS = {
    0: 1.0,  # Monday
    1: 1.0,  # Tuesday
    2: 1.0,  # Wednesday
    3: 1.0,  # Thursday
    4: 1.2,  # Friday (higher logistics volume)
    5: 1.3,  # Saturday (peak weekend dispatch)
    6: 0.7,  # Sunday (reduced dispatch)
}


@dataclass
class GenerationParams:
    """Parameters governing synthetic multi-day dataset generation."""

    start_date: date = DEFAULT_START_DATE
    days: int = DEFAULT_DAYS
    trips_per_city_day: int = DEFAULT_TRIPS_PER_CITY
    weekday_multipliers: Dict[int, float] = field(
        default_factory=lambda: dict(DEFAULT_WEEKDAY_MULTIPLIERS)
    )
    seed: int = DEFAULT_SEED
    output_dir: Path = Path("data/generated")
    file_format: str = "csv"  # 'csv' or 'parquet'
    naming_pattern: str = "trips_{date}.{format}"


class SyntheticTripGenerator:
    """Generates schema-compliant, realistic trips inside city bounding boxes."""

    def __init__(
        self,
        config_bundle: ConfigBundle,
        params: Optional[GenerationParams] = None,
    ) -> None:
        self.config_bundle = config_bundle
        self.dataset_config = config_bundle.dataset_config
        self.cities: List[CityBoundingBox] = config_bundle.cities
        self.params = params or GenerationParams()

        if not self.cities:
            raise ValueError("No cities configured in cities.yaml for coordinate generation.")

    def generate_day_trips(self, target_date: date, rng: random.Random) -> pd.DataFrame:
        """Generate all clean, valid trips across all cities for a single date."""
        day_of_week = target_date.weekday()
        multiplier = self.params.weekday_multipliers.get(day_of_week, 1.0)
        trips_per_city = max(1, int(self.params.trips_per_city_day * multiplier))

        rows: List[Dict[str, Any]] = []
        trip_seq = 1

        for city in self.cities:
            # 10% inward margin to prevent points landing on exact bounding box edges
            lat_span = city.max_lat - city.min_lat
            lon_span = city.max_lon - city.min_lon
            lat_margin = lat_span * 0.1
            lon_margin = lon_span * 0.1

            inner_min_lat = city.min_lat + lat_margin
            inner_max_lat = city.max_lat - lat_margin
            inner_min_lon = city.min_lon + lon_margin
            inner_max_lon = city.max_lon - lon_margin

            for _ in range(trips_per_city):
                # Sample pickup coordinates safely inside city box
                pickup_lat = round(rng.uniform(inner_min_lat, inner_max_lat), 6)
                pickup_lon = round(rng.uniform(inner_min_lon, inner_max_lon), 6)

                # Sample realistic urban trip distance (2.0 to 30.0 km)
                distance_km = round(rng.uniform(2.0, 30.0), 2)

                # Sample realistic urban speed (15.0 to 55.0 km/h, well below 150 km/h limit)
                speed_kmh = rng.uniform(15.0, 55.0)

                # Duration derived consistently from distance and speed
                duration_seconds = int((distance_km / speed_kmh) * 3600.0)

                # Sample pickup time within the date (06:00 to 22:00)
                pickup_hour = rng.randint(6, 21)
                pickup_minute = rng.randint(0, 59)
                pickup_second = rng.randint(0, 59)
                pickup_dt = datetime.combine(
                    target_date,
                    time(pickup_hour, pickup_minute, pickup_second),
                    tzinfo=timezone.utc,
                )
                delivery_dt = pickup_dt + timedelta(seconds=duration_seconds)

                # Authentic Indian identifiers from indpy adapter
                driver_phone = indpy_adapter.generate_mobile()
                postal_code = indpy_adapter.generate_pincode()
                vehicle_plate = indpy_adapter.generate_plate()

                date_str = target_date.strftime("%Y%m%d")
                city_code = city.name[:3].upper()
                trip_id = f"TRIP_{date_str}_{city_code}_{trip_seq:06d}"
                trip_seq += 1

                # Construct record matching configured dataset columns
                row = {
                    "trip_id": trip_id,
                    "pickup_time": pickup_dt.strftime("%Y-%m-%d %H:%M:%S%z"),
                    "delivery_time": delivery_dt.strftime("%Y-%m-%d %H:%M:%S%z"),
                    "pickup_lat": pickup_lat,
                    "pickup_lon": pickup_lon,
                    "city": city.name,
                    "driver_phone": driver_phone,
                    "postal_code": postal_code,
                    "vehicle_plate": vehicle_plate,
                    "trip_status": "COMPLETED",
                    "trip_distance_km": distance_km,
                }
                rows.append(row)

        return pd.DataFrame(rows)

    def generate(self) -> List[Tuple[date, pd.DataFrame]]:
        """Generate in-memory datasets across all configured days."""
        # Seed global random, local RNG instance, and Faker for 100% determinism
        random.seed(self.params.seed)
        rng = random.Random(self.params.seed)
        Faker.seed(self.params.seed)

        results: List[Tuple[date, pd.DataFrame]] = []
        for day_offset in range(self.params.days):
            current_date = self.params.start_date + timedelta(days=day_offset)
            df = self.generate_day_trips(current_date, rng)
            results.append((current_date, df))

        return results

    def generate_all(self) -> List[Path]:
        """Generate multi-day dataset files based on parameters."""
        out_dir = Path(self.params.output_dir)
        out_dir.mkdir(parents=True, exist_ok=True)
        created_files: List[Path] = []

        fmt = self.params.file_format.lower()
        if fmt not in ("csv", "parquet"):
            raise ValueError(f"Unsupported generation format: {fmt}")

        for current_date, df in self.generate():
            date_str = current_date.strftime("%Y%m%d")
            filename = self.params.naming_pattern.format(date=date_str, format=fmt)
            file_path = out_dir / filename

            if fmt == "csv":
                df.to_csv(file_path, index=False)
            elif fmt == "parquet":
                df.to_parquet(file_path, index=False)

            created_files.append(file_path)

        return created_files
