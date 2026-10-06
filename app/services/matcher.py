"""Deterministic text matching shared by every hadith source.

The first three tiers are ported from ``docs/reference/tashih-server.js``;
the two typo-tolerant tiers were added for real user input:

1. exact substring of the normalized text                    -> 1.0
2. same, ignoring spaces ("انماا لاعمال" = "انما الاعمال")     -> 0.97
3. token overlap >= 0.85 (queries of >= 3 tokens)            -> ratio * 0.95
4. typo-tolerant overlap: a token also counts when it is one
   edit away from a hadith word, and the matched words must
   sit close together in the hadith (>= 3 tokens)            -> ratio * 0.90

Queries with fewer than 2 distinctive tokens never match. A score below 1.0
means "not letter-for-letter"; the API says so in ``message_ar``.
"""

from __future__ import annotations

from dataclasses import dataclass
from math import ceil

from app.services.normalize import content_tokens, normalize, tokenize

MIN_QUERY_TOKENS = 2
MIN_OVERLAP_TOKENS = 3
OVERLAP_THRESHOLD = 0.85
OVERLAP_WEIGHT = 0.95
SPACELESS_SCORE = 0.97
MIN_SPACELESS_CHARS = 8
FUZZY_WEIGHT = 0.90
#: Only words at least this long may differ by one edit ("الاعمل" ~ "الاعمال").
MIN_FUZZY_TOKEN_CHARS = 4


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


def within_one_edit(a: str, b: str) -> bool:
    """True if ``a`` and ``b`` are equal or differ by one typo.

    One typo = one inserted, deleted or replaced letter, or two adjacent
    letters swapped.
    """
    if a == b:
        return True
    if abs(len(a) - len(b)) > 1:
        return False
    if len(a) > len(b):
        a, b = b, a
    i = 0
    while i < len(a) and a[i] == b[i]:
        i += 1
    if len(a) == len(b):
        if a[i + 1 :] == b[i + 1 :]:
            return True
        return i + 1 < len(a) and a[i] == b[i + 1] and a[i + 1] == b[i] and a[i + 2 :] == b[i + 2 :]
    return a[i:] == b[i + 1 :]


def _similar(token: str, word: str) -> bool:
    if token == word:
        return True
    return (
        len(token) >= MIN_FUZZY_TOKEN_CHARS
        and len(word) >= MIN_FUZZY_TOKEN_CHARS
        and within_one_edit(token, word)
    )


def _fuzzy_ratio(tokens: tuple[str, ...], hadith_normalized: str, exact_hits: int) -> float:
    """Share of query tokens found (typo-tolerantly) in one compact stretch."""
    needed = ceil(OVERLAP_THRESHOLD * len(tokens))
    # Cheap gate: most of the words must already be right before the slower
    # comparison runs, so a different hadith cannot be "corrected" into a hit.
    if exact_hits < ceil(len(tokens) / 2):
        return 0.0
    words = hadith_normalized.split(" ")
    matched_at: list[tuple[int, int]] = [
        (position, index)
        for position, word in enumerate(words)
        for index, token in enumerate(tokens)
        if _similar(token, word)
    ]
    if len({index for _, index in matched_at}) < needed:
        return 0.0
    window = 2 * len(tokens) + 2
    best = 0
    start = 0
    for end in range(len(matched_at)):
        while matched_at[end][0] - matched_at[start][0] >= window:
            start += 1
        best = max(best, len({index for _, index in matched_at[start : end + 1]}))
    ratio = best / len(tokens)
    return ratio if ratio >= OVERLAP_THRESHOLD else 0.0


def match_score(query: Query, hadith_normalized: str) -> float:
    """Score in [0, 1]; 0.0 means "not a confident match"."""
    if not query.is_searchable:
        return 0.0
    if query.normalized in hadith_normalized:
        return 1.0
    spaceless = query.normalized.replace(" ", "")
    if len(spaceless) >= MIN_SPACELESS_CHARS and spaceless in hadith_normalized.replace(" ", ""):
        return SPACELESS_SCORE
    if len(query.tokens) < MIN_OVERLAP_TOKENS:
        return 0.0
    padded = f" {hadith_normalized} "
    hits = sum(1 for token in query.tokens if f" {token} " in padded)
    ratio = hits / len(query.tokens)
    if ratio >= OVERLAP_THRESHOLD:
        return round(round(ratio, 2) * OVERLAP_WEIGHT, 2)
    fuzzy = _fuzzy_ratio(query.tokens, hadith_normalized, hits)
    return round(round(fuzzy, 2) * FUZZY_WEIGHT, 2) if fuzzy else 0.0
