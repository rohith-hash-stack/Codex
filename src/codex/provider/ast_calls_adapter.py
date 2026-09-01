"""The AST Calls Adapter (HLRD Resource Map; TAD §9; Phase D directive D7).

A clean-room `ProviderAdapter` (D1) that parses Python source with the
standard library's `ast` module and emits `RelationshipType.CALLS`
evidence for **syntactically deterministic** call sites only. No new
dependency: `ast`, `pathlib`, and `tomllib` are the only imports this
provider (and its sibling `pyproject_dependency_adapter`) uses beyond
`codex` itself.

Why this exists (directive D7 audit, `docs/architecture-conformance-
audit.md` §HH): neither `SCIPAdapter` nor `CodeQLAdapter` can produce
`CALLS` — confirmed empirically against real `scip-python`-generated
indexes (`SymbolRole` never carries a call-specific bit; only
`{Definition, ReadAccess}` ever appear) and against `CodeQLAdapter`'s
own SARIF-only scope (no CALLS field in SARIF). `CALL_RELATIONSHIP` is
the single capability with the most leverage across `Intent.FIND_
CALLERS`/`FIND_TESTS`/`TRACE_EXECUTION`/`FIND_IMPACT` (`codex.query_
understanding.engine._REQUIRED_EVIDENCE`), and Python's own `ast`
module is a zero-dependency, always-available, exact syntactic parser
— no guessing at an undocumented third-party format.

**Resolution strategy (directive: "never guess unresolved targets",
"prefer abstention over false CALLS edges")**

This adapter builds its own self-contained, per-repository symbol
table from the same AST walk that finds call sites — it has no access
to any other provider's output (`ProviderAdapter.extract()` receives
only `RepositoryMetadata` + requested capabilities, TAD §8 invariant
#2), so "the repository graph/symbol data" available to it *is* this
adapter's own AST-derived definition table, not SCIP's.

A call site's target expression is resolved **only** when it
syntactically, unambiguously names a function/method this same walk
directly proves is defined somewhere in this repository:

- ``bare_name(...)`` — resolved against that *same file's* own
  module-level function definitions, or (if the name is bound by an
  ``import``/``from ... import`` statement in that file) that
  statement's own resolved target module's top-level function
  definitions. Only absolute and simple relative (``from . import
  x``, ``from .pkg import x``) imports are resolved; anything the
  import machinery can't map to an in-repository ``.py`` file (a
  third-party package, a namespace package, a dynamic import) leaves
  the name unresolved.
- ``self.method(...)`` / ``cls.method(...)`` inside a method body —
  resolved against methods defined **directly** on that method's own
  enclosing class only. Inherited/mixed-in methods are never resolved
  (no MRO walk — a base class's methods, especially with multiple
  inheritance, are not a fact this adapter can determine
  deterministically from one file's AST alone).
- ``module_alias.func(...)`` — resolved only when ``module_alias`` is
  bound by an ``import``/``import ... as`` statement in that file to
  an in-repository module, and ``func`` is a top-level function
  defined in that module.

Every other call shape is **left unresolved, on purpose**: a class
instantiation (``SomeClass()`` — no MRO/``__init__`` resolution
attempted), an attribute call on an arbitrary variable
(``obj.method()`` — no type inference), a call on the result of
another call or subscript, ``super().method()``, a dynamically
constructed call (``getattr(obj, name)()``), a decorator application,
or any name this walk cannot prove resolves to exactly one
in-repository definition. Directive: "if AST call resolution cannot
safely resolve a particular call, leave it unresolved. Do not
compensate with fuzzy/name/substr matching" — there is no fallback
name-similarity search anywhere in this module.

**Scoping (directive: "handle ... nested calls conservatively")**

Only calls made in the *direct* body of a module-level function or a
class method are attributed to that function/method as `CALLS`
evidence. A call made inside a function/lambda defined *within*
another function's body is not attributed to the outer function (that
would misattribute the inner closure's own calls to its enclosing
scope) — and no entity is created for the inner closure either, so
those calls are simply not represented. Likewise, calls made directly
in module-level or class-body statements (outside any function/method)
have no ``FUNCTION``/``METHOD`` entity to serve as their subject and
are not represented. Both are documented, intentional coverage gaps,
not defects — the alternative would be inventing a "module scope" or
"class body scope" caller entity concept HLRD/TAD's ontology has no
provision for. Recursion (a function's own name appearing in its own
body) resolves normally and produces a self-loop `CALLS` edge — TAD's
`Evidence` model imposes no subject != object constraint, and a
self-loop is a true fact, not a defect.

**TESTED_BY (directive: "do not create a separate TESTED_BY
provider")**

This adapter never touches `RelationshipType.TESTED_BY` — a full
repository grep confirms no code anywhere in `codex.*` ever emits or
consumes it, and it is absent from `codex.ontology.relationships.
DERIVED_RELATIONSHIP_TYPES` (query-time-derived types are exactly
``{REACHES, TRANSITIVE_CALLS, INDIRECTLY_DEPENDS_ON}``). The existing
`FIND_TESTS` intent already answers "what tests call/reference X" by
traversing `CALLS`/`REFERENCES`/`IMPORTS` edges directly
(`_REQUIRED_EVIDENCE[Intent.FIND_TESTS]`,
`_CAPABILITY_RELATIONSHIP_TYPES[Capability.CALL_RELATIONSHIP]` in
`codex.query_understanding.engine`) — this adapter's `CALLS` evidence
participates in that existing path automatically, with zero changes
to D8-D13.
"""

