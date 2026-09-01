"""The pyproject.toml Dependency Adapter (HLRD Resource Map; TAD §9;
Phase D directive D7).

A clean-room `ProviderAdapter` (D1) that reads a repository's own
`pyproject.toml` with the standard library's `tomllib` and emits
`RelationshipType.DEPENDS_ON` evidence for **explicitly declared**
package dependencies only. Zero new dependency: `tomllib` is standard
library as of Python 3.11 (this project's own pinned minimum,
`pyproject.toml`'s own ``requires-python = ">=3.11"``).

Why this exists (directive D7 audit, `docs/architecture-conformance-
audit.md` §HH): `Capability.DEPENDENCY` ("Backs `RelationshipType.
DEPENDS_ON`") had no producer. `SCIPAdapter`'s own docstring already
rejected deriving it from SCIP's `Import` role — "a symbol-level fact,
not the package-level claim `DEPENDS_ON` implies" — and this adapter
deliberately does not revisit that: it never looks at source code or
import statements at all, only at the project manifest's own explicit
declaration (directive: "Do not convert source imports into
DEPENDS_ON").

**Extraction scope (directive: "read... standard dependency sections
already compatible with the existing ontology", "avoid inventing
package-resolution semantics")**

Only PEP 621's two standard sections are read:

- ``[project.dependencies]`` — a list of PEP 508 requirement strings.
- ``[project.optional-dependencies]`` — a table of extra-name -> list
  of PEP 508 requirement strings, flattened across every extra.

No other tool's dependency format (Poetry's `[tool.poetry.
dependencies]`, Pipenv, `requirements.txt`, a `[tool.uv]` table, ...)
is read — each has its own, different, undocumented-here semantics
(dependency groups, version resolution, source overrides); reading them
would mean guessing at a schema this adapter has not verified, which
directive D5/D6 precedent already rejects for exactly this reason.

Each requirement string is reduced to its **distribution name only** —
the PEP 508 grammar's leading name token, before any extras (`[...]`),
version specifier, environment marker, or URL. No version, extra, or
marker information is interpreted or asserted; this adapter makes no
claim about *which* version is actually installed, only that the
project's own manifest declares a dependency on that named package
(Class A: the manifest's own explicit assertion, mirroring
`CodeQLAdapter`'s "problem"-kind finding — the artifact's own stated
fact, represented without embellishment).

**Missing/malformed input (directive: "handle... safely")**

A repository with no `pyproject.toml` is `INELIGIBLE_REPOSITORY` at
`check_eligibility` — exactly `SCIPAdapter`'s own precedent for a
missing `index.scip`. `extract()` independently guards the same two
failure modes it could still hit if called directly: an unreadable
file (`OSError`) or a file that isn't valid TOML
(`tomllib.TOMLDecodeError`) — both raise `ProviderExtractionError`,
mirroring `SCIPAdapter`'s `WireFormatError` handling exactly. A
present, valid TOML file that simply has no `[project]` table, or an
empty/absent `dependencies`/`optional-dependencies`, is not an error —
it successfully extracts zero dependencies.
"""

from __future__ import annotations

import re
import tomllib
from collections.abc import Collection
from datetime import datetime
from pathlib import Path
from typing import Final

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.ontology.entities import (
    BaseEntityType,
    RepositorySymbol,
    build_canonical_id,
)
from codex.ontology.relationships import RelationshipType
from codex.provider.capability import Capability
from codex.provider.contract import (
    EligibilityStatus,
    ExtractionResult,
    NormalizedEvidence,
    ProviderEligibility,
    ProviderExtractionError,
    ProviderFailureReason,
    ProviderHealthStatus,
    ValidationResult,
)
from codex.repository.models import RepositoryMetadata

DEFAULT_MANIFEST_FILENAME: Final = "pyproject.toml"

