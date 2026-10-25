"""Unit tests verifying time determinism and injectable Clock protocol."""

from datetime import datetime, timezone

import pytest

from pulsecheck.clock import FrozenClock, SystemClock


@pytest.mark.unit
def test_system_clock_returns_timezone_aware_utc() -> None:
    clock = SystemClock()
    now = clock.now()
    assert now.tzinfo is not None
    assert now.tzinfo == timezone.utc


@pytest.mark.unit
def test_frozen_clock_is_deterministic_and_injectable() -> None:
    fixed_time = datetime(2026, 5, 10, 14, 30, 0, tzinfo=timezone.utc)
    clock = FrozenClock(fixed_time)

    assert clock.now() == fixed_time
    assert clock.now() == fixed_time

    # Mutate frozen time
    next_time = datetime(2026, 5, 10, 15, 0, 0, tzinfo=timezone.utc)
    clock.set_time(next_time)
    assert clock.now() == next_time


@pytest.mark.unit
def test_frozen_clock_converts_naive_datetime_to_utc() -> None:
    naive_dt = datetime(2026, 5, 10, 14, 30, 0)
    clock = FrozenClock(naive_dt)
    assert clock.now().tzinfo == timezone.utc
    assert clock.now() == datetime(2026, 5, 10, 14, 30, 0, tzinfo=timezone.utc)
