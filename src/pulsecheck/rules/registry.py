"""Rule registry and protocol definitions for row-level validations."""

import logging
from typing import Any, Callable, Dict, List, Optional, Protocol, Sequence, Tuple

import pandas as pd

from pulsecheck.clock import Clock
from pulsecheck.config.models import DatasetConfig, RuleConfig

logger = logging.getLogger(__name__)


class RuleEvaluationResult(Protocol):
    """Protocol for the result of evaluating a rule across a DataFrame."""
    pass


# A rule evaluation function takes:
# df: pd.DataFrame
# role_to_col: Dict[str, str] (semantic role -> physical column name)
# rule_config: RuleConfig
# clock: Clock
# context: Dict[str, Any] (e.g. city bounding boxes)
# Returns: pd.Series of booleans, where True indicates the row FAILED the rule.
RuleFn = Callable[
    [pd.DataFrame, Dict[str, str], RuleConfig, Clock, Dict[str, Any]],
    pd.Series,
]


class RegisteredRule:
    """Descriptor for a registered validation rule."""

    def __init__(
        self,
        rule_id: str,
        reason_code: str,
        required_roles: Sequence[str],
        func: RuleFn,
        description: str = "",
    ) -> None:
        self.rule_id = rule_id
        self.reason_code = reason_code
        self.required_roles = list(required_roles)
        self.func = func
        self.description = description

    def is_applicable(self, dataset_config: DatasetConfig) -> Tuple[bool, Optional[str]]:
        """Check if all required roles are present in the dataset schema."""
        configured_roles = {col.role for col in dataset_config.columns}
        missing_roles = [r for r in self.required_roles if r not in configured_roles]
        if missing_roles:
            reason = (
                f"Rule '{self.rule_id}' skipped: "
                f"dataset lacks required role(s): {missing_roles}"
            )
            return False, reason
        return True, None


class RuleRegistry:
    """Registry maintaining active and discoverable validation rules."""

    def __init__(self) -> None:
        self._rules: Dict[str, RegisteredRule] = {}

    def register(
        self,
        rule_id: str,
        reason_code: str,
        required_roles: Sequence[str],
        description: str = "",
    ) -> Callable[[RuleFn], RuleFn]:
        """Decorator to register a rule implementation function."""

        def decorator(fn: RuleFn) -> RuleFn:
            rule = RegisteredRule(
                rule_id=rule_id,
                reason_code=reason_code,
                required_roles=required_roles,
                func=fn,
                description=description,
            )
            self._rules[rule_id] = rule
            return fn

        return decorator

    def get_rule(self, rule_id: str) -> Optional[RegisteredRule]:
        return self._rules.get(rule_id)

    def list_rules(self) -> List[RegisteredRule]:
        return list(self._rules.values())


registry = RuleRegistry()
