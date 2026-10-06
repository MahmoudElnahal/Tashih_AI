"""Dorar (الدرر السنية) hadith encyclopedia source.

Verified response shape (see ``tests/fixtures/dorar_*.json``)::

    {"ahadith": {"result": "<div class=\"hadith\">1 - ...</div>
                             <div class=\"hadith-info\">
                               <span class=\"info-subtitle\">الراوي:</span> ...
                               <span class=\"info-subtitle\">المحدث:</span> ...
                               <span class=\"info-subtitle\">المصدر:</span> ...
                               <span class=\"info-subtitle\">الصفحة أو الرقم:</span> ...
                               <span class=\"info-subtitle\">خلاصة حكم المحدث:</span> <span>...</span>
                             </div> ..."}}

The endpoint is a keyword search: it returns up to 15 results even for a
fabricated sentence. Every result is therefore re-scored with the shared
matcher and only confident matches are returned.
"""

from __future__ import annotations

import asyncio
import html
import json
import logging
import re
import ssl
from dataclasses import dataclass
from typing import Any

import httpx

from app.services.matcher import build_query, match_score
from app.services.normalize import normalize
from app.services.verdict import classify
from app.sources.base import Match, SourceBlocked, SourceError, SourceGrade, SourceUnavailable

logger = logging.getLogger(__name__)

SOURCE_NAME = "dorar"
USER_AGENT = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/126.0 Safari/537.36"
)

_BLOCK = re.compile(
    r'<div class="hadith"[^>]*>(?P<text>.*?)</div>\s*<div class="hadith-info">(?P<info>.*?)</div>',
    re.S,
)
_INFO_FIELD = re.compile(
    r'<span class="info-subtitle">(?P<label>[^<]*)</span>(?P<value>.*?)(?=<span class="info-subtitle">|\Z)',
    re.S,
)
_TAG = re.compile(r"<[^>]+>")
_SPACES = re.compile(r"\s+")
_BIDI_MARKS = re.compile(r"[‎‏‪-‮]")
_LEADING_INDEX = re.compile(r"^\d+\s*-\s*")
_TRAILING_DOTS = re.compile(r"(?:\s*\.)+$")
_CLOUDFLARE = re.compile(r"Attention Required|Just a moment|cf-error|cf-browser-verification", re.I)

_LABELS = {
    "الراوي": "narrator",
    "المحدث": "muhaddith",
    "المصدر": "source_book",
    "الصفحه او الرقم": "number_or_page",
    "خلاصه حكم المحدث": "grade_text",
}


@dataclass(frozen=True, slots=True)
class DorarResult:
    hadith_text: str
    narrator: str | None
    muhaddith: str | None
    source_book: str | None
    number_or_page: str | None
    grade_text: str | None


def _clean(fragment: str) -> str:
    text = html.unescape(_TAG.sub(" ", fragment))
    return _SPACES.sub(" ", _BIDI_MARKS.sub("", text)).strip()


def _value(fragment: str) -> str | None:
    text = _clean(fragment)
    return None if text in ("", "-") else text


def extract_html(payload: Any) -> str:
    """Pull the results HTML out of the JSON payload.

    The live API returns ``{"ahadith": {"result": html}}``; the published
    documentation shows a list of ``{"th": html}`` items, so both are accepted.
    """
    ahadith = payload.get("ahadith") if isinstance(payload, dict) else None
    if isinstance(ahadith, dict) and isinstance(ahadith.get("result"), str):
        return str(ahadith["result"])
    if isinstance(ahadith, list):
        return "\n".join(str(i.get("th", "")) for i in ahadith if isinstance(i, dict))
    raise ValueError("unexpected Dorar payload shape")


