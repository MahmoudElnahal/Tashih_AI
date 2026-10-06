"""Typo tolerance: real user input is misspelled, but a match must never be a guess."""

from __future__ import annotations

import json
import time

import httpx
import pytest

from app.services.intent import clean_query
from app.services.llm import Extraction, validate_extraction
from app.services.matcher import build_query, match_score, within_one_edit
from app.services.normalize import normalize
from app.sources.dorar import extract_html, parse_results, to_matches
from app.sources.offline import OfflineSixBooksSource
from tests.conftest import (
    FIXTURES,
    OFFLINE_SAMPLE,
    ClientFactory,
    FakeLLM,
    FakeSource,
    make_match,
)

NIYYAT = normalize("إنما الأعمال بالنيات وإنما لكل امرئ ما نوى")
CHINA = normalize("اطلبوا العلم ولو في الصين")


@pytest.mark.parametrize(
    ("a", "b", "expected"),
    [
        ("الاعمال", "الاعمال", True),
        ("الاعمل", "الاعمال", True),  # missing letter
        ("الاعماال", "الاعمال", True),  # extra letter
        ("الاعمار", "الاعمال", True),  # wrong letter
        ("الاعملا", "الاعمال", True),  # swapped neighbours
        ("الاعم", "الاعمال", False),  # two letters missing
        ("العلم", "العمل", True),
        ("الصلاه", "الزكاه", False),
        ("", "ا", True),
    ],
)
def test_within_one_edit(a: str, b: str, expected: bool) -> None:
    assert within_one_edit(a, b) is expected
    assert within_one_edit(b, a) is expected


# --------------------------------------------------------------- matcher ----


def test_misplaced_space_matches_as_spaceless() -> None:
    # The exact message from the bug report.
    assert match_score(build_query("انماا لاعمال بالنيات"), NIYYAT) == 0.97
    assert match_score(build_query("انماالاعمال بالنيات"), NIYYAT) == 0.97


@pytest.mark.parametrize(
    ("typed", "hadith"),
    [
        ("انما الاعمل بالنيات", NIYYAT),  # missing letter
        ("انما الاعمال بالنياات", NIYYAT),  # doubled letter
        ("اطلبو العلم ولو فى الصين", CHINA),  # dropped alef
        ("اطلبوا العلم ولو في الصيين", CHINA),
        ("اطلبوا العلم ولو فى الصني", CHINA),  # swapped letters
    ],
)
def test_one_typo_per_word_still_matches(typed: str, hadith: str) -> None:
    assert match_score(build_query(typed), hadith) == 0.9


@pytest.mark.parametrize(
    "typed",
    [
        "انما الافعال بالخواتيم",  # a different saying that shares one word
        "اطلبوا المال ولو في السوق",  # two wrong words out of five
        "قال الفيل للنملة سافرت بالطائرة الى المريخ",
        "انمل الاعمل بالنيت",  # every word wrong: nothing reliable to anchor on
    ],
)
def test_different_text_is_not_rescued_by_fuzziness(typed: str) -> None:
    assert match_score(build_query(typed), NIYYAT) == 0.0
    assert match_score(build_query(typed), CHINA) == 0.0


def test_short_words_must_be_exact() -> None:
    # "في" / "من" / "عن" are too short to tolerate an edit.
    hadith = normalize("من حسن اسلام المرء تركه ما لا يعنيه")
    assert match_score(build_query("عن حسن اسلام المرء"), hadith) == 0.0


def test_fuzzy_words_must_be_close_together() -> None:
    filler = " ".join(f"كلمه{i}" for i in range(40))
    scattered = normalize(f"اطلبوا {filler} العلم {filler} الصين")
    assert match_score(build_query("اطلبو العلم الصين"), scattered) == 0.0


def test_fuzzy_never_changes_what_dorar_noise_returns() -> None:
    parsed = parse_results(
        extract_html(json.loads((FIXTURES / "dorar_fabricated.json").read_text(encoding="utf-8")))
    )
    assert to_matches("قال الفيل للنملة سافرت بالطائرة الى المريخ", parsed) == []


def test_misspelled_query_against_real_dorar_results() -> None:
    parsed = parse_results(
        extract_html(json.loads((FIXTURES / "dorar_china.json").read_text(encoding="utf-8")))
    )
    matches = to_matches("اطلبو العلم ولو فى الصين", parsed)
    assert matches and matches[0].score == 0.9
    assert matches[0].muhaddith == "ابن باز"


