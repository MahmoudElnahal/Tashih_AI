# One Dockerfile, two targets:
#   docker build --target api -t tashih-api .
#   docker build --target ui  -t tashih-ui  .

FROM python:3.12-slim AS base
ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUTF8=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1
RUN useradd --create-home --uid 10001 tashih
WORKDIR /srv

# ---------------------------------------------------------------- API ----
FROM base AS api
COPY requirements.txt .
RUN pip install -r requirements.txt
COPY app ./app
# Dataset cache (mounted as a named volume in docker-compose.yml).
RUN mkdir -p /data/hadith && chown -R tashih:tashih /data
ENV OFFLINE_DATA_DIR=/data/hadith
USER tashih
EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/health', timeout=4).status == 200 else 1)"]
# One worker: the hadith index, cache and rate limiter live in process memory.
CMD ["uvicorn", "app.main:app_factory", "--factory", "--host", "0.0.0.0", "--port", "8000", \
     "--timeout-graceful-shutdown", "20", "--no-access-log"]

# ----------------------------------------------------------------- UI ----
FROM base AS ui
COPY requirements-ui.txt .
RUN pip install -r requirements-ui.txt
COPY ui ./ui
COPY .streamlit ./.streamlit
USER tashih
EXPOSE 8501
HEALTHCHECK --interval=15s --timeout=5s --start-period=20s --retries=5 \
    CMD ["python", "-c", "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8501/_stcore/health', timeout=4).status == 200 else 1)"]
CMD ["streamlit", "run", "ui/app.py", "--server.address=0.0.0.0", "--server.port=8501", \
     "--server.headless=true", "--browser.gatherUsageStats=false"]
