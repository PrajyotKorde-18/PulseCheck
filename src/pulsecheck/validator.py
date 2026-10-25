"""Validation engine executing row rules and compiling reason codes."""

import hashlib
import logging
from typing import Any, Dict, List

import pandas as pd
from pydantic import BaseModel, ConfigDict

from pulsecheck.clock import Clock
from pulsecheck.config.loader import ConfigBundle
from pulsecheck.config.models import DatasetConfig
from pulsecheck.rules.builtin import registry

logger = logging.getLogger(__name__)

KEY_DELIMITER = "|"
EMPTY_KEY_PLACEHOLDER = ""


class RowReason(BaseModel):
    """Specific reason describing why a row was quarantined."""

    model_config = ConfigDict(strict=True)

    code: str
    rule: str
    fields: List[str]
    message: str


class ValidationSummary(BaseModel):
    """High-level summary of a DataFrame validation execution."""

    model_config = ConfigDict(strict=True)

    total_rows: int
    valid_rows: int
    quarantined_rows: int
    skipped_rules: List[str]


class ValidationResult:
    """Detailed result of running all applicable validation rules against a DataFrame."""

    def __init__(
        self,
        df: pd.DataFrame,
        derived_keys: pd.Series,
        row_reasons: List[List[RowReason]],
        skipped_rules: List[str],
    ) -> None:
        self.df = df
        self.derived_keys = derived_keys
        self.row_reasons = row_reasons
        self.skipped_rules = skipped_rules

    @property
    def is_valid_mask(self) -> pd.Series:
        """Boolean Series: True if the row passed all evaluated rules without reasons."""
        return pd.Series([len(reasons) == 0 for reasons in self.row_reasons], index=self.df.index)

    @property
    def reason_codes_per_row(self) -> List[List[str]]:
        """List of reason code strings per row."""
        return [[r.code for r in reasons] for reasons in self.row_reasons]

    def get_summary(self) -> ValidationSummary:
        total = len(self.df)
        valid = int(self.is_valid_mask.sum())
        return ValidationSummary(
            total_rows=total,
            valid_rows=valid,
            quarantined_rows=total - valid,
            skipped_rules=self.skipped_rules,
        )


def derive_composite_key(
    df: pd.DataFrame,
    identity_roles: List[str],
    role_to_col: Dict[str, str],
    algorithm: str = "sha256",
) -> pd.Series:
    """
    Derive deterministic, order-independent record keys for a DataFrame.

    If identity_roles has a single role, the key is the string representation of that column.
    If identity_roles has multiple roles (composite key):
    - Roles are sorted lexicographically to ensure order independence.
    - Values are normalized to strings (with nulls mapped to EMPTY_KEY_PLACEHOLDER).
    - Concatenated with KEY_DELIMITER.
    - Hashed with the requested algorithm (e.g. SHA-256) to produce a fixed-length key.
    """
    if len(identity_roles) == 1:
        col_name = role_to_col.get(identity_roles[0])
        if col_name and col_name in df.columns:
            return df[col_name].fillna(EMPTY_KEY_PLACEHOLDER).astype(str)
        return pd.Series(EMPTY_KEY_PLACEHOLDER, index=df.index)

    # Composite key: sort roles lexicographically for order independence
    sorted_roles = sorted(identity_roles)
    cols = [role_to_col.get(r) for r in sorted_roles]

    def _hash_composite_row(row: pd.Series) -> str:
        parts: List[str] = []
        for role, col in zip(sorted_roles, cols, strict=False):
            if col and col in row and pd.notna(row[col]):
                parts.append(f"{role}={str(row[col]).strip()}")
            else:
                parts.append(f"{role}={EMPTY_KEY_PLACEHOLDER}")
        composite_str = KEY_DELIMITER.join(parts)
        if algorithm == "sha256":
            return hashlib.sha256(composite_str.encode("utf-8")).hexdigest()
        return composite_str

    if df.empty:
        return pd.Series([], dtype=str)
    return df.apply(_hash_composite_row, axis=1)


class Validator:
    """Orchestrates row-level rule execution over DataFrames."""

    def __init__(self, config_bundle: ConfigBundle, clock: Clock) -> None:
        self.config_bundle = config_bundle
        self.clock = clock
        self.dataset_config: DatasetConfig = config_bundle.dataset_config
        self.role_to_col: Dict[str, str] = {
            col.role: col.name for col in self.dataset_config.columns
        }

    def validate(self, df: pd.DataFrame) -> ValidationResult:
        """
        Evaluate all enabled rules on the DataFrame.
        Returns a ValidationResult containing per-row reasons and failure masks.
        """
        derived_keys = derive_composite_key(
            df=df,
            identity_roles=self.dataset_config.identity_roles,
            role_to_col=self.role_to_col,
            algorithm=self.dataset_config.composite_key_hash_algorithm,
        )

        context: Dict[str, Any] = {
            "dataset_config": self.dataset_config,
            "config_bundle": self.config_bundle,
            "derived_keys": derived_keys,
        }

        # Initialize reasons list for each row
        row_reasons: List[List[RowReason]] = [[] for _ in range(len(df))]
        skipped_rules: List[str] = []

        # Find configured rule objects
        rule_configs_by_id = {r.rule_id: r for r in self.config_bundle.rules}

        for rule in registry.list_rules():
            rule_cfg = rule_configs_by_id.get(rule.rule_id)
            if not rule_cfg or not rule_cfg.enabled:
                continue

            # Check if dataset has all required roles
            applicable, skip_reason = rule.is_applicable(self.dataset_config)
            if not applicable:
                logger.info(skip_reason)
                skipped_rules.append(rule.rule_id)
                continue

            # Run vectorized rule evaluation
            failed_mask: pd.Series = rule.func(
                df,
                self.role_to_col,
                rule_cfg,
                self.clock,
                context,
            )

            # Record reason for failed rows
            if failed_mask.any():
                field_names = [
                    self.role_to_col[r]
                    for r in rule.required_roles
                    if r in self.role_to_col
                ]
                for idx_pos, (_original_idx, is_failed) in enumerate(failed_mask.items()):
                    if is_failed:
                        reason = RowReason(
                            code=rule_cfg.reason_code,
                            rule=rule_cfg.rule_id,
                            fields=field_names,
                            message=rule_cfg.description,
                        )
                        row_reasons[idx_pos].append(reason)

        return ValidationResult(
            df=df,
            derived_keys=derived_keys,
            row_reasons=row_reasons,
            skipped_rules=skipped_rules,
        )
