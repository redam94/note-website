#!/bin/bash
set -euo pipefail

# ── Load environment from .env.live ──────────────────────────────────
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.live"

if [ -f "$ENV_FILE" ]; then
  echo "Loading config from .env.live"
  set -a
  source "$ENV_FILE"
  set +a
else
  echo "WARNING: .env.live not found at $ENV_FILE"
  echo "Create it from .env.live.example or set env vars manually."
fi

# ── Configuration (with defaults) ────────────────────────────────────
PROJECT_ID="${PROJECT_ID:-knowledge-base-493120}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-second-brain}"
REPO_NAME="${REPO_NAME:-second-brain}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-second-brain-data}"
DOMAIN="${DOMAIN:-}"

# Required secrets
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD not set in .env.live}"
: "${JWT_SECRET:?JWT_SECRET not set in .env.live}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

echo "=== Deploying Second Brain to GCP Cloud Run ==="
echo "Project: $PROJECT_ID | Region: $REGION | Service: $SERVICE_NAME"

# ── Enable APIs ──────────────────────────────────────────────────────
echo ""
echo "--- Enabling APIs ---"
gcloud services enable \
  cloudbuild.googleapis.com \
  run.googleapis.com \
  artifactregistry.googleapis.com \
  storage.googleapis.com \
  --project=$PROJECT_ID --quiet

# ── Artifact Registry ────────────────────────────────────────────────
echo ""
echo "--- Artifact Registry ---"
gcloud artifacts repositories describe $REPO_NAME \
  --location=$REGION --project=$PROJECT_ID 2>/dev/null || \
gcloud artifacts repositories create $REPO_NAME \
  --repository-format=docker \
  --location=$REGION \
  --project=$PROJECT_ID --quiet

REGISTRY="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME"

# ── GCS bucket for persistent data ───────────────────────────────────
echo ""
echo "--- Storage bucket ---"
gsutil ls -b "gs://$BUCKET_NAME" 2>/dev/null || \
gsutil mb -p $PROJECT_ID -l $REGION "gs://$BUCKET_NAME"

# ── Build combined container ─────────────────────────────────────────
echo ""
echo "--- Building combined container ---"
gcloud builds submit "$SCRIPT_DIR" \
  --tag "$REGISTRY/$SERVICE_NAME" \
  --project=$PROJECT_ID \
  --timeout=1200 \
  --machine-type=e2-highcpu-8 \
  --dockerfile=Dockerfile.combined

# ── Deploy to Cloud Run ──────────────────────────────────────────────
echo ""
echo "--- Deploying ---"
gcloud run deploy $SERVICE_NAME \
  --image "$REGISTRY/$SERVICE_NAME" \
  --region $REGION \
  --project $PROJECT_ID \
  --platform managed \
  --allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 300 \
  --min-instances 1 \
  --max-instances 3 \
  --execution-environment gen2 \
  --add-volume name=data-vol,type=cloud-storage,bucket=$BUCKET_NAME \
  --add-volume-mount volume=data-vol,mount-path=/data \
  --port 3000 \
  --set-env-vars "ADMIN_PASSWORD=$ADMIN_PASSWORD,JWT_SECRET=$JWT_SECRET,DATABASE_URL=/data/knowledge.db,UPLOADS_DIR=/data/uploads,USE_REDIS=false,CORS_ORIGINS=[\"*\"],PORT=3000"

SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
  --region $REGION --project $PROJECT_ID \
  --format='value(status.url)')

# ── Custom domain mapping (if DOMAIN is set) ─────────────────────────
if [ -n "$DOMAIN" ]; then
  echo ""
  echo "--- Mapping domain: $DOMAIN ---"
  gcloud run domain-mappings create \
    --service $SERVICE_NAME \
    --domain "$DOMAIN" \
    --region $REGION \
    --project $PROJECT_ID 2>/dev/null || \
  echo "Domain mapping already exists or requires manual DNS setup."
  echo "  Add a CNAME DNS record: $DOMAIN -> ghs.googlehosted.com"
fi

echo ""
echo "========================================="
echo "  Deployment complete!"
echo "========================================="
echo "  URL: $SERVICE_URL"
if [ -n "$DOMAIN" ]; then
echo "  Domain: https://$DOMAIN (after DNS propagation)"
fi
echo "========================================="
