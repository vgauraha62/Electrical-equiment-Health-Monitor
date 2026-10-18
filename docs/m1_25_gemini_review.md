# M1.25 — Context-grounded Gemini evidence review (local, read-only, gated)

Recorded: 2026-10-18. Local implementation and verification only. **No model
call, cloud call, upload, IAM change, API enablement, resource creation, or
deployment was performed in this milestone.** The demo deployment procedure is
prepared but not executed.

## Purpose

Connect Gemini to the already validated, evidence-grounded asset review
flow. For a selected transformer, the assistant answers the user's question in
natural, concise language using only the validated context and deterministic
evidence the app already prepared, and preserves the deterministic evidence agent
as the source of truth. It separates ledger context, telemetry observations,
approved references, evidence links, and unknowns, and it never invents a missing
fact or turns an exact-ID match into proof of physical identity.

The capability is a distinct, off-by-default feature flag so enabling it never
changes the meaning or behavior of the existing `/api/assets/<id>/explain`
endpoint.

## Feature flag and route

- New flag `ENABLE_GEMINI_REVIEW` (default `false`), reported by `GET /api/health`
  as `gemini_review_enabled`. It is independent of `ENABLE_GEMINI`, which is
  unchanged.
- New route `POST /api/assets/<asset_id>/gemini-review`. It returns `404` when the
  flag is off, `404` when the asset has no telemetry evidence in the target class,
  and `200` otherwise.
- Read-only: the route writes nothing, opens no review case, changes no evidence
  status, and reads no BigQuery table.

## What the model receives (validated input)

`app/gemini_review.py::build_review_inputs` builds the model input as a pure
projection of three already-validated artifacts and then re-checks itself against
them (`validate_review_input`):

- the context bundle (`app/agent_context.py::build_context_bundle`);
- the asset evidence profile (`app/asset_evidence.py::build_asset_evidence`);
- the deterministic evidence review (`app/evidence_review.py::build_evidence_review`).

The input package contains only:

- the selected asset's validated context and field-level provenance/review status
  (recorded/absent, usable-for-context, blocked reason, translation/review status);
- the evidence link with its verification status and the exact-ID caveat;
- the deterministic facts and calculations with their reproducible computations
  and citation-level support audit;
- approved, applicable, source-hashed knowledge chunks only (approved + English +
  reviewed + matching equipment type code), together with their supplied content;
- the explicit gaps, register questions, limitations, unresolved conclusions,
  prohibitions, and the unconfirmed units/timezone status.

The package records binding digests of the bundle, the profile, and the review. A
draft/unreviewed chunk, a drifted digest, or a foreign claim cannot enter the
input: `validate_review_input` fails closed. Model pretraining is never passed as
a source and is never treated as one.

## Question-driven selection (never the whole claim set)

The Smart Assistant answers one question about one asset, so it is not handed the
whole validated claim set. `select_relevant_evidence` chooses the facts, gaps,
register questions, checks, and approved references relevant to the question using
deterministic lexical overlap plus a small intent vocabulary (for example
"missing"/"gap" selects the documented gaps and the register questions, "summarize"
selects a bounded baseline). Selection:

- is bounded (`MAX_SELECTED_FACTS` = 12, plus capped gaps, register questions,
  checks, and references), ordered, and reproducible;
- copies each item **unchanged**, so claim IDs, citations, source locators
  (`locator`, `source_sha256`, `provenance_status`), and review status are preserved
  exactly;
- keeps the deterministic draft summary always, with `fact_count_total` and
  `fact_count_supplied` so the model and the UI know what was sent;
- falls back to a bounded deterministic baseline when the question matches no
  supplied fact, and reports `question_matched_supplied_facts = false` so the answer
  can say the question is not supported by the supplied evidence.

With **no** question (the Equipment Status panel), the full validated set is kept:
that action is a deliberate full review of one asset, not the assistant default. The
model's citations are resolved against the same selection, so a fact that was not
supplied cannot be cited. `explain` is unchanged in shape and remains read-only.

## What the model returns (validated output)

