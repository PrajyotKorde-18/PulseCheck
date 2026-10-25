"""Generate docs/config-reference.md automatically from Pydantic configuration models."""

from pathlib import Path
from typing import Any, List, Type

from pydantic import BaseModel

from pulsecheck.config.models import (
    AlertDestinationsConfig,
    AlertsConfig,
    BatchChecksConfig,
    BenchConfig,
    CityBoundingBox,
    CSVOptions,
    DatasetColumn,
    DatasetConfig,
    DiagnosticsCheckConfig,
    FaultConfig,
    FreshnessCheckConfig,
    MissingScopesCheckConfig,
    PulseCheckSettings,
    ReplayRecoveryConfig,
    RuleConfig,
    VolumeCheckConfig,
)


def format_type(annotation: Any) -> str:
    """Format Python type annotations into human-readable strings."""
    type_str = str(annotation)
    if "typing." in type_str:
        type_str = type_str.replace("typing.", "")
    if "pydantic." in type_str:
        type_str = type_str.replace("pydantic.", "")
    if "pulsecheck.config.models." in type_str:
        type_str = type_str.replace("pulsecheck.config.models.", "")
    return f"`{type_str}`"


def model_to_markdown_table(model_cls: Type[BaseModel], heading: str, description: str) -> str:
    """Convert a Pydantic model's fields into a markdown documentation section."""
    lines: List[str] = [
        f"### {heading}",
        "",
        description,
        "",
        "| Field | Type | Default | Description |",
        "|---|---|---|---|",
    ]

    for field_name, field_info in model_cls.model_fields.items():
        type_repr = format_type(field_info.annotation)
        default_val = field_info.default
        if default_val is ... or default_val is None and field_info.is_required():
            default_str = "**Required**"
        elif default_val is None:
            default_str = "`null`"
        else:
            default_str = f"`{default_val}`"

        desc = field_info.description or "No description provided."
        lines.append(f"| `{field_name}` | {type_repr} | {default_str} | {desc} |")

    lines.append("")
    return "\n".join(lines)


def generate_docs(output_file: Path) -> None:
    """Assemble all configuration models and write docs/config-reference.md."""
    sections: List[str] = [
        "# Configuration Reference",
        "",
        (
            "> **Auto-generated Reference**: This document is generated directly from "
            "PulseCheck's Pydantic models via `python scripts/gen_config_docs.py`."
        ),
        "",
        "## 1. Environment Variables & Runtime Settings (`.env` / OS Environment)",
        "",
        model_to_markdown_table(
            PulseCheckSettings,
            "PulseCheckSettings",
            "Environment variables read by PulseCheck. Documented in `.env.example`.",
        ),
        "## 2. Dataset Specification (`config/dataset.yaml`)",
        "",
        model_to_markdown_table(
            DatasetConfig,
            "DatasetConfig (`dataset:`)",
            "Top-level configuration for source schema, roles, and load behavior.",
        ),
        model_to_markdown_table(
            DatasetColumn,
            "DatasetColumn (`columns[]`)",
            "Individual column-role mapping and type declaration.",
        ),
        model_to_markdown_table(
            CSVOptions,
            "CSVOptions (`csv_options:`)",
            "Parser settings for CSV data sources.",
        ),
        "## 3. Validation Rules Specification (`config/rules.yaml`)",
        "",
        model_to_markdown_table(
            RuleConfig,
            "RuleConfig (`rules[]`)",
            "Individual row validation rule settings, required roles, and thresholds.",
        ),
        "## 4. Batch-Level Checks Specification (`config/checks.yaml`)",
        "",
        model_to_markdown_table(
            BatchChecksConfig,
            "BatchChecksConfig (`batch_checks:`)",
            "Parent container for volume, missing scopes, freshness, and alerts.",
        ),
        model_to_markdown_table(
            VolumeCheckConfig,
            "VolumeCheckConfig (`batch_checks.volume:`)",
            "Volume anomaly check parameters compared against weekday medians.",
        ),
        model_to_markdown_table(
            MissingScopesCheckConfig,
            "MissingScopesCheckConfig (`batch_checks.missing_scopes:`)",
            "Missing scope (e.g. absent city) detection parameters.",
        ),
        model_to_markdown_table(
            FreshnessCheckConfig,
            "FreshnessCheckConfig (`batch_checks.freshness:`)",
            "Data lag and reference clock freshness parameters.",
        ),
        model_to_markdown_table(
            AlertsConfig,
            "AlertsConfig (`batch_checks.alerts:`)",
            "Alert suppression windows and dispatch channels.",
        ),
        model_to_markdown_table(
            AlertDestinationsConfig,
            "AlertDestinationsConfig (`batch_checks.alerts.destinations:`)",
            "Delivery channel switches (log, table, webhook).",
        ),
        model_to_markdown_table(
            DiagnosticsCheckConfig,
            "DiagnosticsCheckConfig (`batch_checks.diagnostics:`)",
            "Thresholds for quarantine root cause concentration detection "
            "(source and day share limits).",
        ),
        "## 5. Scope Geometries (`config/cities.yaml`)",
        "",
        model_to_markdown_table(
            CityBoundingBox,
            "CityBoundingBox (`cities[]`)",
            "Geographic boundaries for city scopes.",
        ),
        "## 6. Fault Injections (`config/faults.yaml`)",
        "",
        model_to_markdown_table(
            FaultConfig,
            "FaultConfig (`faults[]`)",
            "Synthetic defect and anomaly injection specifications.",
        ),
        "## 7. Synthetic Benchmark Specification (`config/checks.yaml` / `bench:`)",
        "",
        model_to_markdown_table(
            BenchConfig,
            "BenchConfig (`bench:`)",
            "Configuration parameters for the synthetic benchmark runner and evaluation metrics.",
        ),
        model_to_markdown_table(
            ReplayRecoveryConfig,
            "ReplayRecoveryConfig (`bench.replay_recovery:` / `faults.yaml:replay_recovery:`)",
            "Configuration for validating quarantine recovery via rule or data remediation.",
        ),
    ]

    output_file.parent.mkdir(parents=True, exist_ok=True)
    with open(output_file, "w", encoding="utf-8") as f:
        f.write("\n".join(sections))


if __name__ == "__main__":
    out_path = Path("docs/config-reference.md")
    generate_docs(out_path)
    print(f"Generated config documentation at {out_path}")
