from __future__ import annotations

import pytest

from app.services.verdict import VerdictCode, classify, verdicts_conflict

S, H, D, M, U = (
    VerdictCode.SAHIH,
    VerdictCode.HASAN,
    VerdictCode.DAIF,
    VerdictCode.MAWDU,
    VerdictCode.UNCLEAR,
)

# Every Arabic string below is a real grade text taken from the Dorar fixtures
# unless marked otherwise.
ARABIC = [
    ("صحيح", S),
    ("صحيح غريب", S),
    ("غريب صحيح", S),
    ("رواية صحيحة", S),
    ("مشهور بالصحة", S),
    ("ثبت في الحديث المجمع على صحته", S),
    ("صحيح الإسناد", S),
    ("صحيح على شرط الشيخين", S),
    ("أخرجه في صحيحه", S),
    ("حسن صحيح", S),
    ("إسناده حسن", H),
    ("حسن غريب", H),  # synthetic
    ("ضعيف", D),
    ("إسناده ضعيف", D),
    ("ضعيف من جميع طرقه [عند جمهور أهل العلم بالحديث]", D),
    ("[روي بإسنادين فيهما ضعيف]", D),
    ("منكر", D),
    ("[ منكر ]", D),
    ("شاذ", D),  # synthetic
    ("ضعيف جداً", D),  # synthetic
    ("[لم يصح]", D),
    ("ليس بصحيح", D),
    ("غير صحيح، وروي من غير وجه", D),
    ("لا يثبت عندنا فيه شيء", D),
    ("لا يصح إسناده والرواية في هذا النحو فيها لين", D),
    ("ضعيف وبعضهم جعله في الموضوعات", D),
    ("[ قال الحاكم: تفرد حميد بن الربيع بهذه اللفظة] قلت: وهو واه", D),
    ("مرسل", D),
    ("مرسل وأما معنى هذا الحديث فصحيح في الأصول", D),  # sound meaning is not a ruling
    ("ليس بحديث، لكن معناه صحيح", M),
    ("ليس بحديث", M),
    ("مرسل، وروي موصولاً", D),
    ("هذا مرسل لا تقوم به الحجة", D),
    ("موضوع", M),  # synthetic
    ("كذب", M),
    ("باطل", M),
    ("باطل لا أصل له", M),
    ("لا أصل له", M),  # synthetic
    ("مكذوب", M),  # synthetic
    ("[فيه] أبو العاتكة لا يعرف، وليس لهذا الحديث أصل", M),
    ("ضعيف بل موضوع", M),  # synthetic
]

ARABIC_UNCLEAR = [
    "خطأ [يعني في إسناده] لا شك فيه عند أحد من أهل العلم بالحديث",
    "غريب من هذا الوجه",
    "رجال أبي يعلى رجال الصحيح",  # names the Sahih books, not a ruling
    "[ذكره في الصحيح المسند]",
    "أصح، وروي من وجه آخر",
    "رجاله كلهم ثقات وإسناده متصل",
    "[فيه] أحمد بن عبد الله الجويباري ممن يضرب المثل بكذبه",  # about a narrator
    "لم يرو هذا الحديث عن مسعر إلا يحيى بن هشام",
    "إسنادها جيد",
    "في صحته نظر",  # synthetic: doubt
    "مرسل أسنده الحاكم وزعم أنه صحيح الإسناد ولم يخرجاه",  # a claim he does not endorse
    "ليس بضعيف",  # synthetic: negated weak word
    "",
    "-",
]

ENGLISH = [
    ("Sahih", S),
    ("Hasan Sahih", S),
    ("Sahih - Agreed Upon", S),
    ("Sahih Lighairihi", S),
    ("Isnaad Sahih", S),
    ("Sahih Bukhari (1023) Sahih Muslim (894)", S),
    ("Hasan", H),
    ("Isnaad Hasan", H),
    ("Hasan Lighairihi", H),
    ("Daif", D),
    ("Very Daif", D),
    ("Daif Isnaad", D),
    ("Munkar", D),
    ("Shadh", D),
    ("Daif Munkar", D),
    ("Weak", D),
    ("Mawdu", M),
    ("Maudu", M),
    ("Batil", M),
    ("Fabricated", M),
]


@pytest.mark.parametrize(("grade", "expected"), ARABIC)
def test_arabic_grades(grade: str, expected: VerdictCode) -> None:
    assert classify(grade).code is expected


@pytest.mark.parametrize("grade", ARABIC_UNCLEAR)
def test_arabic_unrecognised_is_unclear(grade: str) -> None:
    assert classify(grade).code is U


@pytest.mark.parametrize(("grade", "expected"), ENGLISH)
def test_english_grades(grade: str, expected: VerdictCode) -> None:
    assert classify(grade).code is expected


@pytest.mark.parametrize(
    "grade",
    [
        "صحيح وقيل ضعيف",
        "حسن، وفيه ضعف",
        "ضعيف والصواب أنه صحيح",
        "Sahih Daif",
        "Hasan - Munkar",
        "صحيح وقيل موضوع",
    ],
)
def test_mixed_authentic_and_weak_is_unclear(grade: str) -> None:
    assert classify(grade).code is U


def test_narrator_names_are_not_rulings() -> None:
    assert classify("من صحاح الأحاديث وعيونها تفرد به الحسن بن سهل عن قطن").code is S
    assert classify("تفرد به الحسن بن سهل").code is U
    assert classify("رواه حسن بن صالح").code is U


def test_labels() -> None:
    assert classify("صحيح").label_ar == "صحيح"
    assert classify("Hasan Sahih").label_ar == "حسن صحيح"
    assert classify("Hasan").label_ar == "حسن"
    assert classify("Daif").label_ar == "ضعيف"
    assert classify("Very Daif").label_ar == "ضعيف جداً"
    assert classify("ضعيف جدا").label_ar == "ضعيف جداً"
    assert classify("باطل").label_ar == "موضوع (مكذوب)"
    assert classify("غريب").label_ar == "يحتاج مراجعة"


def test_verdicts_conflict() -> None:
    assert verdicts_conflict([S, D])
    assert verdicts_conflict([H, U, M])
    assert not verdicts_conflict([S, H, U])
    assert not verdicts_conflict([D, M, U])
    assert not verdicts_conflict([])


def test_ruling_words_inside_names_are_ignored() -> None:
    # Real Dorar text: «زيد بن ثابت» is a Companion's name, not a ruling.
    text = "لم يرو هذا الحديث عن أبي الزناد إلا ابنه، ولا يروى عن زيد بن ثابت إلا بهذا الإسناد"
    assert classify(text).code is U
    assert classify("رواه ابن حسن عن أبيه").code is U
    assert classify("حديث ثابت").code is S


def test_sound_meaning_is_not_an_authentic_ruling() -> None:
    assert classify("معناه صحيح").code is U
    assert classify("صحيح المعنى").code is U
    assert classify("ضعيف لكن معناه صحيح").code is D
