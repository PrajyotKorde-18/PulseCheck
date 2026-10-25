"""Unit tests for deterministic and order-independent composite key derivation."""

import pandas as pd
import pytest

from pulsecheck.validator import derive_composite_key


@pytest.mark.unit
def test_single_role_identity_derivation() -> None:
    df = pd.DataFrame({"trip_id": ["T-001", "T-002", None]})
    role_to_col = {"id": "trip_id"}
    keys = derive_composite_key(df, identity_roles=["id"], role_to_col=role_to_col)
    assert keys.tolist() == ["T-001", "T-002", ""]


@pytest.mark.unit
def test_composite_key_is_deterministic_and_order_independent() -> None:
    df = pd.DataFrame(
        {
            "vendor": ["V1", "V2"],
            "pickup": ["2026-05-10 10:00:00", "2026-05-10 11:00:00"],
            "zone": ["Z100", "Z200"],
        }
    )
    role_to_col = {
        "vendor_role": "vendor",
        "time_role": "pickup",
        "zone_role": "zone",
    }

    # Order 1: vendor, time, zone
    order_a = ["vendor_role", "time_role", "zone_role"]
    keys_a = derive_composite_key(df, identity_roles=order_a, role_to_col=role_to_col)

    # Order 2: reversed (zone, time, vendor)
    order_b = ["zone_role", "time_role", "vendor_role"]
    keys_b = derive_composite_key(df, identity_roles=order_b, role_to_col=role_to_col)

    # Order 3: arbitrary permutation
    order_c = ["time_role", "zone_role", "vendor_role"]
    keys_c = derive_composite_key(df, identity_roles=order_c, role_to_col=role_to_col)

    # All three must produce identical SHA-256 hashes
    assert keys_a.tolist() == keys_b.tolist()
    assert keys_a.tolist() == keys_c.tolist()

    # Must be 64-character SHA-256 hex strings
    expected_hex_length = 64
    assert len(keys_a.iloc[0]) == expected_hex_length
