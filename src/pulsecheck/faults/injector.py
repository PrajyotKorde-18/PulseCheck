"""Fault injector applying configured defects and producing ground-truth labels."""

import json
import random
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, Optional

from pulsecheck.config.loader import ConfigBundle
from pulsecheck.config.models import FaultConfig
from pulsecheck.faults.base import InjectedFaultRecord
from pulsecheck.faults.registry import fault_registry
from pulsecheck.readers.factory import get_reader

DEFAULT_INJECTOR_SEED = 12345


@dataclass
class InjectionResult:
    """Summary of fault injection execution."""

    input_file: Path
    output_file: Path
    labels_file: Path
    total_input_rows: int
    total_output_rows: int
    faults_applied: int
    fault_records: List[InjectedFaultRecord]


class FaultInjector:
    """Orchestrates parameterized fault injection on clean data batches."""

    def __init__(
        self,
        config_bundle: ConfigBundle,
        fault_configs: Optional[List[FaultConfig]] = None,
        seed: int = DEFAULT_INJECTOR_SEED,
    ) -> None:
        self.config_bundle = config_bundle
        self.dataset_config = config_bundle.dataset_config
        self.fault_configs = fault_configs or config_bundle.faults
        self.seed = seed
        self.role_to_col: Dict[str, str] = {
            col.role: col.name for col in self.dataset_config.columns
        }

    def inject_file(
        self,
        input_path: Path,
        output_path: Optional[Path] = None,
        labels_path: Optional[Path] = None,
    ) -> InjectionResult:
        """Read input file, inject enabled faults, and write faulty data and ground truth labels."""
        input_path = Path(input_path).resolve()
        if not input_path.exists():
            raise FileNotFoundError(f"Source file not found: {input_path}")

        reader = get_reader(self.dataset_config)
        df = reader.read_all(input_path)
        initial_row_count = len(df)

        rng = random.Random(self.seed)
        context: Dict[str, Any] = {
            "rng": rng,
            "role_to_col": self.role_to_col,
            "dataset_config": self.dataset_config,
        }

        applied_records: List[InjectedFaultRecord] = []

        for fault_cfg in self.fault_configs:
            if not fault_cfg.enabled:
                continue

            plugin = fault_registry.get(fault_cfg.name)
            if not plugin:
                continue

            df, record = plugin.apply(df, fault_cfg, context)
            applied_records.append(record)

        # Determine output file path
        if output_path is None:
            stem = input_path.stem
            ext = input_path.suffix
            output_path = input_path.parent / f"{stem}_faulty{ext}"
        else:
            output_path = Path(output_path).resolve()

        output_path.parent.mkdir(parents=True, exist_ok=True)
        if output_path.suffix.lower() == ".parquet":
            df.to_parquet(output_path, index=False)
        else:
            df.to_csv(output_path, index=False)

        # Determine labels file path
        if labels_path is None:
            labels_path = output_path.parent / f"{output_path.stem}_labels.json"
        else:
            labels_path = Path(labels_path).resolve()

        labels_path.parent.mkdir(parents=True, exist_ok=True)

        # Serialize ground-truth labels
        labels_payload = {
            "source_file": str(input_path),
            "faulty_file": str(output_path),
            "seed": self.seed,
            "injected_faults": {
                rec.fault_name: {
                    "expected_detector": rec.expected_detector,
                    "scope": rec.scope,
                    "affected_row_ids": rec.affected_row_ids,
                    "affected_scopes": rec.affected_scopes,
                    "count": len(rec.affected_row_ids)
                    if rec.scope == "rows"
                    else len(rec.affected_scopes),
                    "details": rec.details,
                }
                for rec in applied_records
            },
        }

        with open(labels_path, "w", encoding="utf-8") as f:
            json.dump(labels_payload, f, indent=2)

        return InjectionResult(
            input_file=input_path,
            output_file=output_path,
            labels_file=labels_path,
            total_input_rows=initial_row_count,
            total_output_rows=len(df),
            faults_applied=len(applied_records),
            fault_records=applied_records,
        )
