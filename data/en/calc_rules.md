# Operating-state calculation rules (full translation, draft pending review)

Source: SRC-ZIP-RELATED workbook `yun-xing-zhuang-tai-ji-suan-gui-ze`, sheet 1
(state determination, 24 rows) and sheet 2 (basic/measurement info, 14 rows).
Formulas transcribed faithfully, including ambiguous passages flagged [AMBIGUOUS].
Numeric thresholds are source text, not adopted PoC rules.

## State determination rules (applies to pole-mounted and distribution transformers unless noted)

| Equipment | Condition 1 | Condition 2 | Relation | State | Telemetry used |
|---|---|---|---|---|---|
| Pole/distribution transformer | Load rate >= 80% (and <= 100%) | Lasts 2 hours | AND | Heavy load | Active + reactive power |
| (same) | Load rate > 100% and < 150% [grade wording unclear] | Lasts 2 hours | AND | Overload | Active + reactive power |
| Line, pole/distribution transformer | Active load rate > 60% | Unbalance degree > 25 [unit not stated] | AND | Current unbalance | Unit currents, low-side currents |
| Line, pole/distribution transformer | Any phase voltage above overvoltage value [value not in sheet] | Lasts 15 minutes | AND | Overvoltage | Unit voltages |
| Line, pole/distribution transformer | Any phase voltage below undervoltage value [value not in sheet] | Lasts 15 minutes | AND | Undervoltage | Unit voltages |
| Line | Load rate > 70% | Lasts 1 hour | AND | Heavy load | Active + reactive power |
| (line) | Load rate > 100% | Lasts 1 hour | AND | Overload | Active + reactive power |

Formulas: load rate = sqrt(active^2 + reactive^2) / rated capacity * 100%;
active load rate = active power / rated capacity [source repeats rated capacity —
resolved as a duplication typo against the parallel load-rate formula in the same
sheet; translator resolution, still needs reviewer confirmation] * 100%;
unbalance degree = (max current − min current) / max [source appends unclear rated-capacity text — AMBIGUOUS, kept flagged; note Table A.8 defines connection-type-dependent unbalance bands instead].
Data guards: if two of three phase voltages are zero, no undervoltage determination;
if any current is zero, no unbalance determination; heavy/overload need ledger rated-capacity field, else no determination.
Fill rule: high-side voltage = low-side voltage × 25 (documented in measurement-info sheet).
Measurement-info example values (229.9/39.2/36.8/24.48 …) match the workspace snapshot exactly.

Status: translated_draft (2 of 3 ambiguous passages still flagged; 1 resolved as a
duplication typo against the sheet's own parallel formula, pending reviewer
confirmation). Requires domain review for remaining ambiguity, missing
over/undervoltage values, unbalance unit, and rated-capacity sourcing before any use.