from __future__ import annotations

import ast
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Final

from codex.evidence.model import CoverageStatus, Evidence, EvidenceCohort
from codex.ontology.entities import (
    BaseEntityType,
    RepositorySymbol,
    SourceLocation,
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

_EXCLUDED_DIR_NAMES: Final = frozenset(
    {
        ".git",
        "__pycache__",
        ".venv",
        "venv",
        ".tox",
        "build",
        "dist",
        ".eggs",
        "node_modules",
        ".mypy_cache",
        ".pytest_cache",
        ".ruff_cache",
    }
)
"""Directory names skipped during the repository-wide `.py` file walk —
a fixed, documented exclusion list (the same category of boring,
deterministic implementation constant as `GitAdapter`'s
`DEFAULT_CO_CHANGE_WINDOW`), not an architectural decision."""


@dataclass(frozen=True)
class _DefRecord:
    """One resolvable module-level function or class method definition."""

    qualified_name: str
    """This adapter's own convention: ``<repo-relative-path>::<name>`` for a
    module-level function, ``<repo-relative-path>::<Class>.<name>`` for a
    method — deterministic and collision-free within one repository, not an
    attempt to reproduce any other provider's naming scheme (TAD does not
    mandate a shared cross-provider qualified-name format; `SCIPAdapter` and
    `CodeQLAdapter` each already define their own)."""

    base_type: BaseEntityType
    relative_path: str
    lineno: int
    col_offset: int
    end_lineno: int
    end_col_offset: int


@dataclass(frozen=True)
class _CallSite:
    caller: str
    """`_DefRecord.qualified_name` of the enclosing function/method."""

    callee: str
    """`_DefRecord.qualified_name` of the resolved target."""


@dataclass
class _ModuleImports:
    """Import bindings resolved to an in-repository module path, for one file.

    ``name_to_module``: a *local* bound name (the ``as`` alias if given,
    else the imported name itself) from ``from <module> import <name>
    [as <alias>]``, mapped to ``(target module's dotted path, the
    name as defined *in that module*)`` — the two can differ (``from
    pkg.mod import helper as h`` binds ``h`` locally but must be looked
    up as ``helper`` in ``pkg.mod``'s own definitions), so both are kept
    explicitly rather than assuming they match. Used to resolve
    ``bare_name(...)`` calls that aren't locally defined.

    ``alias_to_module``: a module alias bound via ``import <module>``
    (or ``as <alias>``) — used to resolve ``alias.func(...)`` calls
    (there the attribute name itself, e.g. ``func``, is already the
    name as defined in the target module, so no separate original-name
    tracking is needed here).

    Only bindings this adapter proved resolve to an in-repository ``.py``
    file are recorded; anything else (a third-party package, a name this
    adapter can't map to a file) is simply absent, which is exactly
    "unresolved" downstream — no sentinel value needed.
    """

    name_to_module: dict[str, tuple[str, str]] = field(default_factory=dict)
    alias_to_module: dict[str, str] = field(default_factory=dict)


def _strip_py_suffix(relative_path: str) -> str:
    posix = relative_path.replace("\\", "/")
    if posix.endswith("/__init__.py"):
        return posix[: -len("/__init__.py")]
    if posix.endswith(".py"):
        return posix[: -len(".py")]
    return posix


def _module_path_for(relative_path: str) -> str:
    """A file's dotted module path computed directly from its repository-
    root-relative path, e.g. ``src/pkg/mod.py`` -> ``src.pkg.mod``,
    ``pkg/__init__.py`` -> ``pkg``. Used as the anchor for *relative*
    import resolution (``_resolve_relative_import``), where only relative
    nesting depth matters, and as one of two lookup keys
    ``_module_path_registration_keys`` registers a file's definitions
    under for *absolute* import resolution."""
    return _strip_py_suffix(relative_path).replace("/", ".")


_SRC_LAYOUT_PREFIX: Final = "src/"


def _module_path_registration_keys(relative_path: str) -> list[str]:
    """Every dotted module path an ``import``/``from ... import`` statement
    could plausibly use to reach this file, given only filesystem structure
    (no per-build-backend `pyproject.toml` schema parsing -- this is
    deliberately independent of `PyprojectDependencyAdapter`).

    Always includes the plain repository-root-relative path
    (``_module_path_for``). A file under a top-level ``src/`` directory
    additionally registers the same path with that one leading segment
    stripped -- Python's well-known "src layout" convention (both real
    repositories this adapter is validated against use it: `pyproject.
    toml`'s own `packages = ["src/codex"]` / `where = ["src"]`), under
    which real import statements name the package as ``pkg.mod``, never
    ``src.pkg.mod``. This is a structural, filesystem-observable fact
    (a top-level directory literally named ``src``), not a guess about
    any specific build backend's configuration.
    """
    posix = _strip_py_suffix(relative_path)
    full = posix.replace("/", ".")
    keys = [full]
    if posix.startswith(_SRC_LAYOUT_PREFIX):
        stripped = posix[len(_SRC_LAYOUT_PREFIX) :]
        if stripped:
            keys.append(stripped.replace("/", "."))
    return keys


def _is_virtualenv_root(directory: Path) -> bool:
    """A directory is a Python virtual environment root iff it directly
    contains ``pyvenv.cfg`` -- the standard, structural marker every
    virtualenv (venv/virtualenv/uv/pip) writes there, regardless of the
    directory's own name (``.venv``, ``.venv-work``, ``env39``, ...).
    Checking for this marker, rather than relying solely on the fixed
    ``_EXCLUDED_DIR_NAMES`` list, is what actually keeps this adapter
    from parsing an entire installed dependency tree under an
    arbitrarily-named virtualenv directory."""
    return (directory / "pyvenv.cfg").is_file()


def _discover_python_files(root: Path) -> list[Path]:
    """Walk `root` for `.py` files, pruning excluded directories (by name
    or by virtualenv marker) *before* descending into them -- both for
    correctness (never parsing installed third-party packages as if they
    were this repository's own code) and for performance (never even
    stat-ing the, often huge, contents of a virtualenv)."""
    files: list[Path] = []
    stack: list[Path] = [root]
    while stack:
        current = stack.pop()
        try:
            entries = list(current.iterdir())
        except OSError:
            continue  # unreadable directory -- skipped, not a whole-provider failure
        for entry in entries:
            if entry.is_dir():
                if entry.name in _EXCLUDED_DIR_NAMES or _is_virtualenv_root(entry):
                    continue
                stack.append(entry)
            elif entry.suffix == ".py":
                files.append(entry)
    files.sort()
    return files


def _relative_posix(path: Path, root: Path) -> str:
    return path.relative_to(root).as_posix()


def _resolve_relative_import(*, current_module: str, node: ast.ImportFrom) -> str | None:
    """Resolve a relative ``from . import x`` / ``from .pkg import x`` to an
    absolute dotted module path, given the importing file's own module path.
    Returns ``None`` when the relative import climbs above the repository
    root (this adapter has no package-root concept beyond the repository
    itself, so that case is left unresolved rather than guessed)."""
    parts = current_module.split(".")
    # A relative import is anchored at the *package* containing the current
    # module -- drop the current module's own last segment first (mirrors
    # Python's own import semantics: `from . import x` inside `pkg.mod`
    # resolves relative to `pkg`, not `pkg.mod`).
    package_parts = parts[:-1]
    climb = node.level - 1 if node.level > 0 else 0
    if climb > len(package_parts):
        return None
    base_parts = package_parts[: len(package_parts) - climb] if climb else package_parts
    if node.module:
        base_parts = [*base_parts, *node.module.split(".")]
    if not base_parts:
        return None
    return ".".join(base_parts)


def _collect_imports(
    tree: ast.Module, *, current_module: str, known_modules: frozenset[str]
) -> _ModuleImports:
    """``known_modules`` is every dotted module path this repository is
    already known to contain (``_module_path_registration_keys`` applied
    to every discovered ``.py`` file) — used only to disambiguate ``from
    X import Y``, which Python's own grammar leaves ambiguous between "Y
    is a name defined in module X" and "Y is a submodule `X.Y`". Checking
    against real, already-discovered files is a deterministic fact, not
    a guess.
    """
    imports = _ModuleImports()
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                # `import a.b.c` binds only the top-level name `a` (or the
                # alias, if given) -- only an explicit `as` alias gets the
                # full dotted target recorded, matching real Python binding
                # semantics. A bare `import a.b.c` (no alias) would need
                # multi-level attribute-chain resolution (`a.b.c.func()`)
                # this adapter does not attempt -- left unresolved, on
                # purpose (module docstring).
                if alias.asname:
                    imports.alias_to_module[alias.asname] = alias.name
        elif isinstance(node, ast.ImportFrom):
            if node.level and node.level > 0:
                resolved_module = _resolve_relative_import(
                    current_module=current_module, node=node
                )
            else:
                resolved_module = node.module
            if resolved_module is None:
                continue
            for alias in node.names:
                if alias.name == "*":
                    continue  # a wildcard import binds unknown names -- never resolved
                bound_name = alias.asname or alias.name
                submodule_candidate = f"{resolved_module}.{alias.name}"
                if submodule_candidate in known_modules:
                    # `from pkg import submodule` -- `submodule` is itself
                    # an in-repository module, not a name defined *in*
                    # `pkg`; subsequent `submodule.func()` calls need
                    # `alias_to_module`, the same as a plain `import`.
                    imports.alias_to_module[bound_name] = submodule_candidate
                else:
                    imports.name_to_module[bound_name] = (resolved_module, alias.name)
    return imports


class _DefinitionCollector:
    """Pass 1: record every module-level function and class method
    definition in one file.

    Deliberately walks only ``tree.body`` (module-level statements) and,
    for each ``ClassDef`` found there, only *that class's own* ``body``
    (its direct statements) -- never a generic recursive tree walk. This
    keeps "genuinely top-level" and "genuinely a direct method" exactly
    consistent with ``_extract_calls``'s own call-site-collection loop,
    which walks the same two levels the same way. Anything else (a
    function defined inside another function, inside an ``if``/``try`` at
    module level, or inside a nested class) is not collected -- see the
    module docstring's "handle nested calls conservatively" section.
    """

    def __init__(self, *, relative_path: str) -> None:
        self.relative_path = relative_path
        self.functions: dict[str, _DefRecord] = {}
        """Bare function name -> record, module-level only."""
        self.methods: dict[tuple[str, str], _DefRecord] = {}
        """(class name, method name) -> record, direct members only."""

    def _record(
        self, node: ast.FunctionDef | ast.AsyncFunctionDef, *, class_name: str | None
    ) -> _DefRecord:
        qualified = (
            f"{self.relative_path}::{class_name}.{node.name}"
            if class_name is not None
            else f"{self.relative_path}::{node.name}"
        )
        return _DefRecord(
            qualified_name=qualified,
            base_type=BaseEntityType.METHOD if class_name is not None else BaseEntityType.FUNCTION,
            relative_path=self.relative_path,
            lineno=node.lineno,
            col_offset=node.col_offset,
            end_lineno=node.end_lineno or node.lineno,
            end_col_offset=node.end_col_offset or node.col_offset,
        )

    def visit(self, tree: ast.Module) -> None:
        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef):
                for child in stmt.body:
                    if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        self.methods[(stmt.name, child.name)] = self._record(
                            child, class_name=stmt.name
                        )
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                self.functions[stmt.name] = self._record(stmt, class_name=None)


