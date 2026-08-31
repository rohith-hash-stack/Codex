import pytest

from codex.evidence import CanonicalRelationship, EvidenceStatus
from codex.graph import GraphVersion, InMemoryGraphStore
from codex.ontology import BaseEntityType, RelationshipType, RepositorySymbol


def make_symbol(canonical_id: str, name: str) -> RepositorySymbol:
    return RepositorySymbol(
        canonical_id=canonical_id,
        repository_id="repo1",
        repository_revision="abc123",
        name=name,
        qualified_name=f"pkg.{name}",
        base_type=BaseEntityType.FUNCTION,
    )


def make_store() -> InMemoryGraphStore:
    version = GraphVersion(version_id="v1", repository_id="repo1", repository_revision="abc123")
    return InMemoryGraphStore(version)


def test_upsert_and_get_entity() -> None:
    store = make_store()
    symbol = make_symbol("codex:A", "func_a")
    store.upsert_entity(symbol)

    assert store.get_entity("codex:A") == symbol
    assert store.get_entity("codex:missing") is None


def test_upsert_relationship_and_query() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "func_a"))
    store.upsert_entity(make_symbol("codex:B", "func_b"))

    rel = CanonicalRelationship(
        subject="codex:A",
        predicate=RelationshipType.CALLS,
        object="codex:B",
        confidence=0.9,
        status=EvidenceStatus.SUPPORTED,
    )
    store.upsert_relationship(rel)

    results = store.get_relationships(subject="codex:A", predicate=RelationshipType.CALLS)
    assert len(results) == 1
    assert results[0].object == "codex:B"
    assert store.get_relationships(subject="codex:missing") == []


def test_neighbors_out_and_in() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "func_a"))
    store.upsert_entity(make_symbol("codex:B", "func_b"))
    store.upsert_relationship(
        CanonicalRelationship(subject="codex:A", predicate=RelationshipType.CALLS, object="codex:B")
    )

    assert [n.canonical_id for n in store.neighbors("codex:A", direction="out")] == ["codex:B"]
    assert [n.canonical_id for n in store.neighbors("codex:B", direction="in")] == ["codex:A"]
    assert store.neighbors("codex:A", direction="in") == []
    assert store.neighbors("codex:unknown") == []


def test_neighbors_invalid_direction_raises() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "func_a"))
    with pytest.raises(ValueError, match="direction"):
        store.neighbors("codex:A", direction="sideways")


def test_graph_version_publish_returns_immutable_copy() -> None:
    version = GraphVersion(version_id="v1", repository_id="repo1", repository_revision="abc123")
    published = version.publish()
    assert version.published is False
    assert published.published is True


# --- find_entities (D9 directive Part 11 / §R.5 additive extension) --------


def test_find_entities_by_exact_name() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "func_a"))
    store.upsert_entity(make_symbol("codex:B", "func_b"))

    results = store.find_entities(name="func_a")
    assert [e.canonical_id for e in results] == ["codex:A"]


def test_find_entities_by_qualified_name_substring() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "func_a"))

    assert [e.canonical_id for e in store.find_entities(qualified_name="pkg.func_a")] == ["codex:A"]
    assert [e.canonical_id for e in store.find_entities(qualified_name="pkg")] == ["codex:A"]


def test_find_entities_is_case_insensitive() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "FuncA"))
    assert [e.canonical_id for e in store.find_entities(name="funca")] == ["codex:A"]


def test_find_entities_by_base_type() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "func_a"))
    directory = RepositorySymbol(
        canonical_id="codex:D",
        repository_id="repo1",
        repository_revision="abc123",
        name="src",
        qualified_name="src",
        base_type=BaseEntityType.DIRECTORY,
    )
    store.upsert_entity(directory)

    assert [e.canonical_id for e in store.find_entities(base_type=BaseEntityType.DIRECTORY)] == [
        "codex:D"
    ]


def test_find_entities_no_match_returns_empty() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:A", "func_a"))
    assert store.find_entities(name="nonexistent") == []


def test_find_entities_no_filters_returns_all_sorted_by_canonical_id() -> None:
    store = make_store()
    store.upsert_entity(make_symbol("codex:B", "func_b"))
    store.upsert_entity(make_symbol("codex:A", "func_a"))
    assert [e.canonical_id for e in store.find_entities()] == ["codex:A", "codex:B"]
