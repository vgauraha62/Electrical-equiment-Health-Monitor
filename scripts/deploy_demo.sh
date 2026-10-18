#!/usr/bin/env bash
# Safe demo deployment for the context-grounded Gemini review.
#
# This is a *dedicated, reviewable* procedure for the confirmed demo target. It
# is deliberately NOT the old scripts/deploy.sh: it never enables APIs, never
# changes IAM, never creates resources or buckets, never uploads data to Cloud
# Storage or BigQuery, and never enables the legacy Gemini endpoint or the local
# write features. It refuses to run if a prerequisite is missing rather than
# modifying the environment.
#
# Usage:
#   bash scripts/deploy_demo.sh --dry-run     # inspect the package only (no gcloud)
#   ALLOW_DEMO_DEPLOYMENT=true bash scripts/deploy_demo.sh
#
# Run it from the backend directory. It deploys only this directory's source.
#
# Model profile: defaults to flash-only (Gemini 3.8 Flash only, no fallback, and
# no Gemini 4 Argon). The paid Argon profiles are opt-in and explicit:
#   MODEL_PROFILE=argon-first bash scripts/deploy_demo.sh
#   MODEL_PROFILE=flash-first bash scripts/deploy_demo.sh
#
# Cost/scaling: request-based CPU with default throttling (never always-on CPU),
# min instances 0 (scales to zero when idle), a service-level max of 1 instance
# (--max, not the per-revision --max-instances), 1 vCPU / 512Mi, concurrency 8,
# and startup CPU boost. There is no keepalive or scheduled ping, so the service
# can reach zero instances.
set -euo pipefail

PROJECT_ID="${PROJECT_ID:-keen-phalanx-495303-j0}"
REGION="${REGION:-asia-south1}"
SERVICE="${SERVICE:-distribution-condition-poc}"
RUNTIME_SA="${RUNTIME_SA:-condition-poc-runtime@${PROJECT_ID}.iam.gserviceaccount.com}"
MODEL_PROFILE="${MODEL_PROFILE:-flash-only}"
GEMINI_API_KEY_SECRET="${GEMINI_API_KEY_SECRET:-gemini-api-key}"
# The default profile never calls Gemini 4 Argon. The paid profiles are opt-in.
case "$MODEL_PROFILE" in
  flash-only)
    GEMINI_PRIMARY_MODEL="gemini-3.8-flash"
    GEMINI_FALLBACK_MODEL="none"
    MODEL_PROFILE_NOTE="gemini-3.8-flash only; no fallback, no Gemini 4 Argon"
    ;;
  argon-first)
    GEMINI_PRIMARY_MODEL="gemini-4-argon"
    GEMINI_FALLBACK_MODEL="gemini-3.8-flash"
    MODEL_PROFILE_NOTE="gemini-4-argon primary, gemini-3.8-flash fallback (paid Argon)"
    ;;
  flash-first)
    GEMINI_PRIMARY_MODEL="gemini-3.8-flash"
    GEMINI_FALLBACK_MODEL="gemini-4-argon"
    MODEL_PROFILE_NOTE="gemini-3.8-flash primary, gemini-4-argon fallback (paid Argon)"
    ;;
  *)
    echo "Refusing: unsupported MODEL_PROFILE '$MODEL_PROFILE'. Use flash-only, argon-first, or flash-first." >&2
    exit 2
    ;;
esac
PYTHON="${PYTHON:-python3}"

BACKEND_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$BACKEND_DIR"

if [[ -n "${BQ_TABLE:-}${BQ_KNOWLEDGE_TABLE:-}${BQ_REVIEW_TABLE:-}" ]]; then
  echo "Refusing: BQ_TABLE/BQ_KNOWLEDGE_TABLE/BQ_REVIEW_TABLE are set in this shell." >&2
  echo "The demo runs on bundled local data only. Unset them and retry." >&2
  exit 2
fi

DRY_RUN=false
if [[ "${1:-}" == "--dry-run" || "${1:-}" == "--inspect" ]]; then
  DRY_RUN=true
fi
if [[ "$DRY_RUN" != "true" && "${ALLOW_DEMO_DEPLOYMENT:-}" != "true" ]]; then
  echo "Refusing to deploy. This performs a Cloud Build source upload and a Cloud Run" >&2
  echo "deployment for project ${PROJECT_ID} in ${REGION}." >&2
  echo "Review docs/m1_25_demo_deployment.md, then run:" >&2
  echo "  ALLOW_DEMO_DEPLOYMENT=true bash scripts/deploy_demo.sh" >&2
  echo "For an offline package inspection only, run:" >&2
  echo "  bash scripts/deploy_demo.sh --dry-run" >&2
  exit 2
fi

echo "== Package inspection (current .gcloudignore, snapshot freshness) =="
"$PYTHON" scripts/check_demo_package.py --check-snapshot

ENV_VARS="GOOGLE_CLOUD_PROJECT=${PROJECT_ID}"
ENV_VARS+=",GOOGLE_CLOUD_LOCATION=${REGION}"
ENV_VARS+=",GEMINI_PRIMARY_MODEL=${GEMINI_PRIMARY_MODEL}"
ENV_VARS+=",GEMINI_FALLBACK_MODEL=${GEMINI_FALLBACK_MODEL}"
ENV_VARS+=",GEMINI_THINKING_LEVEL=medium"
ENV_VARS+=",ENABLE_GEMINI_REVIEW=true"
ENV_VARS+=",ENABLE_GEMINI=false"
ENV_VARS+=",ENABLE_AGENT_DRAFT=true"
ENV_VARS+=",ENABLE_ASSET_MAPPING=false"

