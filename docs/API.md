# Tashih API v1 — guide for the backend and Flutter teams

Tashih (تصحيح) verifies hadith texts. You send a text; you get back **the ruling, who graded
it, and the source**. This document is the contract. The machine-readable version is
[`openapi.json`](openapi.json) (regenerate with `make openapi`); a running server also serves
Swagger UI at `/docs`.

- Base URL (local): `http://localhost:8000`
- All bodies are JSON, UTF-8. All versioned routes live under `/v1`.
- Every response carries an `X-Request-ID` header (send your own and it is echoed back).

## The one rule the product is built on

**The ruling never comes from the LLM.** `verdict_code` is computed by deterministic code
([`app/services/verdict.py`](../app/services/verdict.py)) from the grade text returned by the
data source. If a text is not found with confidence the API answers `not_found` and declines
to rule. The LLM is optional and is only allowed to (1) extract the hadith text from a messy
chat message and (2) reword the already-retrieved result; both outputs are checked in code
and dropped if they fail.

Client implication: always show `disclaimer_ar`, and show `best_match.muhaddith` next to the
verdict. A verdict is one scholar's grading, not a consensus.

## Endpoints

| Method | Path | Purpose |
| --- | --- | --- |
| GET | `/health` | Liveness and source status (no auth, no rate limit) |
| POST | `/v1/verify` | Verify one hadith text |
| POST | `/v1/chat` | Chat turn: intent detection + verify + ready-to-show reply |
| GET | `/v1/prayer/times` | Prayer times, Hijri date, next prayer |
| GET | `/v1/prayer/qibla` | Qibla direction |
| GET | `/v1/prayer/focus-schedule` | Focus windows around each prayer (for app blocking) |
| GET | `/v1/daily` | Daily card: hadith of the day + next prayer |

### GET /health

```json
{
  "status": "ok",
  "version": "0.1.0",
  "sources": { "dorar": "ok", "offline": "ready" },
  "llm_enabled": false
}
```

- `sources.dorar`: `ok` | `blocked` (HTTP 403 from Cloudflare) | `unavailable` (timeout or bad
  payload) | `unknown` (not called yet) | `disabled`.
- `sources.offline`: `ready` | `loading` (first start downloads ~40 MB) | `error` | `disabled`.
- `status` is `degraded` when no source can answer.

### POST /v1/verify

Request:

| Field | Type | Notes |
| --- | --- | --- |
| `text` | string, 1..2000 chars | The hadith text. Tashkeel, greetings and wrappers such as "قال رسول الله ﷺ" are stripped before searching. |
| `max_results` | int 1..10, default 3 | `best_match` plus up to `max_results - 1` `other_matches`. |
| `explain` | bool, default true | Ask for `explanation_ar`. Ignored when the server has no OpenAI key. |

```bash
curl -s http://localhost:8000/v1/verify \
  -H 'Content-Type: application/json' \
  -d '{"text": "اطلبوا العلم ولو في الصين"}'
```

Response (real output, shortened):

```json
{
  "request_id": "3f0c5a0e-6f2b-4c1e-9c3e-2d0a7a3b9a11",
  "status": "found",
  "verdict_code": "daif",
  "verdict_ar": "ضعيف",
  "query": "اطلبوا العلم ولو في الصين",
  "best_match": {
    "hadith_text": "اطلبوا العلمَ ولو في الصِّينِ",
    "narrator": null,
    "muhaddith": "ابن باز",
    "source_book": "التحفة الكريمة",
    "number_or_page": "72",
    "grade_text": "ضعيف من جميع طرقه [عند جمهور أهل العلم بالحديث]",
    "grades": [
      { "scholar": "ابن باز", "grade": "ضعيف من جميع طرقه [عند جمهور أهل العلم بالحديث]", "verdict_code": "daif" },
      { "scholar": "القاوقجي", "grade": "ضعيف", "verdict_code": "daif" }
    ],
    "match_score": 1.0
  },
  "other_matches": [],
  "explanation_ar": null,
  "message_ar": "حكم عليه ابن باز بقوله: «ضعيف من جميع طرقه [عند جمهور أهل العلم بالحديث]» (التحفة الكريمة — 72). لا ينبغي نسبته إلى النبي ﷺ.",
  "disclaimer_ar": "الحكم منقول من المصدر وليس من النموذج. للفتوى راجع أهل العلم.",
  "refer_to_scholars": false,
  "scholars_differ": false,
  "source_used": "dorar"
}
```

