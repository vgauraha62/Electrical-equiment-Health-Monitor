# Demo video script — evidence-grounded equipment review

**Audience:** hackathon judges and project partners
**Length:** 2–3 minutes (target ~2 min 30 s)
**Target:** the deployed Distribution Condition PoC (private Cloud Run service)
**Prerequisite:** a telemetry-backed transformer is selected and the `gemini-api-key`
secret holds a Free-tier key, so `ENABLE_GEMINI_REVIEW=true` and the AI Smart
Assistant is live. If Gemini is disabled, use the fallback lines in *If the
assistant is unavailable*.

This is a **presentation of evidence preparation and a grounded, model-assisted
review**. It is **not** a diagnosis, a failure probability, or a maintenance
instruction, and it must not be narrated as one.

Narration below is written to be read aloud; spoken lines are in quotes. On-screen
actions are in **Do:** lines.

---

## 0:00–0:15 — Product overview

**Do:** Land on **Overview**.

> "Electricity equipment information is usually spread across an equipment
> register, operating records, and reference documents. This PoC brings the
> available information together around one asset, keeps every item's source
> trail, and makes the missing evidence visible. It is called
> *Evidence-Grounded Electrical Equipment Review*: equipment health monitoring,
> the smart way."

**Do:** Point to the header, then the three cards.

> "The header shows the shared product identity and the data source — here,
> *Supplied equipment records*. The cards show how many assets are available to
> inspect and the current evidence-register status. That status is a register
> status, not a verdict about any equipment."

---

## 0:15–0:50 — One transformer: ledger context and telemetry

**Do:** Open **Equipment Status** and select transformer
`042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294`.

> "For this transformer the page brings together two things. First, the
> equipment-register context: the equipment type, voltage, manufacturer, model,
> and dates. Second, the recorded measurements, with their observed time strings
> preserved exactly as supplied."

**Do:** Point to the metric grid / history chart and the source line.

> "The register record and the telemetry records share the same ID string. That
> is a useful data link, but on its own an exact string match is not proof that
> both records describe the same physical asset. The interface keeps that
> distinction rather than hiding it."

> "Notice what is still missing: the records carry a time string but no explicit
> timezone, and no unit is asserted for the raw values. The system records those
> as gaps instead of guessing."

---

## 0:50–2:05 — AI Smart Assistant: "What evidence is missing?"

**Do:** Open **AI Smart Assistant**. Confirm the transformer ID is filled, type
`What evidence is missing?`, press **Send** (the question is sent only when you
press Send; there is no history and nothing is sent on page load).

> "This is an explicit, user-started request. The assistant sends my question,
> the deterministic evidence draft built from the reviewed measurements, and only
> the facts and gaps relevant to this question — not the whole evidence set. The
> model returns an answer, and the service checks that answer against the supplied
> evidence before it is displayed."

**Do:** Point to *Direct answer*, then to *Evidence supporting the answer*,
*Unknowns and evidence gaps*, and *Checks a person could verify*. Point to the
selection line and the no-reference note.

> "Here is the answer, shown first. Below it are the items that support it, each
> with its citation and source locator; then the unknowns and documented gaps;
> then checks a person could carry out. It reports both known gaps — the missing
> unit basis and the missing timezone — and it cites the evidence identifiers
> behind each point."

> "It also says, plainly, that **no approved engineering reference applies to this
> question**, so the answer is limited to supported observations and documented
> gaps. Nothing here is a diagnosis, a health score, or a maintenance instruction.
> Where the evidence could not support a statement, the item is dropped, and if
> the answer itself had no support the assistant would abstain rather than
> improvise."

*If the assistant is unavailable:* "The model request did not return a usable
answer on this attempt; the evidence already on the page is unchanged. The
deterministic monitoring review, which only restates recorded measurements and
quality notes, is still available."

---

## 2:05–2:40 — What it supports, and what stays unknown

**Do:** Stay on the answer, scroll to the unknowns and checks.

> "So what does this support? It supports a precise, reviewable statement of what
> was recorded for this asset, where each number came from, and what is still
> missing. That is preparation for a human reviewer."

> "What stays unknown is just as important: the timezone, the unit basis, whether
> the register match is the same physical asset, and the operating, test, and
> maintenance history that is not linked yet. There is also no approved technical
> reference connected, so the system says so instead of inventing one."

> "The next step is to connect those additional records and approved references,
> so a reviewer has more to work with — the interface keeps the evidence and the
> limits visible either way."

**Closing line:**

> "Evidence-grounded equipment review: real records, real gaps, and no
> unsupported conclusions."

---

## Production notes

- Record against the **deployed** service so the video matches what judges see.
- Use the model-profile badge and the source label as-is; do not edit the screen.
- Do not narrate the model's internal reasoning; the UI never shows it, and the
  prompt is explicitly written to keep chain-of-thought out of the answer.
- Keep the whole run under three minutes; trim the Overview segment first.

## Presenter reminders

- Say **recorded observation**, not **confirmed fault**.
- Call the register-to-telemetry link an **exact ID-string match**; never
  "verified physical identity".
- Do not imply all sources are linked, or that the register covers every asset.
- Do not promise approved knowledge references unless the interface shows them;
  for this asset and question it explicitly reports that none apply.
- Never present the output as a diagnosis, failure probability, or maintenance
  instruction.