_EXTERNAL_REVISION_SENTINEL: Final = "external"
"""Same sentinel `SCIPAdapter` uses for `EXTERNAL_LIBRARY` identity, reused
for consistency -- a dependency's identity should not change just because
the *consuming* repository's own revision does."""

_MANAGER_SCHEME: Final = "pypi"
"""PEP 621's `[project.dependencies]` is unambiguously a list of PyPI/pip-
installable distribution names by specification -- not a guess."""

# PEP 508's grammar: a requirement's distribution name is one or more
# letters/digits/`.`/`-`/`_`, starting and ending with a letter or digit.
# This regex extracts exactly that leading token and nothing past it
# (extras `[...]`, version specifiers, environment markers, and `@ url`
# forms all start with a character this pattern does not match).
_DISTRIBUTION_NAME_RE: Final = re.compile(r"^\s*([A-Za-z0-9]([A-Za-z0-9._-]*[A-Za-z0-9])?)")


def _distribution_name(requirement: str) -> str | None:
    """Extract a PEP 508 requirement string's bare distribution name.

    Returns ``None`` for a string this adapter cannot confidently parse
    (empty, or not starting with a valid name token) rather than
    guessing.
    """
    match = _DISTRIBUTION_NAME_RE.match(requirement)
    if match is None:
        return None
    return match.group(1)


def _collect_dependency_names(document: dict[str, object]) -> list[str]:
    project = document.get("project")
    if not isinstance(project, dict):
        return []

    names: set[str] = set()

    dependencies = project.get("dependencies")
    if isinstance(dependencies, list):
        for entry in dependencies:
            if isinstance(entry, str):
                name = _distribution_name(entry)
                if name is not None:
                    names.add(name)

    optional = project.get("optional-dependencies")
    if isinstance(optional, dict):
        for extra_deps in optional.values():
            if not isinstance(extra_deps, list):
                continue
            for entry in extra_deps:
                if isinstance(entry, str):
                    name = _distribution_name(entry)
                    if name is not None:
                        names.add(name)

    return sorted(names)


def _make_evidence(
    evidence_id: str, *, cohort: EvidenceCohort, subject: str, obj: str
) -> Evidence:
    return Evidence(
        evidence_id=evidence_id,
        provider=cohort.provider,
        provider_version=cohort.provider_version,
        snapshot_id=cohort.snapshot_id,
        source_revision=cohort.source_revision,
        subject=subject,
        predicate=RelationshipType.DEPENDS_ON,
        object=obj,
        confidence=1.0,
        freshness=cohort.observed_at,
    )


