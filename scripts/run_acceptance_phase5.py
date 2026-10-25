"""Phase 5 Acceptance Runner: Multi-day history, fault injection, and batch check execution."""

from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Dict, List, Optional, Set

import pandas as pd

from pulsecheck.checks.engine import BatchCheckEngine
from pulsecheck.checks.models import BatchMetricRecord
from pulsecheck.clock import FrozenClock
from pulsecheck.config.loader import load_config
from pulsecheck.generator.engine import GenerationParams, SyntheticTripGenerator

OUTPUT_DIR = Path("temp_acceptance/phase5_acceptance")
OUTPUT_DIR.mkdir(parents=True, exist_ok=True)
INITIAL_HISTORY_DAYS = 3
INITIAL_TRIPS_PER_DAY = 50
INITIAL_SEED = 42
MUMBAI_DROPPED_ROWS = 5


def main() -> None:
    """Execute end-to-end multi-day acceptance run for Phase 5."""
    print("=" * 60)
    print("PHASE 5 ACCEPTANCE: Multi-Day Batch Checks & Alert Verification")
    print("=" * 60)

    bundle = load_config()
    clock = FrozenClock(datetime(2026, 3, 3, 23, 59, 59, tzinfo=timezone.utc))

    # 1. Generate multi-day clean dataset (3 days: 2026-03-01 to 2026-03-03)
    print("\n[Step 1] Generating 3-day history with 50 trips/city/day...")
    params = GenerationParams(
        start_date=date(2026, 3, 1),
        days=INITIAL_HISTORY_DAYS,
        trips_per_city_day=INITIAL_TRIPS_PER_DAY,
        output_dir=OUTPUT_DIR,
        file_format="csv",
        seed=INITIAL_SEED,
    )
    generator = SyntheticTripGenerator(config_bundle=bundle, params=params)
    generated_files = generator.generate_all()
    for p in generated_files:
        print(f"  Generated: {p.name}")

    # 2. Run Days 1 and 2 in logical-date order to build historical baselines
    print("\n[Step 2] Processing Day 1 & Day 2 to establish scope baselines...")
    history_by_scope: Dict[Optional[str], List[BatchMetricRecord]] = defaultdict(list)
    recent_historical_scopes: Set[str] = set()
    engine = BatchCheckEngine(config_bundle=bundle, clock=clock)

    for day_file, logical_dt in [
        (generated_files[0], date(2026, 3, 1)),
        (generated_files[1], date(2026, 3, 2)),
    ]:
        df = pd.read_csv(day_file)
        batch_id = f"BATCH_{logical_dt.strftime('%Y%m%d')}"
        latest_evt = pd.to_datetime(df["pickup_time"]).max().to_pydatetime()
        if latest_evt.tzinfo is None:
            latest_evt = latest_evt.replace(tzinfo=timezone.utc)

        # Record metrics per scope
        for city, count in df["city"].value_counts().items():
            metric = BatchMetricRecord(
                batch_id=batch_id,
                source="logistics_feed",
                scope=str(city),
                logical_date=logical_dt,
                rows_received=int(count),
                rows_loaded=int(count),
                rows_quarantined=0,
                rows_duplicate=0,
                latest_event_time=latest_evt,
                created_at=datetime.now(timezone.utc),
            )
            history_by_scope[str(city)].append(metric)
            recent_historical_scopes.add(str(city))

        scopes = list(df["city"].unique())
        print(f"  Day {logical_dt}: Scopes {scopes} ({len(df)} total rows)")

    # 3. Create faulty Day 3: inject volume drop (Mumbai = 5) and missing city (Delhi dropped)
    print("\n[Step 3] Injecting faults into Day 3 (Volume drop Mumbai, Missing scope Delhi)...")
    day3_path = generated_files[2]
    day3_df = pd.read_csv(day3_path)
    initial_counts = day3_df["city"].value_counts().to_dict()
    print(f"  Day 3 original distribution: {initial_counts}")

    # Drop Delhi completely
    day3_df_faulty = day3_df[day3_df["city"] != "Delhi"].copy()

    # Drop Mumbai to 5 rows (volume drop anomaly)
    mumbai_rows = day3_df_faulty[day3_df_faulty["city"] == "Mumbai"].head(MUMBAI_DROPPED_ROWS)
    other_rows = day3_df_faulty[day3_df_faulty["city"] != "Mumbai"]
    day3_df_faulty = pd.concat([mumbai_rows, other_rows]).reset_index(drop=True)

    faulty_counts = day3_df_faulty["city"].value_counts().to_dict()
    print(f"  Day 3 faulty distribution:   {faulty_counts} (Delhi missing, Mumbai dropped to 5)")

    day3_faulty_path = OUTPUT_DIR / "trips_20260303_faulty.csv"
    day3_df_faulty.to_csv(day3_faulty_path, index=False)
    print(f"  Saved faulty batch to: {day3_faulty_path.name}")

    # 4. Run Day 3 batch checks
    print("\n[Step 4] Executing Batch Checks against Day 3...")
    batch3_id = "BATCH_20260303"
    day3_latest_evt = pd.to_datetime(day3_df_faulty["pickup_time"]).max().to_pydatetime()
    if day3_latest_evt.tzinfo is None:
        day3_latest_evt = day3_latest_evt.replace(tzinfo=timezone.utc)

    current_scope_volumes = {
        str(city): int(count)
        for city, count in day3_df_faulty["city"].value_counts().items()
    }

    report = engine.run_checks(
        batch_id=batch3_id,
        source="logistics_feed",
        logical_date=date(2026, 3, 3),
        current_scope_volumes=current_scope_volumes,
        latest_event_time=day3_latest_evt,
        history_by_scope=history_by_scope,
        recent_historical_scopes=recent_historical_scopes,
        conn=None,
    )

    print("\n" + "=" * 60)
    print("PulseCheck Batch Verification Findings")
    print("=" * 60)
    print(f"Batch ID:       {report.batch_id}")
    print(f"Logical Date:   {report.logical_date}")
    status_str = "PASSED" if report.all_passed else "ALERTS TRIGGERED"
    print(f"Status:         {status_str}")
    print("-" * 60)
    print("Check Findings:")
    for f in report.findings:
        status_tag = f"[{f.severity.upper()}]" if not f.passed else "[PASS]"
        print(f"  {status_tag} {f.check_name} ({f.scope}): {f.message}")

    print("\n" + "=" * 60)
    print("Alerts Table Record Simulation (Dispatched to Alert Engine)")
    print("=" * 60)
    alerts = report.active_alerts
    print(f"Total Active Alert Records: {len(alerts)}")
    for a in alerts:
        print(f"  - Fingerprint:   {a.fingerprint}")
        print(f"    Check:         {a.check_name}")
        print(f"    Scope:         {a.scope}")
        print(f"    Severity:      {a.severity}")
        print(f"    Count:         {a.occurrence_count}")
        print(f"    Status:        {a.status}")
        print(f"    Message:       {a.message}")
        print(f"    First Seen:    {a.first_seen_at.isoformat()}")
        print(f"    Last Notified: {a.last_notified_at.isoformat()}")
        print("-" * 60)


if __name__ == "__main__":
    main()
