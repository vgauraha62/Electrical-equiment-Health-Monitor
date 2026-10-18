# Evidence-Grounded Electrical Equipment Review

An English-language, **read-only** workspace for reviewing the condition
*evidence* of distribution electrical equipment, transformer first. For one
selected asset it assembles the equipment-register context, the recorded
telemetry measurements, the data-quality and coverage notes, and the engineering
evidence status — and keeps every item's source trail visible. An optional
assistant can answer a natural-language question about the selected asset using
only that validated evidence.

This is a demonstration project. It is **not** a diagnosis tool: it does not
score equipment health, does not estimate failure probability, and gives no
maintenance instructions. See [Limitations](#limitations).

## What it does

- **Asset-centric evidence assembly.** Select an observed distribution
  transformer and see its register context (type, voltage, manufacturer, model,
  dates), its snapshot and history measurements, and the data-quality notes for
  that data.
- **A source trail on every number.** Each reading carries a stable evidence
  identifier plus its source file, measurement point, measurement code,
  timestamp, and `data_origin=observed`. Per-file SHA-256 provenance is kept for
  the supplied telemetry exports.
- **Gaps stay visible.** Missing units and an unrecorded timezone are recorded as
  gaps, not inferred. Register-to-telemetry identity is reported as an exact
  ID-string match, never as verified physical identity.
- **Deterministic monitoring review.** Descriptive checks from documented
  methods only: it restates recorded measurements, lists data-readiness routing
  and review candidates, and lists unknowns. No model is involved.
- **Optional grounded assistant.** Answers a question about the selected asset
  from the validated evidence bundle, cites each point, and abstains rather than
  returning unsupported text.

## What it is not

- Not a diagnosis, a health score, or a failure-probability estimate.
- Not a source of maintenance, switching, or dispatch instructions.
- Not a system of record. The human-review queue is stored per running instance
  and may disappear on restart.
- Not a general-purpose chatbot. The assistant answers only from the supplied,
  validated evidence for the selected asset.

## Demo flow

1. **Overview** — the active data source, the number of available assets, and the
   evidence-register status (explicitly "not an equipment verdict").
2. **Equipment Status** — pick a telemetry-backed transformer and review its
   register context, recorded measurements, quality notes, and the deterministic
   monitoring review.
3. **AI Smart Assistant** — enter a question (up to 500 characters) and send it.
   The reply shows a direct answer first, then the evidence behind each point,
   the unknowns, and checks a person could verify.
4. **Evidence status**, **Coverage**, **Reference library**, **Review queue** —
   the engineering evidence register, the per-number source trail, the reference
   sample, and the human-review workflow.

A screen-by-screen walkthrough is in [`docs/user_guide.md`](docs/user_guide.md);
a recorded-demo outline is in
[`docs/demo_video_script.md`](docs/demo_video_script.md).

## Quick start (local)

```bash
cd distribution-condition-poc-backend
python -m pip install -r requirements.txt
python app/main.py
```

Then open `http://127.0.0.1:5000/`. Local preview reads the bundled data files
and needs no Google Cloud credentials. The assistant stays disabled unless
`ENABLE_GEMINI_REVIEW=true` and `GEMINI_API_KEY` are set in the environment.

## Configuration

| Variable | Purpose | Default |
| --- | --- | --- |
| `DATA_DIR` | Telemetry JSON directory read at startup | `data/input/telemetry` |
| `ENABLE_GEMINI_REVIEW` | Enables the grounded evidence assistant | off |
| `GEMINI_API_KEY` | Gemini Developer API key for the assistant | unset |
| `GEMINI_PRIMARY_MODEL` | Assistant model when the assistant is enabled | `gemini-4-argon` |
| `GEMINI_FALLBACK_MODEL` | Assistant fallback model; `none` disables fallback | `gemini-3.8-flash` |
| `GEMINI_THINKING_LEVEL` | Assistant reasoning level | provider default |
| `ENABLE_GEMINI` | Legacy Vertex AI summary endpoint | off |
| `ENABLE_AGENT_DRAFT` | Deterministic draft agent context | off |
| `ENABLE_ASSET_MAPPING` | Manual asset-mapping writes | off |
| `BQ_TABLE`, `BQ_KNOWLEDGE_TABLE`, `BQ_REVIEW_TABLE` | Optional BigQuery sources | unset |
| `PORT` | HTTP port | `5000` |

The Gemini defaults above are the code defaults, which pair the paid
`gemini-4-argon` model with a Flash fallback. The reviewed demo deployment uses
the **`flash-only` profile** — `gemini-3.8-flash` only, `GEMINI_FALLBACK_MODEL=none`
— so the paid model is never called. `argon-first` and `flash-first` are explicit
paid choices. Gemini API usage is billed separately from Google AI Pro and is not
covered by Google Cloud trial credits.

## Architecture

| Layer | Modules | Responsibility |
| --- | --- | --- |
| Web/API | `app/main.py`, `static/` | Flask JSON API and a single-page dashboard (vanilla JS/CSS). |
| Asset assembly | `app/equipment.py`, `app/asset_evidence.py`, `app/evidence_review.py` | Per-asset register context, measurements, evidence profile, gaps. |
| Monitoring | `app/monitoring.py`, `app/features.py`, `app/decision.py` | Deterministic descriptive monitoring and data-readiness routing (no fault classification). |
| Agent context | `app/agent_context.py`, `app/agent_contract.py`, `app/agent_draft.py` | Build and validate the context bundle and the deterministic draft. |
| Grounded assistant | `app/gemini_review.py` | Select question-relevant facts, prompt the model, validate the answer, log the outcome. |
| Provenance | `app/provenance.py`, `data/output/manifest.json` | Per-file SHA-256 for the telemetry exports; source locators on evidence items. |
| Review store | `app/review_store.py` | File-based, append-only human-review cases (temporary, per instance). |

Data flow, endpoint lists, and the assistant's validation contract are documented
in [`docs/system_flow.md`](docs/system_flow.md),
[`docs/backend_api.md`](docs/backend_api.md), and
[`docs/m1_25_gemini_review.md`](docs/m1_25_gemini_review.md).

## Data provenance

- **Telemetry inputs** (`data/input/telemetry/`, eight English-labelled JSON
  exports) are the observed measurements that drive the asset directory,
  canonical records, and monitoring baseline. `data/output/manifest.json` records
  a SHA-256 digest for each export.
- **Reference sample** (`data/input/reference/defect_reference_sample.csv`) is a
  75-row English sample from a defect standards catalog. It is a raw reference
  sample, not approved transformer diagnostic guidance; the full source catalog
  and operating standard are not loaded by the application.
- **Derived artefacts** (`data/output/`) are produced by the offline pipeline in
  `scripts/` (`ingest.py`, `build_equipment_catalog.py`,
  `build_ledger_review_record.py`, `statistical_baseline.py`,
  `build_lineage_inventory.py`, and others). They are deterministic outputs of
  the inputs above.
- **Translated knowledge base** (`data/en/`) is an English translation of the
  supplied material. Translation completion never promotes an item's engineering
  status.
- **Engineering evidence register** (`docs/evidence_register.json`) records which
  source families are verified and which are still pending or unverified. In this
  release no approved engineering reference is connected, so the system reports
  that instead of inventing a rule.
- **Source archive.** The original supplier archives are **not** redistributed
  here; only the derived English inputs and the provenance manifest
  (`docs/english_source_manifest.json`) are included.
- **Synthetic data** (`data/output/synthetic/`) is generated from the canonical
  records and is tagged `data_origin=synthetic`. It is never mixed with observed
  data in the asset directory.

## Repository vs Cloud Run package

Two independent filters exist:

- **The Cloud Run source upload** is filtered by
  [`distribution-condition-poc-backend/.gcloudignore`](.gcloudignore) and
  inspected by [`scripts/check_demo_package.py`](scripts/check_demo_package.py),
  which prints the exact file list and byte total for the current checkout. It
  carries the application, static assets, `requirements.txt`, `Procfile`, the
  eight telemetry inputs, the reference sample, the allowlisted `data/output/`
  artefacts, and `docs/evidence_register.json`.
- **This repository** is source + tests + documentation, and is larger. Tests,
  the offline pipeline (`scripts/`), `schemas/`, `sql/`, the source archive,
  `data/en/`, and most other docs are repository-only.

The two filters are intentionally separate. See
[`docs/system_flow.md`](docs/system_flow.md) for the boundary in detail.

## Tests

Run the full suite from the backend directory:

```bash
cd distribution-condition-poc-backend
python -m unittest discover -s tests -t .
```

- The end-to-end browser test (`tests/test_browser_e2e.py`) drives headless
  Firefox and skips cleanly when Firefox/geckodriver are not installed.
- Three checks depend on the **original supplier archive**, which is not
  redistributed here: `tests/test_linkage.py`,
  `tests/test_english_only.py::TestEnglishOnly::test_source_archive_excluded_and_present`,
  and `tests/test_evidence_workflow.py::EvidenceWorkflowTestCase::test_source_archives_immutable`.
  They cannot pass from this repository alone. `tests/test_linkage.py`
  additionally hard-codes a machine-absolute path to the raw originals, so it is
  not portable even with the archive restored.

## Deployment

The reviewed, low-cost demo path is the private Cloud Run service described in
[`docs/m1_25_demo_deployment.md`](docs/m1_25_demo_deployment.md), driven by
[`scripts/deploy_demo.sh`](scripts/deploy_demo.sh). It checks the effective
source package first, deploys privately (`--no-allow-unauthenticated`,
request-based CPU, minimum instances 0), and maps the Gemini API key from Secret
Manager. Deployment is always an explicit operator action:

```bash
ALLOW_DEMO_DEPLOYMENT=true bash scripts/deploy_demo.sh
```

The service is private, so the `run.app` URL returns `403` without an identity
token; see the deployment doc for the supported access options.

## Limitations

- **Read-only and advisory.** The workspace organises and explains recorded
  evidence. It does not diagnose, rank, or recommend.
- **Missing units and timezone.** The supplied data records no measurement units
  and no timezone; both are preserved as explicit gaps rather than assumed.
- **Identity is a string match.** A register-to-telemetry link is an exact
  ID-string match, which is not proof of physical identity.
- **No approved engineering reference.** With no approved knowledge connected,
  the assistant reports "no approved engineering reference applies" instead of
  producing a rule.
- **English-only.** The interface and the assistant operate in English, and the
  active resources are checked to stay free of untranslated source text.
- **Sample data.** The bundled inputs are a curated sample supplied for this
  demonstration, not a production feed.
