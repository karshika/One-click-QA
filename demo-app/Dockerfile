FROM python:3.12-slim-bookworm
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_NO_CACHE_DIR=1
WORKDIR /app
COPY pyproject.toml ./
COPY demo_app ./demo_app
RUN pip install . && useradd -m -u 10002 demo
USER demo
EXPOSE 8001
CMD ["uvicorn", "demo_app.main:app", "--host", "0.0.0.0", "--port", "8001"]
