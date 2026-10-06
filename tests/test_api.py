"""API tests with mocked sources and a mocked LLM."""

from __future__ import annotations

from app.models import DISCLAIMER_AR
from app.services.llm import Extraction
from app.sources.base import SourceBlocked, SourceUnavailable
from tests.conftest import ClientFactory, FakeLLM, FakeSource, make_match

NIYYAT = "إنما الأعمال بالنيات"
CHINA = "اطلبوا العلم ولو في الصين"

SAHIH = make_match(
    NIYYAT, "صحيح", muhaddith="الألباني", book="غاية المرام", number="14", narrator="عمر بن الخطاب"
)
DAIF = make_match(
    CHINA, "ضعيف", muhaddith="ابن باز", book="التحفة الكريمة", number="72", narrator=None
)
MAWDU = make_match(CHINA, "باطل", muhaddith="الألباني", book="السلسلة الضعيفة", number="416")


def dorar(**kwargs: object) -> FakeSource:
    return FakeSource("dorar", grades_span_results=True, **kwargs)  # type: ignore[arg-type]


def offline(**kwargs: object) -> FakeSource:
    return FakeSource("offline_six_books", **kwargs)  # type: ignore[arg-type]


# ------------------------------------------------------------- /v1/verify ----


def test_found_sahih(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(matches=[SAHIH])])
    response = client.post("/v1/verify", json={"text": NIYYAT})
    body = response.json()

    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == body["request_id"]
    assert body["status"] == "found"
    assert body["verdict_code"] == "sahih"
    assert body["verdict_ar"] == "صحيح"
    assert body["source_used"] == "dorar"
    assert body["best_match"] == {
        "hadith_text": NIYYAT,
        "narrator": "عمر بن الخطاب",
        "muhaddith": "الألباني",
        "source_book": "غاية المرام",
        "number_or_page": "14",
        "grade_text": "صحيح",
        "grades": [{"scholar": "الألباني", "grade": "صحيح", "verdict_code": "sahih"}],
        "match_score": 1.0,
    }
    assert body["other_matches"] == []
    assert body["explanation_ar"] is None
    assert body["disclaimer_ar"] == DISCLAIMER_AR
    assert body["scholars_differ"] is False
    assert "الألباني" in body["message_ar"]
    assert "لا ينبغي نسبته" not in body["message_ar"]


def test_found_daif_says_not_to_attribute(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(matches=[DAIF, MAWDU])])
    body = client.post("/v1/verify", json={"text": CHINA}).json()

    assert body["status"] == "found"
    assert body["verdict_code"] == "daif"
    assert body["best_match"]["narrator"] is None
    assert "لا ينبغي نسبته إلى النبي ﷺ" in body["message_ar"]
    # Dorar: every top-scored grading is listed, not only the primary one.
    assert [g["scholar"] for g in body["best_match"]["grades"]] == ["ابن باز", "الألباني"]
    assert [m["muhaddith"] for m in body["other_matches"]] == ["الألباني"]


def test_split_scholars_get_no_single_verdict(client_factory: ClientFactory) -> None:
    """Some accept, some reject: the headline is «مختلف فيه», not one side's ruling."""
    unclear = make_match(NIYYAT, "غريب من هذا الوجه", muhaddith="ابن حجر")
    weak = make_match(NIYYAT, "إسناده ضعيف", muhaddith="العراقي")
    client = client_factory([dorar(matches=[unclear, SAHIH, weak])])
    body = client.post("/v1/verify", json={"text": NIYYAT, "max_results": 2}).json()

    assert body["status"] == "found"
    assert body["verdict_code"] == "unclear"
    assert body["verdict_ar"] == "مختلف فيه"
    assert body["scholars_differ"] is True
    assert body["refer_to_scholars"] is True
    assert "قبِله: الألباني" in body["message_ar"]
    assert "وردّه: العراقي" in body["message_ar"]
    assert "لا ينبغي نسبته" not in body["message_ar"]
    assert len(body["best_match"]["grades"]) == 3
    assert len(body["other_matches"]) == 1


def test_first_rule_keeps_the_first_definite_verdict(client_factory: ClientFactory) -> None:
    weak = make_match(NIYYAT, "إسناده ضعيف", muhaddith="العراقي")
    client = client_factory([dorar(matches=[SAHIH, weak])], dorar_primary_rule="first")
    body = client.post("/v1/verify", json={"text": NIYYAT}).json()
    assert body["verdict_code"] == "sahih"
    assert body["scholars_differ"] is True
    assert "مختلفة" in body["message_ar"]


