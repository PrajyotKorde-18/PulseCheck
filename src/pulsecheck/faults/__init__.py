"""PulseCheck fault injection subsystem."""

from pulsecheck.faults.base import FaultPlugin, InjectedFaultRecord
from pulsecheck.faults.injector import FaultInjector, InjectionResult
from pulsecheck.faults.registry import FaultPluginRegistry, fault_registry

__all__ = [
    "FaultPlugin",
    "InjectedFaultRecord",
    "FaultPluginRegistry",
    "fault_registry",
    "FaultInjector",
    "InjectionResult",
]
