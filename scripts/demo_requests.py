"""Hit a running Tashih API with the sample inputs and print a summary.

Usage: python -m scripts.demo_requests [base_url]     (default http://localhost:8000)
"""

from __future__ import annotations

import sys
import time
from typing import Any

import httpx

SAMPLES = [
    "إنما الأعمال بالنيات",
    "طلب العلم فريضة على كل مسلم",
    "اطلبوا العلم ولو في الصين",
    "قال الفيل للنملة سافرت بالطائرة الى المريخ",
]
CHAT_EXTRA = [
    "السلام عليكم، ما صحة حديث اطلبوا العلم ولو في الصين؟",
    "ما حكم صلاة الجماعة؟",
    "الصلاة",
]
CAIRO = {"lat": 30.0444, "lon": 31.2357}


def summary(body: dict[str, Any]) -> str:
    best = body.get("best_match") or {}
    return (
        f"status={body.get('status')} verdict={body.get('verdict_code')} "
        f"({body.get('verdict_ar')}) source={body.get('source_used')} "
        f"| {best.get('muhaddith')} | {best.get('source_book')} | {best.get('number_or_page')} "
        f"| explanation={'yes' if body.get('explanation_ar') else 'null'}"
    )


def main() -> None:
    sys.stdout.reconfigure(encoding="utf-8")  # type: ignore[union-attr]
    base = (sys.argv[1] if len(sys.argv) > 1 else "http://localhost:8000").rstrip("/")
    with httpx.Client(base_url=base, timeout=60) as client:
        for _ in range(90):
            health = client.get("/health").json()
            if health["sources"]["offline"] in ("ready", "error", "disabled"):
                break
            time.sleep(2)
        print("GET /health ->", health)

        for text in SAMPLES:
            started = time.perf_counter()
            body = client.post("/v1/verify", json={"text": text}).json()
            print(f"\nPOST /v1/verify  «{text}»  [{(time.perf_counter() - started) * 1000:.0f} ms]")
            print("  ", summary(body))
            print("   message_ar:", body.get("message_ar"))

        for text in SAMPLES + CHAT_EXTRA:
            response = client.post(
                "/v1/chat", json={"messages": [{"role": "user", "content": text}]}
            )
            body = response.json()
            print(f"\nPOST /v1/chat  «{text}»  [HTTP {response.status_code}]")
            print("  ", summary(body))
            print("   reply_ar:", str(body.get("reply_ar")).replace("\n", "\n             "))

        times = client.get("/v1/prayer/times", params=CAIRO).json()
        print("\nGET /v1/prayer/times ->", times.get("date"), times.get("timezone"))
        print("  ", [(p["name_ar"], p["time"][11:16]) for p in times.get("prayers", [])])
        print("   next:", times.get("next_prayer"))
        qibla = client.get("/v1/prayer/qibla", params=CAIRO).json()
        print("GET /v1/prayer/qibla ->", qibla.get("direction_deg"), qibla.get("compass_ar"))
        focus = client.get("/v1/prayer/focus-schedule", params=CAIRO).json()
        print(
            "GET /v1/prayer/focus-schedule ->",
            [(w["name_ar"], w["start"][11:16], w["end"][11:16]) for w in focus.get("windows", [])],
        )
        daily = client.get("/v1/daily", params=CAIRO).json()
        hadith = daily.get("hadith", {})
        print(
            "GET /v1/daily ->",
            daily.get("date"),
            hadith.get("source_book"),
            hadith.get("number"),
            hadith.get("verdict_code"),
        )
        print("   ", str(hadith.get("hadith_text"))[:160])

        bad = client.post("/v1/verify", json={"text": ""})
        print("\nPOST /v1/verify (empty text) ->", bad.status_code, bad.json())


if __name__ == "__main__":
    main()
