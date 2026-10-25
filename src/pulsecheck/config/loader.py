"""Configuration loading, environment variable override, and schema validation."""

from pathlib import Path
from typing import Any, Dict, Optional, Tuple

import yaml
from pydantic import ValidationError

from pulsecheck.config.models import (
    ChecksConfigFile,
    CitiesConfigFile,
    DatasetConfigFile,
    FaultsConfigFile,
    PulseCheckSettings,
    RulesConfigFile,
)


class ConfigError(Exception):
    """Raised when configuration validation or loading fails."""
    pass


class ConfigBundle:
    """Container holding fully validated configuration objects."""

    def __init__(
        self,
        dataset_file: DatasetConfigFile,
        rules_file: RulesConfigFile,
        checks_file: ChecksConfigFile,
        cities_file: CitiesConfigFile,
        settings: PulseCheckSettings,
        config_dir: Path,
        faults_file: Optional[FaultsConfigFile] = None,
    ) -> None:
        self.dataset_config = dataset_file.dataset
        self.rules = rules_file.rules
        self.checks = checks_file.batch_checks
        self.cities = cities_file.cities
        self.faults = faults_file.faults if faults_file else []
        self.bench = checks_file.bench
        if faults_file and faults_file.replay_recovery:
            self.bench.replay_recovery = faults_file.replay_recovery
        self.settings = settings
        self.config_dir = config_dir

    def get_city_bounding_box(self, city_name: str) -> Optional[Tuple[float, float, float, float]]:
        """Return (min_lat, max_lat, min_lon, max_lon) for a city if configured."""
        for city in self.cities:
            if city.name.lower() == city_name.lower():
                return (city.min_lat, city.max_lat, city.min_lon, city.max_lon)
        return None


def _load_yaml_file(file_path: Path) -> Dict[str, Any]:
    """Read and parse a YAML file, handling file existence and parse errors."""
    if not file_path.is_file():
        raise ConfigError(f"Configuration file not found: {file_path}")
    try:
        with open(file_path, "r", encoding="utf-8") as f:
            data = yaml.safe_load(f)
            if data is None:
                raise ConfigError(f"Configuration file is empty: {file_path}")
            if not isinstance(data, dict):
                raise ConfigError(
                    f"Configuration file must contain a YAML mapping (dictionary): {file_path}"
                )
            return data
    except yaml.YAMLError as e:
        raise ConfigError(f"YAML parsing error in {file_path}: {e}") from e


def load_config(config_path_or_dir: Optional[str] = None) -> ConfigBundle:
    """
    Load, parse, and strictly validate the configuration bundle.

    Resolution order for config path:
    1. Explicit argument `config_path_or_dir`
    2. Environment variable `PULSECHECK_CONFIG`
    3. Default workspace path: `./config`
    """
    settings = PulseCheckSettings()

    if config_path_or_dir:
        resolved_path = Path(config_path_or_dir)
    elif settings.pulsecheck_config:
        resolved_path = Path(settings.pulsecheck_config)
    else:
        resolved_path = Path("config")

    if resolved_path.is_file():
        config_dir = resolved_path.parent
    else:
        config_dir = resolved_path

    dataset_path = config_dir / "dataset.yaml"
    rules_path = config_dir / "rules.yaml"
    checks_path = config_dir / "checks.yaml"
    cities_path = config_dir / "cities.yaml"

    try:
        dataset_data = _load_yaml_file(dataset_path)
        dataset_file = DatasetConfigFile.model_validate(dataset_data)
    except ValidationError as e:
        raise ConfigError(f"Invalid dataset configuration in {dataset_path}:\n{e}") from e

    try:
        rules_data = _load_yaml_file(rules_path)
        rules_file = RulesConfigFile.model_validate(rules_data)
    except ValidationError as e:
        raise ConfigError(f"Invalid rules configuration in {rules_path}:\n{e}") from e

    try:
        checks_data = _load_yaml_file(checks_path)
        checks_file = ChecksConfigFile.model_validate(checks_data)
    except ValidationError as e:
        raise ConfigError(f"Invalid batch checks configuration in {checks_path}:\n{e}") from e

    if cities_path.is_file():
        try:
            cities_data = _load_yaml_file(cities_path)
            cities_file = CitiesConfigFile.model_validate(cities_data)
        except ValidationError as e:
            raise ConfigError(f"Invalid cities configuration in {cities_path}:\n{e}") from e
    else:
        cities_file = CitiesConfigFile(cities=[])

    faults_path = config_dir / "faults.yaml"
    faults_file: Optional[FaultsConfigFile] = None
    if faults_path.is_file():
        try:
            faults_data = _load_yaml_file(faults_path)
            faults_file = FaultsConfigFile.model_validate(faults_data)
        except ValidationError as e:
            raise ConfigError(f"Invalid faults configuration in {faults_path}:\n{e}") from e

    # Cross-validation: Check identity roles exist in configured columns
    dataset_roles = {col.role for col in dataset_file.dataset.columns}
    for identity_role in dataset_file.dataset.identity_roles:
        if identity_role not in dataset_roles:
            raise ConfigError(
                f"Identity role '{identity_role}' declared in identity_roles does not match "
                f"any column role in {dataset_path}. Available roles: {sorted(dataset_roles)}"
            )

    return ConfigBundle(
        dataset_file=dataset_file,
        rules_file=rules_file,
        checks_file=checks_file,
        cities_file=cities_file,
        settings=settings,
        config_dir=config_dir,
        faults_file=faults_file,
    )