Field notes:

| Field | Nullable | Notes |
| --- | --- | --- |
| `status` | no | See the enum below. Always check this first. |
| `verdict_code`, `verdict_ar` | yes | Non-null only when `status = found`. |
| `query` | no | The text that was actually searched (after cleaning). |
| `best_match` | yes | Null unless `status = found`. |
| `best_match.narrator` | yes | The offline dataset has no narrator field. Dorar may give it in brackets, e.g. `[عمر بن الخطاب]`, exactly as the source prints it. |
| `best_match.grades` | no | Every grading available for this text, each with its own `verdict_code`. |
| `best_match.match_score` | no | `1.0` = letter-for-letter (after normalization). `0.77`–`0.97` = matched despite typos or small wording differences; see "Matching and typo tolerance". |
| `other_matches` | no | May be empty. Same shape as `best_match`. |
| `explanation_ar` | yes | Null when there is no LLM, the LLM failed, or its answer was rejected by the validator. Never required to render a result. |
| `message_ar` | no | Deterministic summary, or the reason for a non-found status. |
| `scholars_differ` | no | True when the listed gradings include both an accepting and a rejecting verdict. Show a hint and the `grades` list. |
| `refer_to_scholars` | no | True for `not_found`, `unavailable`, an `unclear` verdict, or differing scholars. |
| `source_used` | yes | `dorar` or `offline_six_books`. Null when nothing was searched or every source failed. |

### POST /v1/chat

This is the endpoint the apps should call.

```bash
curl -s http://localhost:8000/v1/chat \
  -H 'Content-Type: application/json' \
  -d '{"messages":[{"role":"user","content":"السلام عليكم، ما صحة حديث اطلبوا العلم ولو في الصين؟"}],"session_id":"abc"}'
```

- `messages`: 1..50 items of `{role: "user" | "assistant", content: string ≤ 4000}`. Only the
  **last user message** is answered; earlier turns are accepted so clients can send the
  transcript unchanged, but they do not influence the ruling.
- `session_id`: optional opaque string, echoed back. The server keeps no session state.

The response has every `/v1/verify` field plus:

| Field | Notes |
| --- | --- |
| `reply_ar` | Chat-ready plain text with `\n` line breaks, built deterministically from the other fields. Show it as is, or build your own card from the structured fields (recommended). |
| `session_id` | Echo of the request value. |

How a message is routed:

1. Heuristics (no LLM): greetings, fiqh questions ("ما حكم…", "هل يجوز…"), Quran/tafsir
   requests and non-Arabic text → `out_of_scope`. A hadith cue with no text ("عايز اتحقق من
   حديث") → `out_of_scope` with a message asking for the text.
2. Otherwise the cleaned text is verified.
3. Only if that search finds nothing and an LLM is configured, the LLM is asked to extract the
   hadith text and fix its spelling. Its answer is used only if it is still the user's own
   text (see "Matching and typo tolerance").

### Enums

Values are lowercase strings. **Clients must tolerate unknown values** (treat an unknown
`status` as an error state and an unknown `verdict_code` as `unclear`).

`status`

| Value | Meaning | What to show |
| --- | --- | --- |
| `found` | A confident match exists. | Verdict badge, hadith, grader, source. |
| `not_found` | No confident match, or the text is too short/generic. | `message_ar`. No verdict. Not finding a text says nothing about its authenticity. |
| `out_of_scope` | Not a hadith-verification request (`/v1/chat` only). | `message_ar`. |
| `unavailable` | Every source failed. | `message_ar` and a retry button. |

`verdict_code`

| Value | `verdict_ar` | Suggested colour |
| --- | --- | --- |
| `sahih` | صحيح / حسن صحيح | green |
| `hasan` | حسن | green |
| `daif` | ضعيف / ضعيف جداً | red |
| `mawdu` | موضوع (مكذوب) | red |
| `unclear` | يحتاج مراجعة / مختلف فيه | grey |

`unclear` with `verdict_ar = "مختلف فيه"` means scholars are split (see below). Otherwise `unclear` means the text was found but the source's wording is not a recognised ruling (for
example "غريب من هذا الوجه", "رجاله ثقات", or wording that mixes authentic and weak terms).
The classifier is conservative on purpose.

### How the primary verdict is chosen

1. Sources are tried in `SOURCES` order (default `dorar,offline`). The first one with a
   confident match answers; a failing source is skipped.
