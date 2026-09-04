"""Codex API server CLI entry point (VS Code + Nervous-System scope
change, `docs/vscode-nervous-system-architecture.md` §9).

``python -m codex.api`` starts the local HTTP JSON server on
``127.0.0.1`` and prints the bound port so a launching process (the VS
Code extension included) can connect without needing a fixed,
possibly-already-taken port. Registers the three providers that need no
external tool or pre-generated index to run against an arbitrary local
Python repository -- `GitAdapter` (history/co-change), `AstCallsAdapter`
(Python AST-derived symbols/calls), and `PyprojectDependencyAdapter`
(`pyproject.toml`-declared package dependencies) -- so this CLI is
immediately usable end-to-end, not only a demo skeleton. `SCIPAdapter`/
`CodeQLAdapter` are not wired here: both require a provider-specific
pre-generated index this CLI has no way to produce itself, and adding
them is a caller/deployment concern, not something `codex.api` should
assume (`docs/vscode-nervous-system-architecture.md` §2: the API layer
never owns provider registration). `PyprojectDependencyAdapter` needs
neither -- exactly like `GitAdapter`'s `.git` directory, it only reads a
manifest already present in the repository it is pointed at (Codex
validation continuation, "PyprojectDependencyAdapter integration gap":
this provider was fully implemented and validated against real
repositories in the D7 milestone, `docs/architecture-conformance-audit.md`
§HH, but was never added to this specific registration site -- an
implementation oversight the criterion above already covered, not a
deliberate exclusion).

**`POST /query`** (API Integration Milestone): wires a real
`OpenAIGateway` in unconditionally -- constructing one does not itself
read `Codex_open_API_key` or touch the network (only `generate()`
does, on every call, per that module's own documented behavior), so
this stays safe even when the variable is unset; `/query` will then
fail per-request with a clear `LLM authentication failed` error
(mapped to `502` by `codex.api.server`) rather than this CLI refusing
to start.
"""

from __future__ import annotations

import argparse
import signal
import sys
import threading
import types

from codex.api.server import serve
from codex.api.service import CodexAPI
from codex.evidence.store import InMemoryEvidenceStore
from codex.llm.openai_gateway import OpenAIGateway
from codex.provider.ast_calls_adapter import AstCallsAdapter
from codex.provider.git_adapter import GitAdapter
from codex.provider.pyproject_dependency_adapter import PyprojectDependencyAdapter
from codex.registry.registry import CapabilityRegistry
from codex.registry.scoring import ProviderScoreProfile

DEFAULT_PROFILE = ProviderScoreProfile(evidence_quality=0.8, cost_factor=0.3)


def _build_api() -> CodexAPI:
    registry = CapabilityRegistry()
    registry.register(GitAdapter(), DEFAULT_PROFILE)
    registry.register(AstCallsAdapter(), DEFAULT_PROFILE)
    # Codex validation continuation ("PyprojectDependencyAdapter
    # integration gap"): registered last, after the two pre-existing
    # providers, preserving their exact relative order -- this adapter is
    # the sole producer of `Capability.DEPENDENCY` (no other registered
    # provider supports it), so appending it changes no existing
    # provider's ordering, priority, or capability resolution.
    registry.register(PyprojectDependencyAdapter(), DEFAULT_PROFILE)
    return CodexAPI(registry, InMemoryEvidenceStore(), gateway=OpenAIGateway())


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(prog="python -m codex.api", description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument(
        "--port", type=int, default=0, help="0 (default) picks a free ephemeral port"
    )
    args = parser.parse_args(argv)

    api = _build_api()
    # `serve()` already starts `serve_forever()` on its own daemon
    # thread and returns immediately -- the main thread here only
    # needs to block until a shutdown signal arrives, never call
    # `serve_forever()` itself a second time.
    server = serve(api, host=args.host, port=args.port)
    bound_port = server.server_address[1]
    print(f"CODEX_API_LISTENING {args.host} {bound_port}", flush=True)

    stop_event = threading.Event()

    def _shutdown(signum: int, frame: types.FrameType | None) -> None:
        stop_event.set()

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    stop_event.wait()
    server.shutdown()
    server.server_close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
