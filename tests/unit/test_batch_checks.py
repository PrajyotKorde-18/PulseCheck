"""Unit test suite for Phase 5 batch-level checks and alert deduplication."""

from datetime import date, datetime, timedelta, timezone

from pulsecheck.checks.alerts import AlertManager
from pulsecheck.checks.freshness import FreshnessChecker
from pulsecheck.checks.models import BatchMetricRecord, CheckFinding
from pulsecheck.checks.scopes import MissingScopesChecker
from pulsecheck.checks.volume import VolumeChecker
from pulsecheck.clock import FrozenClock
from pulsecheck.config.loader import ConfigBundle, load_config

COUNT_ZERO = 0
COUNT_ONE = 1
COUNT_TWO = 2
VOLUME_NORMAL = 100
VOLUME_DROP = 20
VOLUME_FALLBACK_OK = 50
VOLUME_FALLBACK_LOW = 5
VOLUME_WEEKEND = 30
MIN_HISTORY = 3
COOLDOWN_SEC = 3600
AUTO_CLOSE_BATCHES = 3


def make_dummy_config() -> ConfigBundle:
    """Construct ConfigBundle for testing batch checks."""
    bundle = load_config()
    bundle.checks.alerts.destinations.log = False
    bundle.checks.alerts.destinations.table = False
    bundle.checks.alerts.destinations.webhook = False
    return bundle


def make_metric(batch_id: str, logical_date: date, scope: str, rows: int) -> BatchMetricRecord:
    """Helper to generate a BatchMetricRecord."""
    now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    return BatchMetricRecord(
        batch_id=batch_id,
        source="unit_test_feed",
        scope=scope,
        logical_date=logical_date,
        rows_received=rows,
        rows_loaded=rows,
        rows_quarantined=COUNT_ZERO,
        rows_duplicate=COUNT_ZERO,
        latest_event_time=now,
        created_at=now,
    )


def test_normal_day_gives_no_alert_and_drop_gives_alert() -> None:
    """Verifies that normal volume produces 0 alerts and clear volume drop produces 1 alert."""
    bundle = make_dummy_config()
    checker = VolumeChecker(bundle.checks.volume)

    # 3 past Mondays with 100 rows each (Monday = weekday 0)
    history = [
        make_metric("B1", date(2026, 2, 23), "Mumbai", VOLUME_NORMAL),
        make_metric("B2", date(2026, 2, 16), "Mumbai", VOLUME_NORMAL),
        make_metric("B3", date(2026, 2, 9), "Mumbai", VOLUME_NORMAL),
    ]
    monday_weekday = 0

    # Normal day (100 rows)
    res_normal = checker.evaluate_scope_volume("Mumbai", VOLUME_NORMAL, history, monday_weekday)
    assert res_normal.passed is True
    assert res_normal.check_name == "volume_anomaly"

    # Volume drop (20 rows < 50% of 100)
    res_drop = checker.evaluate_scope_volume("Mumbai", VOLUME_DROP, history, monday_weekday)
    assert res_drop.passed is False
    assert res_drop.severity in ("HIGH", "CRITICAL")
    assert "Volume drop detected" in res_drop.message


def test_weekday_matters_low_weekend_volume() -> None:
    """Verifies low weekend volume does not alert when past weekends were also low."""
    bundle = make_dummy_config()
    checker = VolumeChecker(bundle.checks.volume)

    # Past Sundays (weekday 6) had 30 rows each
    sunday_history = [
        make_metric("S1", date(2026, 2, 22), "Mumbai", VOLUME_WEEKEND),
        make_metric("S2", date(2026, 2, 15), "Mumbai", VOLUME_WEEKEND),
        make_metric("S3", date(2026, 2, 8), "Mumbai", VOLUME_WEEKEND),
    ]
    sunday_weekday = 6

    # Current Sunday with 30 rows -> normal for Sunday
    res_sunday = checker.evaluate_scope_volume(
        "Mumbai", VOLUME_WEEKEND, sunday_history, sunday_weekday
    )
    assert res_sunday.passed is True
    assert "Volume normal" in res_sunday.message


