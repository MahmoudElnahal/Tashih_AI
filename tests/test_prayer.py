"""Prayer module tests. Aladhan is mocked with real saved responses."""

from __future__ import annotations

import json
import time
from collections.abc import Callable, Iterator
from datetime import UTC, date, datetime, timedelta

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.daily import DAILY_PHRASES, phrase_for
from app.services.prayer import PrayerService, compass_ar
from app.sources.aladhan import AladhanClient, AladhanError, parse_timings
from app.sources.offline import OfflineSixBooksSource
from tests.conftest import FIXTURES, OFFLINE_SAMPLE, FakeSource

TIMINGS = (FIXTURES / "aladhan_timings_cairo.json").read_text(encoding="utf-8")
QIBLA = (FIXTURES / "aladhan_qibla_cairo.json").read_text(encoding="utf-8")
CAIRO = {"lat": 30.0444, "lon": 31.2357}
TUESDAY = date(2026, 10, 6)
FRIDAY = date(2026, 10, 9)


def at(hour: int, minute: int = 0, day: date = TUESDAY) -> datetime:
    """A UTC instant given as Cairo wall-clock time (UTC+3 in October 2026)."""
    return datetime(day.year, day.month, day.day, hour - 3, minute, tzinfo=UTC)


class Upstream:
    """Mock Aladhan: serves the saved fixtures and records requests."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.fail_with: int | None = None
        self.timeout = False

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        if self.timeout:
            raise httpx.ReadTimeout("slow", request=request)
        if self.fail_with:
            return httpx.Response(self.fail_with, json={"code": self.fail_with, "data": "error"})
        if "/qibla/" in request.url.path:
            return httpx.Response(200, text=QIBLA)
        return httpx.Response(200, text=TIMINGS)


Factory = Callable[..., tuple[TestClient, Upstream]]


@pytest.fixture
def make() -> Iterator[Factory]:
    opened: list[TestClient] = []

    def factory(
        now: datetime = at(13), sources: list[object] | None = None
    ) -> tuple[TestClient, Upstream]:
        upstream = Upstream()
        http = httpx.AsyncClient(transport=httpx.MockTransport(upstream))
        service = PrayerService(AladhanClient(http), clock=lambda: now)
        settings = Settings(_env_file=None, openai_api_key="", rate_limit_per_minute=0)  # type: ignore[call-arg]
        app = create_app(
            settings,
            sources=sources if sources is not None else [FakeSource("dorar")],  # type: ignore[arg-type]
            http_client=http,
            prayer_service=service,
        )
        client = TestClient(app)
        client.__enter__()
        opened.append(client)
        return client, upstream

    yield factory
    for client in opened:
        client.__exit__(None, None, None)


# ------------------------------------------------------------------ parser ----


def test_parse_real_timings_payload() -> None:
    timings = parse_timings(json.loads(TIMINGS), TUESDAY)
    assert timings.timezone == "Africa/Cairo"
    assert (timings.method_id, timings.method_name) == (5, "Egyptian General Authority of Survey")
    assert list(timings.times) == ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"]
    assert timings.times["fajr"].isoformat() == "2026-10-06T05:25:00+03:00"
    assert timings.times["isha"].isoformat() == "2026-10-06T19:52:00+03:00"
    assert (timings.hijri.date, timings.hijri.year, timings.hijri.month_number) == (
        "25-04-1448",
        1448,
        4,
    )
    assert timings.hijri.weekday_ar == "الثلاثاء"


def test_parse_rejects_unexpected_payload() -> None:
    with pytest.raises(AladhanError):
        parse_timings({"data": "Timezone lookup is temporarily unavailable."}, TUESDAY)
    with pytest.raises(AladhanError):
        parse_timings({"data": {"timings": {}, "meta": {}, "date": {}}}, TUESDAY)


def test_times_with_timezone_suffix_and_isha_after_midnight() -> None:
    payload = json.loads(TIMINGS)
    payload["data"]["timings"]["Fajr"] = "05:25 (EEST)"
    payload["data"]["timings"]["Isha"] = "00:10"
    timings = parse_timings(payload, TUESDAY)
    assert timings.times["fajr"].isoformat() == "2026-10-06T05:25:00+03:00"
    assert timings.times["isha"].isoformat() == "2026-10-07T00:10:00+03:00"


@pytest.mark.parametrize(
    ("degrees", "expected"),
    [
        (0, "شمال"),
        (22, "شمال"),
        (23, "شمال شرق"),
        (136.14, "جنوب شرق"),
        (180, "جنوب"),
        (270, "غرب"),
        (359, "شمال"),
    ],
)
def test_compass(degrees: float, expected: str) -> None:
    assert compass_ar(degrees) == expected


# ------------------------------------------------------------------- times ----


def test_prayer_times_and_next_prayer(make: Factory) -> None:
    client, upstream = make(at(13, 0))
    response = client.get("/v1/prayer/times", params=CAIRO)
    body = response.json()

    assert response.status_code == 200
    assert body["date"] == "2026-10-06"
    assert body["timezone"] == "Africa/Cairo"
    assert body["method"] == {"id": 5, "name": "Egyptian General Authority of Survey"}
    assert body["is_friday"] is False
    assert body["hijri"]["date"] == "25-04-1448"
    assert [(p["key"], p["name_ar"], p["time"], p["is_prayer"]) for p in body["prayers"]] == [
        ("fajr", "الفجر", "2026-10-06T05:25:00+03:00", True),
        ("sunrise", "الشروق", "2026-10-06T06:51:00+03:00", False),
        ("dhuhr", "الظهر", "2026-10-06T12:43:00+03:00", True),
        ("asr", "العصر", "2026-10-06T16:05:00+03:00", True),
        ("maghrib", "المغرب", "2026-10-06T18:35:00+03:00", True),
        ("isha", "العشاء", "2026-10-06T19:52:00+03:00", True),
    ]
    assert body["next_prayer"] == {
        "key": "asr",
        "name_ar": "العصر",
        "time": "2026-10-06T16:05:00+03:00",
        "seconds_remaining": 3 * 3600 + 5 * 60,
    }
    # Default method 5, date in Aladhan's DD-MM-YYYY format, rounded coordinates.
    sent = upstream.requests[0]
    assert sent.url.path.endswith("/timings/06-10-2026")
    assert dict(sent.url.params) == {"latitude": "30.04", "longitude": "31.24", "method": "5"}


def test_sunrise_is_never_the_next_prayer(make: Factory) -> None:
    client, _ = make(at(6, 0))
    assert client.get("/v1/prayer/times", params=CAIRO).json()["next_prayer"]["key"] == "dhuhr"


def test_after_isha_next_prayer_is_tomorrows_fajr(make: Factory) -> None:
    client, upstream = make(at(22, 0))
    nxt = client.get("/v1/prayer/times", params=CAIRO).json()["next_prayer"]
    assert nxt["key"] == "fajr"
    assert nxt["time"] == "2026-10-07T05:25:00+03:00"
    assert nxt["seconds_remaining"] == 7 * 3600 + 25 * 60
    assert [r.url.path.rsplit("/", 1)[-1] for r in upstream.requests] == [
        "06-10-2026",
        "07-10-2026",
    ]


def test_local_day_is_used_when_it_differs_from_utc(make: Factory) -> None:
    # 23:30 UTC on the 5th is 02:30 on the 6th in Cairo.
    client, _ = make(datetime(2026, 10, 5, 23, 30, tzinfo=UTC))
    body = client.get("/v1/prayer/times", params=CAIRO).json()
    assert body["date"] == "2026-10-06"
    assert body["next_prayer"]["key"] == "fajr"


def test_explicit_other_date_has_no_next_prayer(make: Factory) -> None:
    client, upstream = make(at(13))
    body = client.get(
        "/v1/prayer/times", params={**CAIRO, "date": "2026-10-09", "method": 4}
    ).json()
    assert body["date"] == "2026-10-09"
    assert body["is_friday"] is True
    assert body["next_prayer"] is None
    assert upstream.requests[0].url.params["method"] == "4"


def test_timings_are_cached_per_day_location_method(make: Factory) -> None:
    client, upstream = make()
    client.get("/v1/prayer/times", params=CAIRO)
    client.get("/v1/prayer/times", params={"lat": 30.0401, "lon": 31.2399})  # same rounded cell
    client.get("/v1/prayer/focus-schedule", params=CAIRO)
    assert len(upstream.requests) == 1
    client.get("/v1/prayer/times", params={**CAIRO, "method": 4})
    assert len(upstream.requests) == 2


# ------------------------------------------------------------------- qibla ----


def test_qibla(make: Factory) -> None:
    client, upstream = make()
    body = client.get("/v1/prayer/qibla", params=CAIRO).json()
    assert body["direction_deg"] == 136.14
    assert body["compass_ar"] == "جنوب شرق"
    assert (body["kaaba_latitude"], body["kaaba_longitude"]) == (21.4225, 39.8262)
    client.get("/v1/prayer/qibla", params=CAIRO)
    assert len(upstream.requests) == 1


# ---------------------------------------------------------- focus schedule ----


def test_focus_schedule_defaults(make: Factory) -> None:
    client, _ = make(at(14))
    body = client.get("/v1/prayer/focus-schedule", params=CAIRO).json()

    assert [w["prayer"] for w in body["windows"]] == ["fajr", "dhuhr", "asr", "maghrib", "isha"]
    assert (body["before_min"], body["during_min"], body["jumuah_during_min"]) == (5, 20, 60)
    asr = body["windows"][2]
    assert asr == {
        "prayer": "asr",
        "name_ar": "العصر",
        "is_jumuah": False,
        "adhan": "2026-10-06T16:05:00+03:00",
        "start": "2026-10-06T16:00:00+03:00",
        "end": "2026-10-06T16:25:00+03:00",
        "duration_min": 25,
        "reminder_ar": "حان وقت صلاة العصر. اترك هاتفك الآن، وتُفتح التطبيقات بعد 25 دقيقة.",
    }
    assert body["blocked_categories"] == ["social", "games", "video"]
    assert body["active_window"] is None


def test_focus_active_window_boundaries(make: Factory) -> None:
    inside, _ = make(at(16, 10))
    assert (
        inside.get("/v1/prayer/focus-schedule", params=CAIRO).json()["active_window"]["prayer"]
        == "asr"
    )
    at_start, _ = make(at(16, 0))
    assert (
        at_start.get("/v1/prayer/focus-schedule", params=CAIRO).json()["active_window"]["prayer"]
        == "asr"
    )
    at_end, _ = make(at(16, 25))
    assert at_end.get("/v1/prayer/focus-schedule", params=CAIRO).json()["active_window"] is None


def test_friday_dhuhr_is_a_longer_jumuah_window(make: Factory) -> None:
    client, _ = make(at(9, day=FRIDAY))
    body = client.get("/v1/prayer/focus-schedule", params=CAIRO).json()
    dhuhr = body["windows"][1]
    assert body["is_friday"] is True
    assert (dhuhr["name_ar"], dhuhr["is_jumuah"], dhuhr["duration_min"]) == ("الجمعة", True, 65)
    assert dhuhr["end"] == "2026-10-09T13:43:00+03:00"
    assert "صلاة الجمعة" in dhuhr["reminder_ar"]
    assert all(
        not w["is_jumuah"] and w["duration_min"] == 25
        for w in body["windows"]
        if w["prayer"] != "dhuhr"
    )
    # The next-prayer label follows too.
    assert client.get("/v1/prayer/times", params=CAIRO).json()["next_prayer"]["name_ar"] == "الجمعة"


def test_focus_overrides_and_sunrise(make: Factory) -> None:
    client, _ = make()
    params = {
        **CAIRO,
        "before_min": 10,
        "during_min": 15,
        "fajr_during_min": 40,
        "isha_during_min": 0,
        "include_sunrise": "true",
    }
    windows = {
        w["prayer"]: w
        for w in client.get("/v1/prayer/focus-schedule", params=params).json()["windows"]
    }
    assert list(windows) == ["fajr", "sunrise", "dhuhr", "asr", "maghrib", "isha"]
    assert windows["fajr"]["start"] == "2026-10-06T05:15:00+03:00"
    assert windows["fajr"]["end"] == "2026-10-06T06:05:00+03:00"
    assert windows["fajr"]["duration_min"] == 50
    assert windows["asr"]["duration_min"] == 25
    assert windows["isha"]["end"] == windows["isha"]["adhan"]


# ------------------------------------------------------------------ errors ----


def assert_error(response: httpx.Response, status: int, code: str) -> None:
    assert response.status_code == status
    error = response.json()["error"]
    assert error["code"] == code
    assert error["message_ar"] and error["request_id"] == response.headers["X-Request-ID"]


def test_upstream_errors_use_the_envelope(make: Factory) -> None:
    client, upstream = make()
    upstream.fail_with = 503
    assert_error(client.get("/v1/prayer/times", params=CAIRO), 502, "upstream_error")
    assert_error(client.get("/v1/prayer/qibla", params=CAIRO), 502, "upstream_error")
    upstream.fail_with, upstream.timeout = None, True
    assert_error(client.get("/v1/prayer/focus-schedule", params=CAIRO), 504, "upstream_timeout")
    # Failures are not cached: the next call succeeds.
    upstream.timeout = False
    assert client.get("/v1/prayer/times", params=CAIRO).status_code == 200


@pytest.mark.parametrize(
    "params",
    [
        {"lat": 91, "lon": 31},
        {"lat": 30, "lon": 181},
        {"lat": 30},
        {**CAIRO, "date": "06-10-2026"},
        {**CAIRO, "method": -1},
    ],
)
def test_validation(make: Factory, params: dict[str, object]) -> None:
    client, upstream = make()
    assert_error(client.get("/v1/prayer/times", params=params), 422, "validation_error")
    assert upstream.requests == []


def test_focus_validation(make: Factory) -> None:
    client, _ = make()
    assert_error(
        client.get("/v1/prayer/focus-schedule", params={**CAIRO, "during_min": 0}),
        422,
        "validation_error",
    )
    assert_error(
        client.get("/v1/prayer/focus-schedule", params={**CAIRO, "before_min": 61}),
        422,
        "validation_error",
    )


# ------------------------------------------------------------------- daily ----


def offline_client(make: Factory, now: datetime = at(13)) -> tuple[TestClient, Upstream]:
    http = httpx.AsyncClient(transport=httpx.MockTransport(lambda r: httpx.Response(500)))
    client, upstream = make(now, sources=[OfflineSixBooksSource(http, data_dir=OFFLINE_SAMPLE)])
    deadline = time.monotonic() + 10
    while client.get("/health").json()["sources"]["offline"] != "ready":
        assert time.monotonic() < deadline, "offline sample did not load"
        time.sleep(0.02)
    return client, upstream


def test_daily_card_is_a_sahih_hadith_with_source(make: Factory) -> None:
    client, _ = offline_client(make)
    body = client.get("/v1/daily", params={"date": "2026-10-06"}).json()

    hadith = body["hadith"]
    assert body["date"] == "2026-10-06"
    assert hadith["source_book"] in ("صحيح البخاري", "صحيح مسلم")
    assert hadith["number"].isdigit()
    assert hadith["verdict_code"] == "sahih"
    assert hadith["grade_text"] == "أخرجه في صحيحه"
    assert len(hadith["hadith_text"]) > 50
    assert body["next_prayer"] is None
    assert body["disclaimer_ar"]


def test_daily_is_deterministic_by_date(make: Factory) -> None:
    client, _ = offline_client(make)
    first = client.get("/v1/daily", params={"date": "2026-10-06"}).json()["hadith"]
    again = client.get("/v1/daily", params={"date": "2026-10-06"}).json()["hadith"]
    assert first == again
    assert phrase_for(date(2026, 10, 6)) != phrase_for(date(2026, 10, 7))
    assert phrase_for(date(2026, 10, 6)) == phrase_for(
        date(2026, 10, 6) + timedelta(days=len(DAILY_PHRASES))
    )


def test_daily_includes_next_prayer_when_location_is_given(make: Factory) -> None:
    client, upstream = offline_client(make, at(13))
    body = client.get("/v1/daily", params=CAIRO).json()
    assert body["date"] == "2026-10-06"
    assert body["next_prayer"]["key"] == "asr"

    upstream.fail_with = 503  # Aladhan down: still a card, without the prayer
    other = client.get("/v1/daily", params={"lat": 21.42, "lon": 39.83}).json()
    assert other["hadith"]["verdict_code"] == "sahih"
    assert other["next_prayer"] is None


def test_daily_needs_the_offline_source(make: Factory) -> None:
    client, _ = make()  # only a fake Dorar source
    assert_error(client.get("/v1/daily"), 503, "service_not_ready")


def test_prayer_routes_are_in_openapi(make: Factory) -> None:
    client, _ = make()
    paths = client.get("/openapi.json").json()["paths"]
    assert {
        "/v1/prayer/times",
        "/v1/prayer/qibla",
        "/v1/prayer/focus-schedule",
        "/v1/daily",
    } <= set(paths)


# -------------------------------------------------------------------- live ----


@pytest.mark.live
async def test_live_aladhan() -> None:
    async with httpx.AsyncClient() as http:
        client = AladhanClient(http)
        timings = await client.timings(datetime.now(UTC).date(), 30.04, 31.24, 5)
        assert timings.timezone == "Africa/Cairo"
        assert timings.times["fajr"] < timings.times["isha"]
        assert 130 < await client.qibla(30.04, 31.24) < 140
