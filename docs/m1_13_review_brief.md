# Technical review brief (for a qualified transformer-domain reviewer)

Machine package: `docs/evidence_acquisition_package.json` (30 requests, priorities
1–6); traceability: `docs/m1_13_traceability.md`. Register unchanged: 20 pending,
10 unverified, 0 verified.

## What existing sources settle and what they do not
- Raw telemetry texts establish code→label mapping with byte-identical values;
  they state no units. Calculation rules document low-side V/A, a ×25 fill rule,
  state formulas (one typo resolved, two passages flagged), and data guards —
  applicability to this telemetry is yours to rule on.
- The ledger row for the target asset states oil-immersed construction within a
  class-namespaced code system (class 0302: code 2 = oil-immersed); code systems
  differ by class, so confirm the reading for this asset.
- DL/T 2106-2020 §9.8/Table A.8 evaluate test/inspection quantities, not telemetry
  streams; rule adoption needs your applicability ruling. Twelve class-level defect
  rows exist with zero target-asset linkage; power units, sign convention,
  timezone, interval encoding, and zero semantics have no source anywhere.

## Contradictions and ambiguities needing your ruling
1. Insulation-medium codes are class-namespaced (same code, different meaning
   across classes) — confirm code 2 = oil-immersed for class 0302.
2. Unbalance definitions differ three ways (PoC spread, calc-sheet formula with
   garbled denominator, Table A.8 connection-type bands).
3. Over/undervoltage threshold values are referenced but absent from the sheet.
4. `pt/ct/tFactor` keys are observed null-only and undefined — rule whether they
   can scale values before any rescaling is ever applied.

## Handoff protocol
Submit evidence as: identified files/records with provenance (source system,
export date, checksum where available); target asset IDs and date ranges;
stated units, scaling, timezone, and measurement context; conflicts documented
with both sides cited, never silently resolved. Record approval or rejection per
item with role, name, and date; the register changes only after acceptance
criteria are met, applied by the PoC team — never by the evidence itself. No
authentication exists in the tooling; reviewer labels are self-asserted unless
confirmed out of band.
