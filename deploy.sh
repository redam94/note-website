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

ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

# ── Secret Manager references ────────────────────────────────────────
# All sensitive values live as secrets, not as plaintext env vars. The
# secrets must exist in the project before the first deploy. One-time setup:
#
#   printf '%s' "$YOUR_PASSWORD" | gcloud secrets create admin-password \
#     --replication-policy=automatic --data-file=- --project=$PROJECT_ID
#   printf '%s' "$YOUR_JWT_SECRET" | gcloud secrets create jwt-secret \
#     --replication-policy=automatic --data-file=- --project=$PROJECT_ID
#   gcloud secrets create github-app-private-key \
#     --replication-policy=automatic --data-file=/path/to/app.pem --project=$PROJECT_ID
#   printf '%s' "$YOUR_WEBHOOK_SECRET" | gcloud secrets create github-webhook-secret \
#     --replication-policy=automatic --data-file=- --project=$PROJECT_ID
#
# Then grant the runtime SA access to each:
#   gcloud secrets add-iam-policy-binding <secret> \
#     --member="serviceAccount:$(gcloud projects describe $PROJECT_ID \
#       --format='value(projectNumber)')-compute@developer.gserviceaccount.com" \
#     --role=roles/secretmanager.secretAccessor --project=$PROJECT_ID
#
# To rotate: `gcloud secrets versions add <secret> --data-file=-` (new value
# on stdin). Cloud Run picks it up on the next revision.
ADMIN_PASSWORD_SECRET="${ADMIN_PASSWORD_SECRET:-admin-password}"
JWT_SECRET_SECRET="${JWT_SECRET_SECRET:-jwt-secret}"
GITHUB_APP_PRIVATE_KEY_SECRET="${GITHUB_APP_PRIVATE_KEY_SECRET:-github-app-private-key}"
GITHUB_WEBHOOK_SECRET_SECRET="${GITHUB_WEBHOOK_SECRET_SECRET:-github-webhook-secret}"

# GitHub App integration (optional — install/callback 503 until all three are set)
GITHUB_APP_ID="${GITHUB_APP_ID:-}"
GITHUB_APP_SLUG="${GITHUB_APP_SLUG:-}"

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
cat > /tmp/cloudbuild.yaml << 'YAML'
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '$_IMAGE', '-f', 'Dockerfile.combined', '.']
images: ['$_IMAGE']
YAML

gcloud builds submit "$SCRIPT_DIR" \
  --config=/tmp/cloudbuild.yaml \
  --substitutions="_IMAGE=$REGISTRY/$SERVICE_NAME" \
  --project=$PROJECT_ID \
  --timeout=1200 \
  --machine-type=e2-highcpu-8

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
  --max-instances 1 \
  --execution-environment gen2 \
  --add-volume name=data-vol,type=cloud-storage,bucket=$BUCKET_NAME \
  --add-volume-mount volume=data-vol,mount-path=/data \
  --port 3000 \
  --set-env-vars "DATABASE_URL=/data/knowledge.db,UPLOADS_DIR=/data/uploads,USE_REDIS=false,CORS_ORIGINS=[\"*\"]${GITHUB_APP_ID:+,GITHUB_APP_ID=$GITHUB_APP_ID}${GITHUB_APP_SLUG:+,GITHUB_APP_SLUG=$GITHUB_APP_SLUG}" \
  --update-secrets "ADMIN_PASSWORD=${ADMIN_PASSWORD_SECRET}:latest,JWT_SECRET=${JWT_SECRET_SECRET}:latest${GITHUB_APP_ID:+,GITHUB_APP_PRIVATE_KEY=${GITHUB_APP_PRIVATE_KEY_SECRET}:latest,GITHUB_WEBHOOK_SECRET=${GITHUB_WEBHOOK_SECRET_SECRET}:latest}"

SERVICE_URL=$(gcloud run services describe $SERVICE_NAME \
  --region $REGION --project $PROJECT_ID \
  --format='value(status.url)')

# ── Load balancer setup (for public access + custom domain) ──────────
echo ""
echo "--- Setting up load balancer ---"

# Serverless NEG
gcloud compute network-endpoint-groups describe ${SERVICE_NAME}-neg \
  --region=$REGION --project=$PROJECT_ID 2>/dev/null || \
