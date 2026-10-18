# Telemetry equipment dictionary (translated, draft pending review)

Source labels from the eight raw telemetry texts; classes never pooled.

| Code | Source class label | English class label | Codes present | Assets | Cadence (observed median) |
|---|---|---|---|---|---|
| 0408001 | distribution transformer | Distribution transformer | 14 (3-phase current, voltage, active/reactive power, totals) | 1 | 15 min history |
| 0401004 | pole-mounted transformer | Pole-mounted transformer | 14 (same set) | 1 | 15 min history |
| 0401005 | pole-mounted circuit breaker | Pole-mounted circuit breaker | 8 (3-phase current, voltage, totals only) | 1 | 5 min history |
| 0408003 | distribution-network circuit breaker | Distribution-network circuit breaker | 8 (same breaker set) | 9 | snapshot only |
| 0403000 | feeder | Feeder | 8 (same breaker set) | 1 | snapshot only |

Measurement-name mapping (14 codes) is established in
`telemetry_source_dictionary.md`. Units: none stated in any file. Zeros are
observed values (breaker histories are mostly zeros); no missing-value
convention is documented. Timezone unconfirmed for all files.

Status: translated_draft; breaker/feeder rows translated for labels only.
