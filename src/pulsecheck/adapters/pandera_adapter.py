"""Adapter module encapsulating pandera DataFrameSchema validation."""

from typing import Any, Dict, List, Tuple

import pandas as pd
import pandera.pandas as pa
from pandera.errors import SchemaError, SchemaErrors


class PanderaAdapter:
    """Encapsulates pandera schema construction and DataFrame validation."""

    @staticmethod
    def build_schema(
        required_columns: List[str],
        type_specs: Dict[str, Any],
        strict: bool = False,
    ) -> pa.DataFrameSchema:
        """Construct a pandera DataFrameSchema from column specifications."""
        columns: Dict[str, pa.Column] = {}
        for col_name, col_type in type_specs.items():
            is_required = col_name in required_columns
            columns[col_name] = pa.Column(
                col_type,
                nullable=not is_required,
                required=is_required,
            )
        return pa.DataFrameSchema(columns=columns, strict=strict, coerce=False)

    @staticmethod
    def validate_dataframe(
        df: pd.DataFrame,
        schema: pa.DataFrameSchema,
    ) -> Tuple[bool, List[str]]:
        """Validate a pandas DataFrame against a pandera schema, returning success and errors."""
        try:
            schema.validate(df, lazy=True)
            return True, []
        except SchemaErrors as e:
            error_messages = [str(err) for err in e.failure_cases["failure_case"].tolist()]
            return False, error_messages
        except SchemaError as e:
            return False, [str(e)]


pandera_adapter = PanderaAdapter()
