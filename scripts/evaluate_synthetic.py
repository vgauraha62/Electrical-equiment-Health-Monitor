"""Transparent baseline detectors + synthetic evaluation (stdlib only).

Detectors (parameters are arbitrary software sensitivity settings, documented
below — never engineering thresholds):
  rolling_mad_v1: rolling median/MAD score over WINDOW past points after WARMUP
    points; flags |x-median| > K*MAD. MAD==0 with value != median flags with a
    fixed score (constant-baseline deviation). Missing values abstain.
  null_v1 (negative control): scores 0, never flags; verifies the harness does
    not force detections.

Label isolation: detectors receive only (scenario_id, code, timestamp, value).
Scenario truth is loaded separately and used only for scoring.

Metrics (definitions recorded in evaluation.json):
  event_recall: truth events overlapped by >=1 flag / truth events (detection
    scenarios; the missing-data scenario reports abstention_rate instead).
  control_false_alarms: flags on control (non-injected) series.
  precision_at_capacity: among top-K flags by score (K = truth event count),
    fraction overlapping truth.
  detection_delay_steps: first flag index minus window start (>=0 when the
    window start itself flags; None when undetected).
  abstention_rate: masked points with no score / masked points.
  multivariate check (cross-phase only): timestamps where the 3-phase spread
    exceeds the warmup-period maximum spread; reported as a flag, not a verdict.
"""
from __future__ import annotations

import json
import statistics
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

OUT_DIR = ROOT / "data/output/synthetic"
DETECTOR_VERSION = "baseline-detectors-v1"
WINDOW = 12
WARMUP = 12
K = 4.0


def detector_input(row):
    """Label-free detector view. Drops scenario truth, changes, and provenance."""
    return {"scenario_id": row.get("scenario_id"),
            "code": row.get("measurement_type_code"),
            "timestamp": row.get("observed_at_original"),
            "value": row.get("measurement_value")}


def rolling_mad(values):
    """Yield (score, flag, abstained) per point. None value -> abstain."""
    median_window, out = [], []
    for value in values:
        if value is None:
            out.append((None, False, True))
            continue
        if len(median_window) < WARMUP:
            median_window.append(value)
            out.append((0.0, False, False))
            continue
        med = statistics.median(median_window)
        mad = statistics.median([abs(v - med) for v in median_window])
        if mad == 0:
            flagged = value != med
            out.append((1.0 if flagged else 0.0, flagged, False))
        else:
            score = abs(value - med) / mad
            out.append((score, score > K, False))
        median_window.append(value)
        median_window = median_window[-WINDOW:]
    return out


def rank_precision(all_flags, truth_indices, control_code):
    """Pooled top-K precision (historical definition, preserved verbatim).

    all_flags: list of (score, code, index). K = number of truth events.
    Flags from target and control series compete in one ranking.
    """
    ranked = sorted(all_flags, reverse=True)
    top = ranked[:len(truth_indices)] if truth_indices else []
    if not top:
        return None
    hits = sum(1 for _, code, i in top
               if i in truth_indices and code != control_code)
    return hits / len(top)


def rank_precision_target_only(all_flags, truth_indices, control_code):
    """Companion metric: ranking restricted to non-control series.

    Added alongside (not instead of) the pooled definition after the audit
    showed pooled top-1 can favor volatile control flags over a detected spike.
    """
    scoped = [(s, code, i) for s, code, i in all_flags if code != control_code]
    ranked = sorted(scoped, reverse=True)
    top = ranked[:len(truth_indices)] if truth_indices else []
    if not top:
        return None
    hits = sum(1 for _, code, i in top if i in truth_indices)
    return hits / len(top)


def group_events(flags):
    events, current = [], None
    for index, flagged in enumerate(flags):
        if flagged and current is None:
            current = [index, index]
        elif flagged:
            current[1] = index
        elif current is not None:
            events.append(tuple(current))
            current = None
    if current is not None:
        events.append(tuple(current))
    return events


def evaluate_scenario(scenario_id, spec, series, truth):
    """series: {code: [(timestamp, value)]} in index order. Truth indices align."""
    truth_indices = {e["index"] for e in truth.get("events", [])}
    control_code = truth.get("control_code")
    detections, abstentions, control_flags = {}, 0, 0
    all_flags = []
    for code, points in sorted(series.items()):
        values = [v for _, v in points]
        scored = rolling_mad(values)
        flags = [flag for _, flag, _ in scored]
        abstentions += sum(1 for _, _, abst in scored if abst)
        if code == control_code or code.startswith("control"):
            control_flags += sum(flags)
        events = group_events(flags)
        detected = {i for start, end in events for i in range(start, end + 1)}
        hits = sorted(truth_indices & detected)
        detections[code] = {
            "flags": sum(flags),
            "events": [list(e) for e in events],
            "truth_hits": hits,
            "scores": [s for s, _, _ in scored if s is not None],
        }
        all_flags.extend((s, code, i) for i, (s, f, _) in enumerate(scored) if f)
    recall = (len({i for d in detections.values() for i in d["truth_hits"]}
                   & truth_indices) / len(truth_indices)) if truth_indices else None
    precision = rank_precision(all_flags, truth_indices, control_code)
    precision_target_only = rank_precision_target_only(
        all_flags, truth_indices, control_code)
    delay = None
    if spec["scenario_type"] in ("sustained_shift", "gradual_drift", "cross_phase"):
        start = spec["window"][0]
        flagged_after = [i for d in detections.values() for i in d["truth_hits"]
                         if i >= start]
        delay = min(flagged_after) - start if flagged_after else None
    elif spec["scenario_type"] == "spike" and truth_indices:
        delay = 0 if any(detections[c]["truth_hits"]
                         for c in detections if c != control_code) else None
    return {"event_recall": recall, "control_false_alarms": control_flags,
            "precision_at_capacity": precision,
            "precision_at_capacity_target_only": precision_target_only,
            "detection_delay_steps": delay,
            "abstentions": abstentions, "per_code": detections}


