from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config import Settings
from app.main import create_app
from app.services.llm import ExplainFacts, Extraction
from app.services.verdict import classify
from app.sources.base import Match, SourceError, SourceGrade

FIXTURES = Path(__file__).parent / "fixtures"
OFFLINE_SAMPLE = FIXTURES / "offline_sample"


def make_match(
    text: str,
    grade: str,
    *,
    muhaddith: str = "الألباني",
    book: str = "السلسلة",
    number: str = "1",
    narrator: str | None = "أنس بن مالك",
    score: float = 1.0,
) -> Match:
    verdict = classify(grade)
    return Match(
        hadith_text=text,
        narrator=narrator,
        muhaddith=muhaddith,
        source_book=book,
        number_or_page=number,
        grade_text=grade,
        grades=(SourceGrade(muhaddith, grade, verdict),),
        score=score,
        verdict=verdict,
    )


class FakeSource:
    """In-memory HadithSource: returns canned matches or raises a canned error."""

    def __init__(
        self,
        name: str,
        *,
        matches: list[Match] | None = None,
        error: SourceError | None = None,
        grades_span_results: bool = False,
    ) -> None:
        self.name = name
        self.grades_span_results = grades_span_results
        self.matches = matches or []
        self.error = error
        self.calls: list[str] = []

    async def search(self, text: str) -> list[Match]:
        self.calls.append(text)
        if self.error is not None:
            raise self.error
        return list(self.matches)

    def health(self) -> str:
        return "blocked" if self.error is not None else "ok"

    async def start(self) -> None:
        return None

    async def close(self) -> None:
        return None


class FakeLLM:
    def __init__(
        self,
        *,
        explanation: str | None = None,
        extraction: Extraction | None = None,
        fail: bool = False,
    ) -> None:
        self.explanation = explanation
        self.extraction = extraction
        self.fail = fail
        self.extract_calls: list[str] = []
        self.explain_calls: list[ExplainFacts] = []

    async def extract(self, message: str) -> Extraction | None:
        self.extract_calls.append(message)
        if self.fail:
            raise TimeoutError("openai down")
        return self.extraction

    async def explain(self, facts: ExplainFacts) -> str | None:
        self.explain_calls.append(facts)
        if self.fail:
            raise TimeoutError("openai down")
        return self.explanation


ClientFactory = Callable[..., TestClient]


@pytest.fixture
def client_factory() -> Iterator[ClientFactory]:
    opened: list[TestClient] = []

    def factory(
        sources: list[FakeSource], llm: FakeLLM | None = None, **settings: object
    ) -> TestClient:
        config = Settings(
            _env_file=None,  # type: ignore[call-arg]
            openai_api_key="",
            **{"rate_limit_per_minute": 0, "api_keys": "", **settings},  # type: ignore[arg-type]
        )
        client = TestClient(create_app(config, sources=sources, llm=llm))  # type: ignore[arg-type]
        client.__enter__()
        opened.append(client)
        return client

    yield factory
    for client in opened:
        client.__exit__(None, None, None)
