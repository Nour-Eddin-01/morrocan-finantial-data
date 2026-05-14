FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && update-ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml README.md ./
COPY src ./src
COPY tests ./tests
COPY fixtures ./fixtures
COPY alembic.ini ./
COPY migrations ./migrations

RUN pip install --no-cache-dir .

CMD ["uvicorn", "tradehub_data.api.app:create_app", "--factory", "--host", "0.0.0.0", "--port", "8000"]
