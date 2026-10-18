# Backend API and evidence flow

## Evidence bundle

The backend reads only observed telemetry with equipment type code `0408001` for the transformer workflow. Each reading receives a stable `evidence_id` derived from its source file, source record, point ID, measurement code, timestamp, and dataset type. Responses retain source file IDs, point IDs, measurement codes, values, units (unknown in this sample), timestamps, and `data_origin=observed`.

The app assembles the selected asset's snapshot, history, coverage, quality warnings, and descriptive indicators on demand. It does not store telemetry as knowledge chunks. The generated bundle is separate from the maintenance knowledge base.

## Endpoints

| Method and path | Purpose |
|---|---|
| `GET /api/health` | Reports configured telemetry/knowledge sources, target equipment class, decision component, and Gemini setting. |
| `GET /api/assets` | Lists observed distribution transformer assets. |
| `GET /api/assets/{asset_id}/evidence` | Returns measurements, history, provenance, data gaps, and the data-readiness route. `GET /api/assets/{asset_id}` remains a compatibility route. |
| `GET /api/assets/{asset_id}/knowledge?q=...` | Retrieves only reviewed, approved English chunks applicable to the transformer class. Empty knowledge is a normal result. |
| `GET /api/knowledge/search?q=...` | Searches approved English knowledge chunks. |
| `GET /api/assets/{asset_id}/decision` | Returns conservative evidence-readiness routing; it never returns a fault class or failure probability. |
| `POST /api/assets/{asset_id}/explain` | Legacy Gemini summary through Vertex AI when enabled. Disabled in the demo deployment; separate from the context-grounded review route. |
| `POST /api/assets/{asset_id}/gemini-review` | Context-grounded Gemini review of the validated context/evidence bundle and deterministic draft only (M1.25). Gated by `ENABLE_GEMINI_REVIEW` (default off); returns structured, server-validated sections or a fail-closed abstention. |
| `POST /api/reviews` | Saves a human outcome, rationale, and evidence/chunk IDs into the separate review table. |
| `GET /api/defects?q=...` | Browses the contact-free 75-row mixed-equipment reference sample only (`sample_status=raw_reference_sample_only`, `approved_guidance=false`, `translation_status=draft_unreviewed`). That sample is not transformer guidance and must never be presented as approved diagnostic evidence. |
| `GET /api/agent/context/{asset_id}` | Read-only deterministic evidence bundle for the agent (M0). Gated by `ENABLE_AGENT_DRAFT` (default off). |
| `GET /api/agent/draft/{asset_id}` | Read-only deterministic draft bound to the bundle digest (M0). Gated by `ENABLE_AGENT_DRAFT` (default off). |

## Agent draft (M0, gated, read-only)

Both `/api/agent/*` routes return `404` unless `ENABLE_AGENT_DRAFT` is true; the
flag is reported by `GET /api/health` as `agent_draft_enabled`. They make no
model, BigQuery, or Vertex call and write nothing.

`GET /api/agent/context/{asset_id}` returns the versioned context bundle
(`agent_context.v1`): evidence items with exact source ids, source hashes, and
locators; machine-recorded gaps; register-backed unresolved questions; and a
`bundle_digest_sha256`. `GET /api/agent/draft/{asset_id}` returns the strict
draft (`agent_draft.v1`) produced by the deterministic reference producer. Every
claim is a direct evidence copy or a reproducible calculation over cited
evidence ids; every suggestion is a register-backed documentary verification
question. On any contract violation the route fails closed and returns a
`rejected` result with no claims. The contract is documented in
`docs/m0_agent_contract.md`.

The AI Smart Assistant calls these read-only routes after the user presses Send,
then calls the existing Gemini review endpoint. It does not create a separate
agent or evidence interpretation path and does not store chat history.

Packaging: the runtime depends only on the already-shipped
`docs/evidence_register.json`. The acquisition package is not read at runtime
(its only relevant field, `requests[].evidence_id`, equals the register id), and
the agent JSON schemas are documentation-only because validation is pure Python.

## Context-grounded Gemini review (M1.25, gated, read-only)

`POST /api/assets/{asset_id}/gemini-review` returns `404` unless
`ENABLE_GEMINI_REVIEW` is true; the flag is reported by `GET /api/health` as
`gemini_review_enabled` and is independent of the legacy `ENABLE_GEMINI` flag
(which is unchanged). The route writes nothing, opens no review case, and changes
no evidence status.

The model receives only a validated projection of the context bundle, the asset
evidence profile, and the deterministic evidence review: the selected asset's
recorded context with field-level provenance and review status; the evidence link
with its verification status and exact-ID caveat; the deterministic facts and
calculations with their computations; approved, reviewed, source-hashed knowledge
chunks only; and the explicit gaps, unknowns, and prohibited conclusions.

The model returns JSON with a `direct_answer` and three sections
(`supporting_points`, `unknowns`, `human_checks`); every item must cite supplied
ids. Only the facts and gaps relevant to the question are sent (bounded selection),
with the deterministic draft summary and the full ids/citations/provenance of each
selected item; with no question the full validated set is sent. The response is
validated server-side: an unknown or fabricated id, a citation that does not
support the statement, a number that conflicts with the deterministic evidence, a
prohibited conclusion, or an inferred unit/timezone is rejected or omitted. A
statement supported by an approved reference must quote the cited chunk verbatim.
If the `direct_answer` cannot be matched to the supplied evidence the route returns
a safe abstention, and unvalidated model text is never shown. When no approved
relevant knowledge is available the response marks it (`no_approved_knowledge`) and
marks `no_relevant_reference`, and the model may only restate the observed evidence
and list gaps. Authentication uses the Gemini Developer API. Cloud Run receives
`GEMINI_API_KEY` from Secret Manager at runtime; the demo sets
`ENABLE_GEMINI_REVIEW=true` and keeps the legacy Vertex route disabled. The default
`flash-only` profile calls Gemini 3.8 Flash only with no fallback, so Gemini 4 Argon
is never called; the paid `argon-first`/`flash-first` profiles add Argon explicitly.
The response includes the actual `model` and `model_route` (`primary`, `fallback`,
or `failed`). See `docs/m1_25_gemini_review.md` and
`docs/m1_25_demo_deployment.md`.

The endpoint accepts an optional JSON `question` string (1–500 characters). The
question drives the deterministic evidence selection and is answered directly, but
cannot change the evidence policy. The Smart Assistant and Equipment Status buttons
both use this endpoint and the same runtime `GEMINI_API_KEY` Secret Manager binding.
The deterministic agent routes themselves do not use an API key or call a model.

## BigQuery tables

- `telemetry_observation`: observed telemetry only; rows are tagged with `data_origin=observed`.
- `knowledge_chunk`: English text with source locator, language, translation status, review status, and equipment applicability. Retrieval is restricted to `language=en`, `translation_status=reviewed`, `status=approved`, and the target equipment class or `all`.
- `human_review`: reviewer outcome and rationale, kept separate from telemetry and knowledge content.

Deployment grants the Cloud Run identity read access for telemetry/knowledge and write access only to the human-review table. Do not load synthetic scenarios into `telemetry_observation`; future scenario tables must carry `data_origin=synthetic` and keep expected labels separate from model inputs.

## Decision boundary

`data_readiness_rules_v1` checks equipment scope, oil-immersed confirmation, units, history presence, and required measurement codes. Because oil construction and measurement units are unconfirmed, the current sample routes to human review. It does not apply electrical fault thresholds. LAYA and scenario evaluation remain future additions and must not be described as active features.
