"""Clock abstraction providing timezone-aware time determinism for PulseCheck."""

from datetime import datetime, timezone
from typing import Protocol


class Clock(Protocol):
    """Protocol for time sources."""

    def now(self) -> datetime:
        """Return the current timezone-aware UTC datetime."""
        ...


class SystemClock:
    """Production clock using system wall-clock in UTC."""

    def now(self) -> datetime:
        return datetime.now(timezone.utc)


class FrozenClock:
    """Deterministic clock returning a fixed datetime for testing and replay."""

    def __init__(self, frozen_time: datetime) -> None:
        if frozen_time.tzinfo is None:
            self._frozen_time = frozen_time.replace(tzinfo=timezone.utc)
        else:
            self._frozen_time = frozen_time.astimezone(timezone.utc)

    def now(self) -> datetime:
        return self._frozen_time

    def set_time(self, new_time: datetime) -> None:
        if new_time.tzinfo is None:
            self._frozen_time = new_time.replace(tzinfo=timezone.utc)
        else:
            self._frozen_time = new_time.astimezone(timezone.utc)

    def advance(
        self,
        seconds: float = 0,
        minutes: float = 0,
        hours: float = 0,
        days: float = 0,
    ) -> None:
        """Advance the frozen clock forward by a specified delta."""
        from datetime import timedelta
        self._frozen_time += timedelta(days=days, hours=hours, minutes=minutes, seconds=seconds)

