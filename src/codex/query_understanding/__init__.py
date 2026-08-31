"""The Query Understanding Engine (TAD §22-28; HLRD §24-29; directive D8).

The controlled boundary between natural-language input and the
repository intelligence system: transforms a query into a validated
`QueryContract`, and nothing else. See `engine.py`'s module docstring.
"""

from codex.query_understanding.complexity import compute_complexity
from codex.query_understanding.engine import (
    QueryUnderstandingResult,
    UnderstandingStatus,
    understand_query,
)
from codex.query_understanding.models import (
    AmbiguityCandidate,
    ComplexityFactors,
    Intent,
    QueryContract,
    TemporalDimension,
)
from codex.query_understanding.session import SessionContext, SessionEntry
from codex.query_understanding.slm import SLMAdapter, SLMInterpretation
from codex.query_understanding.tier0 import Tier0Candidate

__all__ = [
    "AmbiguityCandidate",
    "ComplexityFactors",
    "Intent",
    "QueryContract",
    "QueryUnderstandingResult",
    "SLMAdapter",
    "SLMInterpretation",
    "SessionContext",
    "SessionEntry",
    "TemporalDimension",
    "Tier0Candidate",
    "UnderstandingStatus",
    "compute_complexity",
    "understand_query",
]