def test_short_history_uses_fallback_rule() -> None:
    """Verifies history shorter than minimum uses fallback limits."""
    bundle = make_dummy_config()
    checker = VolumeChecker(bundle.checks.volume)

    # Only 1 past Monday (minimum required is 3)
    short_history = [make_metric("B1", date(2026, 2, 23), "Mumbai", VOLUME_NORMAL)]
    monday_weekday = 0

    # 50 rows is between fallback limits [10, 1000] -> passes
    res_ok = checker.evaluate_scope_volume(
        "Mumbai", VOLUME_FALLBACK_OK, short_history, monday_weekday
    )
    assert res_ok.passed is True
    assert "satisfies limits" in res_ok.message

    # 5 rows is below fallback floor 10 -> alerts
    res_low = checker.evaluate_scope_volume(
        "Mumbai", VOLUME_FALLBACK_LOW, short_history, monday_weekday
    )
    assert res_low.passed is False
    assert "below fallback min of 10" in res_low.message


def test_zero_baseline_does_not_crash_and_behaves_as_documented() -> None:
    """Verifies zero baseline does not divide by zero and behaves as documented."""
    bundle = make_dummy_config()
    checker = VolumeChecker(bundle.checks.volume)

    # Past 3 Mondays had 0 rows
    zero_history = [
        make_metric("Z1", date(2026, 2, 23), "Delhi", COUNT_ZERO),
        make_metric("Z2", date(2026, 2, 16), "Delhi", COUNT_ZERO),
        make_metric("Z3", date(2026, 2, 9), "Delhi", COUNT_ZERO),
    ]
    monday_weekday = 0

    # 0 rows against 0 baseline -> passes
    res_zero = checker.evaluate_scope_volume("Delhi", COUNT_ZERO, zero_history, monday_weekday)
    assert res_zero.passed is True
    assert "matching zero-baseline" in res_zero.message

    # Unexpected volume > 0 against 0 baseline -> alerts
    res_unexpected = checker.evaluate_scope_volume(
        "Delhi", VOLUME_FALLBACK_OK, zero_history, monday_weekday
    )
    assert res_unexpected.passed is False
    assert res_unexpected.severity == "WARNING"
    assert "Unexpected volume" in res_unexpected.message


def test_scope_present_in_history_absent_today_alerts() -> None:
    """Verifies a scope present in recent history and absent today alerts with missing_scope."""
    bundle = make_dummy_config()
    scopes_checker = MissingScopesChecker(bundle.checks.missing_scopes)

    current_scopes = {"Mumbai", "Delhi"}
    historical_scopes = {"Mumbai", "Delhi", "Bengaluru"}

    findings = scopes_checker.evaluate(current_scopes, historical_scopes)
    assert len(findings) == COUNT_ONE
    assert findings[0].check_name == "missing_scope"
    assert findings[0].scope == "Bengaluru"
    assert findings[0].severity == "CRITICAL"
    assert "absent in current batch" in findings[0].message


def test_stale_and_future_timestamps_alert_with_different_codes() -> None:
    """Verifies stale and future timestamps alert with distinct check names."""
    fixed_now = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(fixed_now)
    bundle = make_dummy_config()
    freshness = FreshnessChecker(bundle.checks.freshness, clock=clock)

    # 1. Stale timestamp (2 days old > max_lag 24h)
    stale_time = fixed_now - timedelta(days=2)
    stale_res = freshness.evaluate(stale_time)
    assert stale_res.passed is False
    assert stale_res.check_name == "freshness_anomaly"
    assert stale_res.severity == "WARNING"
    assert "freshness lag exceeded" in stale_res.message

    # 2. Future timestamp (1 hour ahead of clock)
    future_time = fixed_now + timedelta(hours=1)
    future_res = freshness.evaluate(future_time)
    assert future_res.passed is False
    assert future_res.check_name == "future_timestamp"
    assert future_res.severity == "CRITICAL"
    assert "future timestamps" in future_res.message


