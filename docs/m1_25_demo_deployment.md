# M1.25 — Demo deployment procedure (prepared, not executed)

Recorded: 2026-10-18. This procedure is prepared and locally verified only.
**Nothing was deployed, built, uploaded, or changed in Google Cloud in this
milestone.** Deployment is a separate, explicit operator action using the
guarded command below; this document does not claim that deployment has run.

## Target

- Project: `keen-phalanx-495303-j0` (user-confirmed).
- Cloud Run region: `asia-south1` (Mumbai).
- Model API: Gemini Developer API, called with the `GEMINI_API_KEY` from Secret
  Manager. This is billed (or limited by the Flash free tier) separately from
  Google AI Pro and is not covered by Google Cloud trial credits.
- Service: `distribution-condition-poc` (private).
- Runtime identity:
  `condition-poc-runtime@keen-phalanx-495303-j0.iam.gserviceaccount.com`.
- Model profile: defaults to `flash-only`, which calls only Gemini 3.8 Flash
  (`gemini-3.8-flash`) with the fallback disabled (`GEMINI_FALLBACK_MODEL=none`),
  so Gemini 4 Argon is never called. The paid profiles are explicit choices:
  `argon-first` (Argon primary, Flash fallback) and `flash-first` (Flash primary,
  Argon fallback) both call Gemini 4 Argon.
- API key secret: `gemini-api-key`; the value is provided only at runtime via
  Secret Manager and is never stored in the repo or printed by the script. Enter
  the key directly in the Google Cloud Console Secret Manager UI; if no key is
  stored, leave Gemini disabled (see *Gemini API key and Secret Manager*).
- Cost/scaling profile: request-based CPU with default throttling, minimum
  instances `0` (scales to zero), a service-level maximum of `1` instance,
  1 vCPU / 512Mi memory, concurrency `8`, and startup CPU boost (see
  *Cost and scaling*).

This is a *dedicated* procedure. It is deliberately **not** `scripts/deploy.sh`,
which enables APIs, mutates IAM, creates resources and buckets, uploads data,
replaces a BigQuery table, and enables the legacy Gemini endpoint.

## What the procedure does

`scripts/deploy_demo.sh`:

1. Refuses if `BQ_TABLE`/`BQ_KNOWLEDGE_TABLE`/`BQ_REVIEW_TABLE` are set in the
   shell (the demo uses bundled local data only).
2. Runs the packaging gate `scripts/check_demo_package.py --check-snapshot`
   **before packaging**: it applies the current `.gcloudignore`, verifies every
   runtime-required file ships, verifies that no credential, `.env` file, secret,
   archive, development folder, or unexpected data file is included, and runs the
   asset-mapping snapshot freshness check.
3. Sets only the required Gemini API configuration and the new flag, and keeps
   the other integrations off:
   `GOOGLE_CLOUD_PROJECT`, `GOOGLE_CLOUD_LOCATION=asia-south1`,
   `GEMINI_PRIMARY_MODEL`, `GEMINI_FALLBACK_MODEL`, `GEMINI_THINKING_LEVEL`,
   `ENABLE_GEMINI_REVIEW=true`, `ENABLE_GEMINI=false`,
   `ENABLE_AGENT_DRAFT=true`, `ENABLE_ASSET_MAPPING=false`. Under `flash-only`
   the models are `gemini-3.8-flash` and `none`; the app treats `none` as "no
   fallback", so a Flash failure returns the safe model-error response without
   calling any other model. It maps the Secret Manager secret to `GEMINI_API_KEY`
   at runtime. BigQuery is left unset; there are no Cloud Storage uploads and no
   review or asset-mapping writes.
4. Runs read-only preflight checks and **stops with a clear error instead of
   modifying anything** if a required API is not enabled, the runtime service
   account or secret is missing, no enabled secret version exists, or the runtime
   identity lacks `roles/secretmanager.secretAccessor`. It never enables APIs,
   grants roles, creates a service account or secret, reads the secret value, or
   configures IAP or public access.
5. Deploys with an explicit `--project keen-phalanx-495303-j0` (it never relies on
   the active gcloud configuration) from the backend directory only
   (`--source .`), with the specified runtime service account,
   `--no-allow-unauthenticated` (private), `--min-instances 0`, a service-level
   `--max 1`, `--cpu 1`, `--memory 512Mi`, `--concurrency 8`, and `--cpu-boost`.
   It never sets always-allocated CPU (`--no-cpu-throttling`), never pins a warm
   instance, and adds no keepalive, so the service can scale to zero.

## Commands

Package inspection only (no gcloud, safe to run any time):

