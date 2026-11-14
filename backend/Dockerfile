# bookworm: a Debian release officially supported by `playwright install --with-deps`
FROM python:3.12-slim-bookworm

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PLAYWRIGHT_BROWSERS_PATH=/ms-playwright \
    OCQA_DATA_DIR=/data

WORKDIR /app
COPY pyproject.toml ./
COPY app ./app
RUN pip install . && python -m playwright install --with-deps chromium

# Run as an unprivileged user; generated tests run in subprocesses as this user too.
RUN useradd -m -u 10001 ocqa && mkdir -p /data && chown -R ocqa /data /ms-playwright
USER ocqa

EXPOSE 8000
HEALTHCHECK --interval=15s --timeout=5s --retries=10 CMD python -c "import urllib.request;urllib.request.urlopen('http://127.0.0.1:8000/health')"
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