class _CallCollector(ast.NodeVisitor):
    """Pass 2, run once per module-level function or class method body:
    collect this scope's own direct `ast.Call` sites, stopping at any
    nested function/lambda/class boundary (those calls belong to a
    different, unrepresented scope -- see module docstring)."""

    def __init__(self) -> None:
        self.calls: list[ast.Call] = []

    def visit_Call(self, node: ast.Call) -> None:  # noqa: N802
        self.calls.append(node)
        self.generic_visit(node)

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:  # noqa: N802
        pass  # boundary: do not descend

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:  # noqa: N802
        pass  # boundary: do not descend

    def visit_Lambda(self, node: ast.Lambda) -> None:  # noqa: N802
        pass  # boundary: do not descend

    def visit_ClassDef(self, node: ast.ClassDef) -> None:  # noqa: N802
        pass  # boundary: do not descend (a nested class's own methods are separate scopes)


def _resolve_call(
    call: ast.Call,
    *,
    own_functions: dict[str, _DefRecord],
    enclosing_class: str | None,
    own_methods: dict[tuple[str, str], _DefRecord],
    imports: _ModuleImports,
    functions_by_module: dict[str, dict[str, _DefRecord]],
) -> _DefRecord | None:
    """Resolve one call site's target, or ``None`` if it cannot be resolved
    without guessing (see module docstring for the exact rules)."""
    func = call.func

    if isinstance(func, ast.Name):
        name = func.id
        if name in own_functions:
            return own_functions[name]
        binding = imports.name_to_module.get(name)
        if binding is not None:
            target_module, original_name = binding
            return functions_by_module.get(target_module, {}).get(original_name)
        return None

    if isinstance(func, ast.Attribute):
        value = func.value
        if isinstance(value, ast.Name):
            if value.id in {"self", "cls"} and enclosing_class is not None:
                return own_methods.get((enclosing_class, func.attr))
            alias_target_module = imports.alias_to_module.get(value.id)
            if alias_target_module is not None:
                return functions_by_module.get(alias_target_module, {}).get(func.attr)
        return None

    return None  # a call on a subscript/call/other expression -- never resolved


