"""Dorar parser and client, tested against real responses saved from dorar.net."""

from __future__ import annotations

import json

import httpx
import pytest

from app.services.normalize import normalize
from app.services.verdict import VerdictCode
from app.sources.base import SourceBlocked, SourceUnavailable
from app.sources.dorar import DorarSource, extract_html, parse_results, to_matches
from tests.conftest import FIXTURES


def load(name: str) -> str:
    return (FIXTURES / f"{name}.json").read_text(encoding="utf-8")


def results(name: str):  # type: ignore[no-untyped-def]
    return parse_results(extract_html(json.loads(load(name))))


def test_parses_all_fields_from_real_fixture() -> None:
    parsed = results("dorar_niyyat")
    assert len(parsed) == 15

    third = parsed[2]
    assert normalize(third.hadith_text) == "انما الاعمال بالنيات"
    assert "َ" in third.hadith_text  # tashkeel is preserved for display
    assert third.narrator == "عمر بن الخطاب"
    assert third.muhaddith == "ابن تيمية"
    assert third.source_book == "مجموع الفتاوى"
    assert third.number_or_page == "18/24"
    assert third.grade_text == "صحيح غريب"

    assert parsed[1].narrator == "[عمر بن الخطاب]"
    assert parsed[9].muhaddith == "الألباني"
    assert parsed[9].source_book == "غاية المرام"
    assert parsed[9].number_or_page == "14"
    assert parsed[9].grade_text == "صحيح"


def test_text_has_no_markup_index_or_trailing_dots() -> None:
    for result in results("dorar_niyyat") + results("dorar_china"):
        assert "<" not in result.hadith_text
        assert not result.hadith_text[0].isdigit()
        assert not result.hadith_text.endswith(".")
        assert result.grade_text


def test_missing_narrator_is_none() -> None:
    first = results("dorar_china")[0]
    assert first.narrator is None
    assert first.muhaddith == "ابن باز"
    assert first.source_book == "التحفة الكريمة"
    assert first.number_or_page == "72"


def test_documented_list_shape_is_also_accepted() -> None:
    html = extract_html(json.loads(load("dorar_niyyat")))
    assert parse_results(extract_html({"ahadith": [{"th": html}]})) == parse_results(html)
    with pytest.raises(ValueError):
        extract_html({"unexpected": True})


def test_niyyat_matches_are_confident() -> None:
    matches = to_matches("إنما الأعمال بالنيات", results("dorar_niyyat"))
    assert len(matches) == 15
    assert all(m.score == 1.0 for m in matches)
    assert matches[0].verdict.code is VerdictCode.UNCLEAR  # "خطأ [يعني في إسناده] ..."
    assert matches[1].verdict.code is VerdictCode.SAHIH


def test_talab_ilm_first_result() -> None:
    matches = to_matches("طلب العلم فريضة على كل مسلم", results("dorar_talab_ilm"))
    assert matches[0].muhaddith == "الإمام أحمد"
    assert matches[0].grade_text == "كذب"
    assert matches[0].verdict.code is VerdictCode.MAWDU


def test_china_hadith_is_weak() -> None:
    matches = to_matches("اطلبوا العلم ولو في الصين", results("dorar_china"))
    assert [m.score for m in matches[:2]] == [1.0, 1.0]
    assert matches[0].verdict.code is VerdictCode.DAIF
    assert matches[0].verdict.label_ar == "ضعيف"
    # "ولو بالصين" variants are a different wording and are not confident matches.
    assert all("في" in m.hadith_text for m in matches)


def test_keyword_noise_for_fabricated_text_is_rejected() -> None:
    """Dorar returns 15 keyword hits for a made-up sentence; none may survive."""
    parsed = results("dorar_fabricated")
    assert len(parsed) == 15
    assert to_matches("قال الفيل للنملة سافرت بالطائرة الى المريخ", parsed) == []


def _source(handler: httpx.MockTransport) -> DorarSource:
    return DorarSource(httpx.AsyncClient(transport=handler), retry_backoff=0)


async def test_search_sends_browser_headers_and_parses() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, text=load("dorar_china"))

    source = _source(httpx.MockTransport(handler))
    matches = await source.search("اطلبوا العلم ولو في الصين")

    assert matches and matches[0].muhaddith == "ابن باز"
    assert seen[0].url.params["skey"] == "اطلبوا العلم ولو في الصين"
    assert "Mozilla" in seen[0].headers["User-Agent"]
    assert seen[0].headers["Referer"] == "https://dorar.net/"
    assert source.health() == "ok"


async def test_403_retries_once_then_raises_blocked() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        return httpx.Response(403, text="<title>Attention Required! | Cloudflare</title>")

    source = _source(httpx.MockTransport(handler))
    with pytest.raises(SourceBlocked):
        await source.search("إنما الأعمال بالنيات")
    assert calls == 2
    assert source.health() == "blocked"


async def test_retry_recovers_after_one_failure() -> None:
    calls = 0

    def handler(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ReadTimeout("slow", request=request)
        return httpx.Response(200, text=load("dorar_niyyat"))

    source = _source(httpx.MockTransport(handler))
    assert len(await source.search("إنما الأعمال بالنيات")) == 15
    assert calls == 2


async def test_timeout_and_bad_payload_raise_unavailable() -> None:
    def timeout(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectTimeout("down", request=request)

    with pytest.raises(SourceUnavailable):
        await _source(httpx.MockTransport(timeout)).search("إنما الأعمال بالنيات")

    garbage = httpx.MockTransport(lambda r: httpx.Response(200, text="<html>oops</html>"))
    source = _source(garbage)
    with pytest.raises(SourceUnavailable):
        await source.search("إنما الأعمال بالنيات")
    assert source.health() == "unavailable"

    challenge = httpx.MockTransport(
        lambda r: httpx.Response(200, text="<title>Just a moment...</title>")
    )
    with pytest.raises(SourceBlocked):
        await _source(challenge).search("إنما الأعمال بالنيات")
