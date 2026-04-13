#!/bin/bash
set -e
mkdir -p /data /data/uploads

echo "Initializing database..."
python -c "
from sqlalchemy import create_engine, inspect
from app.config import settings
from app.models import Base

engine = create_engine(f'sqlite:///{settings.database_url}')
inspector = inspect(engine)
existing = inspector.get_table_names()

if 'notes' not in existing:
    print('Creating tables from scratch...')
    Base.metadata.create_all(engine)
    print(f'Created tables: {Base.metadata.tables.keys()}')
else:
    print(f'Tables already exist: {existing}')

engine.dispose()
"

echo "Running migrations..."
alembic stamp head 2>&1 || true
alembic upgrade head 2>&1 || echo "WARNING: Migrations had issues, continuing"

echo "Starting server on port 8080..."
exec uvicorn app.main:app --host 0.0.0.0 --port 8080