gcloud compute network-endpoint-groups create ${SERVICE_NAME}-neg \
  --region=$REGION \
  --network-endpoint-type=serverless \
  --cloud-run-service=$SERVICE_NAME \
  --project=$PROJECT_ID 2>/dev/null

# Backend service
gcloud compute backend-services describe ${SERVICE_NAME}-backend \
  --global --project=$PROJECT_ID 2>/dev/null || \
(gcloud compute backend-services create ${SERVICE_NAME}-backend \
  --global \
  --load-balancing-scheme=EXTERNAL_MANAGED \
  --project=$PROJECT_ID && \
gcloud compute backend-services add-backend ${SERVICE_NAME}-backend \
  --global \
  --network-endpoint-group=${SERVICE_NAME}-neg \
  --network-endpoint-group-region=$REGION \
  --project=$PROJECT_ID) 2>/dev/null

# URL map
gcloud compute url-maps describe ${SERVICE_NAME}-urlmap \
  --project=$PROJECT_ID 2>/dev/null || \
gcloud compute url-maps create ${SERVICE_NAME}-urlmap \
  --default-service=${SERVICE_NAME}-backend \
  --project=$PROJECT_ID 2>/dev/null

# Static IP
gcloud compute addresses describe ${SERVICE_NAME}-ip \
  --global --project=$PROJECT_ID 2>/dev/null || \
gcloud compute addresses create ${SERVICE_NAME}-ip \
  --global --project=$PROJECT_ID 2>/dev/null

STATIC_IP=$(gcloud compute addresses describe ${SERVICE_NAME}-ip \
  --global --project=$PROJECT_ID --format='value(address)' 2>&1)

# HTTP proxy + forwarding rule
gcloud compute target-http-proxies describe ${SERVICE_NAME}-http-proxy \
  --project=$PROJECT_ID 2>/dev/null || \
gcloud compute target-http-proxies create ${SERVICE_NAME}-http-proxy \
  --url-map=${SERVICE_NAME}-urlmap \
  --project=$PROJECT_ID 2>/dev/null

gcloud compute forwarding-rules describe ${SERVICE_NAME}-http-rule \
  --global --project=$PROJECT_ID 2>/dev/null || \
gcloud compute forwarding-rules create ${SERVICE_NAME}-http-rule \
  --global \
  --load-balancing-scheme=EXTERNAL_MANAGED \
  --target-http-proxy=${SERVICE_NAME}-http-proxy \
  --address=${SERVICE_NAME}-ip \
  --ports=80 \
  --project=$PROJECT_ID 2>/dev/null

# HTTPS (if domain is set)
if [ -n "$DOMAIN" ]; then
  echo "--- Setting up HTTPS for $DOMAIN ---"

  gcloud compute ssl-certificates describe ${SERVICE_NAME}-cert \
    --global --project=$PROJECT_ID 2>/dev/null || \
  gcloud compute ssl-certificates create ${SERVICE_NAME}-cert \
    --domains=$DOMAIN \
    --global --project=$PROJECT_ID 2>/dev/null

  gcloud compute target-https-proxies describe ${SERVICE_NAME}-https-proxy \
    --global --project=$PROJECT_ID 2>/dev/null || \
  gcloud compute target-https-proxies create ${SERVICE_NAME}-https-proxy \
    --url-map=${SERVICE_NAME}-urlmap \
    --ssl-certificates=${SERVICE_NAME}-cert \
    --global --project=$PROJECT_ID 2>/dev/null

  gcloud compute forwarding-rules describe ${SERVICE_NAME}-https-rule \
    --global --project=$PROJECT_ID 2>/dev/null || \
  gcloud compute forwarding-rules create ${SERVICE_NAME}-https-rule \
    --global \
    --load-balancing-scheme=EXTERNAL_MANAGED \
    --target-https-proxy=${SERVICE_NAME}-https-proxy \
    --address=${SERVICE_NAME}-ip \
    --ports=443 \
    --project=$PROJECT_ID 2>/dev/null
fi

echo ""
echo "========================================="
echo "  Deployment complete!"
echo "========================================="
echo "  Cloud Run: $SERVICE_URL"
echo "  Public IP: $STATIC_IP"
echo "  HTTP:      http://$STATIC_IP"
if [ -n "$DOMAIN" ]; then
echo "  HTTPS:     https://$DOMAIN"
echo ""
echo "  DNS: Set A record for $DOMAIN -> $STATIC_IP"
fi
echo "========================================="