def _bare_name(qualified_name: str) -> str:
    """The plain function/method name from a `_DefRecord.qualified_name`
    (``<path>::<name>`` or ``<path>::<Class>.<name>``).

    Splits on ``::`` first, not ``.`` -- the repo-relative path half
    routinely contains its own literal ``.`` (a ``.py`` suffix, a
    dotted subdirectory), so splitting the whole string on the last
    ``.`` would wrongly return something like ``py::helper`` instead of
    ``helper``. Only the part *after* ``::`` may itself contain a
    ``.`` (a ``Class.method`` qualifier), and only that part's own
    trailing segment is the bare name.
    """
    after_path = qualified_name.rsplit("::", maxsplit=1)[-1]
    return after_path.rsplit(".", maxsplit=1)[-1]


def _location(record: _DefRecord) -> SourceLocation:
    """Converts `_DefRecord`'s raw, unmodified `ast` module line numbers
    (Python's own 1-based, inclusive-both-ends convention -- `node.
    lineno`/`node.end_lineno`, stored as-is by `_record`) into `Source
    Location`'s established 0-based convention (closed 2026-08-31,
    `codex.ontology.entities.SourceLocation`'s own docstring: "matches
    SCIP's own documented `[start, end)` range semantics, and the LSP/
    tree-sitter convention").

    Only the **line** numbers need conversion (`- 1`): a 1-based line
    that is the Nth line becomes the 0-based index of that same Nth
    line (`N - 1`), for both `start_line` and `end_line` -- confirmed
    against real `SCIPAdapter` output for the identical real definition
    (`classify()` in `veyra`: SCIP's own `start_line=16`/`end_line=16`
    for the single-line name-token span at real (1-based) line 17,
    i.e. `17 - 1 = 16`). This is *not* "add one line of slack" --
    `end_line` names the same 0-based line the last included character
    sits on, matching a single-line SCIP span's `start_line == end_line`
    exactly; it is `end_column` (already exclusive, see below) that
    carries this type's "half-open" property, not an independent
    one-past-the-line-count `end_line`.

    Columns need **no** conversion: `ast`'s own `col_offset` (0-based,
    first included column) and `end_col_offset` (0-based, first column
    *not* included -- CPython's own documented convention) already
    match `SourceLocation`'s convention exactly, byte-for-byte the same
    semantics SCIP itself uses.
    """
    return SourceLocation(
        file_path=record.relative_path,
        start_line=record.lineno - 1,
        end_line=record.end_lineno - 1,
        start_column=record.col_offset,
        end_column=record.end_col_offset,
    )


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
        predicate=RelationshipType.CALLS,
        object=obj,
        confidence=1.0,
        freshness=cohort.observed_at,
    )


