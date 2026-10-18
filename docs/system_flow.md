# System, data flow, and agent behaviour

This is the concise technical overview of the PoC: what it is, how data moves
through it, what the "agent" behaviour actually is, and where the limits are. For
endpoints see [`backend_api.md`](backend_api.md); for the assistant contract and
validation rules see [`m1_25_gemini_review.md`](m1_25_gemini_review.md).

## What the product is

An English-language, batch-first, **read-only** workspace for reviewing the
condition evidence of distribution electrical equipment (transformers first). It
assembles, around a single asset:

- equipment-register context (type, voltage, manufacturer, model, dates),
- the recorded measurements with their source trail,
- the evidence gaps that are still open,
- and, on explicit request, a grounded model-assisted answer about that evidence.

It deliberately produces **no diagnosis**, no failure probability, no health
score, and no maintenance instruction. Those are out of scope by design.

## Components

| Layer | Files | Role |
| --- | --- | --- |
| Web/API | `app/main.py`, `static/` | Flask JSON API + single-page dashboard (vanilla JS/CSS). |
| Asset assembly | `app/equipment.py`, `app/asset_evidence.py`, `app/evidence_review.py` | Build per-asset register context, measurements, evidence profile, gaps. |
| Monitoring | `app/monitoring.py`, `app/features.py`, `app/decision.py` | Deterministic descriptive monitoring; data-readiness routing only (no fault classification). |
| Agent context | `app/agent_context.py`, `app/agent_contract.py`, `app/agent_draft.py` | Build the validated context bundle, validate it, and build the deterministic draft. |
| Grounded assistant | `app/gemini_review.py` | Select question-relevant facts, prompt the model, validate the answer, log the outcome. |
| Provenance | `app/provenance.py`, `data/output/manifest.json` | Per-file SHA-256 for the telemetry exports; source locators on evidence items. |
| Review store | `app/review_store.py` | File-based, append-only human-review cases (temporary, per instance). |

## Data flow

```
source exports (data/input/telemetry/*.json)      ← 8 English-labelled JSON exports
        │  scripts/ingest.py
        ▼
canonical records                                 ← data/output/canonical_measurements.ndjson
        │  scripts/build_equipment_catalog.py, build_ledger_review_record.py,
        │  statistical_baseline.py, build_lineage_inventory.py
        ▼
packaged artefacts                                ← equipment_catalog.json, ledger_review_record.json,
        │                                            statistical_baseline.json, asset_mapping_snapshot.json,
        │                                            manifest.json (per-file sha256), evidence_register.json
        ▼
per-asset context bundle (agent_context)          ← validated: ids, citations, locators, provenance
        │  agent_draft.build_deterministic_draft
        ▼
deterministic draft (claims + suggestions)        ← the source of truth for the assistant
        │  gemini_review: lexical + intent selection of relevant facts (bounded)
        ▼
prompt (question + draft + selected facts)  ──►  Gemini (flash-only by default)
        │
        ▼
validated answer → response                       ← every material statement checked against the
                                                     supplied evidence; fails closed to abstention
```

Key points:

- The **deterministic draft is the source of truth**. The model is not allowed to
  introduce facts; it may only restate and organise what the draft and the
  selected facts support.
- Only the facts and gaps **relevant to the question** are sent (bounded
  selection), not the whole evidence set. When there is no question, the full
  validated set is used deliberately.
- Every item keeps its identifier, citation, and source locator through the
  whole pipeline.
- Nothing in the read paths writes data: no artifact, evidence status, or register
  entry changes. Tests assert this (`tests/test_evidence_review.py`,
  `tests/test_agent_context.py`).

## The "agent": behaviour and limits

The equipment assistant is **read-only and request-scoped**. It is not an
autonomous agent and has no memory.

- **Enabled by flags.** `ENABLE_GEMINI_REVIEW` gates the grounded assistant
  (default off; the demo deployment turns it on). `ENABLE_GEMINI` gates the
  legacy Vertex summary endpoint and stays off. `ENABLE_AGENT_DRAFT` gates the
  deterministic draft endpoint. `ENABLE_ASSET_MAPPING` gates asset-mapping writes
  and stays off in the demo.
- **User-started only.** The model is called only after an explicit action
  (pressing Send in the assistant, or the review button on the asset page). It is
  never called on page load, never on a timer, and there is no keepalive.
- **No chain-of-thought exposure.** The prompt forbids reasoning traces and the
  response contract has no field for them.
- **Answer contract.** The response carries a single direct answer plus
  `supporting_points`, `unknowns`, and `human_checks`. Supporting points must be a
  claim, reference, or evidence item, with at least one claim or reference;
  unknowns must name a gap, register question, or dimension; any reference
  citation must carry a verbatim quote.
- **Validation, then fail closed.** The server validates the model's structured
  output against the supplied evidence. Fabricated identifiers, irrelevant or
  unsupported citations, altered facts, and unsupported conclusions are rejected.
  If the direct answer cannot be matched to the supplied evidence, the whole
  response becomes a safe **abstention** rather than showing unvalidated text;
  individually unsupported section items are dropped with a reason code.
- **Model profile.** The demo defaults to `flash-only` (Gemini 3.8 Flash, no
  fallback, so the paid model is never called). `argon-first` and `flash-first`
  are explicit paid choices. The free and paid paths are billed separately from
  Google AI Pro and are not covered by Google Cloud trial credits.
- **Failure is reported, not hidden.** A provider or validation failure returns a
  plain model-error state; the existing evidence stays on the page and the system
  does not present the failure as a model-generated result. It never retries on a
  different model.
- **No writes, no dispatch.** The assistant opens no review case, changes no
  evidence status, and triggers no dispatch or approval action.

## Evidence boundaries that are deliberately preserved

- A register-to-telemetry match is reported as an **exact ID-string match**, not
  verified physical identity.
- Missing units and a missing timezone are recorded as **gaps**, not inferred.
- When no approved engineering reference is connected, the assistant says
  **"no approved engineering reference applies"** instead of producing a rule.
- The evidence register shows verified vs pending/unverified status; translation
  completion never promotes an item's engineering status.

## Packaging boundary (Cloud Run vs repository)

Two different filters apply, and they are not the same:

- **Cloud Run upload** is filtered by
  `distribution-condition-poc-backend/.gcloudignore` and inspected by
  `scripts/check_demo_package.py`, which prints the exact file list and byte
  total for the current checkout. It carries the application, its static
  assets, `requirements.txt`, `Procfile`, the eight telemetry inputs, the
  bundled reference sample, the allowlisted `data/output/` artefacts, and
  `docs/evidence_register.json`. Development material — tests, the offline
  pipeline (`scripts/`), `sql/`, `schemas/`, the source archive, `data/en/`,
  and most other docs — never reaches Cloud Run.
- **GitHub repository** is source + tests + documentation, and is larger than
  the upload. It is governed by the repository's own ignore rules, not by
  `.gcloudignore`; the two filters are independent by design.