def test_disputed_verdict_is_never_sent_to_the_llm(client_factory: ClientFactory) -> None:
    llm = FakeLLM(explanation="حكم عليه الألباني بأنه صحيح.")
    weak = make_match(NIYYAT, "إسناده ضعيف", muhaddith="العراقي")
    body = (
        client_factory([dorar(matches=[SAHIH, weak])], llm)
        .post("/v1/verify", json={"text": NIYYAT})
        .json()
    )
    assert body["verdict_ar"] == "مختلف فيه"
    assert body["explanation_ar"] is None
    assert llm.explain_calls == []


def test_primary_is_the_majority_verdict_not_the_first_result(
    client_factory: ClientFactory,
) -> None:
    """Real case: Dorar lists Ahmad's «كذب» first, but most scholars say «ضعيف»."""
    results = [
        make_match(CHINA, "كذب", muhaddith="الإمام أحمد"),
        make_match(CHINA, "غير صحيح", muhaddith="البزار"),
        make_match(CHINA, "ليس بصحيح", muhaddith="ابن حبان"),
        make_match(CHINA, "[ منكر ]", muhaddith="الساجي"),
        make_match(CHINA, "منكر جدا", muhaddith="الساجي"),  # same scholar counted once
    ]
    body = client_factory([dorar(matches=results)]).post("/v1/verify", json={"text": CHINA}).json()
    assert body["verdict_code"] == "daif"
    assert body["best_match"]["muhaddith"] == "البزار"
    assert len(body["best_match"]["grades"]) == 5
    assert body["scholars_differ"] is False

    first = client_factory([dorar(matches=results)], dorar_primary_rule="first")
    assert first.post("/v1/verify", json={"text": CHINA}).json()["verdict_code"] == "mawdu"


def test_all_grades_unclear_gives_unclear_verdict(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(matches=[make_match(NIYYAT, "غريب من هذا الوجه")])])
    body = client.post("/v1/verify", json={"text": NIYYAT}).json()
    assert body["status"] == "found"
    assert body["verdict_code"] == "unclear"
    assert body["verdict_ar"] == "يحتاج مراجعة"
    assert body["refer_to_scholars"] is True


def test_not_found_declines_to_rule(client_factory: ClientFactory) -> None:
    first, second = dorar(), offline()
    client = client_factory([first, second])
    body = client.post("/v1/verify", json={"text": "قال الفيل للنملة سافرت بالطائرة"}).json()

    assert body["status"] == "not_found"
    assert body["verdict_code"] is None and body["verdict_ar"] is None
    assert body["best_match"] is None
    assert body["refer_to_scholars"] is True
    assert "لا أستطيع الحكم" in body["message_ar"]
    assert first.calls and second.calls  # both sources were consulted


def test_single_word_is_not_searched(client_factory: ClientFactory) -> None:
    source = dorar(matches=[SAHIH])
    body = client_factory([source]).post("/v1/verify", json={"text": "الصلاة"}).json()
    assert body["status"] == "not_found"
    assert source.calls == []


def test_blocked_source_falls_back(client_factory: ClientFactory) -> None:
    blocked = dorar(error=SourceBlocked("dorar", "HTTP 403"))
    fallback = offline(
        matches=[make_match(NIYYAT, "أخرجه في صحيحه", muhaddith="البخاري", book="صحيح البخاري")]
    )
    client = client_factory([blocked, fallback])
    body = client.post("/v1/verify", json={"text": NIYYAT}).json()

    assert body["status"] == "found"
    assert body["verdict_code"] == "sahih"
    assert body["source_used"] == "offline_six_books"
    assert client.get("/health").json()["sources"] == {"dorar": "blocked", "offline": "ok"}


def test_fallback_not_found_mentions_limited_coverage(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(error=SourceBlocked("dorar", "403")), offline()])
    body = client.post("/v1/verify", json={"text": CHINA}).json()
    assert body["status"] == "not_found"
    assert body["source_used"] == "offline_six_books"
    assert "الكتب الستة فقط" in body["message_ar"]


