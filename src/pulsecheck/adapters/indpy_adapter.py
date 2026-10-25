"""Adapter module encapsulating indpy for Indian mobile, PIN, and vehicle plate validation."""

from typing import Any, Optional

import indpy


class IndPyAdapter:
    """Provides validation and synthetic generation of Indian national identifiers."""

    def __init__(self) -> None:
        self._generator: Optional[indpy.Generate] = None

    def _get_generator(self) -> indpy.Generate:
        if self._generator is None:
            self._generator = indpy.Generate()
        return self._generator

    def is_valid_mobile(self, value: Any) -> bool:
        """Validate 10-digit Indian mobile number."""
        if value is None:
            return False
        val_str = str(value).strip()
        if not val_str:
            return False
        return bool(indpy.is_mobile(val_str))

    def is_valid_pincode(self, value: Any) -> bool:
        """Validate 6-digit Indian postal PIN code."""
        if value is None:
            return False
        val_str = str(value).strip()
        if not val_str:
            return False
        return bool(indpy.is_pincode(val_str))

    def is_valid_plate(self, value: Any) -> bool:
        """Validate Indian motor vehicle registration plate."""
        if value is None:
            return False
        val_str = str(value).strip().replace(" ", "").upper()
        if not val_str:
            return False
        return bool(indpy.is_vehicle(val_str))

    def generate_mobile(self) -> str:
        """Generate a valid Indian mobile number directly from indpy's generator."""
        return str(self._get_generator().mobile())

    def generate_pincode(self) -> str:
        """Generate a valid Indian PIN code directly from indpy's generator."""
        return str(self._get_generator().pincode())

    def generate_plate(self) -> str:
        """Generate a valid Indian vehicle plate directly from indpy's generator."""
        return str(self._get_generator().vehicle())


# Global singleton instance
indpy_adapter = IndPyAdapter()
