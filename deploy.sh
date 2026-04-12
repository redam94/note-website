#!/bin/bash
set -euo pipefail

# ── Configuration ────────────────────────────────────────────────────
PROJECT_ID="knowledge-base-493120"
REGION="us-central1"
BACKEND_SERVICE="second-brain-api"
FRONTEND_SERVICE="second-brain-web"
REPO_NAME="second-brain"
BUCKET_NAME="${PROJECT_ID}-second-brain-data"

# Required secrets
: "${ADMIN_PASSWORD:?Set ADMIN_PASSWORD env var}"
: "${JWT_SECRET:?Set JWT_SECRET env var}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

echo "=== Deploying Second Brain to GCP Cloud Run ==="
echo "Project: $PROJECT_ID | Region: $REGION"

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

# ── Build & deploy backend ───────────────────────────────────────────
echo ""
echo "--- Building backend ---"
gcloud builds submit "$(dirname "$0")/backend" \
  --tag "$REGISTRY/$BACKEND_SERVICE" \
  --project=$PROJECT_ID --quiet

echo ""
echo "--- Deploying backend ---"
gcloud run deploy $BACKEND_SERVICE \
  --image "$REGISTRY/$BACKEND_SERVICE" \
  --region $REGION \
  --project $PROJECT_ID \
  --platform managed \
  --allow-unauthenticated \
  --memory 1Gi \
  --cpu 1 \
  --timeout 300 \
  --min-instances 1 \
  --max-instances 3 \
  --execution-environment gen2 \
  --add-volume name=data-vol,type=cloud-storage,bucket=$BUCKET_NAME \
  --add-volume-mount volume=data-vol,mount-path=/data \
  --set-env-vars "ADMIN_PASSWORD=$ADMIN_PASSWORD,JWT_SECRET=$JWT_SECRET,DATABASE_URL=/data/knowledge.db,UPLOADS_DIR=/data/uploads,USE_REDIS=false,CORS_ORIGINS=[\"*\"]"

BACKEND_URL=$(gcloud run services describe $BACKEND_SERVICE \
  --region $REGION --project $PROJECT_ID \
  --format='value(status.url)')
echo "Backend: $BACKEND_URL"

# ── Build & deploy frontend ──────────────────────────────────────────
echo ""
echo "--- Building frontend ---"
gcloud builds submit "$(dirname "$0")" \
  --tag "$REGISTRY/$FRONTEND_SERVICE" \
  --project=$PROJECT_ID --quiet

echo ""
echo "--- Deploying frontend ---"
gcloud run deploy $FRONTEND_SERVICE \
  --image "$REGISTRY/$FRONTEND_SERVICE" \
  --region $REGION \
  --project $PROJECT_ID \
  --platform managed \
  --allow-unauthenticated \
  --memory 512Mi \
  --cpu 1 \
  --timeout 60 \
  --min-instances 0 \
  --max-instances 3 \
  --set-env-vars "BACKEND_URL=$BACKEND_URL"

FRONTEND_URL=$(gcloud run services describe $FRONTEND_SERVICE \
  --region $REGION --project $PROJECT_ID \
  --format='value(status.url)')

echo ""
echo "========================================="
echo "  Deployment complete!"
echo "========================================="
echo "  Frontend: $FRONTEND_URL"
echo "  Backend:  $BACKEND_URL"
echo ""
echo "  Map a custom domain:"
echo "    gcloud run domain-mappings create \\"
echo "      --service $FRONTEND_SERVICE \\"
echo "      --domain YOUR_DOMAIN \\"
echo "      --region $REGION \\"
echo "      --project $PROJECT_ID"
echo ""
echo "  Then add a CNAME DNS record:"
echo "    YOUR_DOMAIN -> ghs.googlehosted.com"
echo "========================================="
