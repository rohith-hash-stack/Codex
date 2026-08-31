from codex.verification.confidence import (
    CONTRADICTION_CAP,
    CONTRADICTION_SIGNIFICANT_THRESHOLD,
    CONTRADICTION_WEAK_THRESHOLD,
    V_WEIGHTS,
    ContradictionLevel,
    VerificationFactors,
    classify_contradiction,
    compute_confidence,
    compute_factors,
)
from codex.verification.contradiction import ContradictionHandlingResult, handle_contradictions
from codex.verification.engine import (
    ClaimVerification,
    is_significantly_contradicted,
    verify_claim,
    verify_claims,
)
from codex.verification.entailment import (
    EntailmentMethod,
    EntailmentResult,
    EntailmentStatus,
    direct_edge_match,
    entail_claim,
    find_path,
)
from codex.verification.state import (
    VERIFIED_CONFIDENCE_THRESHOLD,
    VerificationStatus,
    classify_answer,
    classify_claim,
    to_hlrd_label,
    to_routing_bucket,
)

__all__ = [
    "CONTRADICTION_CAP",
    "CONTRADICTION_SIGNIFICANT_THRESHOLD",
    "CONTRADICTION_WEAK_THRESHOLD",
    "VERIFIED_CONFIDENCE_THRESHOLD",
    "V_WEIGHTS",
    "ClaimVerification",
    "ContradictionHandlingResult",
    "ContradictionLevel",
    "EntailmentMethod",
    "EntailmentResult",
    "EntailmentStatus",
    "VerificationFactors",
    "VerificationStatus",
    "classify_answer",
    "classify_claim",
    "classify_contradiction",
    "compute_confidence",
    "compute_factors",
    "direct_edge_match",
    "entail_claim",
    "find_path",
    "handle_contradictions",
    "is_significantly_contradicted",
    "to_hlrd_label",
    "to_routing_bucket",
    "verify_claim",
    "verify_claims",
]
