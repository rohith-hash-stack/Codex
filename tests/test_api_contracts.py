"""Contract round-trip tests for `codex.api.contracts` (VS Code + Nervous-
System scope change, `docs/vscode-nervous-system-architecture.md` §11).

Exercises `CodexAPI._to_node`/`_to_edge` -- the conversion functions
that are the *entire* API-vs-internal boundary for entity/relationship
shape -- directly against real `RepositorySymbol`/`CanonicalRelationship`
values, confirming every field round-trips without loss or invention.
"""

from __future__ import annotations

from codex.api.service import CodexAPI
from codex.evidence.model import CanonicalRelationship, EvidenceStatus
from codex.ontology.entities import BaseEntityType, RepositorySymbol, SourceLocation
from codex.ontology.relationships import RelationshipType


def _entity(**overrides: object) -> RepositorySymbol:
    defaults: dict[str, object] = {
        "canonical_id": "repo1@rev1:pkg.Foo#bar().",
        "repository_id": "repo1",
        "repository_revision": "rev1",
        "name": "bar",
        "qualified_name": "pkg.Foo#bar().",
        "base_type": BaseEntityType.METHOD,
        "language": "python",
        "roles": ["ENTRY_POINT"],
        "source_location": SourceLocation(file_path="pkg/foo.py", start_line=10, end_line=12),
    }
    defaults.update(overrides)
    return RepositorySymbol(**defaults)  # type: ignore[arg-type]


def _relationship(**overrides: object) -> CanonicalRelationship:
    defaults: dict[str, object] = {
        "subject": "repo1@rev1:pkg.Foo#bar().",
        "predicate": RelationshipType.CALLS,
        "object": "repo1@rev1:pkg.Foo#baz().",
        "confidence": 0.75,
        "status": EvidenceStatus.SUPPORTED,
        "supporting_evidence_ids": ["ev1", "ev2"],
        "contradicting_evidence_ids": ["ev3"],
    }
    defaults.update(overrides)
    return CanonicalRelationship(**defaults)  # type: ignore[arg-type]


class TestVisualizationNode:
    def test_round_trips_every_field_without_inventing_data(self) -> None:
        entity = _entity()
        node = CodexAPI._to_node(entity, distance=2)
        assert node.id == entity.canonical_id
        assert node.name == entity.name
        assert node.qualified_name == entity.qualified_name
        assert node.node_type is BaseEntityType.METHOD
        assert node.roles == ["ENTRY_POINT"]
        assert node.language == "python"
        assert node.source_location == entity.source_location
        assert node.distance == 2

    def test_absent_optional_fields_stay_absent_not_fabricated(self) -> None:
        entity = _entity(language=None, roles=[], source_location=None)
        node = CodexAPI._to_node(entity, distance=0)
        assert node.language is None
        assert node.roles == []
        assert node.source_location is None

    def test_node_type_is_the_real_ontology_enum(self) -> None:
        for base_type in (BaseEntityType.CLASS, BaseEntityType.FUNCTION, BaseEntityType.FILE):
            entity = _entity(base_type=base_type, canonical_id=f"x:{base_type.value}")
            node = CodexAPI._to_node(entity, distance=0)
            assert node.node_type is base_type


class TestVisualizationEdge:
    def test_round_trips_every_field_without_inventing_data(self) -> None:
        relationship = _relationship()
        edge = CodexAPI._to_edge(relationship)
        assert edge.source == relationship.subject
        assert edge.target == relationship.object
        assert edge.relationship_type is RelationshipType.CALLS
        assert edge.status is EvidenceStatus.SUPPORTED
        assert edge.confidence == 0.75
        assert edge.evidence_count == 3
        assert edge.id == f"{relationship.subject}|CALLS|{relationship.object}"

    def test_zero_evidence_reports_zero_not_fabricated(self) -> None:
        relationship = _relationship(supporting_evidence_ids=[], contradicting_evidence_ids=[])
        edge = CodexAPI._to_edge(relationship)
        assert edge.evidence_count == 0

    def test_relationship_type_is_the_real_ontology_enum(self) -> None:
        for predicate in (RelationshipType.IMPLEMENTS, RelationshipType.REFERENCES):
            relationship = _relationship(predicate=predicate)
            edge = CodexAPI._to_edge(relationship)
            assert edge.relationship_type is predicate