def test_cooldown_deduplication_and_re_notification() -> None:
    """Verifies repeats in cooldown are suppressed, and re-notifies after cooldown."""
    t0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(t0)
    bundle = make_dummy_config()
    mgr = AlertManager(bundle.checks.alerts, bundle.settings, clock)

    finding = CheckFinding(
        check_name="volume_anomaly",
        scope="Mumbai",
        severity="HIGH",
        message="Volume drop",
        passed=False,
    )

    # First occurrence -> notified
    active, notified, suppressed, _ = mgr.process_findings([finding])
    assert len(notified) == COUNT_ONE
    assert len(suppressed) == COUNT_ZERO
    assert notified[0].occurrence_count == COUNT_ONE

    # Second occurrence 10 minutes later (inside 1 hour cooldown) -> suppressed, count = 2
    clock.advance(seconds=600)
    active2, notified2, suppressed2, _ = mgr.process_findings([finding])
    assert len(notified2) == COUNT_ZERO
    assert len(suppressed2) == COUNT_ONE
    assert suppressed2[0].occurrence_count == COUNT_TWO

    # Third occurrence after cooldown (70 minutes after initial notification) -> re-notifies!
    clock.advance(seconds=3600)
    active3, notified3, suppressed3, _ = mgr.process_findings([finding])
    assert len(notified3) == COUNT_ONE
    assert notified3[0].occurrence_count == COUNT_TWO + 1


def test_severity_escalation_re_notifies_immediately() -> None:
    """Verifies that an alert with higher severity re-notifies immediately even inside cooldown."""
    t0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(t0)
    bundle = make_dummy_config()
    mgr = AlertManager(bundle.checks.alerts, bundle.settings, clock)

    finding_warning = CheckFinding(
        check_name="volume_anomaly",
        scope="Mumbai",
        severity="WARNING",
        message="Volume spike",
        passed=False,
    )
    active, notified, suppressed, _ = mgr.process_findings([finding_warning])
    assert len(notified) == COUNT_ONE
    assert notified[0].severity == "WARNING"

    # 1 minute later, severity escalates to CRITICAL
    clock.advance(seconds=60)
    finding_critical = CheckFinding(
        check_name="volume_anomaly",
        scope="Mumbai",
        severity="CRITICAL",
        message="Volume total collapse",
        passed=False,
    )
    active2, notified2, suppressed2, _ = mgr.process_findings([finding_critical])
    assert len(notified2) == COUNT_ONE
    assert len(suppressed2) == COUNT_ZERO
    assert notified2[0].severity == "CRITICAL"


def test_alert_auto_closes_after_healthy_batches() -> None:
    """Verifies active alert auto-closes after configurable number of healthy batches."""
    t0 = datetime(2026, 3, 1, 10, 0, 0, tzinfo=timezone.utc)
    clock = FrozenClock(t0)
    bundle = make_dummy_config()
    mgr = AlertManager(bundle.checks.alerts, bundle.settings, clock)

    finding_bad = CheckFinding(
        check_name="volume_anomaly",
        scope="Mumbai",
        severity="HIGH",
        message="Volume drop",
        passed=False,
    )
    mgr.process_findings([finding_bad])

    finding_good = CheckFinding(
        check_name="volume_anomaly",
        scope="Mumbai",
        severity="INFO",
        message="Volume normal",
        passed=True,
    )

    # Batch 1 healthy
    clock.advance(seconds=600)
    _, _, _, closed1 = mgr.process_findings([finding_good])
    assert len(closed1) == COUNT_ZERO

    # Batch 2 healthy
    clock.advance(seconds=600)
    _, _, _, closed2 = mgr.process_findings([finding_good])
    assert len(closed2) == COUNT_ZERO

    # Batch 3 healthy -> auto_close_healthy_batches reached (3)! Auto-closes.
    clock.advance(seconds=600)
    _, _, _, closed3 = mgr.process_findings([finding_good])
    assert len(closed3) == COUNT_ONE
    assert closed3[0].status == "auto_closed"
    assert closed3[0].resolved_at is not None
