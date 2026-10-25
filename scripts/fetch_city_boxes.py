"""
Fetch city bounding boxes from OpenStreetMap Nominatim.

Nominatim Usage Policy Compliance:
- Policy URL: https://operations.osmfoundation.org/policies/nominatim/
- Requirements followed:
  1. User-Agent: Custom descriptive User-Agent identifying the project and maintainer repository.
  2. Rate Limit: Strict minimum 1.2s delay between successive HTTP requests (< 1 request/sec).
  3. Disk Caching: Local cache stored in reports/nominatim_cache.json to avoid duplicate requests.
  4. Network Fallback: In case of network failure, leaves placeholders without guessing coordinates.
"""

import argparse
import json
import sys
import time
import urllib.parse
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional

import yaml

NOMINATIM_POLICY_URL = "https://operations.osmfoundation.org/policies/nominatim/"
USER_AGENT = "PulseCheck-Data-Engineering/1.0 (https://github.com/PrajyotKorde-18/PulseCheck)"
CACHE_FILE = Path("reports/nominatim_cache.json")
DEFAULT_CITIES = ["Mumbai", "Bengaluru", "Delhi"]
REQUEST_DELAY_SECONDS = 1.2


def load_cache() -> Dict[str, Any]:
    """Load cached geocoding responses from disk."""
    if CACHE_FILE.exists():
        try:
            return json.loads(CACHE_FILE.read_text(encoding="utf-8"))
        except Exception:
            return {}
    return {}


def save_cache(cache: Dict[str, Any]) -> None:
    """Save geocoding responses to disk cache."""
    CACHE_FILE.parent.mkdir(parents=True, exist_ok=True)
    CACHE_FILE.write_text(json.dumps(cache, indent=2), encoding="utf-8")


HTTP_OK = 200
BOUNDING_BOX_PARTS = 4


def fetch_nominatim_box(city_name: str, cache: Dict[str, Any]) -> Optional[Dict[str, float]]:
    """Fetch bounding box for city from Nominatim with rate limiting and caching."""
    cache_key = city_name.strip().lower()
    if cache_key in cache:
        print(f"Using cached Nominatim data for '{city_name}'")
        return cache[cache_key]

    params = {
        "q": f"{city_name}, India",
        "format": "json",
        "limit": "1",
    }
    url = f"https://nominatim.openstreetmap.org/search?{urllib.parse.urlencode(params)}"
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": USER_AGENT,
            "Accept": "application/json",
        },
    )

    try:
        time.sleep(REQUEST_DELAY_SECONDS)
        with urllib.request.urlopen(req, timeout=10) as response:
            if response.status != HTTP_OK:
                print(f"WARNING: Nominatim returned HTTP {response.status} for {city_name}")
                return None
            data = json.loads(response.read().decode("utf-8"))
            if not data:
                print(f"WARNING: No Nominatim result found for {city_name}")
                return None

            # Nominatim returns boundingbox as [south_lat, north_lat, west_lon, east_lon]
            raw_box = data[0].get("boundingbox")
            if not raw_box or len(raw_box) != BOUNDING_BOX_PARTS:
                return None

            box = {
                "min_lat": round(float(raw_box[0]), 4),
                "max_lat": round(float(raw_box[1]), 4),
                "min_lon": round(float(raw_box[2]), 4),
                "max_lon": round(float(raw_box[3]), 4),
            }
            cache[cache_key] = box
            save_cache(cache)
            print(f"Fetched Nominatim box for '{city_name}': {box}")
            return box

    except Exception as e:
        print(f"NETWORK/API ERROR for '{city_name}': {e}", file=sys.stderr)
        return None


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fetch city bounding boxes from OpenStreetMap Nominatim"
    )
    parser.add_argument(
        "cities",
        nargs="*",
        default=DEFAULT_CITIES,
        help="List of city names to geocode (defaults to Mumbai, Bengaluru, Delhi)",
    )
    parser.add_argument(
        "--output",
        "-o",
        default="config/cities.yaml",
        help="Path to output cities.yaml file",
    )
    args = parser.parse_args()

    print(f"Notice: Nominatim usage policy URL: {NOMINATIM_POLICY_URL}")
    print(f"User-Agent: {USER_AGENT}")

    cache = load_cache()
    output_records: List[Dict[str, Any]] = []
    missing_cities: List[str] = []

    for city in args.cities:
        box = fetch_nominatim_box(city, cache)
        if box:
            output_records.append(
                {
                    "name": city,
                    "min_lat": box["min_lat"],
                    "max_lat": box["max_lat"],
                    "min_lon": box["min_lon"],
                    "max_lon": box["max_lon"],
                }
            )
        else:
            missing_cities.append(city)
            output_records.append(
                {
                    "name": city,
                    "min_lat": None,
                    "max_lat": None,
                    "min_lon": None,
                    "max_lon": None,
                }
            )

    output_path = Path(args.output)
    output_path.parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        yaml.safe_dump({"cities": output_records}, f, sort_keys=False)

    print(f"\nWrote updated city bounding boxes to {output_path}")

    if missing_cities:
        print(
            "\nACTION REQUIRED: The following cities could not be fetched due to network/API "
            "restrictions:"
        )
        for m in missing_cities:
            print(
                f" - {m}: Please manually fill in min_lat, max_lat, min_lon, max_lon in "
                f"{output_path}."
            )
        return 1

    return 0


if __name__ == "__main__":
    sys.exit(main())
