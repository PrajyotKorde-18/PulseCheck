"""Missing scopes check: verifies all historically active scopes appear in current batch."""

from typing import List, Set

from pulsecheck.checks.models import CheckFinding
from pulsecheck.config.models import MissingScopesCheckConfig


class MissingScopesChecker:
    """Detects when an operational scope (e.g. city) active in recent history disappears."""

    def __init__(self, config: MissingScopesCheckConfig) -> None:
        self.config = config

    def evaluate(
        self,
        current_scopes: Set[str],
        recent_historical_scopes: Set[str],
    ) -> List[CheckFinding]:
        """
        Identify any scope active in recent history that is missing in current batch.

        Args:
            current_scopes: Set of scope names present in the current batch.
            recent_historical_scopes: Set of scope names observed in the lookback window.
        """
        if not self.config.enabled:
            return []

        # Filter out generic 'all' or empty markers
        clean_current = {s for s in current_scopes if s and s != "all"}
        clean_history = {s for s in recent_historical_scopes if s and s != "all"}

        missing_scopes = sorted(clean_history - clean_current)
        findings: List[CheckFinding] = []

        for scope in missing_scopes:
            findings.append(
                CheckFinding(
                    check_name="missing_scope",
                    scope=scope,
                    severity=self.config.severity,
                    message=(
                        f"Scope '{scope}' was active in recent history "
                        f"({self.config.lookback_batches} batches lookback) "
                        f"but is absent in current batch (0 rows received)."
                    ),
                    passed=False,
                    details={
                        "missing_scope": scope,
                        "lookback_batches": self.config.lookback_batches,
                    },
                )
            )

        return findings