2. Only matches tied at the best score are considered.
3. **Dorar** returns one scholar's grading per result, often for different chains (طرق) of
   the same text. All of them go into `best_match.grades`. A Dorar answer is then
   cross-checked against the six-books dataset (local, no network):
   - **The text is in Sahih al-Bukhari or Sahih Muslim** → the verdict is `sahih`,
     `best_match` is the Bukhari/Muslim entry (`grade_text = "أخرجه في صحيحه"`,
     `source_used = "offline_six_books"`), and Dorar's gradings stay in `grades`. If some of
     them are negative (criticism of side chains), `scholars_differ` is true and `message_ar`
     says so.
   - **The text is in one of the other four books** → that book's gradings (Al-Albani and
     others) are added to `grades` before the verdict is decided, and the entry is appended
     to `other_matches`.
   - Only an exact match (score ≥ 0.95) can do either; a typo-tolerant match cannot.
4. **Scholars split** (at least one accepting grading and one rejecting grading, and the text
   is not in al-Bukhari/Muslim) → no side is picked. `verdict_code = "unclear"`,
   `verdict_ar = "مختلف فيه"`, `scholars_differ = true`, and `message_ar` names who accepted
   and who rejected. The LLM is not asked to explain a disputed or unclear result.
5. **Otherwise** the verdict is the one given by the most scholars (each scholar counted once
   per verdict, unrecognised gradings ignored, ties broken by Dorar's order); `best_match` is
   the first result carrying it.
6. **Offline six books answering on its own** (Dorar down): the primary grade is Al-Albani's
   when present, otherwise the first grade listed. Al-Bukhari and Muslim carry no grades in
   the dataset and are reported as `sahih` with `grade_text = "أخرجه في صحيحه"`.

`DORAR_PRIMARY_RULE=first` switches steps 4–5 to "first result with a definite verdict".

Why not simply take Dorar's first result? Real examples from 2026-10-06:

| Text | Dorar's first result | What the API reports | Why |
| --- | --- | --- | --- |
| إنما الأعمال بالنيات | Ibn Abd al-Barr: "خطأ…" (one chain) | صحيح — صحيح البخاري #1 | It is in al-Bukhari |
| طلب العلم فريضة على كل مسلم | Imam Ahmad: «كذب» (one chain) | ضعيف | 7 of 8 scholars in the results grade it weak |
| لا ضرر ولا ضرار | Ibn al-Mundhir: weak chain | مختلف فيه | an-Nawawi and Al-Albani accept it, others reject |
| من حسن إسلام المرء تركه ما لا يعنيه | Ibn Adi: weak chain | مختلف فيه | Dorar's 15 results are all criticism; at-Tirmidhi's graders accept it |

Known limit: Dorar's API returns at most 15 results, so a scholar's grading that is not among
them, and not in the six-books dataset either, is not seen.

### Matching and typo tolerance

Users misspell. The service tolerates typos without guessing, in three layers.

**1. Wrapper stripping (no AI).** Greetings, questions and colloquial phrasing around the
hadith are removed before searching: "ما صحة حديث …", "قال رسول الله ﷺ …", "… هو دا حديث
صحيح", "… صح ولا غلط", "سمعت حديث بيقول …". `query` in the response is what was searched.

**2. Deterministic matching (no AI).** Text is normalized (tashkeel and tatweel removed,
أإآٱ→ا, ى→ي, ة→ه, non-Arabic dropped), then every candidate from the source is scored:

| `match_score` | Rule |
| --- | --- |
| `1.0` | The query is an exact substring of the hadith |
| `0.97` | Same, ignoring spaces ("انماا لاعمال" = "انما الاعمال") |
| `ratio × 0.95` | ≥ 85% of the query's words appear in the hadith (queries of ≥ 3 words) |
| `ratio × 0.90` | Typo-tolerant: a word also counts when it is one edit away from a hadith word (one letter missing, extra, wrong, or two swapped) |

Guards on the typo-tolerant tier, so a different hadith is never "corrected" into a match:
at least half of the words must be exactly right; only words of 4+ letters may differ; the
matched words must sit close together in the hadith; and ≥ 85% of the words must match.
Queries with fewer than 2 distinctive words are not searched at all ("قال رسول الله" does
not count).

**3. LLM spelling correction (`/v1/chat` only, only after layers 1–2 find nothing).** The
model may fix spelling but may not complete, extend or swap the hadith: its text is accepted
only if at least 70% of it is made of letter runs that appear, in order, in the user's own
message. The corrected text is then searched and scored by layer 2 like any other query.

Transparency: whenever `match_score < 1.0`, `message_ar` ends with a note that the typed
text is not letter-for-letter the source text; when the LLM corrected the spelling,
`message_ar` shows the corrected text. Clients should always display
`best_match.hadith_text` so the user can confirm it is the hadith they meant.

Dorar's endpoint is a keyword search and returns 15 results even for an invented sentence, so
every Dorar result is re-scored with these rules before it is trusted. Dorar's own search is
typo-tolerant, which is why a misspelled query usually still brings back the right
candidates.

## Errors

Every non-2xx response uses one envelope:

```json
{
  "error": {
    "code": "validation_error",
    "message_ar": "البيانات المرسلة غير صحيحة.",
    "message_en": "text: String should have at least 1 character",
    "request_id": "1e053e09-83e1-4a60-8a8e-42ed0bc53e08"
  }
}
```

| HTTP | `code` | When |
| --- | --- | --- |
| 401 | `unauthorized` | `API_KEYS` is set and `X-API-Key` is missing or wrong |
| 404 | `not_found` | Unknown route |
| 422 | `validation_error` | Body or query parameters failed validation |
| 429 | `rate_limited` | Per-IP limit exceeded; see the `Retry-After` header (seconds) |
| 502 | `upstream_error` | Aladhan returned an error (prayer routes) |
| 503 | `service_not_ready` | `/v1/daily` while the offline dataset is still loading |
| 504 | `upstream_timeout` | Aladhan timed out (prayer routes) |
| 500 | `internal_error` | Unexpected failure |

`message_ar` is safe to show to users; `message_en` is for developers.

A hadith source being down is **not** an HTTP error: `/v1/verify` and `/v1/chat` answer
HTTP 200 with `status = "unavailable"`, so the chat can always render a reply.

## Authentication, rate limiting, CORS

- **API key**: disabled by default. When the server sets `API_KEYS=k1,k2`, every `/v1` route
  requires `X-API-Key`. `/health` stays open.
- **Rate limit**: `RATE_LIMIT_PER_MINUTE` (default 60) per client IP, fixed one-minute
  window, in memory. Behind a reverse proxy, run uvicorn with `--proxy-headers` so the real
  client IP is used.
- **CORS**: `CORS_ORIGINS` is a comma-separated list, or `*`.

## Dorar: the 403 problem and the fallback

Checked from this project's development machine on 2026-10-06:

- `GET https://dorar.net/dorar_api.json?skey=<text>` returns
  `{"ahadith": {"result": "<html>"}}`. The results are HTML, not structured JSON; the parser
  ([`app/sources/dorar.py`](../app/sources/dorar.py)) is written against saved real responses
  in `tests/fixtures/dorar_*.json`. The shape shown in Dorar's documentation (a list of
  `{th: html}` items) is accepted as well.
