"""Post-hoc claim verification and breakdown analysis for
`benchmark_runs/expansion_v1_openai_run.json` — mirrors `scripts/
analyze_canonical_run.py`, extended with abstention-correctness scoring
(does the model's own claims/explanation match the ground truth's
`should_abstain` flag?) and breakdowns by fan-out bucket and hop depth.
Read-only; never modifies the run artifact or the corpus.
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
from codex.benchmark.expansion_corpus import make_itsdangerous_repository  # noqa: E402
from codex.benchmark.models import DevelopmentCorpus  # noqa: E402
from codex.evidence.store import InMemoryEvidenceStore  # noqa: E402
from codex.ingestion.pipeline import IngestionPipeline  # noqa: E402
from codex.ontology.relationships import RelationshipType  # noqa: E402
from codex.provider.scip_adapter import SCIPAdapter  # noqa: E402
from codex.registry.registry import CapabilityRegistry  # noqa: E402
from codex.registry.scoring import ProviderScoreProfile  # noqa: E402

ARTIFACT_PATH = REPO_ROOT / "benchmark_runs" / "expansion_v1_openai_run.json"
CORPUS_PATH = REPO_ROOT / "tests" / "fixtures" / "benchmark" / "codex_expansion_corpus_v1.json"

_HOP_DEPTH = {
    "FIND_CALLERS": 1,
    "FIND_TESTS": 1,
    "FIND_DEPENDENCIES": 1,
    "FIND_IMPLEMENTATIONS": 1,
    "FIND_REFERENCES": 1,
    "ARCHITECTURE_ANALYSIS": 2,
}


def _ingest_graphs():
    codex_result, _r, _e, _repo = ingest_codex_self()

    def scip(make_repo, index_filename):
        registry = CapabilityRegistry()
        registry.register(
            SCIPAdapter(index_filename=index_filename),
            ProviderScoreProfile(evidence_quality=0.9, cost_factor=0.3),
        )
        return IngestionPipeline(registry, InMemoryEvidenceStore()).run(make_repo())

    return {
        "codex": codex_result.graph_store,
        "click": scip(make_click_repository, "click_sample.scip").graph_store,
        "flask": scip(make_flask_repository, "flask_sample.scip").graph_store,
        "itsdangerous": scip(make_itsdangerous_repository, "itsdangerous_sample.scip").graph_store,
    }


def _resolve(graph, text):
    """Resolve a claim's subject/object text to a real canonical_id, by
    canonical_id, exact qualified_name, or exact bare `.name` match (the
    three identifier formats observed in real model output -- e.g.
    `BadSignature#` is a real entity's bare `.name`, not a fabrication,
    even though it is neither a canonical_id nor a full qualified_name)."""
    if text is None:
        return None
    entity = graph.get_entity(text)
    if entity is not None:
        return entity.canonical_id
    for e in graph.find_entities():
        if (
            e.qualified_name == text
            or e.qualified_name.rstrip(".()") == text.rstrip(".()")
            or e.name == text
            or e.name.rstrip(".()") == text.rstrip(".()")
        ):
            return e.canonical_id
    return None


def main() -> int:
    artifact = json.loads(ARTIFACT_PATH.read_text())
    dimensions = artifact["dimensions"]
    corpus = DevelopmentCorpus.model_validate_json(CORPUS_PATH.read_bytes())
    graphs = _ingest_graphs()

    rows = []
    for repo_id, record in artifact["records_by_repository"].items():
        graph = graphs[repo_id]
        for qid, result in record["results"].items():
            case = corpus.corpus.cases[qid]
            category = corpus.categories[qid].value
            label = corpus.corpus.labels[qid]
            sa = result.get("structured_answer")
            fan_out = len(label.relevant_entity_ids or []) if not label.should_abstain else None

            grounded = fabricated = real_edge = 0
            if sa is not None:
                for claim in sa["claims"]:
                    subj_id = _resolve(graph, claim["subject"])
                    obj_id = _resolve(graph, claim["object"])
                    if label.should_abstain:
                        continue  # scored separately as abstention correctness
                    if subj_id is not None and obj_id is not None:
                        grounded += 1
                        try:
                            predicate = RelationshipType(claim["predicate"])
                        except ValueError:
                            predicate = None
                        if predicate is not None and graph.get_relationships(
                            subject=subj_id, predicate=predicate, object_id=obj_id
                        ):
                            real_edge += 1
                    else:
                        fabricated += 1

            # Abstention correctness: for should_abstain cases, did the
            # model produce zero *grounded* claims (correct) or invent one
            # that resolves to real, unrelated entities (a genuine false
            # positive, distinct from an honest "no evidence" placeholder)?
            abstention_false_positive = None
            if label.should_abstain and sa is not None:
                false_positive_claims = 0
                for claim in sa["claims"]:
                    subj_id = _resolve(graph, claim["subject"])
                    obj_id = _resolve(graph, claim["object"])
                    if subj_id is not None and obj_id is not None:
                        false_positive_claims += 1
                abstention_false_positive = false_positive_claims > 0

            rows.append(
                {
                    "repository": repo_id,
                    "category": category,
                    "query_text": case.query_text,
                    "dimension": dimensions[qid],
                    "hop_depth": _HOP_DEPTH.get(category),
                    "should_abstain": label.should_abstain,
                    "fan_out": fan_out,
                    "usage_total_tokens": result.get("usage_total_tokens"),
                    "claims": len(sa["claims"]) if sa else 0,
                    "grounded": grounded,
                    "fabricated": fabricated,
                    "real_edge": real_edge,
                    "abstention_false_positive": abstention_false_positive,
                }
            )

    print(f"{'repo':13s} {'category':22s} {'hop':4s} {'fan_out':8s} {'grounded':9s} "
          f"{'fabricated':11s} {'real_edge':10s} {'FP-abstain':11s} tokens  query")
    for r in rows:
        print(
            f"{r['repository']:13s} {r['category']:22s} {str(r['hop_depth']):4s} "
            f"{str(r['fan_out']):8s} {r['grounded']:9d} {r['fabricated']:11d} "
            f"{r['real_edge']:10d} {str(r['abstention_false_positive']):11s} "
            f"{r['usage_total_tokens']:6d}  {r['query_text']!r}"
        )

    print("\n=== candidate_count (fan_out) -> fabrication_rate (positive cases only) ===")
    for r in sorted((r for r in rows if not r["should_abstain"]), key=lambda r: r["fan_out"] or 0):
        total = r["claims"] or 1
        print(f"  fan_out={r['fan_out']:4}  fabrication_rate={r['fabricated']/total:.2f}  "
              f"({r['fabricated']}/{r['claims']})  {r['query_text']!r}")

    print("\n=== hop_depth -> grounding rate (positive cases only) ===")
    by_hop: dict[int, list[dict]] = {}
    for r in rows:
        if r["should_abstain"]:
            continue
        by_hop.setdefault(r["hop_depth"], []).append(r)
    for hop, items in sorted(by_hop.items()):
        total_claims = sum(i["claims"] for i in items)
        total_grounded = sum(i["grounded"] for i in items)
        rate = total_grounded / total_claims if total_claims else float("nan")
        print(f"  hop_depth={hop}  grounded={total_grounded}/{total_claims} ({rate:.2f})  "
              f"n_cases={len(items)}")

    print("\n=== Abstention correctness (5 should_abstain cases) ===")
    n_correct = sum(1 for r in rows if r["should_abstain"] and not r["abstention_false_positive"])
    n_total = sum(1 for r in rows if r["should_abstain"])
    print(f"  correctly abstained (no grounded-real claim): {n_correct}/{n_total}")
    for r in rows:
        if r["should_abstain"]:
            print(f"    {r['query_text']!r}: false_positive={r['abstention_false_positive']}, "
                  f"claims={r['claims']}")

    print("\n=== Totals (positive cases only) ===")
    positive = [r for r in rows if not r["should_abstain"]]
    print(json.dumps({
        "claims": sum(r["claims"] for r in positive),
        "grounded": sum(r["grounded"] for r in positive),
        "fabricated": sum(r["fabricated"] for r in positive),
        "real_edge": sum(r["real_edge"] for r in positive),
        "total_tokens_all_14": sum(r["usage_total_tokens"] for r in rows),
    }, indent=2))

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