The model returns one JSON object (`gemini_answer.v2`) with exactly:
`output_version`, `asset_id`, `direct_answer`, `supporting_points`, `unknowns`,
`human_checks`, `abstained`, `abstention_reason`. `direct_answer` is a single item;
the other three are lists of the same item shape. Each item is
`{text, citations: [{kind, id}], quotes: [string]}`.

The prompt requires `output_version` to be the exact literal `gemini_answer.v2`,
and `MODEL_OUTPUT_SCHEMA` constrains the field to that single value
(`"enum": ["gemini_answer.v2"]`). The validator still compares the received value
strictly against the same constant, so a different version, different case, or
trailing whitespace is rejected (`bad_value`) — the schema is a hint to the model,
not a relaxation of the check.

Server-side validation (`validate_model_output`) fails closed:

- **Whole-response rejection** for an empty/malformed/non-JSON response, a wrong
  or unknown top-level shape, an unknown output version, an asset mismatch, a
  non-boolean abstention, too many items, or an abstention that carries items.
- **Whole-response abstention** when the `direct_answer` itself cannot be matched
  to the supplied evidence (`direct_answer_unsupported`): the material statement is
  never shown as unvalidated model text.
- **Per-item omission** (reported in `omitted` with a stable reason code) for:
  - an unknown or fabricated citation id or kind (`unresolved_citation`,
    `unknown_key`);
  - a citation that resolves but does not support the statement: a supporting point
    must cite a supplied deterministic claim or an approved reference, a direct
    answer must rest on evidence or a documented gap, and any directly cited evidence
    must belong to the cited claim (`unsupported_claim`, `irrelevant_citation`);
  - a statement whose numbers are not recorded values of the cited evidence or
    whose measurement code is outside the cited claim (`unsupported_number`,
    `conflicting_evidence`);
  - a prohibited conclusion or an asserted identity/timezone/unit
    (`prohibited_conclusion`, `identity_conclusion`, `timezone_inference`,
    `unit_inference`);
  - any item that cites an approved reference without a verbatim quote from the
    cited chunk (`unsupported_reference`).

Section kinds are enforced: `supporting_points` may cite a claim, an approved
reference, or its evidence; `unknowns` must cite a gap, register question, or
dimension; `human_checks` must cite a supplied non-operational check.

`citation id exists` is never treated as proof of support. A statement is supported
only when it is anchored to a supplied deterministic claim whose recorded result
agrees with the text; a reference-supported statement is supported only by a
verbatim quote copied from the cited chunk. An unsupported `direct_answer` produces
an **abstention**, not prose, and unvalidated model text is never shown.

## No-approved-knowledge state

When no approved relevant chunk is supplied (the current default: there is no
approved-chunk store), the input package has zero references and the response
sets `no_approved_knowledge = true` with the deterministic
`knowledge_state.statement`, and `no_relevant_reference = true` because no
approved engineering reference applies. The model may only restate the observed
evidence and list gaps; it makes no engineering diagnosis, failure prediction,
maintenance instruction, or unsupported recommendation, and the reference section
stays empty. The UI states this plainly, so a question such as *"What evidence is
missing?"* is answered honestly: the missing documentation (units/scaling, timestamp
timezone) and the register questions are listed, and the answer says no approved
engineering reference exists.

## Authentication and secrets

The model is called through the Gemini Developer API using
`genai.Client(api_key=...)`. The default `flash-only` profile calls
`gemini-3.8-flash` only, with the fallback disabled (`GEMINI_FALLBACK_MODEL=none`),
so a Flash failure returns the safe model-error response and Gemini 4 Argon is
never called. The paid `argon-first`/`flash-first` profiles call `gemini-4-argon`
as primary or fallback. The actual model and whether the fallback was used are
returned as `model` and `model_route`. A model response that fails citation
validation is rejected as-is; the fallback is for provider call failures, not
another attempt to bypass output validation.

Cloud Run receives `GEMINI_API_KEY` only through a Secret Manager binding. The
key is not part of source, `.env`, deployment arguments, logs, or the API
response. Enter the key yourself in the Secret Manager Console UI; if it is not
stored there, Gemini stays disabled in the deployment. Use a key from a
Free-tier AI Studio project with no billing configured — not the
`keen-phalanx-495303-j0` project, which showed "Prepay required". The runtime
identity needs `roles/secretmanager.secretAccessor` on the key secret. These
calls are billed under the Gemini Developer API; Google AI Pro and Google Cloud
trial credits do not cover API usage, and the Gemini 3.8 Flash free tier has its
own separate terms and limits. Tests use mocked provider responses and make no
live model calls.

