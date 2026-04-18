#!/bin/bash
# Deploy the ARQ worker service to Cloud Run.
#
# Usage:
#   bash deploy-worker.sh            # first-time: provision Redis + VPC, build, deploy both services
#   bash deploy-worker.sh --update   # code change: rebuild images and redeploy both services only
#
# Prerequisites:
#   • deploy.sh has already been run at least once (main service exists)
#   • .env.live is configured with PROJECT_ID, REGION, etc.
#   • gcloud authenticated: gcloud auth login && gcloud auth configure-docker

set -euo pipefail

REDEPLOY_ONLY=false
for arg in "$@"; do
  [[ "$arg" == "--update" ]] && REDEPLOY_ONLY=true
done

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
ENV_FILE="$SCRIPT_DIR/.env.live"

if [ -f "$ENV_FILE" ]; then
  echo "Loading config from .env.live"
  set -a
  source "$ENV_FILE"
  set +a
else
  echo "WARNING: .env.live not found. Set env vars manually."
fi

# ── Configuration ─────────────────────────────────────────────────────
PROJECT_ID="${PROJECT_ID:-knowledge-base-493120}"
REGION="${REGION:-us-central1}"
SERVICE_NAME="${SERVICE_NAME:-second-brain}"
WORKER_SERVICE="${SERVICE_NAME}-worker"
REPO_NAME="${REPO_NAME:-second-brain}"
BUCKET_NAME="${BUCKET_NAME:-${PROJECT_ID}-second-brain-data}"
REDIS_INSTANCE="${SERVICE_NAME}-redis"
CONNECTOR_NAME="${SERVICE_NAME}-connector"
NETWORK="default"

# Required
: "${ADMIN_PASSWORD:?ADMIN_PASSWORD not set}"
: "${JWT_SECRET:?JWT_SECRET not set}"
ANTHROPIC_API_KEY="${ANTHROPIC_API_KEY:-}"

REGISTRY="$REGION-docker.pkg.dev/$PROJECT_ID/$REPO_NAME"

echo "=== Deploying ARQ Worker to GCP Cloud Run ==="
echo "Project: $PROJECT_ID | Region: $REGION | Worker: $WORKER_SERVICE"

if $REDEPLOY_ONLY; then
  echo "--- Update mode: skipping infrastructure, reading existing Redis config ---"
  REDIS_HOST=$(gcloud redis instances describe $REDIS_INSTANCE \
    --region=$REGION --project=$PROJECT_ID \
    --format='value(host)' 2>/dev/null || echo "")
  REDIS_PORT=$(gcloud redis instances describe $REDIS_INSTANCE \
    --region=$REGION --project=$PROJECT_ID \
    --format='value(port)' 2>/dev/null || echo "6379")
  if [ -z "$REDIS_HOST" ]; then
    echo "ERROR: Redis instance '$REDIS_INSTANCE' not found. Run without --update first."
    exit 1
  fi
  REDIS_URL="redis://$REDIS_HOST:$REDIS_PORT"
  echo "Redis: $REDIS_URL"
else
  # ── 1. Enable APIs ──────────────────────────────────────────────────
  echo ""
  echo "--- Enabling APIs ---"
  gcloud services enable \
    redis.googleapis.com \
    vpcaccess.googleapis.com \
    --project=$PROJECT_ID --quiet

  # ── 2. Cloud Memorystore Redis ──────────────────────────────────────
  echo ""
  echo "--- Cloud Memorystore Redis ---"
  if gcloud redis instances describe $REDIS_INSTANCE \
       --region=$REGION --project=$PROJECT_ID &>/dev/null; then
    echo "Redis instance '$REDIS_INSTANCE' already exists"
  else
    echo "Creating Redis instance (this takes ~5 minutes)..."
    gcloud redis instances create $REDIS_INSTANCE \
      --size=1 \
      --region=$REGION \
      --tier=BASIC \
      --network=$NETWORK \
      --project=$PROJECT_ID \
      --quiet
  fi

  REDIS_HOST=$(gcloud redis instances describe $REDIS_INSTANCE \
    --region=$REGION --project=$PROJECT_ID \
    --format='value(host)')
  REDIS_PORT=$(gcloud redis instances describe $REDIS_INSTANCE \
    --region=$REGION --project=$PROJECT_ID \
    --format='value(port)')
  REDIS_URL="redis://$REDIS_HOST:$REDIS_PORT"
  echo "Redis: $REDIS_URL"

  # ── 3. VPC Serverless Connector ─────────────────────────────────────
  echo ""
  echo "--- VPC Serverless Connector ---"
  if gcloud compute networks vpc-access connectors describe $CONNECTOR_NAME \
       --region=$REGION --project=$PROJECT_ID &>/dev/null; then
    echo "Connector '$CONNECTOR_NAME' already exists"
  else
    echo "Creating VPC connector..."
    gcloud compute networks vpc-access connectors create $CONNECTOR_NAME \
      --region=$REGION \
      --network=$NETWORK \
      --range=10.8.0.0/28 \
      --min-instances=2 \
      --max-instances=3 \
      --machine-type=f1-micro \
      --project=$PROJECT_ID \
      --quiet
  fi
