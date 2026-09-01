"""Canonical path normalization (TAD §12; `SourceLocation` contract closure).

Shared by Entity Resolution's normalized-identity matching (below) and
consistent with `SourceLocation.file_path`'s own documented convention
(`codex.ontology.entities`): repository-root-relative, ``/``-separated,
no leading ``./`` or ``/``.
"""

from __future__ import annotations


def normalize_repo_relative_path(path: str) -> str:
    """Normalize a provider-supplied path into the canonical FILE identity form.

    Deterministic, pure, and provider-agnostic:

    - backslashes become forward slashes (a provider on a Windows-style
      path convention, or one that never normalized its own separators);
    - a leading ``./`` is stripped (some producers write paths relative-
      with-dot rather than bare-relative);
    - a leading ``/`` is stripped (an absolute-looking path is treated as
      already repository-root-relative — Codex adapters never emit a
      real filesystem-absolute path as a qualified_name, so a leading
      ``/`` here only ever means "repo root", never a different
      filesystem root);
    - redundant internal ``//`` segments collapse to one ``/``.

    This does **not** resolve ``..`` segments or attempt any filesystem
    access — it is a pure string transform over an already-repository-
    scoped path, not a path-traversal-safe resolver (that concern lives
    at each provider's own file-access boundary, e.g. `RepositoryManager`,
    not here).
    """
    normalized = path.replace("\\", "/")
    while normalized.startswith("./"):
        normalized = normalized[2:]
    normalized = normalized.lstrip("/")
    while "//" in normalized:
        normalized = normalized.replace("//", "/")
    return normalized


__all__ = ["normalize_repo_relative_path"]
