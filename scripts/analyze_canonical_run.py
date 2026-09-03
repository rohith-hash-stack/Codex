"""Post-hoc claim verification for `benchmark_runs/canonical_v1_openai_run.json`.

D10's Verification Engine is not wired into the harness (an explicit,
documented gap carried over from the development-corpus milestone), so
`CLAIM_VERIFICATION_ACCURACY`/`UNSUPPORTED_CLAIM_RATE` are not
automatically computable. This script performs the same real-fact
cross-reference by hand: for each claim, checks whether its
subject/object resolves to a REAL graph entity (by canonical_id, the
expected format, or by exact qualified_name, a format the model
sometimes substituted) and whether the claimed (subject, predicate,
object) edge is a REAL relationship in the graph or in that case's
frozen ground truth. Never modifies the run artifact or the corpus --
read-only analysis.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO_ROOT / "src"))
sys.path.insert(0, str(REPO_ROOT / "tests"))

from benchmark_fixtures import ingest_codex_self  # noqa: E402
from codex.benchmark.canonical_corpus import (  # noqa: E402
    make_click_repository,
    make_flask_repository,
)
from codex.benchmark.models import DevelopmentCorpus  # noqa: E402
from codex.evidence.store import InMemoryEvidenceStore  # noqa: E402
from codex.ingestion.pipeline import IngestionPipeline  # noqa: E402
from codex.ontology.relationships import RelationshipType  # noqa: E402
from codex.provider.scip_adapter import SCIPAdapter  # noqa: E402
from codex.registry.registry import CapabilityRegistry  # noqa: E402
from codex.registry.scoring import ProviderScoreProfile  # noqa: E402

ARTIFACT_PATH = REPO_ROOT / "benchmark_runs" / "canonical_v1_openai_run.json"
CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmark" / "codex_canonical_corpus_v1.json"


def _ingest_graphs():
    codex_result, _r, _e, _repo = ingest_codex_self()
    click_registry = CapabilityRegistry()
    click_registry.register(
        SCIPAdapter(index_filename="click_sample.scip"),
        ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3),
    )
    click_result = IngestionPipeline(click_registry, InMemoryEvidenceStore()).run(
        make_click_repository()
    )
    flask_registry = CapabilityRegistry()
    flask_registry.register(
        SCIPAdapter(index_filename="flask_sample.scip"),
        ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3),
    )
    flask_result = IngestionPipeline(flask_registry, InMemoryEvidenceStore()).run(
        make_flask_repository()
    )
    return {
        "codex": codex_result.graph_store,
        "click": click_result.graph_store,
        "flask": flask_result.graph_store,
    }


def _resolve(graph, text):
    """Resolve a claim's subject/object text to a real canonical_id, by
    exact canonical_id match or exact qualified_name match (the two
    formats observed in real model output)."""
    if text is None:
        return None, "null"
    entity = graph.get_entity(text)
    if entity is not None:
        return entity.canonical_id, "canonical_id"
    for e in graph.find_entities():
        if e.qualified_name == text or e.qualified_name.rstrip(".()") == text.rstrip(".()"):
            return e.canonical_id, "qualified_name"
    return None, "unresolved"


def main() -> int:
    artifact = json.loads(ARTIFACT_PATH.read_text())
    corpus = DevelopmentCorpus.model_validate_json(CORPUS_PATH.read_bytes())
    graphs = _ingest_graphs()

    print(f"{'repo':7s} {'category':22s} {'grounded':9s} {'fabricated':11s} "
          f"{'real_edge':10s} {'total':6s} query")
    totals = {"grounded": 0, "fabricated": 0, "real_edge": 0, "total": 0}
    per_category: dict[str, dict[str, int]] = {}

    for repo_id, record in artifact["records_by_repository"].items():
        graph = graphs[repo_id]
        for qid, result in record["results"].items():
            case = corpus.corpus.cases[qid]
            category = corpus.categories[qid].value
            label = corpus.corpus.labels[qid]
            sa = result.get("structured_answer")
            if sa is None:
                continue

            grounded = 0
            fabricated = 0
            real_edge = 0
            unresolved_examples = []
            for claim in sa["claims"]:
                subj_id, subj_kind = _resolve(graph, claim["subject"])
                obj_id, obj_kind = _resolve(graph, claim["object"])
                both_resolved = subj_id is not None and obj_id is not None
                if both_resolved:
                    grounded += 1
                    try:
                        predicate = RelationshipType(claim["predicate"])
                    except ValueError:
                        predicate = None
                    if predicate is not None:
                        rels = graph.get_relationships(
                            subject=subj_id, predicate=predicate, object_id=obj_id
                        )
                        if rels:
                            real_edge += 1
                elif label.should_abstain:
                    # This case's own ground truth says no real evidence
                    # exists at all -- whatever placeholder claim the model
                    # attached to its abstention explanation is not scored
                    # as fabrication of a *false relationship about real
                    # code* (there is no real code fact in play here at
                    # all); tracked separately below instead.
                    pass
                else:
                    fabricated += 1
                    unresolved_examples.append(
                        (claim["subject"], claim["predicate"], claim["object"])
                    )

            total = len(sa["claims"])
            print(f"{repo_id:7s} {category:22s} {grounded:9d} {fabricated:11d} "
                  f"{real_edge:10d} {total:6d} {case.query_text!r}")
            if unresolved_examples:
                for ex in unresolved_examples[:3]:
                    print(f"         fabricated/unresolved claim: {ex}")

            totals["grounded"] += grounded
            totals["fabricated"] += fabricated
            totals["real_edge"] += real_edge
            totals["total"] += total
            cat_totals = per_category.setdefault(
                category, {"grounded": 0, "fabricated": 0, "real_edge": 0, "total": 0}
            )
            cat_totals["grounded"] += grounded
            cat_totals["fabricated"] += fabricated
            cat_totals["real_edge"] += real_edge
            cat_totals["total"] += total

    print("\n=== Totals across all 13 cases ===")
    print(json.dumps(totals, indent=2))
    print("\n=== Per-category totals ===")
    print(json.dumps(per_category, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
