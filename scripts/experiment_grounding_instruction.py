"""Finding 2 controlled experiment ("Diagnose and Fix Canonical v1
Evidence/Fabrication Findings" checkpoint): tests whether an explicit
grounding instruction in the system prompt eliminates the fabrication
observed in `codex-canonical-v1`'s two flask cases ("What implements
Scaffold?", "Architecture of Flask?" -- both large candidate-entity
sets with very sparse real relationships: 52 entities/5 relationships,
80 entities/1 relationship respectively).

Everything else identical to the real run: same corpus queries, same
real retrieval (correct `EvidenceStore` reused throughout -- see
`tests/test_scip_evidence_propagation.py` for why that matters), same
model, same response schema. Only the system prompt's instructions are
different, with one added sentence-level grounding rule.

**Result**: fabrication persisted under the candidate instruction (see
`docs/canonical-benchmark-v1-findings-report.md` §Finding 2 for the
full before/after). This is evidence *against* a fixable prompt-contract
defect, not for one -- kept as a real, reproducible negative result,
not silently discarded. No change was made to `codex.llm.openai_gateway`
as a result.
"""

from __future__ import annotations

import json
import os
import sys
import urllib.request
from datetime import UTC, datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from codex.benchmark.canonical_corpus import make_flask_repository  # noqa: E402
from codex.evidence.store import InMemoryEvidenceStore  # noqa: E402
from codex.ingestion.pipeline import IngestionPipeline  # noqa: E402
from codex.llm.schema import StructuredAnswer  # noqa: E402
from codex.planner.planner import execute_query, plan_query  # noqa: E402
from codex.provider.scip_adapter import SCIPAdapter  # noqa: E402
from codex.query_understanding.engine import understand_query  # noqa: E402
from codex.registry.registry import CapabilityRegistry  # noqa: E402
from codex.registry.scoring import ProviderScoreProfile  # noqa: E402

NOW = datetime(2026, 9, 3, tzinfo=UTC)

CANDIDATE_INSTRUCTIONS = (
    "You are Codex's structured-answer generator. Respond with a single JSON "
    "object matching exactly this JSON Schema, and nothing else (no markdown "
    "fences, no prose outside the JSON object):\n{schema}\n\n"
    "Grounding rule: every claim's subject/predicate/object MUST correspond to "
    "an actual entry in evidence_package.relationships (backed by "
    "evidence_package.evidence) -- never inferred merely because two entities "
    "both appear in evidence_package.entities. If evidence_package.relationships "
    "contains no relevant entry for part of the query, state that plainly in "
    "the explanation and do not invent a plausible-sounding claim to fill the gap."
)


def run_case(query_text: str) -> None:
    repository = make_flask_repository()
    registry = CapabilityRegistry()
    store = InMemoryEvidenceStore()
    registry.register(
        SCIPAdapter(index_filename="flask_sample.scip"),
        ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3),
    )
    result = IngestionPipeline(registry, store).run(repository)

    understanding = understand_query(
        query_text, repository_id=repository.repository_id, now=NOW
    )
    assert understanding.contract is not None
    plan = plan_query(
        query_contract=understanding.contract,
        graph=result.graph_store,
        ingestion_result=result,
        registry=registry,
        repository=repository,
    )
    package = execute_query(
        plan, graph=result.graph_store, evidence_store=store, ingestion_result=result
    )

    schema = StructuredAnswer.model_json_schema()
    instructions = CANDIDATE_INSTRUCTIONS.format(schema=json.dumps(schema))
    user_content = json.dumps(
        {"query": query_text, "evidence_package": package.model_dump(mode="json")}
    )
    body = {
        "model": "gpt-4o-mini",
        "messages": [
            {"role": "system", "content": instructions},
            {"role": "user", "content": user_content},
        ],
        "response_format": {"type": "json_object"},
        "max_tokens": 4096,
    }
    api_key = os.environ["Codex_open_API_key"]
    request = urllib.request.Request(
        "https://api.openai.com/v1/chat/completions",
        data=json.dumps(body).encode("utf-8"),
        method="POST",
        headers={"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"},
    )
    with urllib.request.urlopen(request, timeout=60) as response:
        payload = json.loads(response.read().decode("utf-8"))
    content = payload["choices"][0]["message"]["content"]
    answer = StructuredAnswer.model_validate_json(content)

    real_ids = {e.canonical_id for e in package.entities}
    grounded = sum(1 for c in answer.claims if c.subject in real_ids and c.object in real_ids)
    fabricated = len(answer.claims) - grounded

    print(f"=== {query_text} ===")
    print(f"real relationships in package: {len(package.relationships)} "
          f"/ real entities: {len(package.entities)}")
    print(f"claims: {len(answer.claims)}  grounded: {grounded}  fabricated: {fabricated}")
    for claim in answer.claims:
        if claim.subject not in real_ids or claim.object not in real_ids:
            print(f"  UNGROUNDED: {claim.subject} {claim.predicate} {claim.object}")
    print()


def main() -> int:
    run_case("What implements Scaffold?")
    run_case("Architecture of Flask?")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
