from __future__ import annotations

import pytest

from app.services.matcher import build_query, match_score
from app.services.normalize import normalize, tokenize


def test_strips_tashkeel_and_tatweel() -> None:
    assert normalize("إِنَّمَا الْأَعْمَالُ بِالنِّيَّاتِ") == "انما الاعمال بالنيات"
    assert normalize("العـــلم") == "العلم"


def test_unifies_letter_forms() -> None:
    assert normalize("أحمد إبراهيم آمن ٱلله") == "احمد ابراهيم امن الله"
    assert normalize("على هدى") == "علي هدي"
    assert normalize("فريضة") == "فريضه"


def test_drops_non_arabic_and_collapses_spaces() -> None:
    assert normalize('  "طلب   العلم" ﷺ ،، hello 123 ') == "طلب العلم"
    assert normalize(None) == ""


def test_tokenize_unique_and_skips_single_letters() -> None:
    assert tokenize("و العلم و العلم نور") == ["العلم", "نور"]


HADITH = normalize("طَلَبُ الْعِلْمِ فَرِيضَةٌ عَلَى كُلِّ مُسْلِمٍ وَوَاضِعُ الْعِلْمِ عِنْدَ غَيْرِ أَهْلِهِ")


def test_exact_substring_scores_one() -> None:
    assert match_score(build_query("طلب العلم فريضة على كل مسلم"), HADITH) == 1.0


def test_token_overlap_needs_085() -> None:
    # 6 of 7 tokens present = 0.857 -> 0.86 * 0.95
    query = build_query("فريضة العلم طلب على كل مسلم جدا")
    assert match_score(query, HADITH) == pytest.approx(0.82)
    # 5 of 7 tokens -> below the threshold
    assert match_score(build_query("فريضة العلم طلب على كل انسان جدا"), HADITH) == 0.0


def test_overlap_requires_three_tokens() -> None:
    assert match_score(build_query("مسلم طلب"), HADITH) == 0.0


@pytest.mark.parametrize("text", ["الصلاة", "", "hello world", "قال رسول الله صلى الله عليه وسلم"])
def test_unsearchable_queries_never_match(text: str) -> None:
    query = build_query(text)
    assert not query.is_searchable
    assert match_score(query, normalize("قال رسول الله صلي الله عليه وسلم الصلاه نور")) == 0.0


def test_fabricated_sentence_does_not_match() -> None:
    assert match_score(build_query("قال الفيل للنملة سافرت بالطائرة الى المريخ"), HADITH) == 0.0
