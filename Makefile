PY ?= python

.PHONY: up down logs test test-live lint format openapi dev dev-ui install check-daily

install:  ## install runtime + dev dependencies into the current environment
	$(PY) -m pip install -r requirements-dev.txt

up:  ## build and start the API (8000) and the demo UI (8501)
	docker compose up --build -d

down:
	docker compose down

logs:
	docker compose logs -f api

test:
	$(PY) -m pytest

test-live:  ## also hit the real Aladhan API
	$(PY) -m pytest -m live

lint:
	$(PY) -m ruff check .
	$(PY) -m ruff format --check .
	$(PY) -m mypy

format:
	$(PY) -m ruff format .
	$(PY) -m ruff check . --fix

openapi:  ## regenerate docs/openapi.json for client generation
	$(PY) -m scripts.export_openapi

dev:  ## run the API locally with auto-reload
	$(PY) -m uvicorn app.main:app_factory --factory --reload --port 8000

dev-ui:
	$(PY) -m streamlit run ui/app.py

check-daily:  ## verify the daily-hadith phrases against the real dataset
	$(PY) -m scripts.check_daily
