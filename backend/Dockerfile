FROM python:3.11-slim

WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

COPY pyproject.toml .
RUN pip install --no-cache-dir .

COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .
COPY start.sh .
RUN chmod +x start.sh

ENV DATABASE_URL=/data/knowledge.db
ENV UPLOADS_DIR=/data/uploads
ENV USE_REDIS=false
ENV CORS_ORIGINS=["*"]

EXPOSE 8080

CMD ["./start.sh"]