- dorar.net is behind Cloudflare. Blocking is **not only by IP**: from the same machine and
  IP, a request made with httpx's default TLS settings got HTTP 403 ("Attention Required"),
  while Python's standard library got HTTP 200. The difference is one TLS option
  (`post_handshake_auth`). `DorarSource` therefore uses the standard library's TLS context.
  Certificate verification is unchanged.
- Datacenter IPs were reported blocked earlier (n8n Cloud, a cloud sandbox). That has not been
  re-tested with the current client, so treat a cloud deployment as unverified.
- Non-UTF-8 query encoding makes Dorar answer HTTP 500. Always percent-encode as UTF-8.

What the service does when Dorar fails (403, timeout, 5xx, unparseable body): retry once after
`DORAR_RETRY_BACKOFF_SECONDS`, then fall back to the next source. `/health` reports
`dorar: blocked` or `unavailable`. Answers produced while a preferred source was failing are
not cached, so Dorar is tried again on the next request.

The fallback (`offline_six_books`) only covers al-Bukhari, Muslim, Abu Dawud, at-Tirmidhi,
an-Nasa'i and Ibn Majah (34,153 hadith). A famous weak hadith that is in none of them, such as
"اطلبوا العلم ولو في الصين", is `not_found` there, and `message_ar` says the search was limited
to the six books.

Dorar's API is published for site owners. For production traffic, contact Dorar
(support@dorar.net) rather than relying on the current behaviour.