DEPLOY_CMD=(gcloud run deploy "$SERVICE"
  --project "$PROJECT_ID"
  --source .
  --region "$REGION"
  --service-account "$RUNTIME_SA"
  --no-allow-unauthenticated
  --min-instances 0
  --max 1
  --cpu 1
  --memory 512Mi
  --concurrency 8
  --cpu-boost
  --set-env-vars "$ENV_VARS"
  --set-secrets "GEMINI_API_KEY=${GEMINI_API_KEY_SECRET}:latest"
  --quiet)

echo
echo "== Planned deployment =="
echo "  project:        ${PROJECT_ID}"
echo "  region:         ${REGION}"
echo "  service:        ${SERVICE}"
echo "  runtime SA:     ${RUNTIME_SA}"
echo "  model profile:  ${MODEL_PROFILE} (${MODEL_PROFILE_NOTE})"
echo "  API key secret: ${GEMINI_API_KEY_SECRET} (value not displayed)"
echo "  source:         ${BACKEND_DIR} (this directory only, via .gcloudignore)"
echo "  env vars:       ${ENV_VARS}"
echo "  access:         private (no public access; configure IAP separately if desired)"
echo "  scaling:        request-based CPU, min instances 0, service-level max 1 instance"
echo "  resources:      1 vCPU, 512Mi memory, concurrency 8, startup CPU boost"
echo "  BigQuery:       unset (bundled sample data only)"
echo "  upload:         filtered source package (allowlisted demo data included) is sent to Cloud Build; no separate Cloud Storage or BigQuery data upload"
echo "  command:        ${DEPLOY_CMD[*]}"

if [[ "$DRY_RUN" == "true" ]]; then
  echo
  echo "Dry run complete. No gcloud command was executed."
  exit 0
fi

echo
echo "== Preflight (read-only; nothing is created or granted) =="
command -v gcloud >/dev/null 2>&1 || {
  echo "gcloud is not installed or not on PATH." >&2; exit 3; }

gcloud projects describe "$PROJECT_ID" >/dev/null 2>&1 || {
  echo "Cannot access project ${PROJECT_ID}. Check the active account and permissions." >&2
  exit 3; }

REQUIRED_APIS=(run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com secretmanager.googleapis.com)
ENABLED_APIS="$(gcloud services list --project "$PROJECT_ID" --enabled --format='value(config.name)' 2>/dev/null || true)"
MISSING_APIS=()
for api in "${REQUIRED_APIS[@]}"; do
  grep -qx "$api" <<<"$ENABLED_APIS" || MISSING_APIS+=("$api")
done
if (( ${#MISSING_APIS[@]} )); then
  echo "Refusing: required APIs are not enabled: ${MISSING_APIS[*]}" >&2
  echo "Enable the required APIs through your project setup; this procedure will not change them." >&2
  exit 3
fi

gcloud iam service-accounts describe "$RUNTIME_SA" --project "$PROJECT_ID" >/dev/null 2>&1 || {
  echo "Refusing: runtime service account ${RUNTIME_SA} does not exist." >&2
  echo "Create it and grant Secret Manager access yourself; this procedure will not." >&2
  exit 3; }

gcloud secrets describe "$GEMINI_API_KEY_SECRET" --project "$PROJECT_ID" >/dev/null 2>&1 || {
  echo "Refusing: Secret Manager secret ${GEMINI_API_KEY_SECRET} does not exist." >&2
  echo "Create it with a Gemini API key (free-tier key for flash-only; paid key for Argon);" >&2
  echo "this procedure will not read or create secrets." >&2
  exit 3; }

SECRET_VERSION="$(gcloud secrets versions list "$GEMINI_API_KEY_SECRET" \
  --project "$PROJECT_ID" --filter='state=ENABLED' --limit=1 --format='value(name)' 2>/dev/null || true)"
if [[ -z "$SECRET_VERSION" ]]; then
  echo "Refusing: secret ${GEMINI_API_KEY_SECRET} has no enabled version." >&2
  exit 3
fi

PROJECT_IAM="$(gcloud projects get-iam-policy "$PROJECT_ID" --format=json 2>/dev/null || echo '{}')"
SECRET_IAM="$(gcloud secrets get-iam-policy "$GEMINI_API_KEY_SECRET" \
  --project "$PROJECT_ID" --format=json 2>/dev/null || echo '{}')"
if ! "$PYTHON" - "$RUNTIME_SA" "$PROJECT_IAM" "$SECRET_IAM" <<'PY'
import json, sys
sa = f"serviceAccount:{sys.argv[1]}"
found = False
for encoded in sys.argv[2:]:
    try:
        policy = json.loads(encoded)
    except json.JSONDecodeError:
        policy = {}
    for binding in policy.get("bindings", []):
        if binding.get("role") == "roles/secretmanager.secretAccessor" and sa in binding.get("members", []):
            found = True
raise SystemExit(0 if found else 1)
PY
then
  echo "Refusing: ${RUNTIME_SA} lacks roles/secretmanager.secretAccessor on the project or secret." >&2
  echo "Grant the narrow secret-level role yourself; this procedure will not change IAM." >&2
  exit 3
fi

echo "Preflight passed: project reachable, APIs enabled, runtime identity and Gemini API secret are ready."
echo
echo "== Deploying =="
"${DEPLOY_CMD[@]}"

echo
gcloud run services describe "$SERVICE" --project "$PROJECT_ID" --region "$REGION" \
  --format='value(status.url)'
echo "Service is private. Grant Cloud Run Invoker (or configure IAP) to the intended accounts."
