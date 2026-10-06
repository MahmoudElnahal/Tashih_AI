"""Deterministic text matching shared by every hadith source.

Ported from ``docs/reference/tashih-server.js`` (``search``):

* exact substring of the normalized text  -> score 1.0
* otherwise token overlap >= 0.85, only for queries of >= 3 tokens
  -> score ``ratio * 0.95``
* queries with fewer than 2 (non-formulaic) tokens never match
"""

from __future__ import annotations

from dataclasses import dataclass

from app.services.normalize import content_tokens, normalize, tokenize

MIN_QUERY_TOKENS = 2
MIN_OVERLAP_TOKENS = 3
OVERLAP_THRESHOLD = 0.85
OVERLAP_WEIGHT = 0.95


@dataclass(frozen=True, slots=True)
class Query:
    original: str
    normalized: str
    tokens: tuple[str, ...]

    @property
    def is_searchable(self) -> bool:
        """False for a single word or a purely formulaic phrase."""
        return (
            len(self.tokens) >= MIN_QUERY_TOKENS
            and len(content_tokens(list(self.tokens))) >= MIN_QUERY_TOKENS
        )


def build_query(text: str) -> Query:
    normalized = normalize(text)
    return Query(original=text, normalized=normalized, tokens=tuple(tokenize(normalized)))


def match_score(query: Query, hadith_normalized: str) -> float:
    """Score in [0, 1]; 0.0 means "not a confident match"."""
    if not query.is_searchable:
        return 0.0
    if query.normalized in hadith_normalized:
        return 1.0
    if len(query.tokens) >= MIN_OVERLAP_TOKENS:
        padded = f" {hadith_normalized} "
        hits = sum(1 for token in query.tokens if f" {token} " in padded)
        ratio = hits / len(query.tokens)
        if ratio >= OVERLAP_THRESHOLD:
            return round(round(ratio, 2) * OVERLAP_WEIGHT, 2)
    return 0.0