def parse_results(results_html: str) -> list[DorarResult]:
    results: list[DorarResult] = []
    for block in _BLOCK.finditer(results_html):
        text = _TRAILING_DOTS.sub("", _LEADING_INDEX.sub("", _clean(block["text"]))).strip()
        if not text:
            continue
        fields: dict[str, str | None] = {}
        for field in _INFO_FIELD.finditer(block["info"]):
            key = _LABELS.get(normalize(field["label"]))
            if key:
                fields[key] = _value(field["value"])
        results.append(
            DorarResult(
                hadith_text=text,
                narrator=fields.get("narrator"),
                muhaddith=fields.get("muhaddith"),
                source_book=fields.get("source_book"),
                number_or_page=fields.get("number_or_page"),
                grade_text=fields.get("grade_text"),
            )
        )
    return results


def to_matches(text: str, results: list[DorarResult]) -> list[Match]:
    """Keep confident matches only; best score first, Dorar's order within a score."""
    query = build_query(text)
    matches: list[Match] = []
    for r in results:
        score = match_score(query, normalize(r.hadith_text))
        if score <= 0:
            continue
        verdict = classify(r.grade_text)
        grades = (
            (SourceGrade(r.muhaddith or "غير مذكور", r.grade_text, verdict),)
            if r.grade_text
            else ()
        )
        matches.append(
            Match(
                hadith_text=r.hadith_text,
                narrator=r.narrator,
                muhaddith=r.muhaddith,
                source_book=r.source_book,
                number_or_page=r.number_or_page,
                grade_text=r.grade_text,
                grades=grades,
                score=score,
                verdict=verdict,
            )
        )
    matches.sort(key=lambda m: -m.score)
    return matches


def stdlib_https_context() -> ssl.SSLContext:
    """TLS context configured exactly like Python's own ``http.client``.

    Verified on 2026-10-06: Cloudflare in front of dorar.net answers HTTP 403 to
    httpx's default TLS handshake but HTTP 200 to the standard library's, from
    the same machine and IP. The only difference is ``post_handshake_auth``,
    which ``http.client`` enables. Certificate verification is unchanged.
    """
    context = ssl.create_default_context()
    context.post_handshake_auth = True
    return context


class DorarSource:
    name = SOURCE_NAME
    grades_span_results = True

    def __init__(
        self,
        client: httpx.AsyncClient | None = None,
        *,
        base_url: str = "https://dorar.net/dorar_api.json",
        timeout: float = 10.0,
        retry_backoff: float = 0.5,
    ) -> None:
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(verify=stdlib_https_context(), timeout=timeout)
        self._base_url = base_url
        self._timeout = timeout
        self._retry_backoff = retry_backoff
        self._status = "unknown"

    def health(self) -> str:
        return self._status

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def search(self, text: str) -> list[Match]:
        try:
            results_html = await self._fetch(text)
        except SourceError as first:
            logger.warning("dorar attempt 1 failed, retrying", extra={"detail": first.detail})
            await asyncio.sleep(self._retry_backoff)
            try:
                results_html = await self._fetch(text)
            except SourceBlocked:
                self._status = "blocked"
                raise
            except SourceUnavailable:
                self._status = "unavailable"
                raise
        self._status = "ok"
        return to_matches(text, parse_results(results_html))

    async def _fetch(self, text: str) -> str:
        try:
            response = await self._client.get(
                self._base_url,
                params={"skey": text},
                headers={
                    "User-Agent": USER_AGENT,
                    "Referer": "https://dorar.net/",
                    "Accept": "application/json, text/javascript, */*",
                },
                timeout=self._timeout,
            )
        except httpx.TimeoutException as exc:
            raise SourceUnavailable(self.name, "timeout") from exc
        except httpx.HTTPError as exc:
            raise SourceUnavailable(self.name, f"network error: {type(exc).__name__}") from exc

        if response.status_code == 403:
            raise SourceBlocked(self.name, "HTTP 403 (Cloudflare block)")
        if response.status_code != 200:
            raise SourceUnavailable(self.name, f"HTTP {response.status_code}")
        try:
            return extract_html(json.loads(response.text))
        except ValueError as exc:
            if _CLOUDFLARE.search(response.text[:4000]):
                raise SourceBlocked(self.name, "Cloudflare challenge page") from exc
            raise SourceUnavailable(self.name, "unparseable response") from exc
