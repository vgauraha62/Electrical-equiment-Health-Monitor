# Demo script: evidence-grounded equipment review

**Audience:** hackathon judges and project partners  
**Length:** about 4 minutes  
**Target:** the deployed Distribution Condition PoC

> For a shorter, recorded walkthrough, use
> [`demo_video_script.md`](demo_video_script.md) (2–3 minutes). This longer
> script is for a live presentation.

## Opening

**Presenter:** “Electricity networks depend on many kinds of equipment, from transformers to breakers and feeders. The information needed to understand an asset is often spread across an equipment register and separate operating records. Our PoC brings the available information together around a selected asset, keeps track of where it came from, and makes gaps visible. That is the foundation for a broader equipment-health workflow.”

**Presenter:** “Today’s demo shows evidence preparation and a model-assisted review for a selected transformer. It does not claim that every source is linked or that the system makes an operational diagnosis.”

## 1. Overview

**Do:** Open the Overview screen.

**Presenter:** “This is the starting point for the review. The application is organized around equipment and its available evidence, so a user can move from the asset to the records that may help explain it.”

If asked what the product is:

**Presenter:** “We are building an infrastructure layer that connects computing and electricity-system information with a practical workflow for reviewing the health evidence of electrical equipment.”

## 2. Select an equipment item

**Do:** Open Equipment Status and select transformer `042983E8-9A90-4A58-887E-5F12AE1DCE7D-97294`.

**Presenter:** “For this transformer, the page brings together the available telemetry and the equipment-register context. The measurements show what was recorded. The ledger provides useful context such as equipment type, voltage, manufacturer, model, and dates.”

**Presenter:** “The two records share the same ID string. That gives us a useful data link, but an exact string match by itself does not prove that both records refer to the same physical asset. We preserve that distinction in the interface.”

**Do:** Point to a measurement chart or evidence table and the ledger facts.

**Presenter:** “The ledger fields help organize the review and guide which supporting material would be relevant. They do not replace operating measurements, test results, inspection findings, or maintenance history. Where those records are unavailable or not linked, the system should say so rather than fill in the gaps.”

## 3. Explain what the evidence supports

**Do:** Open the Gemini evidence-review panel on the selected transformer and click its review button once.

**Presenter:** “This is an explicit, user-started request. The review service receives the selected asset’s validated context, supported observations and calculations, relevant approved references when available, and the known unknowns. It checks the response against those inputs before displaying it.”

**Presenter:** “A citation must support the particular statement beside it. A source ID merely existing is not enough. If the evidence does not support a statement, or the response crosses a boundary such as inferring an unknown unit or physical identity, that item is omitted or the result is limited.”

### If the model review succeeds

**Presenter:** “Here the model has returned an explanation grounded in the evidence shown. We can inspect each cited observation and distinguish it from a statement drawn from an approved reference. The model is helping a person review evidence; it is not issuing an operating instruction.”

### If the model request fails or returns no supported explanation

**Presenter:** “The model request did not produce a usable explanation on this attempt. The evidence already on the page remains available, and the system does not present a failed request as a model-generated result. This is a provider or response-validation issue to resolve; it does not change the underlying records.”

**Presenter:** “There is currently no approved relevant knowledge source to support a technical rule-based conclusion. The system can still organize and summarize supported observations, but it must not turn that into a diagnosis, failure probability, or maintenance instruction.”

## 4. Show the coverage and relationship view

**Do:** Open Coverage and select the same asset in the relationship explorer if available.

**Presenter:** “This view helps us inspect how the available records relate to the selected equipment. It distinguishes a confirmed source link from a possible or unavailable relationship. The deployed package currently has a limited source inventory, so this screen does not claim to show every source family or every possible relationship.”

If the relationship profile is unavailable in the deployed view:

**Presenter:** “The deployment does not include the development-only full lineage inventory. The dashboard still shows the packaged equipment and telemetry coverage, while the broader source-family inventory is a planned packaging improvement.”

## 5. AI Smart Assistant

**Do:** Open AI Smart Assistant, confirm the selected transformer, and ask
`What evidence is missing?`.

**Presenter:** “This is the connected assistant. When I press Send, it sends my question, the deterministic evidence draft, and only the facts and gaps relevant to this question — not the whole evidence set. The service checks the model's answer against that evidence before it is displayed.”

**Presenter:** “The direct answer appears first, followed by the evidence behind each point with its citations, then the unknowns and documented gaps, then checks a person could verify. For this question it reports both known gaps — the missing unit basis and the missing timezone — and it says plainly that no approved engineering reference applies, so the answer stays within supported observations and documented gaps.”

**Presenter:** “It is a separate capability from the deterministic monitoring review and from the equipment-page preview button; all three share the same validated evidence. Nothing is sent on page load, there is no conversation history, and if the model request fails or the answer cannot be supported the system abstains instead of improvising.”

## Closing

**Presenter:** “The PoC demonstrates the first useful step: organizing equipment context with available measurements, preserving source traceability, and checking whether explanations are supported. The next steps are to connect more operating, test, inspection, and maintenance records and to expand approved technical references; the conversational assistant already uses that same evidence-grounded review service.”

## Presenter reminders

- Say **recorded observation**, not **confirmed fault**, unless a reviewed source supports that conclusion.
- Describe the ledger-to-telemetry link as an **exact ID-string match**; do not call it verified physical identity.
- Do not imply that all equipment records have telemetry or that all data families are linked.
- The AI Smart Assistant is connected, but only after an explicit Send; never imply it responds automatically or keeps memory.
- If Gemini fails, use the failure dialogue above. Do not improvise a model answer or claim that a failed request generated one.
- Do not promise approved knowledge references unless the interface actually shows them for this asset.