def multivariate_spread_check(series):
    """Cross-phase spread vs warmup maximum. Descriptive flag only."""
    codes = sorted(series)
    if len(codes) < 3:
        return {"note": "fewer than 3 series; check not applicable"}
    n = min(len(series[c]) for c in codes)
    spreads = []
    for i in range(n):
        vals = [series[c][i][1] for c in codes]
        if any(v is None for v in vals) or sum(vals) == 0:
            continue
        mu = sum(vals) / len(vals)
        spreads.append((max(vals) - min(vals)) / abs(mu) * 100 if mu else 0.0)
    if len(spreads) <= WARMUP:
        return {"note": "insufficient points"}
    baseline_max = max(spreads[:WARMUP])
    flagged = [i for i in range(WARMUP, len(spreads)) if spreads[i] > baseline_max]
    return {"warmup_max_spread": baseline_max, "flagged_indices": flagged,
            "note": "spread exceeds warmup maximum; descriptive flag, not a verdict"}


def main():
    derived = [json.loads(line) for line in (OUT_DIR / "scenarios.ndjson")
               .read_text(encoding="utf-8").splitlines() if line.strip()]
    truth = json.loads((OUT_DIR / "truth.json").read_text(encoding="utf-8"))
    manifest = json.loads((OUT_DIR / "manifest.json").read_text(encoding="utf-8"))
    by_scenario = {}
    for row in derived:
        view = detector_input(row)
        by_scenario.setdefault(view["scenario_id"], {}).setdefault(
            view["code"], []).append((view["timestamp"], view["value"]))
    specs = {s["scenario_id"]: s for s in manifest["scenarios"]}
    results = {}
    for sid in sorted(by_scenario):
        spec = specs[sid]
        res = evaluate_scenario(sid, spec, by_scenario[sid], truth[sid])
        if spec["scenario_type"] == "cross_phase":
            res["multivariate"] = multivariate_spread_check(by_scenario[sid])
        if spec["scenario_type"] == "missing_data":
            masked = len(truth[sid]["events"])
            res["abstention_rate"] = (res["abstentions"] / masked) if masked else None
            res["event_recall"] = None
            res["precision_at_capacity"] = None
            res["precision_at_capacity_target_only"] = None
            res["precision_note"] = ("not applicable: masked truth points abstain "
                                     "by design and carry no score")
        results[sid] = res
    evaluation = {
        "evaluation_version": "synthetic-evaluation-v1",
        "detector": {"rolling_mad_v1": {"window": WINDOW, "warmup": WARMUP, "k": K,
                                        "note": "K is an arbitrary software sensitivity "
                                                "parameter, not an engineering threshold"},
                     "null_v1": "scores 0, never flags (negative control comparator)"},
        "metric_definitions": {
            "event_recall": "per scenario: truth events overlapped by >=1 flag / truth events (target series only)",
            "control_false_alarms": "per scenario: flags on non-injected control series",
            "precision_at_capacity": "per scenario, pooled across target+control flags: hit fraction among top-K flags, K = truth event count",
            "precision_at_capacity_target_only": "companion: same ranking restricted to non-control series",
            "detection_delay_steps": "per scenario: first in-window flag index minus window start",
            "abstention_rate": "per scenario: masked points with no score / masked points"},
        "partition_note": "Each scenario evaluated within its own partition; "
                          "variants of one parent window never split.",
        "scope_warning": "Synthetic experiments only. No fault diagnosis, no failure "
                         "probability, no real-world performance claim.",
        "results": results,
    }
    (OUT_DIR / "detections.json").write_text(
        json.dumps({"note": "per-point scores live in evaluation per_code entries"},
                   ensure_ascii=True) + "\n", encoding="utf-8")
    (OUT_DIR / "evaluation.json").write_text(
        json.dumps(evaluation, ensure_ascii=True, indent=1, sort_keys=True) + "\n",
        encoding="utf-8")
    for sid, res in results.items():
        print(f"{sid}: recall={res['event_recall']} fa_control={res['control_false_alarms']} "
              f"prec={res['precision_at_capacity']} delay={res['detection_delay_steps']} "
              f"abst={res['abstentions']}")
    print(f"wrote {OUT_DIR}")


if __name__ == "__main__":
    main()
