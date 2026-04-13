#!/bin/bash
set -e

LOCAL_DATA="/app/localdata"
GCS_DATA="/data"
DB_FILE="knowledge.db"

mkdir -p "$LOCAL_DATA" "$LOCAL_DATA/uploads"

# ── Restore from GCS mount (if DB exists there) ─────────────────────
if [ -f "$GCS_DATA/$DB_FILE" ]; then
  echo "Restoring database from persistent storage..."
  cp "$GCS_DATA/$DB_FILE" "$LOCAL_DATA/$DB_FILE"
  echo "Restored $(du -h "$LOCAL_DATA/$DB_FILE" | cut -f1) database"
fi

# Restore uploads
if [ -d "$GCS_DATA/uploads" ] && [ "$(ls -A "$GCS_DATA/uploads" 2>/dev/null)" ]; then
  echo "Restoring uploads..."
  cp -r "$GCS_DATA/uploads/"* "$LOCAL_DATA/uploads/" 2>/dev/null || true
fi

# ── Use local paths for SQLite (not GCS FUSE) ────────────────────────
export DATABASE_URL="$LOCAL_DATA/$DB_FILE"
export UPLOADS_DIR="$LOCAL_DATA/uploads"

# ── Init database ────────────────────────────────────────────────────
cd /app/backend
echo "Initializing database..."
python -c "
from sqlalchemy import create_engine, inspect
from app.config import settings
from app.models import Base
engine = create_engine(f'sqlite:///{settings.database_url}')
inspector = inspect(engine)
existing = inspector.get_table_names()
if 'notes' not in existing:
    print('Creating tables...')
    Base.metadata.create_all(engine)
else:
    print(f'Tables exist: {existing}')
engine.dispose()
"
alembic stamp head 2>&1 || true
alembic upgrade head 2>&1 || true

# ── Background: sync DB to GCS every 60 seconds ─────────────────────
(
  while true; do
    sleep 60
    if [ -f "$LOCAL_DATA/$DB_FILE" ] && [ -d "$GCS_DATA" ]; then
      cp "$LOCAL_DATA/$DB_FILE" "$GCS_DATA/$DB_FILE" 2>/dev/null || true
      # Sync new uploads
      if [ -d "$LOCAL_DATA/uploads" ]; then
        cp -r "$LOCAL_DATA/uploads/"* "$GCS_DATA/uploads/" 2>/dev/null || true
      fi
    fi
  done
) &
SYNC_PID=$!

# ── Graceful shutdown: sync before exit ──────────────────────────────
cleanup() {
  echo "Syncing to persistent storage before exit..."
  cp "$LOCAL_DATA/$DB_FILE" "$GCS_DATA/$DB_FILE" 2>/dev/null || true
  cp -r "$LOCAL_DATA/uploads/"* "$GCS_DATA/uploads/" 2>/dev/null || true
  kill $SYNC_PID 2>/dev/null || true
  echo "Sync complete"
}
trap cleanup SIGTERM SIGINT EXIT

# ── Start backend ────────────────────────────────────────────────────
echo "Starting backend on :8080..."
uvicorn app.main:app --host 127.0.0.1 --port 8080 &
BACKEND_PID=$!

for i in $(seq 1 30); do
  if curl -sf http://127.0.0.1:8080/api/documents > /dev/null 2>&1; then
    echo "Backend ready"
    break
  fi
  sleep 1
done

# ── Start frontend ───────────────────────────────────────────────────
echo "Starting frontend on :${PORT:-3000}..."
cd /app/frontend
HOSTNAME=0.0.0.0 PORT=${PORT:-3000} BACKEND_URL=http://127.0.0.1:8080 node server.js &
FRONTEND_PID=$!

# Wait for either to exit
wait -n $BACKEND_PID $FRONTEND_PID
