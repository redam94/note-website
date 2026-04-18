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
    print('Creating tables from models...')
    Base.metadata.create_all(engine)
    print('Stamping alembic to latest...')
    engine.dispose()
    import subprocess
    subprocess.run(['python', '-m', 'alembic', 'stamp', 'head'], check=True)
else:
    print(f'Tables exist: {existing}')
    # Check if new tables are missing despite alembic thinking it is up to date
    if 'spaces' not in existing:
        print('Missing spaces table — resetting alembic version to run migrations...')
        # Find the last migration that the DB actually has applied
        if 'note_comments' in existing:
            stamp_to = 'd4e5f6a7b8c9'
        elif 'subgraph_nodes' in existing and 'space_id' in [c['name'] for c in inspector.get_columns('notes')]:
            stamp_to = 'c3d4e5f6a7b8'
        elif 'subgraph_nodes' in existing:
            stamp_to = 'b2c3d4e5f6a7'
        else:
            stamp_to = 'f65b0e1d2eda'
        engine.dispose()
        import subprocess
        subprocess.run(['python', '-m', 'alembic', 'stamp', stamp_to], check=True)
        subprocess.run(['python', '-m', 'alembic', 'upgrade', 'head'], check=True)
    else:
        engine.dispose()
        import subprocess
        subprocess.run(['python', '-m', 'alembic', 'upgrade', 'head'], check=True)
"

# ── Background: sync DB to GCS every 60 seconds ─────────────────────
# If the worker sentinel exists, merge its writes before pushing to GCS.
WORKER_DB="worker.db"
SENTINEL="$GCS_DATA/.worker_synced"

merge_worker_db() {
  if [ -f "$SENTINEL" ] && [ -f "$GCS_DATA/$WORKER_DB" ]; then
    echo "Merging worker database..."
    python3 - <<'PYEOF'
import os, sqlite3
local = os.environ.get("DATABASE_URL", "/app/localdata/knowledge.db")
worker = "/data/worker.db"
if not os.path.exists(worker):
    exit(0)
conn = sqlite3.connect(local)
conn.execute(f"ATTACH DATABASE '{worker}' AS worker")
tables = ["notes", "graph_edges", "subgraph_nodes", "subgraph_edges", "note_links"]
for t in tables:
    try:
        conn.execute(f"INSERT OR IGNORE INTO {t} SELECT * FROM worker.{t}")
    except Exception as e:
        print(f"  skip {t}: {e}")
# Propagate document status updates from worker (done/error)
try:
    conn.execute("""
        UPDATE documents SET status = w.status, error = w.error,
          processing_step = w.processing_step
        FROM worker.documents w
        WHERE documents.id = w.id
          AND w.status IN ('done','error')
          AND documents.status = 'processing'
    """)
except Exception as e:
    print(f"  skip document status merge: {e}")
conn.commit()
conn.execute("DETACH DATABASE worker")
conn.close()
print("Worker merge complete")
PYEOF
    rm -f "$SENTINEL"
  fi
}

(
  while true; do
    sleep 60
    if [ -f "$LOCAL_DATA/$DB_FILE" ] && [ -d "$GCS_DATA" ]; then
      merge_worker_db
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
  merge_worker_db
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
