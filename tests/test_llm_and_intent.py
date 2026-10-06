from __future__ import annotations

import pytest

from app.services.intent import Intent, clean_query, guess_intent
from app.services.llm import ExplainFacts, Extraction, validate_explanation, validate_extraction
from app.services.verdict import classify


def facts(grade: str, book: str = "السلسلة الضعيفة", muhaddith: str = "الألباني") -> ExplainFacts:
    verdict = classify(grade)
    return ExplainFacts(
        hadith_text="اطلبوا العلم ولو في الصين",
        narrator="أنس بن مالك",
        muhaddith=muhaddith,
        source_book=book,
        number_or_page="416",
        grade_text=grade,
        verdict_code=verdict.code,
        verdict_ar=verdict.label_ar,
    )


# ------------------------------------------------- explanation validator ----


def test_accepts_explanation_consistent_with_weak_verdict() -> None:
    text = "حكم عليه الألباني بأنه ضعيف في السلسلة الضعيفة. لذلك لا ينبغي نسبته إلى النبي ﷺ."
    assert validate_explanation(text, facts("ضعيف")) == text


def test_drops_explanation_that_contradicts_weak_verdict() -> None:
    assert validate_explanation("هذا حديث صحيح رواه الألباني.", facts("ضعيف")) is None
    assert validate_explanation("الحديث حسن ويمكن العمل به.", facts("باطل")) is None


def test_drops_explanation_that_contradicts_authentic_verdict() -> None:
    sahih = facts("صحيح", book="صحيح الجامع")
    assert validate_explanation("هذا الحديث ضعيف ولا يصح.", sahih) is None
    assert validate_explanation("الحديث موضوع مكذوب.", sahih) is None
    ok = "حكم عليه الألباني بأنه صحيح في صحيح الجامع."
    assert validate_explanation(ok, sahih) == ok


def test_negated_authentic_wording_is_allowed_for_weak_verdict() -> None:
    text = "حكم عليه الألباني بأنه ضعيف، أي أنه لم يثبت وليس صحيحاً."
    assert validate_explanation(text, facts("ضعيف")) == text


def test_book_title_words_do_not_trigger_contradiction() -> None:
    # A weak hadith whose source book title contains "صحيح".
    weak_in_sahih_titled_book = facts("ضعيف", book="صحيح ابن حبان")
    text = "حكم عليه الألباني بأنه ضعيف، وهو مذكور في صحيح ابن حبان."
    assert validate_explanation(text, weak_in_sahih_titled_book) == text


def test_unclear_verdict_allows_no_ruling_words() -> None:
    unclear = facts("غريب من هذا الوجه")
    assert validate_explanation("الحديث صحيح.", unclear) is None
    assert validate_explanation("الحديث ضعيف.", unclear) is None
    ok = "عبارة المحدث تحتاج إلى مراجعة أهل العلم."
    assert validate_explanation(ok, unclear) == ok


@pytest.mark.parametrize(
    "text",
    [
        "",
        "   ",
        None,
        "ضعيف. " * 5,  # more than 3 sentences
        "ض" * 500,  # too long
        "الحديث ضعيف ولا يجوز العمل به.",  # fiqh ruling
        "الحديث ضعيف وقد أجمع العلماء على ذلك.",  # consensus claim
    ],
)
def test_drops_malformed_or_out_of_bounds_explanations(text: str | None) -> None:
    assert validate_explanation(text, facts("ضعيف")) is None


# -------------------------------------------------- extraction validator ----


def test_extraction_must_be_literal_text_from_the_message() -> None:
    message = "السلام عليكم، سمعت أن اطلبوا العلم ولو في الصين حديث، هل هذا صحيح؟"
    good = Extraction("hadith_check", "اطلبوا العلم ولو في الصين")
    assert validate_extraction(message, good) == good
    # The model "completed" the hadith from memory: rejected.
    invented = Extraction("hadith_check", "اطلبوا العلم ولو في الصين فإن طلب العلم فريضة")
    assert validate_extraction(message, invented) is None
    assert validate_extraction(message, Extraction("hadith_check", "  ")) is None
    assert validate_extraction(message, Extraction("out_of_scope", "x")) == Extraction(
        "out_of_scope", ""
    )


# ------------------------------------------------------------- heuristics ----


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("إنما الأعمال بالنيات", "إنما الأعمال بالنيات"),
        ("إِنَّمَا الْأَعْمَالُ بِالنِّيَّاتِ", "إنما الأعمال بالنيات"),
        ("ما صحة حديث اطلبوا العلم ولو في الصين؟", "اطلبوا العلم ولو في الصين"),
        (
            "السلام عليكم ورحمة الله وبركاته، ما صحة حديث: طلب العلم فريضة على كل مسلم",
            "طلب العلم فريضة على كل مسلم",
        ),
        ("قال رسول الله صلى الله عليه وسلم: إنما الأعمال بالنيات", "إنما الأعمال بالنيات"),
        ("قال النبي ﷺ «طلب العلم فريضة على كل مسلم»", "طلب العلم فريضة على كل مسلم"),
        ("هل حديث اطلبوا العلم ولو في الصين صحيح؟", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين هل هذا الحديث صحيح", "اطلبوا العلم ولو في الصين"),
        ("حديث إنما الأعمال بالنيات ما صحته؟", "إنما الأعمال بالنيات"),
        ("لو سمحت تحقق من حديث النظافة من الإيمان", "النظافة من الإيمان"),
    ],
)
def test_clean_query_strips_wrappers(message: str, expected: str) -> None:
    assert clean_query(message) == expected


def test_clean_query_does_not_cut_inside_words() -> None:
    # "حديثا" / "صحيحة" must not be treated as wrappers.
    assert clean_query("من حفظ على أمتي أربعين حديثا") == "من حفظ على أمتي أربعين حديثا"


@pytest.mark.parametrize(
    "message",
    [
        "ما حكم صلاة الجماعة؟",
        "هل يجوز الجمع بين الصلاتين",
        "ما تفسير سورة الإخلاص",
        "hello",
        "مرحبا",
        "شكرا",
        "",
        "😀",
    ],
)
def test_out_of_scope_messages(message: str) -> None:
    guess = guess_intent(message)
    assert guess.intent is Intent.OUT_OF_SCOPE
    assert guess.confident


def test_cue_without_text_asks_for_the_text() -> None:
    assert guess_intent("عايز اتحقق من حديث").intent is Intent.NEEDS_TEXT
    assert guess_intent("ما صحة هذا الحديث؟").intent is Intent.NEEDS_TEXT


def test_plain_pasted_text_is_a_confident_hadith_check() -> None:
    guess = guess_intent("طلب العلم فريضة على كل مسلم")
    assert guess.intent is Intent.HADITH_CHECK and guess.confident


def test_bare_question_is_not_confident() -> None:
    guess = guess_intent("من هو أول الخلفاء الراشدين؟")
    assert guess.intent is Intent.HADITH_CHECK and not guess.confident
