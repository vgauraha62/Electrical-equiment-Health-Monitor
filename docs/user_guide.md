# User guide — how to use the demo

This guide is for someone using the running demo. It describes what each screen
does, what the AI Smart Assistant will and will not do, and how to run the demo
locally or reach the deployed service. For the design and safety rules behind
the behaviour, see [`system_flow.md`](system_flow.md); for the deployment
procedure, see [`m1_25_demo_deployment.md`](m1_25_demo_deployment.md).

The PoC is a **read-only evidence workspace**. It organises recorded
measurements and equipment-register context for one asset, keeps each item's
source trail, and makes missing evidence visible. It is **not** a diagnosis tool,
does not score equipment health, and gives no maintenance instructions.

## The screens

| Screen | What it is for |
| --- | --- |
| **Overview** | Starting point. Shows the active data source, how many assets are available, and the evidence-register status ("not an equipment verdict"). |
| **Equipment Status** | Select an asset and review its register context and recorded measurements, the data-quality notes, and the deterministic monitoring review. |
| **Review queue** | Human review workflow: open cases, record notes, and move a case through its states. Reviewer labels are self-asserted; a completed review means the evidence was reviewed, and never means equipment is healthy or faulty. |
| **Evidence status** | The engineering evidence register: which source families are verified and which are still pending or unverified. |
| **Coverage** | Where every number comes from, and which records relate to a selected asset. |
| **Reference library** | The English sample catalog. The rows are a raw reference sample; translations are draft and are not approved transformer diagnostic guidance. |
| **Reports / Meteorological / Agent orchestration / AI knowledge base** | Product-direction previews. They are marked as not connected and make no backend calls. |

## The AI Smart Assistant

The assistant answers a question about **one selected transformer**, grounded in
the evidence for that asset. It is enabled only when the deployment sets
`ENABLE_GEMINI_REVIEW=true` and the `gemini-api-key` secret holds a key.

**How to use it**

1. Open **Equipment Status** and select a telemetry-backed transformer (or open
   **AI Smart Assistant** and enter the same asset ID).
2. Open **AI Smart Assistant**. The selected asset is shown under *Asset context*.
3. Type a question (up to 500 characters) and press **Send**.

The question is sent only when you press Send. The assistant does not send
anything on page load, keeps no conversation history, and exposes no
chain-of-thought.

**What you get back**

- A **direct answer** shown first.
- **Evidence supporting the answer** — the observations, references, or evidence
  items behind each point, with their citation and source locator.
- **Unknowns and evidence gaps** — what the evidence does not establish.
- **Checks a person could verify** — follow-up actions for a human reviewer.

Above the answer you may also see how many of the deterministic facts were
relevant to your question, and a note that **no approved engineering reference
applies** when that is the case. The reference state is honest: this deployment
has no approved technical knowledge connected, so the assistant says so instead
of inventing a rule.

**What the assistant will not do**

- It does not diagnose, produce a failure probability or health score, or give
  maintenance instructions.
- It does not answer from its own knowledge: every material statement must be
  supported by the supplied evidence, or it is dropped.
- If the answer itself cannot be matched to the supplied evidence, it
  **abstains** rather than returning unvalidated text.
- If the model request fails, it reports a plain model-error state and leaves the
  evidence on the page unchanged. It never retries on a paid model.
- It only runs on the free `flash-only` profile by default (Gemini 3.8 Flash, no
  fallback). The paid profiles are explicit deployment choices.

**What to look at on Equipment Status instead**

The deterministic **monitoring review** on the Equipment Status page is separate
from the assistant: it restates recorded measurements and quality notes, lists
review candidates from documented methods only, and lists unknowns and explicitly
unsupported conclusions. No model is involved.

## Running the demo locally

```
cd distribution-condition-poc-backend
python -m pip install -r requirements.txt
python app/main.py
```

Then open `http://127.0.0.1:5000/`. Local preview reads the bundled data files
and needs no Google Cloud credentials. Gemini stays disabled unless
`ENABLE_GEMINI_REVIEW=true` and `GEMINI_API_KEY` are set in the environment.

## Reaching the deployed demo

The service is private (`--no-allow-unauthenticated`), so the `run.app` URL
returns `403` without a Google identity token. Two supported ways to use it:

- Authenticated local proxy:
  `gcloud run services proxy distribution-condition-poc --region asia-south1 --port 8080`
  then browse `http://localhost:8080`.
- Grant the intended accounts `roles/run.invoker` on the service and let them
  authenticate with their own identity.

Making the demo public is a separate, reviewed change; see
[`m1_25_demo_deployment.md`](m1_25_demo_deployment.md).

## Local, temporary data

Review cases are stored temporarily in the running service. They may disappear on
restart and may differ across instances. Do not treat the review queue as a
system of record.