class AstCallsAdapter:
    """``ProviderAdapter`` for Python `ast`-derived call sites (directive D7)."""

    @property
    def provider_name(self) -> str:
        return "ast_calls"

    @property
    def provider_version(self) -> str:
        """No third-party tool version applies -- this adapter *is* the
        parser, using only the standard library's own `ast` module."""
        return "stdlib-ast"

    @property
    def supported_capabilities(self) -> frozenset[Capability]:
        return frozenset({Capability.CALL_RELATIONSHIP})

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

    def __init__(self) -> None:
        self._freshness: datetime | None = None

    def validate(self) -> ValidationResult:
        return ValidationResult(ok=True)

    def check_eligibility(self, repository: RepositoryMetadata) -> ProviderEligibility:
        if not Path(repository.local_path).is_dir():
            return ProviderEligibility(
                status=EligibilityStatus.INELIGIBLE_REPOSITORY,
                reason=f"no repository directory at {repository.local_path}",
            )
        return ProviderEligibility(status=EligibilityStatus.ELIGIBLE)

    def extract(
        self, repository: RepositoryMetadata, capabilities: Collection[Capability]
    ) -> ExtractionResult:
        requested = frozenset(capabilities) & self.supported_capabilities
        root = Path(repository.local_path)

        if not root.is_dir():
            raise ProviderExtractionError(
                self.provider_name,
                ProviderFailureReason.UNAVAILABLE,
                f"no repository directory at {root}",
            )

        successful: list[str] = []
        failed: list[str] = []
        call_sites: list[_CallSite] = []
        definitions: dict[str, _DefRecord] = {}

        if Capability.CALL_RELATIONSHIP in requested:
            try:
                definitions, call_sites = _extract_calls(root)
                successful.append(Capability.CALL_RELATIONSHIP.value)
            except Exception:  # noqa: BLE001 - isolate this capability, directive D5 §14 precedent
                failed.append(Capability.CALL_RELATIONSHIP.value)

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
            "definitions": definitions,
            "call_sites": call_sites,
        }
        return ExtractionResult(cohort=cohort, raw_reference=None, raw_payload=payload)

    def normalize(self, result: ExtractionResult) -> NormalizedEvidence:
        payload = result.raw_payload
        repository_id: str = payload["repository_id"]
        revision: str = payload["revision"]
        definitions: dict[str, _DefRecord] = payload["definitions"]
        call_sites: list[_CallSite] = payload["call_sites"]

        entities: dict[str, RepositorySymbol] = {}
        evidence: list[Evidence] = []
        canonical_by_qualified: dict[str, str] = {}

        def ensure_entity(record: _DefRecord) -> str:
            canonical_id = canonical_by_qualified.get(record.qualified_name)
            if canonical_id is not None:
                return canonical_id
            canonical_id = build_canonical_id(
                repository_id=repository_id,
                repository_revision=revision,
                qualified_name=record.qualified_name,
                base_type=record.base_type,
            )
            canonical_by_qualified[record.qualified_name] = canonical_id
            name = _bare_name(record.qualified_name)
            entities[canonical_id] = RepositorySymbol(
                canonical_id=canonical_id,
                repository_id=repository_id,
                repository_revision=revision,
                name=name,
                qualified_name=record.qualified_name,
                base_type=record.base_type,
                roles=[],
                source_location=_location(record),
            )
            return canonical_id

        counter = 0
        seen_edges: set[tuple[str, str]] = set()
        for site in sorted(call_sites, key=lambda s: (s.caller, s.callee)):
            caller_record = definitions.get(site.caller)
            callee_record = definitions.get(site.callee)
            if caller_record is None or callee_record is None:
                continue  # never fabricate -- both ends must be a real recorded definition
            edge = (site.caller, site.callee)
            if edge in seen_edges:
                continue
            seen_edges.add(edge)
            subject_id = ensure_entity(caller_record)
            object_id = ensure_entity(callee_record)
            evidence.append(
                _make_evidence(
                    f"ast_calls:{revision}:call:{counter}",
                    cohort=result.cohort,
                    subject=subject_id,
                    obj=object_id,
                )
            )
            counter += 1

        return NormalizedEvidence(
            entities=list(entities.values()), evidence=evidence, cohort=result.cohort
        )