## Prayer and daily-companion endpoints

Data comes from the [Aladhan API](https://aladhan.com/prayer-times-api). All datetimes are
ISO-8601 with the location's UTC offset (for example `2026-10-06T16:05:00+03:00`), so no
timezone arithmetic is needed on the client.

Common query parameters:

| Param | Notes |
| --- | --- |
| `lat`, `lon` | Required. Decimal degrees. |
| `date` | Optional `YYYY-MM-DD`. Default: today at the location. |
| `method` | Optional Aladhan method id. Default `PRAYER_DEFAULT_METHOD` = 5 (Egyptian General Authority of Survey). Others: 4 Umm Al-Qura, 3 Muslim World League, 2 ISNA, 1 Karachi. |

Timings are cached for 24 hours per (date, coordinates rounded to 2 decimals ≈ 1 km, method).

### GET /v1/prayer/times

```bash
curl -s 'http://localhost:8000/v1/prayer/times?lat=30.0444&lon=31.2357'
```

```json
{
  "request_id": "…",
  "date": "2026-10-06",
  "timezone": "Africa/Cairo",
  "latitude": 30.0444,
  "longitude": 31.2357,
  "method": { "id": 5, "name": "Egyptian General Authority of Survey" },
  "hijri": { "date": "25-04-1448", "day": 25, "month_number": 4, "month_ar": "رَبيع الثاني", "month_en": "Rabīʿ al-thānī", "year": 1448, "weekday_ar": "الثلاثاء" },
  "is_friday": false,
  "prayers": [
    { "key": "fajr", "name_ar": "الفجر", "time": "2026-10-06T05:25:00+03:00", "is_prayer": true },
    { "key": "sunrise", "name_ar": "الشروق", "time": "2026-10-06T06:51:00+03:00", "is_prayer": false },
    { "key": "dhuhr", "name_ar": "الظهر", "time": "2026-10-06T12:43:00+03:00", "is_prayer": true },
    { "key": "asr", "name_ar": "العصر", "time": "2026-10-06T16:05:00+03:00", "is_prayer": true },
    { "key": "maghrib", "name_ar": "المغرب", "time": "2026-10-06T18:35:00+03:00", "is_prayer": true },
    { "key": "isha", "name_ar": "العشاء", "time": "2026-10-06T19:52:00+03:00", "is_prayer": true }
  ],
  "next_prayer": { "key": "asr", "name_ar": "العصر", "time": "2026-10-06T16:05:00+03:00", "seconds_remaining": 11100 },
  "source": "aladhan"
}
```

- `next_prayer` is the next of the five prayers (never sunrise). After Isha it is tomorrow's
  Fajr. It is `null` when `date` is not today at the location.
- `seconds_remaining` is a snapshot at response time. Count down on the device from
  `next_prayer.time`; do not poll.
- On Fridays the `next_prayer.name_ar` for dhuhr is "الجمعة" (the `key` stays `dhuhr`).

### GET /v1/prayer/qibla

`{ "direction_deg": 136.14, "compass_ar": "جنوب شرق", "kaaba_latitude": 21.4225, "kaaba_longitude": 39.8262, … }`

`direction_deg` is clockwise from **true** north. A phone compass gives magnetic north; apply
the device's declination (the platform location APIs provide it) before drawing the arrow.

### GET /v1/prayer/focus-schedule

The contract for the "lock distracting apps during prayer" feature.

| Param | Default | Notes |
| --- | --- | --- |
| `before_min` | 5 | Minutes before the adhan the window starts (0..60) |
| `during_min` | 20 | Minutes after the adhan the window ends (1..180) |
| `jumuah_during_min` | 60 | Used for dhuhr on Fridays |
| `fajr_during_min`, `dhuhr_during_min`, `asr_during_min`, `maghrib_during_min`, `isha_during_min` | — | Per-prayer override of `during_min` (0..180). A dhuhr override also applies on Fridays. |
| `include_sunrise` | false | Sunrise is not a prayer; it gets a window only if this is true |

```json
{
  "date": "2026-10-06",
  "timezone": "Africa/Cairo",
  "is_friday": false,
  "before_min": 5,
  "during_min": 20,
  "jumuah_during_min": 60,
  "windows": [
    {
      "prayer": "asr",
      "name_ar": "العصر",
      "is_jumuah": false,
      "adhan": "2026-10-06T16:05:00+03:00",
      "start": "2026-10-06T16:00:00+03:00",
      "end": "2026-10-06T16:25:00+03:00",
      "duration_min": 25,
      "reminder_ar": "حان وقت صلاة العصر. اترك هاتفك الآن، وتُفتح التطبيقات بعد 25 دقيقة."
    }
  ],
  "active_window": null,
  "blocked_categories": ["social", "games", "video"],
  "source": "aladhan"
}
```

- A window is the half-open interval `[start, end)`.
- `active_window` is the window containing the server's current time, or `null`.
- `blocked_categories` is a recommended default as plain strings. The client maps them to
  installed apps and lets the user edit the list.

**What the backend does not do.** It only computes the schedule. Blocking happens on the
device and must keep working offline: fetch the schedule once a day (and for tomorrow), store
it, and schedule local alarms/notifications from it.

Platform notes for the mobile team:

- **Android**: feasible. Typical building blocks are Usage Access (`UsageStatsManager`) to
  detect the foreground app, a draw-over-other-apps overlay (`SYSTEM_ALERT_WINDOW`) or an
  Accessibility Service to cover it, and exact alarms for window start/end. Each needs an
  explicit user grant, and Play policy restricts Accessibility use, so plan the permission
  flow and the store declaration early.
- **iOS**: heavily restricted. App blocking requires the Screen Time APIs (FamilyControls,
  ManagedSettings, DeviceActivity) and the Family Controls entitlement, which Apple grants on
  request. Without it, the app can only send a notification at window start.
- Because of that difference the API carries no platform assumptions. An iOS build can use
  the same windows for reminders only.

### GET /v1/daily

`lat`, `lon`, `date`, `method` are all optional.

```json
{
  "request_id": "…",
  "date": "2026-10-06",
  "hadith": {
    "hadith_text": "حَدَّثَنَا ابْنُ سَلاَمٍ … مَنْ صَامَ رَمَضَانَ إِيمَانًا وَاحْتِسَابًا غُفِرَ لَهُ مَا تَقَدَّمَ مِنْ ذَنْبِهِ",
    "source_book": "صحيح البخاري",
    "number": "38",
    "verdict_code": "sahih",
    "verdict_ar": "صحيح",
    "grade_text": "أخرجه في صحيحه"
  },
  "next_prayer": { "key": "asr", "name_ar": "العصر", "time": "2026-10-06T16:05:00+03:00", "seconds_remaining": 11100 },
  "disclaimer_ar": "الحكم منقول من المصدر وليس من النموذج. للفتوى راجع أهل العلم."
}
```

- Nothing is generated. The date selects one entry of a curated list of well-known hadith
  ([`app/services/daily.py`](../app/services/daily.py)); the text, book and number are read
  from the offline dataset, restricted to al-Bukhari and Muslim. `make check-daily` verifies
  every entry against the real dataset.
- `hadith_text` includes the chain of narration (isnad) as printed in the book.
- `next_prayer` is `null` when `lat`/`lon` are omitted or Aladhan is down. The hadith is still
  returned.
- Needs the offline source (`SOURCES` must include `offline`); returns 503
  `service_not_ready` while the dataset is loading.

## Versioning policy

- The contract is `/v1`. Within v1, changes are **additive only**: new optional request
  fields, new response fields, new enum values, new endpoints. Clients must ignore unknown
  fields and tolerate unknown enum values.
- Field names, types and nullability documented here do not change within v1. No field is
  polymorphic.
- A breaking change ships as `/v2` alongside `/v1`.
- `docs/openapi.json` is committed. Regenerate it (`make openapi`) in the same change that
  touches the models, and generate the Dart client from it.

## Extending the backend

- **New hadith source**: implement the `HadithSource` protocol in
  [`app/sources/base.py`](../app/sources/base.py) (`search`, `health`, `start`, `close`),
  return only confident matches scored with `app/services/matcher.py`, and register the name
  in `build_sources` in [`app/main.py`](../app/main.py).
- **New domain** (Quran, duas, …): add a router under `app/routers/` and a service under
  `app/services/`, like the prayer module. For chat, add an `Intent` in
  [`app/services/intent.py`](../app/services/intent.py) and dispatch on it in
  `ChatService._answer`. `/v1/verify` and `/v1/chat` response shapes stay as they are.
- **State is in process memory** (hadith index, caches, rate limiter), so the API runs as one
  worker. Scaling out needs a shared cache and rate limiter first.
