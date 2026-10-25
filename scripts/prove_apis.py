"""Verification script proving real Pandera and indpy APIs."""

import indpy
import pandas as pd
import pandera
import pandera.pandas as pa


def main() -> None:
    # 1. indpy Verification
    gen = indpy.Generate()
    real_mobile = gen.mobile()
    real_pincode = gen.pincode()
    real_plate = gen.vehicle()

    print("=== indpy Generator & Validator Proof ===")
    print(f"Generated Mobile:  {real_mobile} -> Valid: {indpy.is_mobile(str(real_mobile))}")
    print(f"Generated Pincode: {real_pincode} -> Valid: {indpy.is_pincode(str(real_pincode))}")
    print(f"Generated Plate:   {real_plate} -> Valid: {indpy.is_vehicle(str(real_plate))}")

    # 2. Pandera Verification
    schema = pa.DataFrameSchema(
        {
            "id": pa.Column(str),
            "amount": pa.Column(float, pa.Check.greater_than(0.0)),
        }
    )
    df = pd.DataFrame({"id": ["A1", "A2"], "amount": [10.5, 42.0]})
    validated = schema.validate(df)
    print("\n=== Pandera DataFrameSchema Proof ===")
    print(f"Pandera Version: {pandera.__version__}")
    print("Validated rows:")
    print(validated)


if __name__ == "__main__":
    main()
