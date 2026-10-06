"""Cheap, deterministic intent detection and hadith-text extraction.

This is the first stage of the chat router. The LLM is consulted only when
these heuristics are not enough (see ``app/services/chat.py``). New domains
(Quran, duas, ...) plug in by adding an ``Intent`` and a handler there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.services.matcher import build_query

_DIACRITICS = re.compile(r"[ؐ-ًؚ-ٰٟـۖ-ۭ]")
_BIDI_MARKS = re.compile(r"[‎‏‪-‮]")
_SPACES = re.compile(r"\s+")
_ARABIC_LETTER = re.compile(r"[ء-ي]")
_EDGE_PUNCT = " \t\n\r:：،,.؛;!؟?\"'«»“”‘’()[]{}-–—…"
_QUOTED = re.compile(r"«([^»]{6,})»|“([^”]{6,})”|\"([^\"]{6,})\"")


class Intent(StrEnum):
    HADITH_CHECK = "hadith_check"
    OUT_OF_SCOPE = "out_of_scope"
    NEEDS_TEXT = "needs_text"


@dataclass(frozen=True, slots=True)
class IntentGuess:
    intent: Intent
    hadith_text: str
    #: False when the heuristics are unsure and a second opinion is worthwhile.
    confident: bool


def _flex(pattern: str) -> str:
    """Let a pattern written with plain letters match common spelling variants."""
    return (
        pattern.replace("ا", "[اأإآ]")
        .replace("ي", "[يى]")
        .replace("ه", "[هة]")
        .replace(" ", r"\s+")
    )


_SALAWAT = r"(?: (?:ﷺ|صلي الله عليه وسلم|صلي الله عليه واله وسلم|عليه الصلاه والسلام|عليه السلام))?"
_PROPHET = r"(?:رسول الله|النبي|الرسول|نبي الله|المصطفي)"

# Ways people ask "is it authentic?": صحيح / حقيقي / مظبوط / موثوق ...
_AUTH = r"(?:صحيح|صح|حقيقي|حقيقيه|مظبوط|مضبوط|موثوق|سليم|معتمد)"
_THIS = r"(?:هذا|هاذا|هذه|ده|دا|دي|هو|هي)"
_REALLY = r"(?: (?:فعلا|حقا|بجد|حقيقي|اصلا))?"

_LEADING = [
    re.compile(_flex(p))
    for p in (
        r"^(?:و ?)?(?:السلام عليكم(?: ورحمه الله(?: تعالي)?(?: وبركاته)?)?|مرحبا|اهلا(?: وسهلا)?|مساء الخير|صباح الخير)",
        r"^(?:لو سمحت|من فضلك|يا شيخ|شيخنا|فضيله الشيخ|بالله عليك|رجاء|ممكن)",
        r"^(?:ما|ايه|اي)(?: هي| هو)? (?:مدي )?(?:صحه|درجه|حكم)(?: هذا)? (?:ال)?حديث",
        r"^هل(?: هذا)? (?:ال)?حديث(?: صحيح)?",
        r"^هل (?:صح|يصح|ثبت)(?: حديث)?",
        r"^(?:اريد|عايز|عاوز|ابغي|ابي|احتاج)(?: ان)? (?:اعرف|اتحقق من|اتاكد من|معرفه)(?: صحه| درجه)?(?: هذا)?(?: (?:ال)?حديث)?",
        r"^(?:تحقق|تاكد|تثبت) من(?: صحه)?(?: هذا)?(?: (?:ال)?حديث)?",
        r"^(?:صحه|درجه)(?: هذا)? (?:ال)?حديث",
        r"^(?:هل |هو )?(?:هذا|هاذا|ده|دا|دي) (?:ال)?حديث(?: (?:اللي|الذي) (?:بيقول|يقول))?",
        r"^(?:في|فيه|سمعت|قرات|شفت|وصلني|جالي)(?: ان)? (?:ال)?حديث(?: (?:بيقول|يقول|نصه|عن))?",
        r"^(?:ال)?حديث (?:ده|دا|هذا)",
        r"^(?:ال)?حديث(?: (?:بيقول|يقول|نصه))?",
        rf"^(?:عن \S+(?: \S+){{0,3}} )?(?:ان )?{_PROPHET}{_SALAWAT} (?:انه )?(?:قال|يقول)",
        rf"^(?:عن|قال|يقول|سمعت){_SALAWAT} {_PROPHET}{_SALAWAT}(?: (?:انه )?(?:قال|يقول))?",
        r"^(?:قال|يقول) ﷺ",
        rf"^(?:هل |هو )?{_AUTH}{_REALLY} ان",
        rf"^(?:هل |هو )?(?:فعلا |حقا )?{_PROPHET}{_SALAWAT}{_REALLY} (?:قال|بيقول|يقول){_REALLY}",
        rf"^(?:هل |هو )?(?:فعلا |حقا )?(?:قال|ورد عن|روي عن|ثبت عن){_SALAWAT} {_PROPHET}{_SALAWAT}{_REALLY}",
        r"^(?:هل |هو )?(?:في|فيه|يوجد|هناك|ورد) (?:ال)?حديث(?: (?:بيقول|يقول|نصه|عن|اسمه))?",
    )
]
_TRAILING = [
    re.compile(_flex(p))
    for p in (
        # "... صحيح؟", "... هو ده حديث حقيقي ولا لا", "... كلام مظبوط ولا غلط"
        rf"(?:هل )?(?:{_THIS} )*(?:ال)?(?:(?:حديث|كلام) )?{_AUTH}{_REALLY}"
        rf"(?: (?:ام|او|ولا|والا) (?:ضعيف|لا|لاء|غلط|خطا|موضوع|مكذوب|كذب|ايه|(?:مش|غير|ليس) {_AUTH}))?$",
        rf"(?:هل )?(?:{_THIS} )+(?:ال)?(?:حديث|كلام)(?: (?:ولا لا|ولا لاء|ام لا))?$",
        r"(?:ايه|ما|وش|شو) (?:صحته|حكمه|درجته|حقيقته|صحه (?:ال)?حديث(?: ده| دا| هذا)?)$",
        r"ما (?:مدي )?(?:صحته|صحه هذا الحديث|درجته|حكمه)$",
        r"هل (?:يصح|صح|ثبت|ورد)$",
        r"(?:ولا لا|ولا لاء|ام لا|او لا|ولا ايه)$",
        r"(?:وشكرا|شكرا|جزاكم الله خيرا|جزاك الله خيرا|بارك الله فيكم)$",
    )
]

_HADITH_CUE = re.compile(
    _flex(
        rf"حديث|احاديث|صحه|صحته|{_PROPHET} (?:ﷺ|صلي الله عليه وسلم)? ?(?:قال|يقول)|قال {_PROPHET}|ﷺ"
    )
)
_STRONG_OUT_OF_SCOPE = re.compile(
    _flex(
        r"^(?:ما|ايه) حكم(?! (?:هذا )?(?:ال)?حديث)|هل يجوز|هل يحل|فتوي|تفسير|سوره |\bايه \d|"
        r"مواقيت|وقت صلاه|اتجاه القبله|دعاء |اكتب لي|ترجم|برمج"
    )
)
_QUESTION_START = re.compile(
    _flex(r"^(?:ما|ماذا|من|متي|اين|كيف|لماذا|ليه|ازاي|هل|كم|اشرح|عرف|اذكر|ما هو|ما هي)")
    + "(?![ء-ي])"
)


def _tidy(text: str) -> str:
    text = _BIDI_MARKS.sub("", _DIACRITICS.sub("", text))
    return _SPACES.sub(" ", text).strip(_EDGE_PUNCT)


def clean_query(text: str) -> str:
    """Strip tashkeel, greetings, question wrappers and attribution phrases.

    Deterministic and conservative: it only removes text at the edges and
    never rewrites the hadith itself.
    """
    quoted = _QUOTED.search(text)
    if quoted:
        inner = _tidy(next(g for g in quoted.groups() if g))
        if build_query(inner).is_searchable:
            return inner

    current = _tidy(text)
    changed = True
    while changed and current:
        changed = False
        for pattern in _LEADING:
            match = pattern.search(current)
            if match and match.end() > 0:
                rest = current[match.end() :]
                # Only cut at a word boundary.
                if rest and _ARABIC_LETTER.match(rest[0]):
                    continue
                current = _tidy(rest)
                changed = True
        for pattern in _TRAILING:
            match = pattern.search(current)
            if match and match.start() < len(current):
                head = current[: match.start()]
                if head and _ARABIC_LETTER.match(head[-1]):
                    continue
                current = _tidy(head)
                changed = True
    return current


def guess_intent(message: str) -> IntentGuess:
    tidy = _tidy(message)
    if not _ARABIC_LETTER.search(tidy):
        return IntentGuess(Intent.OUT_OF_SCOPE, "", confident=True)

    has_cue = bool(_HADITH_CUE.search(tidy))
    text = clean_query(message)
    searchable = build_query(text).is_searchable

    if _STRONG_OUT_OF_SCOPE.search(tidy) and not has_cue:
        return IntentGuess(Intent.OUT_OF_SCOPE, "", confident=True)
    if not searchable:
        if has_cue:
            return IntentGuess(Intent.NEEDS_TEXT, "", confident=True)
        return IntentGuess(Intent.OUT_OF_SCOPE, "", confident=True)
    if has_cue:
        return IntentGuess(Intent.HADITH_CHECK, text, confident=True)
    if (
        _QUESTION_START.search(tidy)
        or tidy.endswith(("؟", "?"))
        or message.rstrip().endswith(("؟", "?"))
    ):
        return IntentGuess(Intent.HADITH_CHECK, text, confident=False)
    return IntentGuess(Intent.HADITH_CHECK, text, confident=True)
