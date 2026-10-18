# Evidence-Grounded Equipment Review

Product Engineering Report

**Team: Tangyuan**

Members: Grace Yuan Juan and Vaibhav Gauraha

Our Distribution Condition PoC helps engineers review health evidence for electrical equipment. In the current demo, a user selects a transformer, sees its equipment-register context and available telemetry, examines how those records are linked, and can request a Gemini explanation for that asset. The product turns a scattered evidence-gathering task into a traceable review. It supports engineering judgment; it does not issue a confirmed diagnosis or operating instruction.

## 1. Project Overview and Objectives

Electrical equipment is described by records created for different purposes. An equipment register identifies the asset. Operational systems record measurements. Test reports, inspections, defect histories, maintenance records, and technical references may be held elsewhere. An engineer reviewing one transformer must first work out which records belong together and what each record can support.

We built the PoC around that practical use case. The application gives a reviewer one asset-focused place to inspect available information, its source, and its relationship to other records. The selected-asset Gemini review can then explain supported observations and make evidence gaps easier to notice. This helps the engineer move from “Where is the information?” to “What does the available evidence show, and what should I examine next?”

Our engineering objective is traceability. A value remains connected to its source and review state. A candidate record match is not silently treated as confirmed physical identity. Missing units, uncertain timestamps, and unavailable records remain visible. The engineer can therefore understand both the evidence and its limits before making a consequential decision.

## 2. Problem Definition and Data Qualification

The underlying difficulty is not simply that industrial data is spread across files. Identifiers may differ, measurements may lack clear units or time semantics, and the evidence may be incomplete. A measurement without dependable asset context can be misread. A matching identifier can help locate related information, but it does not by itself establish physical identity or prove that a technical rule applies.

The reconciled dataset used for this prototype contains 5,970 telemetry observations across 13 assets and 3,811 equipment-ledger records. Two of the 13 telemetry assets have exact identifier-string matches to ledger records. The transformer used in the demo has 1,372 telemetry observations and a matching ledger record. These figures describe the supplied dataset slice, not complete fleet coverage.

| Evidence area | What the current demo contains |
| --- | --- |
| Equipment register | Nine available fields for the target profile: equipment type, voltage level, equipment state, insulation medium, manufacturer, model, station, operating date, and manufacture date. Line and pole are unavailable. |
| Operating telemetry | 1,372 observations for the demo transformer. Descriptive metrics are presented separately from register facts. |
| Record relationship | An exact `psr_id` string match labeled `id_string_only`. It is a data link, not proof of physical identity. |
| Evidence not in this slice | Units and timestamp semantics remain unresolved. Test, inspection, defect, and work-order records are unavailable for this review. |

Each available ledger field retains a source locator, source hash, and translation or review status. The product uses the current structured data design. The demo does not imply that every asset has telemetry or that all evidence families are connected. Additional records enter the workflow only through controlled ingestion and review; public uploads are not enabled initially.

## 3. Current System Architecture and Implementation

The interface requests a structured asset evidence profile from the read-only API at `GET /api/lineage/equipment/{asset_id}`. The profile is derived from a validated agent context bundle rather than an unverified join made in the interface. Its versioned contract carries evidence identifiers, source hashes and locators, relationship method, review state, and unresolved dimensions.

The agent’s role is to assemble and organize the selected asset’s evidence context for the application. It preserves which facts and relationships are available for review. It is not the same as the Gemini explanation, and it does not independently diagnose equipment or choose an operational action.

The API validation re-derives the profile. It rejects altered endpoints, missing provenance, digest drift, forged statuses, and unsupported conclusions. Failures return a reason code without a profile; a missing asset returns not found. Unsupported relationships remain unavailable or candidate-only. This is how the product keeps the review connected to checked evidence instead of relying on a display-only join.

Gemini is invoked through a separate, explicit user action on Equipment Status. For the selected asset, the backend prepares its validated context, supported observations and calculations, and known unknowns. The review service checks the response against the supplied inputs before displaying it. Citations are intended to support the statements they accompany. For the demonstrated transformer, no approved relevant technical knowledge source is currently available; the review is therefore limited to supported observations and documented gaps, not a technical-rule diagnosis.

Reported engineering verification includes 385 automated tests passing locally and three browser tests passing for the evidence-profile flow in Firefox. The evidence register and listed frozen inputs were reported unchanged. These results cover the reported local checks; they are not performance measurements for the deployed PoC or its Gemini review.

## 4. Proposed Google Cloud Architecture

Our Google Cloud design describes how the product can be hosted and operated as it develops. The current demo is a deployed PoC, while the precise Google Cloud services hosting that deployment have not been established in this report. We therefore distinguish the working product workflow from the proposed cloud components.

Cloud Run is the proposed application runtime for the API and interface, initially as one application service if the existing components are compatible. Cloud Storage is intended for approved source files and controlled ingestion. Identity and Access Management (IAM) defines which people and services can access each resource. Secret Manager holds backend credentials and keys outside the browser and source code. Cloud Logging and Cloud Monitoring provide the operational records and signals used to understand service behavior.