fi

# ── 4. Build worker image ─────────────────────────────────────────────
echo ""
echo "--- Building worker image ---"
cat > /tmp/cloudbuild-worker.yaml << YAML
steps:
  - name: 'gcr.io/cloud-builders/docker'
    args: ['build', '-t', '\$_IMAGE', '-f', 'Dockerfile.worker', '.']
images: ['\$_IMAGE']
YAML

gcloud builds submit "$SCRIPT_DIR" \
  --config=/tmp/cloudbuild-worker.yaml \
  --substitutions="_IMAGE=$REGISTRY/$WORKER_SERVICE" \
  --project=$PROJECT_ID \
  --timeout=1200 \
  --machine-type=e2-highcpu-8

# ── 5. Deploy worker Cloud Run service ───────────────────────────────
echo ""
echo "--- Deploying worker service ---"

WORKER_ENV="ADMIN_PASSWORD=$ADMIN_PASSWORD"
WORKER_ENV+=",JWT_SECRET=$JWT_SECRET"
WORKER_ENV+=",REDIS_URL=$REDIS_URL"
WORKER_ENV+=",USE_REDIS=true"
WORKER_ENV+=",DATABASE_URL=/data/knowledge.db"
WORKER_ENV+=",UPLOADS_DIR=/data/uploads"
WORKER_ENV+=",DOC_CONCURRENCY=1"
if [ -n "$ANTHROPIC_API_KEY" ]; then
  WORKER_ENV+=",ANTHROPIC_API_KEY=$ANTHROPIC_API_KEY"
fi

gcloud run deploy $WORKER_SERVICE \
  --image "$REGISTRY/$WORKER_SERVICE" \
  --region $REGION \
  --project $PROJECT_ID \
  --platform managed \
  --no-allow-unauthenticated \
  --memory 2Gi \
  --cpu 2 \
  --timeout 3600 \
  --min-instances 1 \
  --max-instances 1 \
  --concurrency 1 \
  --execution-environment gen2 \
  --vpc-connector $CONNECTOR_NAME \
  --vpc-egress private-ranges-only \
  --add-volume name=data-vol,type=cloud-storage,bucket=$BUCKET_NAME \
  --add-volume-mount volume=data-vol,mount-path=/data \
  --port 8080 \
  --set-env-vars "$WORKER_ENV"

# ── 6. Update main service with Redis vars + VPC connector ───────────
echo ""
echo "--- Updating main service with Redis config ---"

MAIN_ENV="USE_REDIS=true"
MAIN_ENV+=",REDIS_URL=$REDIS_URL"

gcloud run services update $SERVICE_NAME \
  --region $REGION \
  --project $PROJECT_ID \
  --update-env-vars "$MAIN_ENV" \
  --vpc-connector $CONNECTOR_NAME \
  --vpc-egress private-ranges-only \
  --quiet

echo ""
echo "========================================="
if $REDEPLOY_ONLY; then
  echo "  Update complete!"
else
  echo "  Worker deployment complete!"
fi
echo "========================================="
echo "  Redis:          $REDIS_URL"
echo "  Worker service: $WORKER_SERVICE"
echo "  Main service:   $SERVICE_NAME"
echo ""
echo "  Monitor worker logs:"
echo "    gcloud run services logs read $WORKER_SERVICE \\"
echo "      --region $REGION --project $PROJECT_ID --follow"
echo "========================================="
