"""Tashih demo UI (Streamlit). A thin client of the FastAPI service.

Everything shown here comes from the API; the UI contains no rulings and no
religious content of its own.
"""

from __future__ import annotations

import html
import os
from datetime import datetime
from typing import Any

import httpx
import streamlit as st

API_BASE_URL = os.environ.get("API_BASE_URL", "http://localhost:8000").rstrip("/")
API_KEY = os.environ.get("API_KEY", "")
TIMEOUT = httpx.Timeout(45.0, connect=5.0)

EXAMPLES = [
    "إنما الأعمال بالنيات",
    "طلب العلم فريضة على كل مسلم",
    "اطلبوا العلم ولو في الصين",
]
CITIES: dict[str, tuple[float, float]] = {
    "القاهرة": (30.0444, 31.2357),
    "الإسكندرية": (31.2001, 29.9187),
    "مكة المكرمة": (21.4225, 39.8262),
    "المدينة المنورة": (24.4672, 39.6111),
    "الرياض": (24.7136, 46.6753),
    "دبي": (25.2048, 55.2708),
    "عمّان": (31.9539, 35.9106),
    "إسطنبول": (41.0082, 28.9784),
    "لندن": (51.5074, -0.1278),
}
METHODS = {
    "الهيئة المصرية العامة للمساحة": 5,
    "أم القرى (مكة المكرمة)": 4,
    "رابطة العالم الإسلامي": 3,
    "جامعة العلوم الإسلامية بكراتشي": 1,
    "الجمعية الإسلامية لأمريكا الشمالية": 2,
}
BADGE_CLASS = {"sahih": "ok", "hasan": "ok", "daif": "bad", "mawdu": "bad"}
CATEGORY_AR = {"social": "التواصل الاجتماعي", "games": "الألعاب", "video": "الفيديو"}

CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Tajawal:wght@400;500;700&family=Amiri:wght@400;700&display=swap');
:root { --ink:#1d2b24; --muted:#6b7a72; --line:#e3e8e4; --card:#ffffff; --bg:#f6f8f6;
        --ok:#157347; --ok-bg:#e3f4ea; --bad:#b3261e; --bad-bg:#fbe9e7; --neutral:#5f6b66; --neutral-bg:#eceff0;
        --accent:#0f6b5c; }
html, body, .stApp, .stMarkdown, p, li, label, h1, h2, h3, button, input, textarea, summary {
  font-family:'Tajawal', sans-serif !important; }
/* Streamlit draws its icons with an icon font: never override it. */
[data-testid="stIconMaterial"], .material-symbols-rounded, .material-icons {
  font-family:'Material Symbols Rounded' !important; }
.stApp { direction: rtl; background: var(--bg); }
.block-container { max-width: 860px; padding-top: 2.2rem; }
h1, h2, h3, p, li, label, .stMarkdown, [data-testid="stChatMessageContent"] { text-align: right; }
[data-testid="stChatMessage"] { flex-direction: row-reverse; gap: .6rem; background: transparent; }
[data-testid="stChatInput"] textarea { direction: rtl; text-align: right; }
[data-testid="stExpander"] details { background:var(--card); border:1px solid var(--line); border-radius:12px; }
[data-testid="stExpander"] summary, [data-testid="stExpander"] summary p { color:var(--ink) !important; }
.stApp, .stApp p, .stApp label, .stCaption { color:var(--ink); }
.stTabs [data-baseweb="tab-list"] { gap: .4rem; }
.stTabs [data-baseweb="tab"] { font-size: 1.02rem; font-weight: 500; padding: .4rem 1rem; }
.brand { display:flex; align-items:baseline; gap:.7rem; margin-bottom:.2rem; }
.brand h1 { font-size:2rem; margin:0; color:var(--accent); font-weight:700; }
.brand span { color:var(--muted); font-size:.98rem; }
.card { background:var(--card); border:1px solid var(--line); border-radius:14px; padding:1rem 1.15rem; margin:.35rem 0 .6rem; }
.user-bubble { background:#e7f1ee; border-radius:14px 14px 4px 14px; padding:.6rem .95rem; display:inline-block; }
.badge { display:inline-block; padding:.22rem .8rem; border-radius:999px; font-weight:700; font-size:.98rem; }
.badge.ok { background:var(--ok-bg); color:var(--ok); }
.badge.bad { background:var(--bad-bg); color:var(--bad); }
.badge.neutral { background:var(--neutral-bg); color:var(--neutral); }
.hadith { font-family:'Amiri', serif; font-size:1.28rem; line-height:2.05; color:var(--ink);
          border-right:3px solid var(--accent); padding:.15rem .9rem; margin:.75rem 0; }
.meta { display:grid; grid-template-columns:auto 1fr; gap:.3rem .9rem; font-size:.97rem; margin:.4rem 0 .2rem; }
.meta dt { color:var(--muted); } .meta dd { margin:0; color:var(--ink); }
.explain { background:#f3f7f5; border-radius:10px; padding:.6rem .8rem; margin-top:.65rem; font-size:.98rem; }
.note { color:var(--ink); font-size:.98rem; margin-top:.55rem; }
.disclaimer { color:var(--muted); font-size:.84rem; margin-top:.7rem; border-top:1px dashed var(--line); padding-top:.5rem; }
.grades { width:100%; border-collapse:collapse; font-size:.93rem; }
.grades td { border-bottom:1px solid var(--line); padding:.38rem .3rem; vertical-align:top; }
.dot { display:inline-block; width:.6rem; height:.6rem; border-radius:50%; margin-left:.4rem; }
.dot.ok{background:var(--ok)} .dot.bad{background:var(--bad)} .dot.neutral{background:#b4bdb8}
.times { display:grid; grid-template-columns:repeat(6,1fr); gap:.5rem; }
.time { background:var(--card); border:1px solid var(--line); border-radius:12px; text-align:center; padding:.6rem .2rem; }
.time b { display:block; font-size:1.18rem; color:var(--ink); direction:ltr; }
.time span { color:var(--muted); font-size:.9rem; }
.time.next { border-color:var(--accent); background:#e7f1ee; }
.count { font-size:2rem; font-weight:700; color:var(--accent); direction:ltr; display:inline-block; }
.timeline { position:relative; height:46px; background:#e9eeeb; border-radius:10px; margin:.5rem 0 1.6rem; direction:ltr; }
.timeline .win { position:absolute; top:0; height:100%; background:var(--accent); opacity:.85; border-radius:4px; min-width:4px; }
.timeline .win.jumuah { background:#b7791f; }
.timeline .now { position:absolute; top:-5px; height:56px; width:2px; background:var(--bad); }
.timeline .tick { position:absolute; top:50px; font-size:.72rem; color:var(--muted); transform:translateX(-50%); }
.winrow { display:flex; justify-content:space-between; border-bottom:1px solid var(--line); padding:.4rem 0; font-size:.96rem; }
.winrow span:last-child { direction:ltr; color:var(--muted); }
.pill { display:inline-block; background:var(--neutral-bg); border-radius:999px; padding:.15rem .7rem; margin-left:.3rem; font-size:.88rem; }
@media (max-width: 640px) { .times { grid-template-columns:repeat(3,1fr); } }
</style>
"""


def esc(value: object) -> str:
    return html.escape(str(value or ""))


def api(method: str, path: str, **kwargs: Any) -> tuple[dict[str, Any] | None, str | None]:
    """Call the API. Returns (body, None) or (None, Arabic error message)."""
    headers = {"X-API-Key": API_KEY} if API_KEY else {}
    try:
        response = httpx.request(
            method, f"{API_BASE_URL}{path}", headers=headers, timeout=TIMEOUT, **kwargs
        )
    except httpx.TimeoutException:
        return None, "انتهت مهلة الاتصال بالخادم. حاول مرة أخرى."
    except httpx.HTTPError:
        return None, "تعذر الاتصال بخادم «تصحيح». تأكد أن الخدمة تعمل ثم حاول مرة أخرى."
    try:
        body = response.json()
    except ValueError:
        return None, f"رد غير متوقع من الخادم ({response.status_code})."
    if response.status_code >= 400:
        message = (body.get("error") or {}).get("message_ar") if isinstance(body, dict) else None
        return None, message or f"حدث خطأ ({response.status_code})."
    return body, None


# --------------------------------------------------------------------- chat --


def badge(result: dict[str, Any]) -> str:
    status = result.get("status")
    if status == "found":
        kind = BADGE_CLASS.get(result.get("verdict_code") or "", "neutral")
        return f'<span class="badge {kind}">{esc(result.get("verdict_ar"))}</span>'
    labels = {"not_found": "لم يُعثر عليه", "unavailable": "المصادر غير متاحة"}
    return f'<span class="badge neutral">{labels[status]}</span>' if status in labels else ""


def match_meta(match: dict[str, Any]) -> str:
    source = " — ".join(
        str(part) for part in (match.get("source_book"), match.get("number_or_page")) if part
    )
    rows = [
        ("الراوي", match.get("narrator")),
        ("المحدِّث", match.get("muhaddith")),
        ("المصدر", source),
        ("خلاصة الحكم", match.get("grade_text")),
    ]
    cells = "".join(f"<dt>{label}</dt><dd>{esc(value)}</dd>" for label, value in rows if value)
    return f'<dl class="meta">{cells}</dl>'


def render_result(result: dict[str, Any]) -> None:
    best = result.get("best_match")
    if result.get("status") != "found" or not best:
        st.markdown(
            f'<div class="card">{badge(result)}<div class="note">{esc(result.get("message_ar"))}</div></div>',
            unsafe_allow_html=True,
        )
        return

    parts = [
        badge(result),
        f'<div class="hadith">{esc(best["hadith_text"])}</div>',
        match_meta(best),
        f'<div class="note">{esc(result.get("message_ar"))}</div>',
    ]
    if result.get("explanation_ar"):
        parts.append(f'<div class="explain">{esc(result["explanation_ar"])}</div>')
    source_label = {"dorar": "الدرر السنية", "offline_six_books": "الكتب الستة (قاعدة محلية)"}
    parts.append(
        f'<div class="disclaimer">{esc(result.get("disclaimer_ar"))}'
        f" · المصدر: {esc(source_label.get(result.get('source_used') or '', result.get('source_used')))}</div>"
    )
    st.markdown(f'<div class="card">{"".join(parts)}</div>', unsafe_allow_html=True)

    grades = best.get("grades") or []
    if len(grades) > 1:
        with st.expander(f"كل أحكام المحدثين في المصدر ({len(grades)})"):
            rows = "".join(
                f'<tr><td style="white-space:nowrap"><span class="dot {BADGE_CLASS.get(g["verdict_code"], "neutral")}"></span>'
                f"{esc(g['scholar'])}</td><td>{esc(g['grade'])}</td></tr>"
                for g in grades
            )
            st.markdown(f'<table class="grades">{rows}</table>', unsafe_allow_html=True)
    others = result.get("other_matches") or []
    if others:
        with st.expander(f"نتائج أخرى مطابقة ({len(others)})"):
            for other in others:
                st.markdown(
                    f'<div class="hadith" style="font-size:1.08rem">{esc(other["hadith_text"])}</div>'
                    f"{match_meta(other)}",
                    unsafe_allow_html=True,
                )


def chat_tab() -> None:
    st.session_state.setdefault("history", [])
    history: list[dict[str, Any]] = st.session_state["history"]

    if not history:
        st.markdown(
            '<div class="card">اكتب نص الحديث أو الصقه، وسأذكر لك <b>حكمه</b>، و<b>من حكم عليه</b>، '
            "و<b>مصدره</b>. الحكم منقول من المصادر الحديثية وليس من الذكاء الاصطناعي، "
            "وإذا لم أجد النص فلن أحكم عليه.</div>",
            unsafe_allow_html=True,
        )

    st.caption("جرّب أحد هذه الأمثلة:")
    pending: str | None = None
    for column, example in zip(st.columns(len(EXAMPLES)), EXAMPLES, strict=True):
        if column.button(example, use_container_width=True):
            pending = example

    for turn in history:
        with st.chat_message("user"):
            st.markdown(
                f'<div class="user-bubble">{esc(turn["user"])}</div>', unsafe_allow_html=True
            )
        with st.chat_message("assistant"):
            if turn.get("error"):
                st.error(turn["error"])
            else:
                render_result(turn["result"])

    typed = st.chat_input("اكتب نص الحديث هنا…")
    message = typed or pending
    if not message:
        return

    with st.chat_message("user"):
        st.markdown(f'<div class="user-bubble">{esc(message)}</div>', unsafe_allow_html=True)
    with st.chat_message("assistant"), st.spinner("جارٍ البحث في المصادر…"):
        messages = []
        for turn in history[-5:]:
            messages.append({"role": "user", "content": turn["user"]})
            if turn.get("result"):
                messages.append({"role": "assistant", "content": turn["result"]["reply_ar"][:4000]})
        messages.append({"role": "user", "content": message[:4000]})
        result, error = api("POST", "/v1/chat", json={"messages": messages})
    history.append({"user": message, "result": result, "error": error})
    st.rerun()


# ------------------------------------------------------------------- prayer --


def clock(iso: str) -> str:
    return datetime.fromisoformat(iso).strftime("%H:%M")


def countdown(seconds: int) -> str:
    hours, rest = divmod(max(0, seconds), 3600)
    return f"{hours:02d}:{rest // 60:02d}"


def compass_svg(direction: float) -> str:
    return f"""
<svg viewBox="0 0 200 200" width="190" height="190" role="img" aria-label="اتجاه القبلة">
  <circle cx="100" cy="100" r="88" fill="#fff" stroke="#e3e8e4" stroke-width="2"/>
  <g font-family="Tajawal" font-size="13" fill="#6b7a72" text-anchor="middle">
    <text x="100" y="28">ش</text><text x="100" y="184">ج</text>
    <text x="178" y="105">ق</text><text x="22" y="105">غ</text>
  </g>
  <g transform="rotate({direction:.2f} 100 100)">
    <line x1="100" y1="100" x2="100" y2="38" stroke="#0f6b5c" stroke-width="5" stroke-linecap="round"/>
    <polygon points="100,22 89,44 111,44" fill="#0f6b5c"/>
  </g>
  <circle cx="100" cy="100" r="6" fill="#1d2b24"/>
</svg>"""


def timeline_html(schedule: dict[str, Any]) -> str:
    def position(iso: str) -> float:
        moment = datetime.fromisoformat(iso)
        return (moment.hour * 60 + moment.minute) / 1440 * 100

    parts = []
    for window in schedule["windows"]:
        left = position(window["start"])
        width = max(0.4, window["duration_min"] / 1440 * 100)
        css = "win jumuah" if window["is_jumuah"] else "win"
        parts.append(
            f'<div class="{css}" style="left:{left:.2f}%;width:{width:.2f}%" '
            f'title="{esc(window["name_ar"])} {clock(window["start"])}–{clock(window["end"])}"></div>'
        )
    for hour in range(0, 25, 4):
        parts.append(f'<div class="tick" style="left:{hour / 24 * 100:.1f}%">{hour:02d}:00</div>')
    zone = datetime.fromisoformat(schedule["windows"][0]["start"]).tzinfo
    now = datetime.now(zone)
    if now.date().isoformat() == schedule["date"]:
        parts.append(
            f'<div class="now" style="left:{(now.hour * 60 + now.minute) / 1440 * 100:.2f}%"></div>'
        )
    return f'<div class="timeline">{"".join(parts)}</div>'


def prayer_tab() -> None:
    left, right = st.columns(2)
    city = left.selectbox("المدينة", list(CITIES), index=0)
    method_name = right.selectbox("طريقة الحساب", list(METHODS), index=0)
    lat, lon = CITIES[city]
    base = {"lat": lat, "lon": lon, "method": METHODS[method_name]}

    with st.spinner("جارٍ تحميل المواقيت…"):
        times, error = api("GET", "/v1/prayer/times", params=base)
    if error or not times:
        st.error(error or "تعذر تحميل المواقيت.")
        return

    hijri = times["hijri"]
    st.markdown(
        f'<div class="card"><b>{esc(hijri["weekday_ar"])}</b> · {hijri["day"]} {esc(hijri["month_ar"])} '
        f'{hijri["year"]} هـ · {esc(times["date"])} · <span style="color:var(--muted)">{esc(times["timezone"])}</span></div>',
        unsafe_allow_html=True,
    )

    upcoming = times.get("next_prayer")
    next_key = upcoming["key"] if upcoming else None
    cells = "".join(
        f'<div class="time{" next" if p["key"] == next_key and p["time"] == upcoming["time"] else ""}">'
        f"<span>{esc(p['name_ar'])}</span><b>{clock(p['time'])}</b></div>"
        for p in times["prayers"]
    )
    st.markdown(f'<div class="times">{cells}</div>', unsafe_allow_html=True)

    info, qibla_col = st.columns([3, 2])
    with info:
        if upcoming:
            st.markdown(
                f'<div class="card">الصلاة القادمة: <b>{esc(upcoming["name_ar"])}</b> الساعة '
                f'<span style="direction:ltr;display:inline-block">{clock(upcoming["time"])}</span><br>'
                f'<span class="count">{countdown(upcoming["seconds_remaining"])}</span> '
                f'<span style="color:var(--muted)">ساعة:دقيقة متبقية</span></div>',
                unsafe_allow_html=True,
            )
        daily, _ = api("GET", "/v1/daily", params={"lat": lat, "lon": lon})
        if daily:
            hadith = daily["hadith"]
            st.markdown(
                f'<div class="card"><span class="badge ok">حديث اليوم · {esc(hadith["verdict_ar"])}</span>'
                f'<div class="hadith" style="font-size:1.1rem">{esc(hadith["hadith_text"])}</div>'
                f'<div class="disclaimer">{esc(hadith["source_book"])} — رقم {esc(hadith["number"])} · '
                f"{esc(hadith['grade_text'])}</div></div>",
                unsafe_allow_html=True,
            )
    with qibla_col:
        qibla, qibla_error = api("GET", "/v1/prayer/qibla", params={"lat": lat, "lon": lon})
        if qibla:
            st.markdown(
                f'<div class="card" style="text-align:center"><div>اتجاه القبلة</div>'
                f"{compass_svg(qibla['direction_deg'])}"
                f'<div><b style="direction:ltr;display:inline-block">{qibla["direction_deg"]:.1f}°</b> '
                f"من الشمال الحقيقي · {esc(qibla['compass_ar'])}</div></div>",
                unsafe_allow_html=True,
            )
        else:
            st.warning(qibla_error or "تعذر تحميل اتجاه القبلة.")

    st.subheader("نوافذ التركيز وقت الصلاة")
    before_col, during_col = st.columns(2)
    before = before_col.slider("دقائق قبل الأذان", 0, 30, 5)
    during = during_col.slider("دقائق بعد الأذان", 5, 60, 20, step=5)
    schedule, schedule_error = api(
        "GET",
        "/v1/prayer/focus-schedule",
        params={**base, "before_min": before, "during_min": during},
    )
    if schedule_error or not schedule:
        st.error(schedule_error or "تعذر تحميل جدول التركيز.")
        return

    st.markdown(timeline_html(schedule), unsafe_allow_html=True)
    rows = "".join(
        f'<div class="winrow"><span><b>{esc(w["name_ar"])}</b> · {w["duration_min"]} دقيقة</span>'
        f"<span>{clock(w['start'])} – {clock(w['end'])}</span></div>"
        for w in schedule["windows"]
    )
    active = schedule.get("active_window")
    status = (
        f'<span class="badge bad">نافذة تركيز نشطة الآن: {esc(active["name_ar"])}</span>'
        if active
        else '<span class="badge neutral">لا توجد نافذة تركيز نشطة الآن</span>'
    )
    pills = "".join(
        f'<span class="pill">{esc(CATEGORY_AR.get(c, c))}</span>'
        for c in schedule["blocked_categories"]
    )
    st.markdown(
        f'<div class="card">{status}{rows}<div class="note">الفئات المقترح قفلها: {pills}</div></div>',
        unsafe_allow_html=True,
    )
    st.info(
        "ماذا سيفعل تطبيق الجوال؟ يقرأ هذه النوافذ من الخادم (‎/v1/prayer/focus-schedule‎)، "
        "ويقفل التطبيقات المشتتة داخل كل نافذة مع رسالة تذكير، ثم يفتحها تلقائياً عند نهايتها. "
        "الخادم يحسب الجدول فقط؛ القفل نفسه يتم على الجهاز (متاح على أندرويد، ومقيّد على iOS).",
        icon="📱",
    )
    st.caption(f"مثال رسالة القفل: «{schedule['windows'][0]['reminder_ar']}»")


def main() -> None:
    st.set_page_config(page_title="تصحيح — التحقق من الأحاديث", page_icon="📗", layout="centered")
    st.markdown(CSS, unsafe_allow_html=True)
    st.markdown(
        '<div class="brand"><h1>تصحيح</h1><span>تحقّق من الحديث قبل أن تنشره</span></div>',
        unsafe_allow_html=True,
    )
    chat, prayer = st.tabs(["التحقق من الأحاديث", "الصلاة"])
    with chat:
        chat_tab()
    with prayer:
        prayer_tab()


main()
