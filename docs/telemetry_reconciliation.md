# Telemetry reconciliation (measured against sources)

## Duplicate-or-extend verdict: DUPLICATE
All eight supplied files are byte-identical in records, points, and asset IDs
to the existing workspace JSON inputs (only `measTypeName` was translated).
They extend nothing; ingesting them again would double-count. Canonical and
serving datasets already contain this exact data (5,970 records, 13 assets).

## History/snapshot relations (same asset verified in all three classes with history)
- Distribution transformer: history ends 2026-08-25 00:00, snapshot at 14:45 —
  no timestamp overlap; snapshot is a later standalone read.
- Pole-mounted transformer: history runs through 2026-08-25 14:45 — shares the
  snapshot timestamp (the 14 consistent cross-export overlaps; values identical).
- Pole-mounted breaker: history ends 2026-08-25 00:00, snapshot at 16:30 — no overlap.

## Per-class populations (never pooled)
13 assets total: 3 history-bearing (15-min transformer cadences; 5-min breaker
cadence — cadences differ by class), 10 snapshot-only. Zeros: breaker history
2,223/2,312 and network-breaker 72/72 zeros observed (plausibly de-energized
readings); transformer files near-zero zeros. Zeros are values, not faults;
no missing-value convention exists (0 nulls in all files).

## Unresolved semantics (unchanged)
Units, scaling, CT/PT factors, sign convention, timezone, cadence meaning, and
operating-state interpretation remain unconfirmed for every file. Breaker/feeder
8-code sets lack per-phase power entirely, so cross-class comparison is
structurally invalid beyond shared V/A readings — which still must not be pooled.

## Recommendation
Do not re-ingest (exact duplicates). If newer exports arrive, ingest only after
hash comparison against `data/output/manifest.json` proves new content.

## Authoritative-root confirmation (14 files, all hashes identical)
The 14-file authoritative root supplied with this task (the `data` directory
sibling to the workspace, holding the related-materials collection and the
eight-file measurement-data folder) is byte-identical file-for-file to the
vendored archive (verified per-file SHA-256).

## Cross-dataset linkage (measured, candidate only)
- Telemetry↔ledger via `psr_id`/`ast_id`: 2 of 13 telemetry assets (target
  transformer + one other). 11 telemetry IDs absent from the 3,811-row ledger.
- Defect containers (354 distinct)↔ledger: 0 matches on either identifier —
  container namespace unlinked to the ledger.
- Defect↔telemetry: exactly 1 candidate link — container `28DKX-3681`
  (pole-mounted breaker, conductive joint/lead record, discovered ~2025-06)
  matches a snapshot-only telemetry asset (8 points, 2026-08-25, no history).
  Time gap ~1 year, severity null, elimination state undecoded: a candidate
  match, not a verified label.
- Defect↔library via standard code: 37 of 43 codes mapped; 6 unmapped.
- Temporal: defect discoveries span 2009–2026-07, overlapping the telemetry
  window only at the tail; per-asset linkage above is the only join.
- Ambiguous: `container_type` codes (`dkx` 795, `xl` 138, other 76, `zf08` 2)
  undecoded; 216 defect rows lack equipment type; insulation codes are
  class-namespaced (see ledger dictionary).
- No register status changes follow: linkage is unproven and semantics unknown.