def test_all_sources_down_is_unavailable(client_factory: ClientFactory) -> None:
    client = client_factory(
        [
            dorar(error=SourceBlocked("dorar", "403")),
            offline(error=SourceUnavailable("offline_six_books", "loading")),
        ]
    )
    response = client.post("/v1/verify", json={"text": NIYYAT})
    body = response.json()

    assert response.status_code == 200
    assert body["status"] == "unavailable"
    assert body["verdict_code"] is None
    assert body["source_used"] is None
    assert client.get("/health").json()["status"] == "degraded"


def test_repeated_query_is_served_from_cache(client_factory: ClientFactory) -> None:
    source = dorar(matches=[SAHIH])
    client = client_factory([source])
    first = client.post("/v1/verify", json={"text": NIYYAT}).json()
    second = client.post("/v1/verify", json={"text": "إِنَّمَا الأعمالُ بالنيّات"}).json()

    assert len(source.calls) == 1
    assert second["best_match"] == first["best_match"]
    assert second["request_id"] != first["request_id"]


def test_fallback_answers_are_not_cached(client_factory: ClientFactory) -> None:
    blocked = dorar(error=SourceBlocked("dorar", "403"))
    client = client_factory([blocked, offline(matches=[SAHIH])])
    client.post("/v1/verify", json={"text": NIYYAT})
    client.post("/v1/verify", json={"text": NIYYAT})
    assert len(blocked.calls) == 2  # Dorar is retried on the next request


# -------------------------------------------------------------------- LLM ----


def test_valid_explanation_is_returned(client_factory: ClientFactory) -> None:
    llm = FakeLLM(explanation="حكم عليه ابن باز بأنه ضعيف. لا ينبغي نسبته إلى النبي ﷺ.")
    body = (
        client_factory([dorar(matches=[DAIF])], llm).post("/v1/verify", json={"text": CHINA}).json()
    )
    assert body["explanation_ar"] == llm.explanation
    # The LLM only ever sees the retrieved fields.
    sent = llm.explain_calls[0]
    assert (sent.muhaddith, sent.grade_text, sent.verdict_code.value) == ("ابن باز", "ضعيف", "daif")


def test_contradicting_explanation_is_dropped(client_factory: ClientFactory) -> None:
    llm = FakeLLM(explanation="هذا حديث صحيح ثابت عن النبي ﷺ.")
    body = (
        client_factory([dorar(matches=[DAIF])], llm).post("/v1/verify", json={"text": CHINA}).json()
    )
    assert body["status"] == "found"
    assert body["verdict_code"] == "daif"
    assert body["explanation_ar"] is None


def test_openai_down_still_returns_full_result(client_factory: ClientFactory) -> None:
    llm = FakeLLM(fail=True)
    client = client_factory([dorar(matches=[SAHIH])], llm)
    body = client.post("/v1/verify", json={"text": NIYYAT}).json()
    assert body["status"] == "found"
    assert body["verdict_code"] == "sahih"
    assert body["best_match"]["source_book"] == "غاية المرام"
    assert body["explanation_ar"] is None

    chat = client.post("/v1/chat", json={"messages": [{"role": "user", "content": NIYYAT}]}).json()
    assert chat["status"] == "found" and chat["explanation_ar"] is None


def test_explain_false_skips_the_llm(client_factory: ClientFactory) -> None:
    llm = FakeLLM(explanation="حكم عليه الألباني بأنه صحيح.")
    client = client_factory([dorar(matches=[SAHIH])], llm)
    body = client.post("/v1/verify", json={"text": NIYYAT, "explain": False}).json()
    assert body["explanation_ar"] is None
    assert llm.explain_calls == []


# --------------------------------------------------------------- /v1/chat ----


def chat(client, content: str, **extra: object):  # type: ignore[no-untyped-def]
    return client.post(
        "/v1/chat", json={"messages": [{"role": "user", "content": content}], **extra}
    )


def test_chat_found_builds_reply_without_llm(client_factory: ClientFactory) -> None:
    source = dorar(matches=[DAIF])
    client = client_factory([source])
    response = chat(client, f"السلام عليكم، ما صحة حديث {CHINA}؟", session_id="abc")
    body = response.json()

    assert response.status_code == 200
    assert source.calls == [CHINA]
    assert body["status"] == "found"
    assert body["query"] == CHINA
    assert body["session_id"] == "abc"
    reply = body["reply_ar"]
    for expected in (
        "الحكم: ضعيف",
        CHINA,
        "ابن باز",
        "التحفة الكريمة — 72",
        "لا ينبغي نسبته",
        DISCLAIMER_AR,
    ):
        assert expected in reply