The model runs only on an explicit user action: the route is
`POST /api/assets/<asset_id>/gemini-review`, and the browser calls it only when
the user clicks the equipment review button, starts a monitoring review, or
presses Send in AI Smart Assistant. Nothing calls Gemini on page load or
automatically (`static/app.js` has no scheduled poll or keepalive). The Smart
Assistant first loads the read-only deterministic context and draft, then calls
the same review route with an optional question (maximum 500 characters). That
question can guide emphasis but cannot override the evidence policy. Both UI
paths use the same runtime `GEMINI_API_KEY` secret; the deterministic agent does
not make model calls or use a key.

On the Equipment Status page, **AI summary preview** uses this same route. The
**Generate monitoring review** button first loads deterministic monitoring
results, then requests the same context-grounded Gemini review when
`gemini_review_enabled` is true. It displays the model result in the AI summary
panel, or a clear failure/disabled message while leaving the deterministic
monitoring results available. The browser reuses an in-flight or successful
response for the same asset during the page session so using both controls does
not needlessly duplicate a model request. Failed model results are not cached,
so the user can retry.

## Failure diagnostics (redacted)

A provider failure used to collapse into a generic `rejected · model_error`, so
the deployed cause was invisible. Every failed call now records a redacted
`diagnostics` object and logs it at `WARNING`:

- `error_class` / `provider_error_class` — exception class names only. The
  adapter wraps a provider error in `ModelRouteError`, so the cause chain is
  walked to the innermost exception that carries a status.
- `http_status` — the HTTP status code when one is present.
- `provider_status` — a short provider status token (for example
  `PERMISSION_DENIED` or `RESOURCE_EXHAUSTED`), validated against a strict
  character/length pattern.
- `category` — a coarse bucket (`unauthenticated`, `permission_denied`,
  `not_found`, `rate_limited`, `timeout`, `dependency_error`, `client_error`,
  `model_route_error`, …).

Only those fields are captured. The request prompt, the model input, the API
key, the exception message, and any response body are never logged or returned.
`model_generated` is `false` whenever no model output was produced: a failed
request or an empty model response. `model_call` stays `true` because a call was
attempted, and `model_route` is `failed`. Citation and claim validation is
unchanged and still fails closed.

## Outcome logging (structured, sanitized)

Every review outcome emits one JSON line through the `app.gemini_review` logger so
it can be found and filtered in Cloud Run Logs Explorer. Both provider failures
(`reason_code = model_error`) and post-generation validation outcomes are logged:

    {"event": "gemini_review_outcome", "status": "...", "reason_code": "...",
     "reason_message": "...", "model": "...", "model_route": "...",
     "output_version_expected": "gemini_answer.v2",
     "output_version_received": "...", "output_version_matched": true,
     "diagnostics": {...}}   // failures only

- `status` — `explained` / `abstained` / `rejected`, logged at `INFO` for a clean
  outcome (an accepted answer or a model abstention) and `WARNING` for anything the
  app refused, including an unsupported `direct_answer`.
- `reason_code` / `reason_message` — the same values the API returns. The message
  is sanitized (quoted/bracketed spans become `[redacted]`, whitespace collapsed,
  length capped) so model-authored key names cannot leak.
- `model` / `model_route` — the model actually called and whether the fallback ran.
- `output_version_expected` / `output_version_received` / `output_version_matched` —
  the expected literal, the received value kept only when it is a short
  `[A-Za-z0-9_.-]{1,64}` token (`invalid` otherwise), and whether they matched.
- `diagnostics` — the redacted provider fields above, on failure only.

Prompts, user questions, asset/evidence content, API keys, and raw model output are
never logged. A message-only handler is installed on the module logger only when
the process has no logging handler, so the events reach Cloud Run's collector even
under gunicorn (whose default handler prints only `WARNING` and above).

