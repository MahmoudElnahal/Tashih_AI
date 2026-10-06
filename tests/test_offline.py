"""Offline six-books source on a small bundled sample of the real dataset."""

from __future__ import annotations

import shutil
from pathlib import Path

import httpx
import pytest

from app.services.verdict import VerdictCode
from app.sources.base import SourceUnavailable
from app.sources.offline import BOOKS, OfflineSixBooksSource
from tests.conftest import OFFLINE_SAMPLE


def _no_network(request: httpx.Request) -> httpx.Response:
    raise AssertionError(f"unexpected network call: {request.url}")


@pytest.fixture
async def source() -> OfflineSixBooksSource:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_no_network))
    src = OfflineSixBooksSource(client, data_dir=OFFLINE_SAMPLE)
    await src.wait_ready()
    return src


async def test_loads_sample_without_network(source: OfflineSixBooksSource) -> None:
    assert source.health() == "ready"
    assert source.size == 17


async def test_niyyat_is_bukhari_number_one_sahih(source: OfflineSixBooksSource) -> None:
    matches = await source.search("إنما الأعمال بالنيات")
    best = matches[0]
    assert best.source_book == "صحيح البخاري"
    assert best.number_or_page == "1"
    assert best.score == 1.0
    assert best.verdict.code is VerdictCode.SAHIH
    assert best.grade_text == "أخرجه في صحيحه"
    assert best.muhaddith == "البخاري"


async def test_bukhari_is_preferred_on_equal_scores(source: OfflineSixBooksSource) -> None:
    # This wording exists in both al-Bukhari #1 and an-Nasai #75.
    matches = await source.search("كانت هجرته إلى دنيا يصيبها")
    assert [(m.source_book, m.number_or_page, m.score) for m in matches] == [
        ("صحيح البخاري", "1", 1.0),
        ("سنن النسائي", "75", 1.0),
        # Same hadith worded «لدنيا» instead of «إلى دنيا»: a typo-tolerant match.
        ("صحيح مسلم", "4927", 0.9),
    ]


async def test_talab_ilm_is_ibn_majah_224_very_weak(source: OfflineSixBooksSource) -> None:
    best = (await source.search("طلب العلم فريضة على كل مسلم"))[0]
    assert best.source_book == "سنن ابن ماجه"
    assert best.number_or_page == "224"
    assert best.muhaddith == "الألباني"
    assert best.grade_text == "Very Daif"
    assert best.verdict.code is VerdictCode.DAIF
    assert best.verdict.label_ar == "ضعيف جداً"
    assert [g.scholar for g in best.grades] == [
        "الألباني",
        "محمد فؤاد عبد الباقي",
        "شعيب الأرناؤوط",
        "زبير علي زئي",
    ]


async def test_fabricated_and_single_word_do_not_match(source: OfflineSixBooksSource) -> None:
    assert await source.search("قال الفيل للنملة سافرت بالطائرة الى المريخ") == []
    assert await source.search("الصلاة") == []


async def test_search_while_loading_raises_unavailable() -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(_no_network))
    source = OfflineSixBooksSource(client, data_dir=OFFLINE_SAMPLE)
    with pytest.raises(SourceUnavailable):
        await source.search("إنما الأعمال بالنيات")


async def test_downloads_missing_books_into_cache(tmp_path: Path) -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        name = request.url.path.rsplit("/", 1)[-1]
        requested.append(name)
        return httpx.Response(200, content=(OFFLINE_SAMPLE / name).read_bytes())

    shutil.copy(OFFLINE_SAMPLE / "ara-bukhari.min.json", tmp_path / "ara-bukhari.min.json")
    client = httpx.AsyncClient(transport=httpx.MockTransport(handler))
    source = OfflineSixBooksSource(client, data_dir=tmp_path, base_url="https://cdn.test/editions")
    await source.wait_ready()

    assert source.health() == "ready"
    assert sorted(requested) == sorted(f"{b.id}.min.json" for b in BOOKS[1:])
    assert all((tmp_path / f"{b.id}.min.json").exists() for b in BOOKS)
    assert not list(tmp_path.glob("*.part"))


async def test_download_failure_sets_error_status(tmp_path: Path) -> None:
    client = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    source = OfflineSixBooksSource(client, data_dir=tmp_path)
    await source.wait_ready()
    assert source.health() == "error"
    with pytest.raises(SourceUnavailable):
        await source.search("إنما الأعمال بالنيات")
