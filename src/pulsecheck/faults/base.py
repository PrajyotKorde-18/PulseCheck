"""Fault plugin interface protocol and label models."""

from dataclasses import dataclass, field
from typing import Any, Dict, List, Protocol, Tuple

import pandas as pd

from pulsecheck.config.models import FaultConfig


@dataclass
class InjectedFaultRecord:
    """Ground truth record of an applied fault."""

    fault_name: str
    expected_detector: str
    scope: str  # 'rows' or 'batch'
    affected_row_ids: List[str] = field(default_factory=list)
    affected_scopes: List[str] = field(default_factory=list)
    details: Dict[str, Any] = field(default_factory=dict)


class FaultPlugin(Protocol):
    """Protocol for fault injection plugins."""

    def apply(
        self,
        df: pd.DataFrame,
        fault_cfg: FaultConfig,
        context: Dict[str, Any],
    ) -> Tuple[pd.DataFrame, InjectedFaultRecord]:
        """Apply fault mutation and return mutated DataFrame with ground-truth record."""
        ...
