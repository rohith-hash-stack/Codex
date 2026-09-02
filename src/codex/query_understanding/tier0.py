"""Deterministic Tier-0 intent detection (TAD §23; directive D8 Phases 6, 10).

Pure regex/lexical pattern matching — **no** fuzzy matching, no LLM/SLM
call, no learned model of any kind. Every candidate's score is a
function of which *category* of pattern matched, per directive Phase 6's
own four categories:

1. **Exact structural pattern** ("who calls X", "find callers of X") →
   high score, clears TAD §23's `>0.95` deterministic-execution bar.
2. **Strong lexical pattern** with real structure but genuine semantic
   ambiguity about what's being asked (e.g. a causal "why does X call Y"
   — the *fact* is structurally extractable, but "why" demands
   explanation a regex cannot deterministically supply) → mid-high
   score, lands in TAD §23's `0.70-0.95` SLM-disambiguation band.
3. **Ambiguous keyword** (the word "call"/"calls" present with no
   surrounding structural pattern) → low score, `<0.70`, "SLM invoked
   without a deterministic prior intent" (TAD §23's own phrase).
4. **No meaningful match** → no candidates at all; the caller (`engine.py`)
   represents this as `Intent.UNKNOWN` directly.

A bare occurrence of an ambiguous word MUST NOT by itself produce a
category-1 score — enforced structurally here by requiring category-1
patterns to match a *specific surrounding phrase shape*, never a bare
keyword (directive Phase 6's explicit "must not... call... alone...
FIND_CALLERS" requirement).
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from codex.query_understanding.models import Intent

_STRUCTURAL_SCORE: float = 0.97
"""Category 1 — clears TAD §23's >0.95 deterministic bar."""

_LEXICAL_SCORE: float = 0.80
"""Category 2 — inside TAD §23's 0.70-0.95 SLM-disambiguation band."""

_AMBIGUOUS_SCORE: float = 0.35
"""Category 3 — below TAD §23's 0.70 floor."""

_MIN_TARGET_LEN = 2
"""A "target" shorter than this (e.g. a stray single letter) is treated
as noise, not a real identifier -- deterministic, not a guess."""


@dataclass(frozen=True)
class Tier0Candidate:
    """One candidate intent with its Tier-0 match score and any
    extracted target identifiers (directive Phase 6: "return candidate
    intents with scores")."""

    intent: Intent
    score: float
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class _Rule:
    intent: Intent
    pattern: re.Pattern[str]
    score: float
    target_groups: tuple[int, ...] = (1,)