The product flow remains asset-centered: a reviewer selects an equipment item, the API returns its validated profile, and an explicit Gemini review uses the prepared evidence context. In a hosted implementation, service identities control access to approved sources and logs record operational behavior. Real project data can appear in a public demo only after explicit approval; the initial design has no public upload path.

BigQuery is a future option for analyzing broader structured equipment and operating datasets once their schema and quality support it. The current Gemini review is part of the demonstrated product. Broader Gemini workflows, Vertex AI evaluation or deployment, and a connected conversational assistant remain future capabilities subject to data, security, and engineering acceptance.

## 5. Implementation Roadmap and Future Scope

Our development sequence follows the dependencies between the product’s capabilities. We first establish a secure, observable cloud deployment for the current interface and API. This makes the selected-asset workflow available in a controlled hosted environment and provides the operational evidence needed to assess reliability and performance.

We then expand the evidence context by qualifying test, inspection, defect, maintenance, work-order, and technical-reference records. Each new source needs verified provenance, review status, units and time basis where applicable, and an understood relationship to the asset. Broader coverage makes later analysis more useful because the system can connect operating observations with the engineering and historical context needed to interpret them.

Analytical services follow evidence qualification. Repeatable calculations and applicable engineering criteria are reviewed with equipment specialists. BigQuery may support larger-scale structured analysis when the data volume and quality justify it. AI evaluation continues alongside this work, while broader diagnostic workflows remain gated until their evidence selection, citations, uncertainty handling, and engineering behavior have been reviewed.

Tangyuan is planned as an orchestration layer above the evidence APIs and validated analytical services. We will introduce it only after those interfaces and services stabilize. Its role would be to coordinate approved steps in an investigation, preserve execution history, and request human approval according to the risk of each workflow. Longer-term work may extend from individual assets to recurring reviews and fleet-level analysis, with domain-specific validation for each equipment class. The roadmap is dependency-based and has no fixed dates.

## 6. Verification, Acceptance Criteria and Risk Controls

The working product demonstrates evidence preparation and an explicit Gemini review for one selected asset. That capability is distinct from automated diagnosis. Acceptance of wider operational use proceeds through cloud and deployment readiness, evidence integrity, engineering readiness, and then workflow-specific AI or diagnostic approval.

Cloud acceptance establishes that the application is deployed as intended, access is controlled through least-privilege IAM, secrets are handled by the backend, and logs and monitoring signals are available. Evidence acceptance confirms source locators and digests, validates ingested records, and preserves the difference between exact identifier matches and verified physical relationships. Engineering acceptance covers units, timestamp meaning, applicable criteria, reproducible calculations, and the handling of conflicting or missing evidence.

For expanded AI workflows, expert-reviewed cases assess whether the system selects relevant evidence, cites it accurately, communicates uncertainty, and declines unsupported requests. Human approval is assigned by workflow risk. Retrieval and evidence organization may be automated after validation; consequential diagnosis, recommendations, and actions require the designated qualified reviewer. Controlled ingestion, provenance, fail-closed validation, and visible uncertainty apply throughout the workflow.

## 7. Deployment Validation and Performance Evaluation

The Cloud Run Observability dashboard for distribution-condition-poc in asia-south1 showed the service healthy and serving traffic. The selected “Last 1 day” window ran from 18 October 2026, 00:10:39, to 19 October 2026, 00:10:39 (UTC+5:30). The ranges below are dashboard-observed service metrics for that window, not results from a controlled load test.

| Metric | Result | Measurement context |
| --- | --- | --- |
| Request rate | 0 to 0.07 requests/second | Observed chart range; maximum shown for the selected day |
| Request latency | 0.005 to 48.169 seconds | Chart includes 50th, 95th, and 99th percentile series; separate percentile values were not exposed in the dashboard summary |
| End-to-end request latency | 0.008 to 48.169 seconds | Observed chart range |
| Latency breakdown | 0.005 to 34.639 seconds | Observed range across latency components |
| Container instances | 0 to 2 instances | Observed chart range |
| Maximum concurrent requests | 0.5 to 5.98 | Observed chart range |
| Container CPU utilisation | 0.5% to 7.99% | Observed chart range |
| Container memory utilisation | 16.625% to 36.99% | Observed chart range |
| Container startup latency | 1.233 to 2.076 seconds | Observed chart range |
| Billable container instance time | 0 to 0.162 seconds/second | Observed chart range |
| Network bytes sent / received | 0 to 9.664 kB/s sent; 0 to 43.527 B/s received | Observed chart ranges |
| In-memory file system usage | 0.384 to 0.76 billion bytes (dashboard display) | Observed chart range |

The request-latency charts include 50th, 95th, and 99th percentile series, but the dashboard summary available for this snapshot exposed the overall chart ranges rather than exact values for each percentile. The request-count chart reports a maximum rate of 0.07 requests per second. Its 5xx series includes a sampled non-zero point of 0.007 requests per second at 18 October, 16:45. This is a rate sample, not a total error count. The console view does not provide a controlled peak-throughput benchmark.

The current product result is a traceable, single-asset review: engineers can inspect equipment context and available telemetry, understand how records are linked, and request a cited Gemini explanation. This can reduce the effort spent assembling context and help identify what needs investigation. The application makes the first step of industrial equipment-health review clearer while leaving final engineering judgment with the people responsible for the asset.
