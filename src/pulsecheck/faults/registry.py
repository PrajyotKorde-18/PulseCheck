"""Registry for fault injection plugins."""

from typing import Dict, List, Optional

from pulsecheck.faults.base import FaultPlugin
from pulsecheck.faults.plugins import (
    BadMobileFault,
    BadPinFault,
    BadPlateFault,
    DroppedRequiredColumnFault,
    DuplicateIdsFault,
    FutureTimestampsFault,
    MissingCityFault,
    NullIdFault,
    OutOfBoxCoordinatesFault,
    StaleBatchFault,
    SwappedLatLonFault,
    SwappedTimesFault,
    VolumeDropFault,
)


class FaultPluginRegistry:
    """Manages available fault injection plugins."""

    def __init__(self) -> None:
        self._plugins: Dict[str, FaultPlugin] = {}
        self._register_defaults()

    def register(self, name: str, plugin: FaultPlugin) -> None:
        """Register a new fault plugin by unique name."""
        self._plugins[name] = plugin

    def get(self, name: str) -> Optional[FaultPlugin]:
        """Retrieve plugin instance by name."""
        return self._plugins.get(name)

    def list_plugins(self) -> List[str]:
        """List registered plugin names."""
        return list(self._plugins.keys())

    def _register_defaults(self) -> None:
        self.register("null_id", NullIdFault())
        self.register("swapped_times", SwappedTimesFault())
        self.register("swapped_lat_lon", SwappedLatLonFault())
        self.register("out_of_box_coordinates", OutOfBoxCoordinatesFault())
        self.register("bad_mobile", BadMobileFault())
        self.register("bad_pin", BadPinFault())
        self.register("bad_plate", BadPlateFault())
        self.register("duplicate_ids", DuplicateIdsFault())
        self.register("future_timestamps", FutureTimestampsFault())
        self.register("dropped_required_column", DroppedRequiredColumnFault())
        self.register("missing_city", MissingCityFault())
        self.register("volume_drop", VolumeDropFault())
        self.register("stale_batch", StaleBatchFault())


# Global singleton registry
fault_registry = FaultPluginRegistry()
