"""Deterministic verdict classification from a source's grade text.

The verdict code is NEVER produced by the LLM. It is computed here from the
grade string returned by the data source (Arabic for Dorar, English for the
offline dataset). The classifier is deliberately conservative: anything it
does not recognise, and anything that mixes authentic and weak wording, is
``unclear``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from enum import StrEnum

from app.services.normalize import normalize


class VerdictCode(StrEnum):
    SAHIH = "sahih"
    HASAN = "hasan"
    DAIF = "daif"
    MAWDU = "mawdu"
    UNCLEAR = "unclear"


@dataclass(frozen=True, slots=True)
class Verdict:
    code: VerdictCode
    label_ar: str


@dataclass(frozen=True, slots=True)
class GradeSignals:
    """What kinds of wording a text contains."""

    sahih: bool = False
    hasan: bool = False
    weak: bool = False
    fabricated: bool = False
    very: bool = False
    ambiguous: bool = False

    @property
    def authentic(self) -> bool:
        return self.sahih or self.hasan

    @property
    def negative(self) -> bool:
        return self.weak or self.fabricated


_L = "ء-ي"  # Arabic letters


def _word(pattern: str, prefixes: str = "") -> re.Pattern[str]:
    """Match ``pattern`` as a whole Arabic word, optionally with given prefixes."""
    return re.compile(rf"(?<![{_L}]){prefixes}(?:{pattern})(?![{_L}])")


_CONJ = "(?:و|ف)?"
_CONJ_AL = "(?:و|ف)?(?:ال)?"

# All Arabic patterns run on text normalized by ``normalize`` (ة->ه, ى->ي, أ->ا).
_AR_NEGATED_NEGATIVE = _word(r"(?:و|ف)?(?:ليس|ليست|غير|لا)\s+ب?(?:ضعيف|منكر|شاذ|موضوع|باطل)\S*")
_AR_NEGATED_AUTHENTIC = _word(
    r"(?:و|ف)?(?:لا|لم|ليس|ليست|غير|ما)\s+ب?(?:يصح|تصح|صح|صحيح|صحيحا|صحيحه|يثبت|تثبت|ثابت|ثابتا|ثابته)"
)
_AR_NO_ORIGIN = _word(r"(?:و|ف)?(?:لا\s+اصل|ليس\s+(?:\S+\s+){1,2}اصل)")
_AR_FABRICATED = _word(r"موضوع|موضوعه|مكذوب|مكذوبه|كذب|باطل|باطله|مختلق|مفتري|مصنوع", _CONJ_AL)
_AR_WEAK = _word(
    r"ضعيف|ضعيفه|ضعيفا|ضعفه|ضعف|منكر|منكره|شاذ|شاذه|واه|واهي|واهيه|متروك|معلول|مضطرب|لين|"
    r"مرسل|مرسله|منقطع|منقطعه|معضل",
    _CONJ_AL,
)
_AR_VERY = _word(r"(?:ضعيف|ضعيفه|منكر|واه|واهي|لين)(?:\s+\S+)?\s+جدا|واه|واهي|متروك", _CONJ_AL)
# No "ال" prefix on purpose: "رجال الصحيح" / "في الصحيح المسند" name a book, not a ruling.
_AR_SAHIH = _word(r"صحيح|صحيحه|صحيحا|صحاح|صححه|صح|ثابت|ثبت|علي\s+صحته|بالصحه", _CONJ)
# "الحسن بن ..." is a narrator name; a bare "حسن" followed by "بن" is too.
_AR_HASAN = re.compile(rf"(?<![{_L}]){_CONJ}(?:حسن|حسنه|حسنا)(?![{_L}])(?!\s+بن(?![{_L}]))")
_AR_DOUBT = _word(r"نظر|زعم|زعموا|يزعم")
# Ruling words inside a person's name: "زيد بن ثابت", "ابن حسن".
_AR_NAME = _word(r"(?:بن|ابن|ابي|ابو|ابا|ام)\s+(?:ثابت|حسن)")
# "معناه صحيح" judges the meaning, not the attribution to the Prophet ﷺ.
_AR_SOUND_MEANING = _word(
    r"(?:معناه|معني(?:\s+\S+){0,3}?)\s+(?:ف|و)?(?:صحيح|صحيحا|ثابت|حسن)|صحيح\s+المعني", _CONJ
)
# "ليس بحديث": not a hadith at all.
_AR_NOT_A_HADITH = _word(r"(?:ليس|ليست)\s+ب?(?:حديث|حديثا)", _CONJ)

_EN_FABRICATED = re.compile(r"maudu|mawdu|mawdoo|fabricated|forged|batil")
_EN_WEAK = re.compile(r"daif|da'if|dhaif|da`if|weak|munkar|shadh|malool")
_EN_VERY = re.compile(r"very|jiddan")
_EN_SAHIH = re.compile(r"sahih|saheeh|authentic")
_EN_HASAN = re.compile(r"hasan")

LABELS_AR: dict[VerdictCode, str] = {
    VerdictCode.SAHIH: "صحيح",
    VerdictCode.HASAN: "حسن",
    VerdictCode.DAIF: "ضعيف",
    VerdictCode.MAWDU: "موضوع (مكذوب)",
    VerdictCode.UNCLEAR: "يحتاج مراجعة",
}
LABEL_HASAN_SAHIH = "حسن صحيح"
LABEL_VERY_DAIF = "ضعيف جداً"

UNCLEAR = Verdict(VerdictCode.UNCLEAR, LABELS_AR[VerdictCode.UNCLEAR])
#: Scholars are split between accepting and rejecting: no single verdict is reported.
DISPUTED = Verdict(VerdictCode.UNCLEAR, "مختلف فيه")


def analyze(text: str | None) -> GradeSignals:
    """Detect authentic / weak / fabricated wording in Arabic or English text."""
    raw = text or ""
    ar = normalize(raw)
    en = raw.lower()

    ar = _AR_SOUND_MEANING.sub(" ", _AR_NAME.sub(" ", ar))
    not_a_hadith = bool(_AR_NOT_A_HADITH.search(ar))
    ar = _AR_NOT_A_HADITH.sub(" ", ar)
    ambiguous = bool(_AR_NEGATED_NEGATIVE.search(ar)) or bool(_AR_DOUBT.search(ar))
    ar = _AR_NEGATED_NEGATIVE.sub(" ", ar)
    negated_authentic = bool(_AR_NEGATED_AUTHENTIC.search(ar))
    ar = _AR_NEGATED_AUTHENTIC.sub(" ", ar)
    no_origin = bool(_AR_NO_ORIGIN.search(ar))
    ar = _AR_NO_ORIGIN.sub(" ", ar)

    return GradeSignals(
        sahih=bool(_AR_SAHIH.search(ar)) or bool(_EN_SAHIH.search(en)),
        hasan=bool(_AR_HASAN.search(ar)) or bool(_EN_HASAN.search(en)),
        weak=negated_authentic or bool(_AR_WEAK.search(ar)) or bool(_EN_WEAK.search(en)),
        fabricated=no_origin
        or not_a_hadith
        or bool(_AR_FABRICATED.search(ar))
        or bool(_EN_FABRICATED.search(en)),
        very=bool(_AR_VERY.search(ar)) or bool(_EN_VERY.search(en)),
        ambiguous=ambiguous,
    )


def classify(grade_text: str | None) -> Verdict:
    """Map a grade string to a verdict.

    Rules, in order:

    1. authentic wording together with weak/fabricated wording -> ``unclear``
    2. doubt wording ("فيه نظر") or a negated weak word      -> ``unclear``
    3. fabricated wording                                     -> ``mawdu``
    4. weak wording, or a negated authentic word ("لا يصح")   -> ``daif``
    5. "sahih" wording (including "حسن صحيح")                 -> ``sahih``
    6. "hasan" wording                                        -> ``hasan``
    7. anything else                                          -> ``unclear``
    """
    s = analyze(grade_text)
    if s.ambiguous or (s.authentic and s.negative):
        return UNCLEAR
    if s.fabricated:
        return Verdict(VerdictCode.MAWDU, LABELS_AR[VerdictCode.MAWDU])
    if s.weak:
        return Verdict(VerdictCode.DAIF, LABEL_VERY_DAIF if s.very else LABELS_AR[VerdictCode.DAIF])
    if s.sahih:
        label = LABEL_HASAN_SAHIH if s.hasan else LABELS_AR[VerdictCode.SAHIH]
        return Verdict(VerdictCode.SAHIH, label)
    if s.hasan:
        return Verdict(VerdictCode.HASAN, LABELS_AR[VerdictCode.HASAN])
    return UNCLEAR


ACCEPTED = frozenset({VerdictCode.SAHIH, VerdictCode.HASAN})
REJECTED = frozenset({VerdictCode.DAIF, VerdictCode.MAWDU})


def verdicts_conflict(codes: list[VerdictCode]) -> bool:
    """True when some scholars accept the hadith and others reject it."""
    present = set(codes)
    return bool(present & ACCEPTED) and bool(present & REJECTED)