def test_chat_answers_the_last_user_message(client_factory: ClientFactory) -> None:
    source = dorar(matches=[SAHIH])
    client = client_factory([source])
    body = client.post(
        "/v1/chat",
        json={
            "messages": [
                {"role": "user", "content": CHINA},
                {"role": "assistant", "content": "..."},
                {"role": "user", "content": NIYYAT},
            ]
        },
    ).json()
    assert source.calls == [NIYYAT]
    assert body["verdict_code"] == "sahih"


def test_chat_out_of_scope_does_not_touch_sources_or_llm(client_factory: ClientFactory) -> None:
    source, llm = dorar(matches=[SAHIH]), FakeLLM()
    client = client_factory([source], llm)
    body = chat(client, "ما حكم صلاة الجماعة؟").json()

    assert body["status"] == "out_of_scope"
    assert body["verdict_code"] is None and body["best_match"] is None
    assert "التحقق من الأحاديث فقط" in body["reply_ar"]
    assert source.calls == [] and llm.extract_calls == []


def test_chat_asks_for_text_when_only_a_cue_is_given(client_factory: ClientFactory) -> None:
    body = chat(client_factory([dorar()]), "عايز اتحقق من حديث").json()
    assert body["status"] == "out_of_scope"
    assert "اكتب نص الحديث" in body["reply_ar"]


def test_chat_uses_llm_extraction_only_after_a_miss(client_factory: ClientFactory) -> None:
    class PickySource(FakeSource):
        async def search(self, text: str):  # type: ignore[no-untyped-def]
            self.calls.append(text)
            return [DAIF] if text == CHINA else []

    message = f"سمعت الخطيب امس يذكر {CHINA} وكنت متعجبا"
    source = PickySource("dorar", grades_span_results=True)
    llm = FakeLLM(extraction=Extraction("hadith_check", CHINA))
    body = chat(client_factory([source], llm), message).json()

    assert llm.extract_calls == [message]
    assert source.calls[-1] == CHINA
    assert body["status"] == "found" and body["verdict_code"] == "daif"


def test_chat_rejects_llm_extraction_not_present_in_message(client_factory: ClientFactory) -> None:
    source = dorar()
    llm = FakeLLM(extraction=Extraction("hadith_check", NIYYAT))  # not what the user wrote
    body = chat(client_factory([source], llm), "سمعت كلاما عن فضل طلب المعرفة والسفر لها").json()
    assert body["status"] == "not_found"
    assert len(source.calls) == 1


def test_chat_unsure_question_becomes_out_of_scope_when_llm_says_so(
    client_factory: ClientFactory,
) -> None:
    llm = FakeLLM(extraction=Extraction("out_of_scope", ""))
    body = chat(client_factory([dorar()], llm), "من هو أول الخلفاء الراشدين؟").json()
    assert body["status"] == "out_of_scope"


def test_chat_unavailable(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(error=SourceUnavailable("dorar", "timeout"))])
    body = chat(client, NIYYAT).json()
    assert body["status"] == "unavailable"
    assert "تعذر الوصول" in body["reply_ar"]


# ---------------------------------------------------------- cross-cutting ----


def assert_error(response, status: int, code: str) -> None:  # type: ignore[no-untyped-def]
    assert response.status_code == status
    error = response.json()["error"]
    assert set(error) == {"code", "message_ar", "message_en", "request_id"}
    assert error["code"] == code
    assert error["message_ar"] and error["message_en"]
    assert error["request_id"] == response.headers["X-Request-ID"]


def test_validation_errors_use_the_envelope(client_factory: ClientFactory) -> None:
    client = client_factory([dorar()])
    assert_error(client.post("/v1/verify", json={"text": ""}), 422, "validation_error")
    assert_error(client.post("/v1/verify", json={"text": "ا" * 2001}), 422, "validation_error")
    assert_error(
        client.post("/v1/verify", json={"text": NIYYAT, "max_results": 0}), 422, "validation_error"
    )
    assert_error(client.post("/v1/verify", json={}), 422, "validation_error")
    assert_error(client.post("/v1/chat", json={"messages": []}), 422, "validation_error")
    assert_error(
        client.post("/v1/chat", json={"messages": [{"role": "system", "content": "x"}]}),
        422,
        "validation_error",
    )
    assert_error(client.post("/v1/verify", content=b"not json"), 422, "validation_error")