# Category 1 -- exact structural patterns (TAD §23's own "strongly match" example).
_STRUCTURAL_RULES: tuple[_Rule, ...] = (
    _Rule(Intent.FIND_CALLERS, re.compile(r"\bwho\s+calls\s+([\w.]+)", re.I), _STRUCTURAL_SCORE),
    _Rule(
        Intent.FIND_CALLERS,
        re.compile(r"\bfind\s+(?:the\s+)?callers\s+of\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(Intent.FIND_CALLERS, re.compile(r"\bwhat\s+calls\s+([\w.]+)", re.I), _STRUCTURAL_SCORE),
    _Rule(
        Intent.FIND_CALLERS,
        re.compile(r"\blist\s+(?:the\s+)?callers\s+of\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.FIND_TESTS,
        re.compile(r"\bwhich\s+tests?\s+(?:call|cover|test|exercise)\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.FIND_TESTS,
        re.compile(r"\bfind\s+tests?\s+(?:for|covering|that\s+call)\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.FIND_IMPLEMENTATIONS,
        re.compile(r"\b(?:find\s+)?implementations?\s+of\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    # GAP-5 fix (`docs/architecture-conformance-audit.md` §KK.8): natural
    # reference-query phrasing -- "What references X?"/"Who references
    # X?" and the pre-existing noun-phrase form "references to X" both
    # map to the new `Intent.FIND_REFERENCES` (see `models.Intent`'s own
    # docstring for why no existing intent is an honest fit).
    _Rule(
        Intent.FIND_REFERENCES,
        re.compile(r"\b(?:what|who)\s+references?\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.FIND_REFERENCES,
        re.compile(r"\breferences?\s+to\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.FIND_IMPACT,
        re.compile(r"\bimpact\s+of\s+(?:changing\s+)?([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.FIND_DEPENDENCIES,
        re.compile(r"\bwhat\s+does\s+([\w.]+)\s+depend\s+on\b", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.HISTORY_ANALYSIS,
        re.compile(r"\bhistory\s+of\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
    _Rule(
        Intent.ARCHITECTURE_ANALYSIS,
        re.compile(r"\barchitecture\s+of\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,
    ),
)

# Category 2 -- strong lexical/structural pattern, genuine semantic ambiguity
# (a "why" question has an extractable fact but demands explanation a regex
# cannot deterministically supply -- directive Phase 6's own "Why does
# authenticate call database?" example belongs here, not category 1).
_LEXICAL_RULES: tuple[_Rule, ...] = (
    _Rule(
        Intent.TRACE_EXECUTION,
        re.compile(r"\bwhy\s+does\s+([\w.]+)\s+call\s+([\w.]+)", re.I),
        _LEXICAL_SCORE,
        target_groups=(1, 2),
    ),
    _Rule(
        Intent.TRACE_EXECUTION,
        re.compile(r"\btrace\s+(?:the\s+)?execution\s+of\s+([\w.]+)", re.I),
        _STRUCTURAL_SCORE,  # "trace execution of X" IS an unambiguous structural request
    ),
    _Rule(
        Intent.CODE_LOOKUP,
        re.compile(r"\beverything\s+related\s+to\s+([\w.]+)", re.I),
        _AMBIGUOUS_SCORE + 0.10,  # broad/vague but at least names a concrete target
    ),
)

# Category 3 -- bare ambiguous keyword, no surrounding structure at all.
_AMBIGUOUS_RULES: tuple[_Rule, ...] = (
    _Rule(Intent.FIND_CALLERS, re.compile(r"\bcalls?\b", re.I), _AMBIGUOUS_SCORE, target_groups=()),
)


def _extract_targets(match: re.Match[str], groups: tuple[int, ...]) -> tuple[str, ...]:
    targets = []
    for group in groups:
        value = match.group(group)
        if value and len(value) >= _MIN_TARGET_LEN:
            targets.append(value)
    return tuple(targets)


def _apply_rules(text: str, rules: tuple[_Rule, ...]) -> list[Tier0Candidate]:
    candidates = []
    for rule in rules:
        match = rule.pattern.search(text)
        if match is not None:
            candidates.append(
                Tier0Candidate(rule.intent, rule.score, _extract_targets(match, rule.target_groups))
            )
    return candidates


def detect(query_text: str) -> list[Tier0Candidate]:
    """Deterministic candidate intents for ``query_text``, highest score
    first. Returns an empty list for "no meaningful match" (category 4)
    — the caller represents that as `Intent.UNKNOWN`, never fabricates a
    candidate for it.

    Category precedence: structural (1) and lexical (2) rules are tried
    first since they carry real information; if *any* fire, bare
    ambiguous-keyword matches (3) for the *same* intent are suppressed
    (a query that already strongly matched "who calls X" as FIND_CALLERS
    doesn't also need a redundant, lower-confidence "contains the word
    call" candidate for the same intent cluttering the result) — but an
    ambiguous match for a *different* intent nobody else claimed is
    still surfaced, preserving genuine ambiguity between intents.
    """
    strong = _apply_rules(query_text, _STRUCTURAL_RULES) + _apply_rules(query_text, _LEXICAL_RULES)
    strong_intents = {c.intent for c in strong}
    weak = [c for c in _apply_rules(query_text, _AMBIGUOUS_RULES) if c.intent not in strong_intents]
    candidates = strong + weak
    candidates.sort(key=lambda c: c.score, reverse=True)
    return candidates


__all__ = ["Tier0Candidate", "detect"]