```
cd distribution-condition-poc-backend
bash scripts/deploy_demo.sh --dry-run
```

The default profile is `flash-only` (Gemini 3.8 Flash only, no fallback, no
Gemini 4 Argon). The paid profiles are explicit choices: `MODEL_PROFILE=argon-first`
or `MODEL_PROFILE=flash-first`. Inspect any of them without deploying, for
example `MODEL_PROFILE=argon-first bash scripts/deploy_demo.sh --dry-run`.

The planned deployment command printed by the dry run is:

```
gcloud run deploy distribution-condition-poc \
  --project keen-phalanx-495303-j0 \
  --source . --region asia-south1 \
  --service-account condition-poc-runtime@keen-phalanx-495303-j0.iam.gserviceaccount.com \
  --no-allow-unauthenticated --min-instances 0 --max 1 \
  --cpu 1 --memory 512Mi --concurrency 8 --cpu-boost \
  --set-env-vars GOOGLE_CLOUD_PROJECT=keen-phalanx-495303-j0,GOOGLE_CLOUD_LOCATION=asia-south1,GEMINI_PRIMARY_MODEL=gemini-3.8-flash,GEMINI_FALLBACK_MODEL=none,GEMINI_THINKING_LEVEL=medium,ENABLE_GEMINI_REVIEW=true,ENABLE_GEMINI=false,ENABLE_AGENT_DRAFT=true,ENABLE_ASSET_MAPPING=false \
  --set-secrets GEMINI_API_KEY=gemini-api-key:latest \
  --quiet
```

`--source .` sends the filtered source package — including the explicitly
allowlisted demo data — to Cloud Build. There is no separate Cloud Storage or
BigQuery data upload.

## Cost and scaling (low-cost, private demo)

The deployment uses a scale-to-zero shape suitable for a small demo:

- **Request-based CPU** (Cloud Run default throttling): CPU is allocated only
  while the container serves a request. The script never passes
  `--no-cpu-throttling`, so CPU is never always-allocated.
- **Minimum instances `0`**: the service scales to zero when idle, so there is no
  idle instance cost.
- **Service-level maximum `1` instance** (`--max 1`, not the per-revision
  `--max-instances`): capacity is capped at one container. This is a
  *service-level* setting applied across revisions. It limits capacity and is
  **not** a hard billing cap: an in-flight request, a brief surge, or a
  deployment can still consume the allotted resources, and Cloud Run does not
  guarantee the cap in every surge or deployment scenario.
- **1 vCPU / 512Mi**: the app runs a single gunicorn worker with 8 threads
  (`Procfile`) and loads only the ~4.4 MB packaged data — no heavy compute or
  large in-memory working set — so 512Mi is comfortable. Raise it only if logs
  show out-of-memory restarts.
- **Concurrency `8`**: matches the 8 gunicorn threads. With `--max 1` the service
  handles roughly 8 simultaneous requests; a larger burst briefly queues at the
  container or returns `429`/`503`. If the demo needs more parallelism, raise
  concurrency and gunicorn threads together.
- **Startup CPU boost** (`--cpu-boost`): allocates extra CPU during startup to
  shorten cold starts. It is billed only during startup and **does not keep the
  service warm**; the service still scales to zero.
- **No keepalive**: there is no scheduled ping or background poll in the app
  (`static/app.js` has no `setInterval`), so nothing prevents scale-to-zero.

These settings are a starting point, not a billing guarantee. Review actual
usage in Cloud Billing and Cloud Run metrics after the demo.

## Gemini API key and Secret Manager

- Cloud Run reads the key at runtime only through the Secret Manager reference
  `--set-secrets GEMINI_API_KEY=gemini-api-key:latest`. The key is never in the
  source, a committed or local `.env`, the deployment output, or the Cloud Build
  package (`.gcloudignore` denies `.env*`, `secrets/`, and credentials last).
- **Enter the key yourself in the Google Cloud Console Secret Manager UI**
  (Security → Secret Manager → create/rotate the `gemini-api-key` secret). Do not
  send or paste the key to any assistant or into chat.
- If you do **not** store the key in Secret Manager, leave Gemini disabled on the
  deployed app: a key that exists only on your computer is unavailable to Cloud
  Run. With `ENABLE_GEMINI_REVIEW=false` the review panel shows "not enabled in
  this deployment".
- Use a key from an AI Studio project **confirmed to be on the Free tier with no
  billing configured**. Do not use a key from the `keen-phalanx-495303-j0` AI
  Studio project, which showed "Prepay required". Google Cloud free-trial credits
  do **not** cover Gemini API charges.
