#!/bin/bash
# ARQ worker entrypoint for Cloud Run.
# Copies DB from GCS, syncs changes back every 30s with a sentinel for the web service.
set -e

LOCAL_DATA="/app/localdata"
GCS_DATA="/data"
DB_FILE="knowledge.db"
WORKER_DB="worker.db"
SENTINEL="$GCS_DATA/.worker_synced"

mkdir -p "$LOCAL_DATA" "$LOCAL_DATA/uploads"

# ── Pull latest DB from GCS ───────────────────────────────────────────
if [ -f "$GCS_DATA/$DB_FILE" ]; then
  echo "[worker] Restoring database from persistent storage..."
  cp "$GCS_DATA/$DB_FILE" "$LOCAL_DATA/$DB_FILE"
  echo "[worker] Restored $(du -h "$LOCAL_DATA/$DB_FILE" | cut -f1) database"
else
  echo "[worker] No existing database found — starting fresh"
fi

# Pull uploads for any files that need processing
if [ -d "$GCS_DATA/uploads" ] && [ "$(ls -A "$GCS_DATA/uploads" 2>/dev/null)" ]; then
  echo "[worker] Restoring uploads..."
  cp -r "$GCS_DATA/uploads/"* "$LOCAL_DATA/uploads/" 2>/dev/null || true
fi

export DATABASE_URL="$LOCAL_DATA/$DB_FILE"
export UPLOADS_DIR="$LOCAL_DATA/uploads"

# ── Background: sync to GCS every 30s, signal web service ────────────
sync_to_gcs() {
  if [ -f "$LOCAL_DATA/$DB_FILE" ] && [ -d "$GCS_DATA" ]; then
    cp "$LOCAL_DATA/$DB_FILE" "$GCS_DATA/$WORKER_DB" 2>/dev/null || true
    touch "$SENTINEL" 2>/dev/null || true
    # Sync new uploads back too
    if [ -d "$LOCAL_DATA/uploads" ]; then
      cp -r "$LOCAL_DATA/uploads/"* "$GCS_DATA/uploads/" 2>/dev/null || true
    fi
  fi
}

(
  while true; do
    sleep 30
    sync_to_gcs
  done
) &
SYNC_PID=$!

# ── Health probe on :8080 (Cloud Run requirement) ─────────────────────
# A minimal HTTP server so Cloud Run reports the container as healthy.
python3 -c "
import http.server, threading
class H(http.server.BaseHTTPRequestHandler):
    def do_GET(self):
        self.send_response(200); self.end_headers()
        self.wfile.write(b'ok')
    def log_message(self, *a): pass
s = http.server.HTTPServer(('0.0.0.0', 8080), H)
t = threading.Thread(target=s.serve_forever, daemon=True)
t.start()
import time
while True: time.sleep(3600)
" &
HEALTH_PID=$!

# ── Graceful shutdown ─────────────────────────────────────────────────
cleanup() {
  echo "[worker] SIGTERM received — final sync to GCS..."
  sync_to_gcs
  kill $SYNC_PID 2>/dev/null || true
  kill $HEALTH_PID 2>/dev/null || true
  echo "[worker] Shutdown complete"
}
trap cleanup SIGTERM SIGINT

# ── Run ARQ worker ────────────────────────────────────────────────────
echo "[worker] Starting ARQ worker..."
cd /app/backend
python -m arq app.worker.WorkerSettings &
ARQ_PID=$!

wait $ARQ_PID
