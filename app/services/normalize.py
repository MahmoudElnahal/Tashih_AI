"""Arabic text normalization used for matching.

Ported from ``docs/reference/tashih-server.js`` (``normalize``).
"""

from __future__ import annotations

import re

# Tashkeel, superscript alef, tatweel and Quranic annotation marks.
_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟـۖ-ۭ]")
_ALEF_FORMS = re.compile(r"[أإآٱ]")
# Anything that is not a plain Arabic letter (hamza..ghain, feh..yeh) or whitespace.
_NON_ARABIC = re.compile(r"[^ء-غف-ي\s]")
_SPACES = re.compile(r"\s+")

# Formulaic words that appear in almost every hadith. A query made only of
# these (e.g. "قال رسول الله") identifies nothing, so they do not count toward
# the minimum number of query tokens.
FORMULAIC_TOKENS = frozenset(
    {
        "قال", "يقول", "قالت", "رسول", "الله", "النبي", "نبي", "صلي", "عليه", "وسلم",
        "عن", "ان", "انه", "حدثنا", "اخبرنا", "سمعت", "رضي", "عنه", "عنها", "عنهما",
        "حديث", "الحديث",
    }
)  # fmt: skip


def normalize(text: str | None) -> str:
    """Strip tashkeel/tatweel, unify letter forms, drop non-Arabic, collapse spaces."""
    s = _DIACRITICS.sub("", text or "")
    s = _ALEF_FORMS.sub("ا", s)
    s = s.replace("ى", "ي").replace("ة", "ه")
    s = _NON_ARABIC.sub(" ", s)
    return _SPACES.sub(" ", s).strip()


def tokenize(normalized: str) -> list[str]:
    """Unique tokens longer than one letter, in first-seen order."""
    seen: dict[str, None] = {}
    for token in normalized.split(" "):
        if len(token) > 1:
            seen.setdefault(token)
    return list(seen)


def content_tokens(tokens: list[str]) -> list[str]:
    return [t for t in tokens if t not in FORMULAIC_TOKENS]