To find these events, open Cloud Run → `distribution-condition-poc` → **Logs**, or
Logs Explorer, and query:

    resource.type="cloud_run_revision"
    resource.labels.service_name="distribution-condition-poc"
    jsonPayload.event="gemini_review_outcome"
    jsonPayload.status="rejected"

The line is pure JSON, so Cloud Logging parses it into `jsonPayload`; filter on any
field, for example `jsonPayload.reason_code="bad_value"` or
`jsonPayload.output_version_matched=false`. Widen the time range to cover the
review attempt.

## UI

A focused panel is mounted on the selected transformer asset detail. It separates
**deterministic evidence (not model-generated)** from **model-generated
explanation (unverified)**, labels the model status, shows the no-approved-knowledge
state and any abstention plainly, reports omitted items, and escapes every model
string. When the flag is off the panel says so.

Both the panel and the Smart Assistant render the accepted **direct answer first**,
then the evidence behind it, the unknowns and evidence gaps, and the checks a person
could verify. Every citation is shown as a visible, usable `kind:id` token. The
assistant also states plainly when no approved engineering reference applies and how
many of the deterministic facts were relevant to the question. Model text is only
ever the accepted `direct_answer` and accepted `sections` items; a rejected or
omitted item is never rendered. Step-by-step reasoning is neither requested nor
shown.

The status line separates three states, driven by the response's `model_generated`
and `explanation_accepted` flags:

- **Gemini returned nothing** (`model_generated` false): "the model request failed,
  so no model output was produced", with the redacted category/status, under
  **Model output: none (request failed)**.
- **Gemini returned output the app did not accept** (`model_generated` true,
  `explanation_accepted` false, for example `rejected · bad_value` or an
  unsupported `direct_answer`): "Gemini returned output, but the app accepted no
  explanation/answer from it, so no model text is shown", under **Model output
  withheld (no accepted explanation)**.
- **Gemini returned output the app accepted** (`explanation_accepted` true): the
  answer is attributed to the model, under **Model-generated explanation
  (unverified)**.

Rejected model text is never rendered: the response carries only the accepted
`direct_answer` and `sections` items and omits rejected text, so a failed or
rejected call is never presented as "{model} generated" anything.

## Tests

`tests/test_gemini_review.py` (deterministic, mocked model) covers flag-off
behavior, empty approved knowledge, approved relevant chunks, irrelevant and
fabricated citations, conflicting evidence, unknown units/time/criteria, malformed
output, model errors, and no writes / no review-case creation. It also covers:

- the output-version contract (the prompt and schema require exactly
  `gemini_answer.v2`; a matching version proceeds to item validation and any other
  value — including case or whitespace differences — is rejected);
- question-driven selection (a question sends only the bounded relevant facts, a
  "what is missing" question supplies both documented gaps, and a citation outside
  the selection is refused);
- a section-specific answer shape (`supporting_points`, `unknowns`, `human_checks`)
  and rejection of a wrong kind for a section;
- an unsupported or prohibited `direct_answer` producing a safe abstention rather
  than unvalidated text;
- an honest answer to *"What evidence is missing?"* that cites the documented gaps
  and states that no approved engineering reference applies;
- the model-output-vs-accepted-answer UI distinction with rejected text withheld;
- the structured outcome logging for success, rejection, an unsupported answer, and
  provider failure (including sentinel tests proving prompts, user questions, API
  keys, and model text never appear in a log line).

`tests/test_flash_only_profile.py` covers the no-Argon routing and the safe
model-error result. `tests/test_demo_deployment.py` covers the packaging inspector
and the deploy procedure. `tests/test_browser_e2e.py` drives the browser mock and
checks that the direct answer is rendered first. No live model or cloud call is
made.

## Boundaries preserved

Source datasets, frozen artifacts, `docs/evidence_register.json` contents and
statuses, existing routes, and existing contracts are unchanged. The only route
change is additive (`POST /api/assets/<id>/gemini-review`), the only health change
is additive (`gemini_review_enabled`), and the only endpoint behavior change is
none. The register remains 20 `evidence_pending`, 10 `unverified`, 0 verified, 0
approvals.