class PyprojectDependencyAdapter:
    """``ProviderAdapter`` for `pyproject.toml`-declared dependencies (directive D7)."""

    def __init__(self, *, manifest_filename: str = DEFAULT_MANIFEST_FILENAME) -> None:
        self._manifest_filename = manifest_filename
        self._freshness: datetime | None = None

    @property
    def provider_name(self) -> str:
        return "pyproject_deps"

    @property
    def provider_version(self) -> str:
        """No third-party tool version applies -- this adapter *is* the
        parser, using only the standard library's own `tomllib`."""
        return "stdlib-tomllib"

    @property
    def supported_capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.DEPENDENCY})

    @property
    def health_status(self) -> ProviderHealthStatus:
        # No external executable/service/network dependency -- pure stdlib.
        return ProviderHealthStatus.HEALTHY

    def availability(self, capability: Capability, repository: RepositoryMetadata) -> float:
        if capability not in self.supported_capabilities:
            return 0.0
        return 1.0 if self.check_eligibility(repository).eligible else 0.0

    @property
    def freshness(self) -> datetime | None:
        return self._freshness

    def validate(self) -> ValidationResult:
        return ValidationResult(ok=True)

    def _manifest_path(self, repository: RepositoryMetadata) -> Path:
        return Path(repository.local_path) / self._manifest_filename

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        path = self._manifest_path(repository)
        if not path.is_file():
            return ProviderEligibility(
                status=EligibilityStatus.INELIGIBLE_REPOSITORY,
                reason=f"no {self._manifest_filename} found at {path}",
            )
        return ProviderEligibility(status=EligibilityStatus.ELIGIBLE)

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        requested = frozenset(capabilities) & self.supported_capabilities
        path = self._manifest_path(repository)

        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ProviderExtractionError(
                self.provider_name, ProviderFailureReason.UNAVAILABLE, f"cannot read {path}: {exc}"
            ) from exc

        try:
            document = tomllib.loads(data.decode("utf-8"))
        except (tomllib.TOMLDecodeError, UnicodeDecodeError) as exc:
            raise ProviderExtractionError(
                self.provider_name,
                ProviderFailureReason.UNAVAILABLE,
                f"malformed {self._manifest_filename} at {path}: {exc}",
            ) from exc

        successful: list[str] = []
        failed: list[str] = []
        dependency_names: list[str] | None = None

        if Capability.DEPENDENCY in requested:
            try:
                dependency_names = _collect_dependency_names(document)
                successful.append(Capability.DEPENDENCY.value)
            except Exception:  # noqa: BLE001 - isolate this capability, directive D5 §14 precedent
                failed.append(Capability.DEPENDENCY.value)

        coverage = (
            CoverageStatus.NONE
            if not successful and not failed
            else CoverageStatus.PARTIAL
            if failed
            else CoverageStatus.FULL
        )
        cohort = EvidenceCohort(
            provider=self.provider_name,
            provider_version=self.provider_version,
            snapshot_id=repository.head_revision,
            source_revision=repository.head_revision,
            successful_capabilities=successful,
            failed_capabilities=failed,
            partial_capabilities=[],
            coverage_status=coverage,
        )
        self._freshness = cohort.observed_at

        payload = {
            "repository_id": repository.repository_id,
            "revision": repository.head_revision,
            "dependency_names": dependency_names,
        }
        return ExtractionResult(cohort=cohort, raw_reference=None, raw_payload=payload)

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision: str = payload["revision"]
        dependency_names: list[str] | None = payload["dependency_names"]

        if dependency_names is None:
            return NormalizedEvidence(entities=[], evidence=[], cohort=result.cohort)

        repository_canonical_id = build_canonical_id(
            repository_id=repository_id,
            repository_revision=revision,
            qualified_name=repository_id,
            base_type=BaseEntityType.REPOSITORY,
        )
        entities: dict[str, RepositorySymbol] = {
            repository_canonical_id: RepositorySymbol(
                canonical_id=repository_canonical_id,
                repository_id=repository_id,
                repository_revision=revision,
                name=repository_id,
                qualified_name=repository_id,
                base_type=BaseEntityType.REPOSITORY,
                roles=[],
            )
        }
        evidence: list[Evidence] = []

        for counter, name in enumerate(dependency_names):
            qualified_name = f"{_MANAGER_SCHEME}:{name}"
            canonical_id = build_canonical_id(
                repository_id=repository_id,
                repository_revision=_EXTERNAL_REVISION_SENTINEL,
                qualified_name=qualified_name,
                base_type=BaseEntityType.EXTERNAL_LIBRARY,
            )
            entities[canonical_id] = RepositorySymbol(
                canonical_id=canonical_id,
                repository_id=repository_id,
                repository_revision=_EXTERNAL_REVISION_SENTINEL,
                name=name,
                qualified_name=qualified_name,
                base_type=BaseEntityType.EXTERNAL_LIBRARY,
                roles=[],
            )
            evidence.append(
                _make_evidence(
                    f"pyproject_deps:{revision}:dependency:{counter}",
                    cohort=result.cohort,
                    subject=repository_canonical_id,
                    obj=canonical_id,
                )
            )

        return NormalizedEvidence(
            entities=list(entities.values()), evidence=evidence, cohort=result.cohort
        )


__all__ = ["DEFAULT_MANIFEST_FILENAME", "PyprojectDependencyAdapter"]