# -------------------------------------------------------------- wrappers ----


@pytest.mark.parametrize(
    ("message", "expected"),
    [
        ("انماا لاعمال بالنيات هو دا حديث صحيح", "انماا لاعمال بالنيات"),
        ("اطلبوا العلم ولو في الصين ده حديث صحيح؟", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين صح ولا غلط", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين صحيح ولا ضعيف", "اطلبوا العلم ولو في الصين"),
        ("هو ده حديث اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("سمعت حديث بيقول اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("في حديث بيقول النظافة من الإيمان ايه صحته", "النظافة من الإيمان"),
        ("الحديث ده اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("طلب العلم فريضة على كل مسلم هو ده حديث", "طلب العلم فريضة على كل مسلم"),
        ("هل حديث اطلبوا العلم ولو في الصين حقيقي", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين حقيقي ولا لا", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين ده كلام مظبوط؟", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين حديث حقيقي ولا مش حقيقي", "اطلبوا العلم ولو في الصين"),
        ("هو النبي قال فعلا اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("هل النبي صلى الله عليه وسلم قال اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("هل صحيح ان الرسول قال اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("هل قال رسول الله اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("هل في حديث بيقول اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("هل ورد عن النبي اطلبوا العلم ولو في الصين", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين ده حديث ولا لا", "اطلبوا العلم ولو في الصين"),
        ("اطلبوا العلم ولو في الصين موثوق؟", "اطلبوا العلم ولو في الصين"),
        ("هل حقيقي ان النبي قال اطلبوا العلم ولو في الصين ولا ايه", "اطلبوا العلم ولو في الصين"),
    ],
)
def test_colloquial_wrappers_are_stripped(message: str, expected: str) -> None:
    assert clean_query(message) == expected


# ------------------------------------------------------ LLM typo fixing ----


def test_llm_may_fix_spelling() -> None:
    message = "انماا لاعمال بلنيات هو دا حديث صحيح"
    fixed = Extraction("hadith_check", "إنما الأعمال بالنيات")
    assert validate_extraction(message, fixed) == fixed


def test_llm_may_not_complete_or_swap_the_hadith() -> None:
    message = "انماا لاعمال بلنيات هو دا حديث صحيح"
    completed = Extraction("hadith_check", "إنما الأعمال بالنيات وإنما لكل امرئ ما نوى")
    swapped = Extraction("hadith_check", "طلب العلم فريضة على كل مسلم")
    assert validate_extraction(message, completed) is None
    assert validate_extraction(message, swapped) is None


# ------------------------------------------------------------------- API ----


class RealisticDorar(FakeSource):
    """Scores the real saved Dorar results for «إنما الأعمال بالنيات»."""

    async def search(self, text: str):  # type: ignore[no-untyped-def]
        self.calls.append(text)
        parsed = parse_results(
            extract_html(json.loads((FIXTURES / "dorar_niyyat.json").read_text(encoding="utf-8")))
        )
        return to_matches(text, parsed)


REPORTED = "انماا لاعمال بالنيات هو دا حديث صحيح"


def ask(client, content: str):  # type: ignore[no-untyped-def]
    return client.post("/v1/chat", json={"messages": [{"role": "user", "content": content}]}).json()


def test_reported_message_is_now_found(client_factory: ClientFactory) -> None:
    """«انماا لاعمال بالنيات هو دا حديث صحيح» used to return not_found."""
    source = RealisticDorar("dorar", grades_span_results=True)
    body = ask(client_factory([source]), REPORTED)
    assert source.calls == ["انماا لاعمال بالنيات"]
    assert body["status"] == "found"
    assert body["best_match"]["match_score"] == 0.97
    assert "لا يطابق نص المصدر حرفاً بحرف" in body["message_ar"]
    # Dorar alone lists gradings on both sides for this text (side chains).
    assert body["verdict_ar"] == "مختلف فيه"


def test_text_in_bukhari_is_sahih_even_when_dorar_lists_criticised_chains(
    client_factory: ClientFactory,
) -> None:
    offline = OfflineSixBooksSource(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        data_dir=OFFLINE_SAMPLE,
    )
    client = client_factory([RealisticDorar("dorar", grades_span_results=True), offline])  # type: ignore[list-item]
    deadline = time.monotonic() + 10
    while client.get("/health").json()["sources"]["offline"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.02)

    for message, score in ((REPORTED, 0.97), ("إنما الأعمال بالنيات", 1.0)):
        body = ask(client, message)
        best = body["best_match"]
        assert (body["verdict_code"], body["verdict_ar"]) == ("sahih", "صحيح")
        assert (best["source_book"], best["number_or_page"]) == ("صحيح البخاري", "1")
        assert best["grade_text"] == "أخرجه في صحيحه"
        assert best["match_score"] == score
        assert body["source_used"] == "offline_six_books"
        # Dorar's gradings are still listed, and the disagreement is mentioned.
        assert len(best["grades"]) > 5
        assert body["scholars_differ"] is True
        assert "أحكام أخرى مختلفة" in body["message_ar"]
        assert body["other_matches"][0]["muhaddith"]


def test_typo_tolerant_tier_cannot_trigger_the_sahihayn_rule(client_factory: ClientFactory) -> None:
    """A 0.9 (one-edit) match is not strong enough to settle the verdict."""
    from app.services.verify import SAHIHAYN_MIN_SCORE

    assert SAHIHAYN_MIN_SCORE > 0.9


def test_exact_match_has_no_literal_warning(client_factory: ClientFactory) -> None:
    source = FakeSource("dorar", matches=[make_match("إنما الأعمال بالنيات", "صحيح")])
    body = client_factory([source]).post("/v1/verify", json={"text": "إنما الأعمال بالنيات"}).json()
    assert "حرفاً بحرف" not in body["message_ar"]


def test_llm_correction_is_used_after_a_miss_and_disclosed(client_factory: ClientFactory) -> None:
    class OnlyCorrect(FakeSource):
        async def search(self, text: str):  # type: ignore[no-untyped-def]
            self.calls.append(text)
            return [make_match(text, "صحيح")] if text == "إنما الأعمال بالنيات" else []

    source = OnlyCorrect("dorar", grades_span_results=True)
    llm = FakeLLM(extraction=Extraction("hadith_check", "إنما الأعمال بالنيات"))
    body = (
        client_factory([source], llm)
        .post(
            "/v1/chat", json={"messages": [{"role": "user", "content": "انما الاعمل بلنيات صح؟"}]}
        )
        .json()
    )
    assert source.calls[-1] == "إنما الأعمال بالنيات"
    assert body["status"] == "found"
    assert body["query"] == "إنما الأعمال بالنيات"
    assert "صُحِّحت كتابة النص قبل البحث" in body["message_ar"]
    assert "صُحِّحت كتابة النص قبل البحث" in body["reply_ar"]


def test_six_books_gradings_are_added_to_dorar_results(client_factory: ClientFactory) -> None:
    """Dorar lists only weak chains; Ibn Majah #223 is graded Sahih by Al-Albani."""
    text = "إنما ورثوا العلم فمن أخذه أخذ بحظ وافر"

    class OnlyCritics(FakeSource):
        async def search(self, query: str):  # type: ignore[no-untyped-def]
            self.calls.append(query)
            return [make_match(text, "إسناده ضعيف", muhaddith="ابن القطان")]

    offline = OfflineSixBooksSource(
        httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500))),
        data_dir=OFFLINE_SAMPLE,
    )
    client = client_factory([OnlyCritics("dorar", grades_span_results=True), offline])  # type: ignore[list-item]
    deadline = time.monotonic() + 10
    while client.get("/health").json()["sources"]["offline"] != "ready":
        assert time.monotonic() < deadline
        time.sleep(0.02)

    body = client.post("/v1/verify", json={"text": text}).json()
    scholars = [g["scholar"] for g in body["best_match"]["grades"]]
    assert scholars[0] == "ابن القطان" and "الألباني" in scholars
    assert body["verdict_ar"] == "مختلف فيه"
    assert body["source_used"] == "dorar"
    assert "قبِله: الألباني" in body["message_ar"]
    assert any(m["source_book"] == "سنن ابن ماجه" for m in body["other_matches"])


def test_hadith_ending_with_an_ordinary_word_is_not_trimmed() -> None:
    # Words that merely resemble a wrapper inside the hadith itself stay put.
    assert clean_query("ان الصدق يهدي الى البر") == "ان الصدق يهدي الى البر"
    assert clean_query("من قال لا اله الا الله دخل الجنة") == "من قال لا اله الا الله دخل الجنة"
    assert clean_query("قل الحق ولو كان مرا") == "قل الحق ولو كان مرا"
