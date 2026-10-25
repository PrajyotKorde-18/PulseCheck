"""Unit tests for third-party adapters (indpy and pandera)."""

import pandas as pd
import pytest

from pulsecheck.adapters.indpy_adapter import indpy_adapter
from pulsecheck.adapters.pandera_adapter import pandera_adapter


@pytest.mark.unit
def test_indpy_adapter_with_library_generated_values() -> None:
    # Source of truth: test values generated directly by indpy's official generator
    gen_mobile = indpy_adapter.generate_mobile()
    gen_pincode = indpy_adapter.generate_pincode()
    gen_plate = indpy_adapter.generate_plate()

    # Verify generated values pass validation
    assert indpy_adapter.is_valid_mobile(gen_mobile) is True
    assert indpy_adapter.is_valid_pincode(gen_pincode) is True
    assert indpy_adapter.is_valid_plate(gen_plate) is True

    # Corrupted / invalid values
    assert indpy_adapter.is_valid_mobile("12345") is False
    assert indpy_adapter.is_valid_mobile(None) is False
    assert indpy_adapter.is_valid_pincode("000000") is False
    assert indpy_adapter.is_valid_pincode(None) is False
    assert indpy_adapter.is_valid_plate("INVALID_PLATE") is False
    assert indpy_adapter.is_valid_plate(None) is False


@pytest.mark.unit
def test_pandera_adapter_validation() -> None:
    schema = pandera_adapter.build_schema(
        required_columns=["id_col"],
        type_specs={"id_col": str, "val_col": int},
    )

    # Valid DataFrame
    valid_df = pd.DataFrame({"id_col": ["a", "b"], "val_col": [10, 20]})
    passed, errors = pandera_adapter.validate_dataframe(valid_df, schema)
    assert passed is True
    assert len(errors) == 0

    # Invalid DataFrame: wrong type in val_col
    invalid_df = pd.DataFrame({"id_col": ["a", "b"], "val_col": ["NOT_INT", "ALSO_NOT"]})
    passed, errors = pandera_adapter.validate_dataframe(invalid_df, schema)
    assert passed is False
    assert len(errors) > 0
