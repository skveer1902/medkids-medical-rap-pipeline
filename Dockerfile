FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

RUN useradd --create-home --uid 10001 medkids

COPY requirements.txt .
RUN pip install -r requirements.txt

COPY app ./app
COPY scripts ./scripts
COPY data/sample_topics ./data/sample_topics
COPY tests ./tests
COPY pyproject.toml ./

RUN mkdir -p /app/data/outputs && chown -R medkids:medkids /app/data
USER medkids

# Secrets come from the environment at run time (docker run --env-file .env ...).
ENV APP_ENV=development \
    DATABASE_URL=sqlite:////app/data/medkids.db \
    OUTPUTS_DIR=/app/data/outputs

EXPOSE 8000
HEALTHCHECK --interval=30s --timeout=5s --retries=3 \
  CMD python -c "import urllib.request,sys; sys.exit(0 if urllib.request.urlopen('http://127.0.0.1:8000/api/health').status==200 else 1)"

CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000"]
