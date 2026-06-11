#!/usr/bin/env bash
# Deploy Restaurant GM to Cloud Run: one image, two services.
#
#   ./deploy.sh            build + deploy app service, then worker service
#
# Requirements: gcloud authed, billing-enabled project set, .env present.
# Secrets travel as env vars via a generated env file (gitignored) — the
# MongoDB URI contains commas/ampersands, so --set-env-vars is unsafe.

set -euo pipefail
cd "$(dirname "$0")"

REGION="${REGION:-us-central1}"
PROJECT="$(gcloud config get-value project 2>/dev/null)"
[ -f .env ] || { echo "ERROR: .env missing"; exit 1; }
set -a; source .env; set +a

# Cloud Run reads Vertex creds from the runtime service account (ADC) — no
# API key needed. GOOGLE_CLOUD_LOCATION stays global for gemini-3.5-flash.
cat > .env.cloudrun.yaml <<EOF
MONGODB_CONNECTION_STRING: "${MONGODB_CONNECTION_STRING}"
MONGODB_DB_NAME: "${MONGODB_DB_NAME:-restaurant_gm}"
GOOGLE_GENAI_USE_VERTEXAI: "true"
GOOGLE_CLOUD_PROJECT: "${PROJECT}"
GOOGLE_CLOUD_LOCATION: "global"
EOF

echo "── deploying app service (build happens in Cloud Build) ──"
# --timeout 3600: SSE feed + chat are long-lived connections
# --max-instances 1: chat sessions are in-memory; one instance for the demo
gcloud run deploy restaurant-gm-app \
  --source . --region "$REGION" --allow-unauthenticated \
  --memory 1Gi --cpu 1 --timeout 3600 \
  --min-instances 1 --max-instances 1 \
  --env-vars-file .env.cloudrun.yaml

APP_IMAGE="$(gcloud run services describe restaurant-gm-app --region "$REGION" \
  --format='value(spec.template.spec.containers[0].image)')"
echo "── deploying worker service (same image: $APP_IMAGE) ──"
# --no-cpu-throttling + min 1: change-stream listeners must run continuously
gcloud run deploy restaurant-gm-worker \
  --image "$APP_IMAGE" --region "$REGION" --no-allow-unauthenticated \
  --memory 1Gi --cpu 1 \
  --min-instances 1 --max-instances 1 --no-cpu-throttling \
  --command python --args="-u,-m,plumbing.launcher" \
  --env-vars-file .env.cloudrun.yaml

rm -f .env.cloudrun.yaml
echo
echo "Hosted URL:"
gcloud run services describe restaurant-gm-app --region "$REGION" --format='value(status.url)'
echo
echo "If agent calls fail with 403 on Vertex, grant the runtime SA access:"
echo "  gcloud projects add-iam-policy-binding $PROJECT --role roles/aiplatform.user \\"
echo "    --member serviceAccount:\$(gcloud iam service-accounts list --filter='compute' --format='value(email)' | head -1)"
