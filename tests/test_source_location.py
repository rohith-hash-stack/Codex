"""Behavioral tests for the canonical `SourceLocation` contract (Phase D
directive "SourceLocation Closure"): 0-based, half-open coordinates,
line-only vs full-precision locations, and deterministic rejection of
malformed ranges. Provider-specific range *conversion* (SCIP's own
0-based half-open producer behavior) is already covered by
`test_scip_index.py`/`test_scip_adapter.py` and is not duplicated here.
"""

import pytest
from pydantic import ValidationError

from codex.ontology.entities import SourceLocation


def test_line_only_location_is_valid() -> None:
    location = SourceLocation(file_path="src/a.py", start_line=4, end_line=4)
    assert location.start_column is None
    assert location.end_column is None


def test_full_precision_location_is_valid() -> None:
    location = SourceLocation(
        file_path="src/a.py", start_line=4, end_line=4, start_column=2, end_column=10
    )
    assert location.start_column == 2
    assert location.end_column == 10


def test_zero_width_location_is_valid() -> None:
    """A start position equal to its end position is a valid (empty) range."""
    location = SourceLocation(
        file_path="src/a.py", start_line=4, end_line=4, start_column=6, end_column=6
    )
    assert location.start_column == location.end_column


def test_multi_line_range_is_valid_without_column_ordering_constraint() -> None:
    """Across different lines, start_column may exceed end_column -- the
    ordering constraint only applies when start_line == end_line, since
    lines are independent coordinate spaces."""
    location = SourceLocation(
        file_path="src/a.py", start_line=4, end_line=6, start_column=50, end_column=0
    )
    assert location.start_line == 4
    assert location.end_line == 6


def test_end_line_before_start_line_is_rejected() -> None:
    with pytest.raises(ValidationError, match="end_line"):
        SourceLocation(file_path="src/a.py", start_line=10, end_line=2)


def test_end_column_before_start_column_on_same_line_is_rejected() -> None:
    with pytest.raises(ValidationError, match="end_column"):
        SourceLocation(
            file_path="src/a.py", start_line=4, end_line=4, start_column=10, end_column=2
        )


def test_partial_column_precision_is_rejected() -> None:
    with pytest.raises(ValidationError, match="start_column and end_column"):
        SourceLocation(file_path="src/a.py", start_line=4, end_line=4, start_column=2)
    with pytest.raises(ValidationError, match="start_column and end_column"):
        SourceLocation(file_path="src/a.py", start_line=4, end_line=4, end_column=2)


@pytest.mark.parametrize(
    "field", ["start_line", "end_line", "start_column", "end_column"]
)
def test_negative_coordinates_are_rejected(field: str) -> None:
    kwargs = {"file_path": "src/a.py", "start_line": 0, "end_line": 0}
    if field in {"start_column", "end_column"}:
        kwargs["start_column"] = 0
        kwargs["end_column"] = 0
    kwargs[field] = -1
    with pytest.raises(ValidationError):
        SourceLocation(**kwargs)


def test_normalization_is_deterministic() -> None:
    """The same inputs always produce structurally equal, reproducible output."""
    first = SourceLocation(
        file_path="src/a.py", start_line=1, end_line=1, start_column=0, end_column=5
    )
    second = SourceLocation(
        file_path="src/a.py", start_line=1, end_line=1, start_column=0, end_column=5
    )
    assert first == second