def _extract_calls(root: Path) -> tuple[dict[str, _DefRecord], list[_CallSite]]:
    files = _discover_python_files(root)

    per_file_collectors: dict[str, _DefinitionCollector] = {}
    per_file_trees: dict[str, ast.Module] = {}
    functions_by_module: dict[str, dict[str, _DefRecord]] = {}
    all_definitions: dict[str, _DefRecord] = {}

    for path in files:
        relative_path = _relative_posix(path, root)
        try:
            source = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue  # an unreadable file is skipped, not a whole-provider failure
        try:
            tree = ast.parse(source, filename=relative_path)
        except SyntaxError:
            continue  # unparseable source is skipped, not guessed at

        collector = _DefinitionCollector(relative_path=relative_path)
        collector.visit(tree)
        per_file_collectors[relative_path] = collector
        per_file_trees[relative_path] = tree
        for key in _module_path_registration_keys(relative_path):
            functions_by_module[key] = collector.functions

        for record in collector.functions.values():
            all_definitions[record.qualified_name] = record
        for record in collector.methods.values():
            all_definitions[record.qualified_name] = record

    known_modules = frozenset(functions_by_module)
    call_sites: list[_CallSite] = []
    for relative_path, collector in per_file_collectors.items():
        tree = per_file_trees[relative_path]
        current_module = _module_path_for(relative_path)
        imports = _collect_imports(
            tree, current_module=current_module, known_modules=known_modules
        )

        # Deliberately iterate only *direct* module-body / class-body
        # statements here, never `ast.walk` -- `ast.walk` also yields
        # nested/closure FunctionDefs regardless of scope, and a nested
        # closure sharing a name with an unrelated module-level function
        # would otherwise be misidentified as that function's own body via
        # `collector.functions.get(node.name)` (a real name collision, not
        # a hypothetical one). Direct-body iteration makes "genuinely
        # top-level" unambiguous by construction.
        # `collector.functions`/`collector.methods` were built from this
        # exact same `tree.body` / class-`body` traversal (`_Definition
        # Collector.visit`), so a lookup by `stmt.name` / `(stmt.name,
        # child.name)` here is always already present -- direct indexing,
        # not `.get()` + a None-check for a case that cannot occur.
        for stmt in tree.body:
            if isinstance(stmt, ast.ClassDef):
                for child in stmt.body:
                    if not isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                        continue
                    _collect_call_sites_for_body(
                        child,
                        caller=collector.methods[(stmt.name, child.name)],
                        enclosing_class=stmt.name,
                        own_functions=collector.functions,
                        own_methods=collector.methods,
                        imports=imports,
                        functions_by_module=functions_by_module,
                        call_sites=call_sites,
                    )
            elif isinstance(stmt, (ast.FunctionDef, ast.AsyncFunctionDef)):
                _collect_call_sites_for_body(
                    stmt,
                    caller=collector.functions[stmt.name],
                    enclosing_class=None,
                    own_functions=collector.functions,
                    own_methods=collector.methods,
                    imports=imports,
                    functions_by_module=functions_by_module,
                    call_sites=call_sites,
                )

    return all_definitions, call_sites


def _collect_call_sites_for_body(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    *,
    caller: _DefRecord,
    enclosing_class: str | None,
    own_functions: dict[str, _DefRecord],
    own_methods: dict[tuple[str, str], _DefRecord],
    imports: _ModuleImports,
    functions_by_module: dict[str, dict[str, _DefRecord]],
    call_sites: list[_CallSite],
) -> None:
    collector = _CallCollector()
    for stmt in node.body:
        collector.visit(stmt)
    for call in collector.calls:
        resolved = _resolve_call(
            call,
            own_functions=own_functions,
            enclosing_class=enclosing_class,
            own_methods=own_methods,
            imports=imports,
            functions_by_module=functions_by_module,
        )
        if resolved is not None:
            call_sites.append(
                _CallSite(caller=caller.qualified_name, callee=resolved.qualified_name)
            )


__all__ = ["AstCallsAdapter"]