def test_unknown_route_uses_the_envelope(client_factory: ClientFactory) -> None:
    assert_error(client_factory([dorar()]).get("/v1/nope"), 404, "not_found")


def test_api_key_required_when_configured(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(matches=[SAHIH])], api_keys="k1, k2")
    assert_error(client.post("/v1/verify", json={"text": NIYYAT}), 401, "unauthorized")
    assert_error(
        client.post("/v1/verify", json={"text": NIYYAT}, headers={"X-API-Key": "wrong"}),
        401,
        "unauthorized",
    )
    ok = client.post("/v1/verify", json={"text": NIYYAT}, headers={"X-API-Key": "k2"})
    assert ok.status_code == 200
    assert client.get("/health").status_code == 200  # health stays open


def test_rate_limit_per_ip(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(matches=[SAHIH])], rate_limit_per_minute=2)
    assert client.post("/v1/verify", json={"text": NIYYAT}).status_code == 200
    assert client.post("/v1/verify", json={"text": NIYYAT}).status_code == 200
    limited = client.post("/v1/verify", json={"text": NIYYAT})
    assert_error(limited, 429, "rate_limited")
    assert int(limited.headers["Retry-After"]) >= 1
    assert client.get("/health").status_code == 200


def test_request_id_is_echoed_or_generated(client_factory: ClientFactory) -> None:
    client = client_factory([dorar()])
    assert (
        client.get("/health", headers={"X-Request-ID": "client-id-12345"}).headers["X-Request-ID"]
        == "client-id-12345"
    )
    generated = client.get("/health", headers={"X-Request-ID": "bad id\n"}).headers["X-Request-ID"]
    assert len(generated) == 36


def test_health_and_cors(client_factory: ClientFactory) -> None:
    client = client_factory([dorar(), offline()], cors_origins="https://app.example")
    body = client.get("/health").json()
    assert body["status"] == "ok"
    assert set(body) == {"status", "version", "sources", "llm_enabled"}
    assert body["llm_enabled"] is False

    preflight = client.options(
        "/v1/verify",
        headers={"Origin": "https://app.example", "Access-Control-Request-Method": "POST"},
    )
    assert preflight.headers["access-control-allow-origin"] == "https://app.example"


def test_openapi_documents_the_contract(client_factory: ClientFactory) -> None:
    spec = client_factory([dorar()]).get("/openapi.json").json()
    assert {"/health", "/v1/verify", "/v1/chat"} <= set(spec["paths"])
    schemas = spec["components"]["schemas"]
    assert {"VerifyResponse", "ChatResponse", "ErrorResponse", "HadithMatch"} <= set(schemas)
    assert "422" in spec["paths"]["/v1/verify"]["post"]["responses"]


def test_unclear_verdict_is_never_sent_to_the_llm(client_factory: ClientFactory) -> None:
    llm = FakeLLM(explanation="حكم عليه ابن حجر بأنه يحتاج مراجعة.")
    client = client_factory([dorar(matches=[make_match(NIYYAT, "غريب من هذا الوجه")])], llm)
    body = client.post("/v1/verify", json={"text": NIYYAT}).json()
    assert body["verdict_code"] == "unclear"
    assert body["explanation_ar"] is None
    assert llm.explain_calls == []


def test_overreaching_explanation_is_dropped(client_factory: ClientFactory) -> None:
    """Real model output seen in testing: commentary beyond the ruling."""
    llm = FakeLLM(
        explanation="حكم عليه الألباني بأنه صحيح. يمكن الاعتماد عليه في الأمور المتعلقة بالدين."
    )
    body = (
        client_factory([dorar(matches=[SAHIH])], llm)
        .post("/v1/verify", json={"text": NIYYAT})
        .json()
    )
    assert body["verdict_code"] == "sahih"
    assert body["explanation_ar"] is None


def test_rejected_llm_correction_stays_not_found(client_factory: ClientFactory) -> None:
    # The model answers with a different hadith: the text is rejected, and an
    # unsure message is reported as not_found rather than out_of_scope.
    llm = FakeLLM(extraction=Extraction("hadith_check", NIYYAT))
    body = chat(client_factory([dorar()], llm), "طلب المعرفه واجب علي الجميع صح؟").json()
    assert body["status"] == "not_found"
