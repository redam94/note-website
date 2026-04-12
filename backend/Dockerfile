FROM python:3.11-slim

WORKDIR /app

# Install system deps
RUN apt-get update && apt-get install -y --no-install-recommends \
    build-essential \
    && rm -rf /var/lib/apt/lists/*

# Install Python deps
COPY pyproject.toml .
RUN pip install --no-cache-dir .

# Copy app code
COPY app/ app/
COPY alembic/ alembic/
COPY alembic.ini .

# Create data and uploads dirs
RUN mkdir -p /data /uploads

# Environment
ENV DATABASE_URL=/data/knowledge.db
ENV UPLOADS_DIR=/uploads
ENV USE_REDIS=false
ENV CORS_ORIGINS=["*"]

# Run migrations then start server
CMD alembic upgrade head && \
    uvicorn app.main:app --host 0.0.0.0 --port 8080