- The model is called only after an explicit user action (clicking "Generate
  grounded explanation") — never on page load or automatically.

To deploy after the listed project prerequisites are ready, run:

```
cd distribution-condition-poc-backend
ALLOW_DEMO_DEPLOYMENT=true bash scripts/deploy_demo.sh
```

## Prerequisite permissions (must already exist; the procedure will not create them)

- An authenticated account with permission to deploy Cloud Run from source
  (Cloud Run Developer/Admin, Cloud Build, Artifact Registry, and
  `iam.serviceAccounts.actAs` on the runtime service account).
- Enabled APIs in `keen-phalanx-495303-j0`:
  `run.googleapis.com`, `cloudbuild.googleapis.com`,
  `artifactregistry.googleapis.com`, `secretmanager.googleapis.com`.
- The runtime service account
  `condition-poc-runtime@keen-phalanx-495303-j0.iam.gserviceaccount.com` exists and
  has `roles/secretmanager.secretAccessor` on the `gemini-api-key` secret.
- A Gemini Developer API key from a **Free-tier AI Studio project with no billing
  configured** is stored as an enabled version of the `gemini-api-key` secret
  (entered in the Secret Manager Console). Do not use a key from the
  `keen-phalanx-495303-j0` AI Studio project ("Prepay required"). The `flash-only`
  default uses the free tier; the paid Argon profiles require a separately billed
  paid key. Never put the key in the repository, a shell command, or `.env`.
- The demo's Cloud Run service stays private; grant `roles/run.invoker` (or
  configure IAP) to the intended accounts separately after deployment.

## Reaching the demo URL (private service)

The service is deployed with `--no-allow-unauthenticated`, so the `run.app` URL
printed after deployment
(`gcloud run services describe distribution-condition-poc --region asia-south1 --format='value(status.url)'`)
returns `403` unless the caller presents a Google identity token. This procedure
does not make the service public and does not configure IAP; there is no approved
public or IAP access method recorded in this repository.

For a teammate or judge to use the demo, do the following separately and only
with approval:

1. Grant the named human accounts or a Google Group `roles/run.invoker` on the
   service, for example
   `gcloud run services add-iam-policy-binding distribution-condition-poc --region asia-south1 --member='user:NAME@example.com' --role='roles/run.invoker'`.
2. They authenticate with their own Google identity and call the URL with an
   identity token, for example
   `curl -H "Authorization: Bearer $(gcloud auth print-identity-token)" "$URL/"`,
   or open it through an authenticated local proxy with
   `gcloud run services proxy distribution-condition-poc --region asia-south1 --port 8080`
   and browse `http://localhost:8080`.
3. If a browser demo for a wider audience is required, front the private service
   with an approved method (for example an internal load balancer with IAP) as a
   separate, reviewed change.

Decide the access method with the teammate and judges before the demo and record
it here.

## Deployment readiness checklist

1. Confirm the four required APIs are enabled (this can incur Google Cloud
   charges under the linked billing account; Cloud free-trial credits cover
   eligible Cloud services but **not** Gemini API usage).
2. Confirm the runtime service account exists.
3. In the Secret Manager Console, create the `gemini-api-key` secret with a key
   from a Free-tier AI Studio project (never the keen-phalanx project), then grant
   that runtime service account `roles/secretmanager.secretAccessor` on the
   secret. If you skip this, keep `ENABLE_GEMINI_REVIEW=false` so Gemini stays
   disabled.
4. Choose the private-access method for the intended demo users (see *Reaching
   the demo URL*) and grant `roles/run.invoker` to their accounts or group.
5. Run the package inspection and review the effective file list, cost/scaling
   settings, and the exact command.

## Model access and billing note

Gemini Developer API usage is billed separately from Google AI Pro and is **not**
covered by Google Cloud trial credits. The `flash-only` default keeps the demo on
Gemini 3.8 Flash, which has a free tier with its own separate terms, rate limits,
and data-use conditions; read those terms and confirm they fit the demo before
relying on the free tier. Google AI Pro can include Gemini 4 Argon access in the
consumer Gemini app, but does not pay for Gemini API calls, so the paid
`argon-first` and `flash-first` profiles require a separately billed paid API
account and key. This model path does not call Vertex AI.

Official references: [Gemini 4 Argon API guide](https://ai.google.dev/gemini-api/docs/latest-model),
[Gemini API pricing](https://ai.google.dev/gemini-api/docs/pricing),
[Gemini 3.8 Flash model guide](https://ai.google.dev/gemini-api/docs/models/gemini-3.8-flash).
