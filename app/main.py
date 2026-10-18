from __future__ import annotations

import csv
import hashlib
import json
import os
import re
import time
import uuid
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory

try:
    from .decision import evaluate_data_readiness
except ImportError:  # Supports `python app/main.py` during local preview.
    from decision import evaluate_data_readiness

try:
    from .monitoring import RequestError, build_response_id, build_review, validate_request
except ImportError:
    from monitoring import RequestError, build_response_id, build_review, validate_request

try:
    from . import review_store
except ImportError:
    import review_store

try:
    from . import evidence_intake
except ImportError:
    import evidence_intake

try:
    from . import equipment as equipment_directory
except ImportError:
    import equipment as equipment_directory

try:
    from . import agent_context as agent_context_module
    from . import agent_contract as agent_contract_module
    from . import agent_draft as agent_draft_module
    from . import asset_evidence as asset_evidence_module
    from . import asset_mapping as asset_mapping_module
    from . import evidence_review as evidence_review_module
    from . import gemini_review as gemini_review_module
except ImportError:  # Supports `python app/main.py` during local preview.
    import agent_context as agent_context_module
    import agent_contract as agent_contract_module
    import agent_draft as agent_draft_module
    import asset_evidence as asset_evidence_module
    import asset_mapping as asset_mapping_module
    import evidence_review as evidence_review_module
    import gemini_review as gemini_review_module


ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = Path(os.getenv("DATA_DIR", ROOT / "data/input/telemetry"))
DEFECT_CSV = ROOT / "data/input/reference/defect_reference_sample.csv"
PROJECT = os.getenv("GOOGLE_CLOUD_PROJECT", "")
LOCATION = os.getenv("GOOGLE_CLOUD_LOCATION", "asia-south1")
VERTEX_LOCATION = os.getenv("VERTEX_LOCATION", "global")
MODEL = os.getenv("GEMINI_MODEL", "gemini-2.5-flash")
ENABLE_GEMINI = os.getenv("ENABLE_GEMINI", "false").lower() == "true"
# The context-grounded Gemini review is a distinct, off-by-default capability. It
# explains only the validated context/evidence bundle and deterministic draft. It
# is separate from ENABLE_GEMINI so enabling it never changes the existing
# /api/assets/<id>/explain behavior or meaning.
ENABLE_GEMINI_REVIEW = os.getenv("ENABLE_GEMINI_REVIEW", "false").lower() == "true"
ENABLE_AGENT_DRAFT = os.getenv("ENABLE_AGENT_DRAFT", "false").lower() == "true"
# Manual asset mapping writes local files only; the flag defaults off so a
# deployment without authentication never exposes the mutation routes silently.
ENABLE_ASSET_MAPPING = os.getenv("ENABLE_ASSET_MAPPING", "false").lower() == "true"
TARGET_EQUIPMENT_TYPE = "0408001"
BQ_TABLE = os.getenv("BQ_TABLE", "").strip()
KNOWLEDGE_TABLE = os.getenv("BQ_KNOWLEDGE_TABLE", "").strip()
REVIEW_TABLE = os.getenv("BQ_REVIEW_TABLE", "").strip()
REQUIRED_TRANSFORMER_CODES = {
    "A_phsA", "A_phsB", "A_phsC",
    "PhV_phsA", "PhV_phsB", "PhV_phsC",
    "PhW_phsA", "PhW_phsB", "PhW_phsC",
    "PhVAr_phsA", "PhVAr_phsB", "PhVAr_phsC",
    "TotW", "TotVar",
}
REVIEW_OUTCOMES = {"review", "request_data", "no_further_action_for_sample"}
TABLE_NAME_RE = re.compile(r"^[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+$")

app = Flask(__name__, static_folder=str(ROOT / "static"))
_cache = {"at": 0.0, "rows": None}


def _table_name(value: str, setting: str) -> str:
    if not TABLE_NAME_RE.fullmatch(value):
        raise RuntimeError(f"{setting} must be a fully-qualified project.dataset.table name")
    return value


def _evidence_id(row: dict) -> str:
    material = "|".join(str(row.get(key, "")) for key in (
        "source_file_id", "source_record_index", "measurement_point_id",
        "measurement_type_code", "observed_at", "dataset_type",
    ))
    return "ev_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _local_rows() -> list[dict]:
    rows = []
    for path in sorted(DATA_DIR.glob("*.json")):
        payload = json.loads(path.read_text(encoding="utf-8"))
        dataset_type = "history" if "history" in path.stem else "snapshot"
        for record_index, record in enumerate(payload.get("data", {}).get("result", {}).get("records", [])):
            for point in record.get("telemetryValue", []):
                raw_value = point.get("measValue")
                try:
                    numeric_value = float(raw_value) if raw_value is not None else None
                except (TypeError, ValueError):
                    numeric_value = None
                row = {
                    "source_file_id": path.name,
                    "source_record_index": record_index,
                    "psr_id": record.get("psrId") or "unknown-asset",
                    "measurement_point_id": record.get("measPointId") or "unknown-point",
                    "equipment_type_code": record.get("psrType") or "unknown",
                    "measurement_type_code": record.get("measTypeCode") or "unknown",
                    "measurement_name": record.get("measTypeName") or "Unknown measurement",
                    "measurement_value": numeric_value,
                    "measurement_unit": None,
                    "observed_at": point.get("dataTime") or "",
                    "dataset_type": dataset_type,
                    "data_origin": "observed",
                    "source_uri": path.name,
                    "quality_flags": "unit not confirmed",
                }
                row["evidence_id"] = _evidence_id(row)
                rows.append(row)
    return rows


def _rows() -> list[dict]:
    now = time.monotonic()
    if _cache["rows"] is not None and now - _cache["at"] < 30:
        return _cache["rows"]
    if BQ_TABLE:
        from google.cloud import bigquery

        table = _table_name(BQ_TABLE, "BQ_TABLE")
        client = bigquery.Client(project=PROJECT or None, location=LOCATION)
        query = f"""
            SELECT source_file_id, source_record_index, psr_id,
                   measurement_point_id, equipment_type_code,
                   measurement_type_code, measurement_name, measurement_value,
                   measurement_unit, observed_at, dataset_type, data_origin,
                   source_uri, quality_flags
            FROM `{table}`
            WHERE data_origin = 'observed'
        """
        loaded = []
        for result in client.query(query).result():
            row = dict(result)
            row["observed_at"] = str(row.get("observed_at") or "")
            row["measurement_value"] = float(row["measurement_value"]) if row.get("measurement_value") is not None else None
            row["evidence_id"] = _evidence_id(row)
            loaded.append(row)
        rows = loaded
    else:
        rows = _local_rows()
    _cache.update(at=now, rows=rows)
    return rows


def _target_rows() -> list[dict]:
    return [row for row in _rows() if row.get("equipment_type_code") == TARGET_EQUIPMENT_TYPE]


def _assets() -> list[dict]:
    grouped = defaultdict(list)
    for row in _target_rows():
        grouped[row.get("psr_id") or "unknown-asset"].append(row)
    assets = []
    for asset_id, values in grouped.items():
        latest = max((row.get("observed_at", "") for row in values), default="")
        assets.append({
            "asset_id": asset_id,
            "equipment_type_code": TARGET_EQUIPMENT_TYPE,
            "observation_count": len(values),
            "latest_observation": latest,
            "data_origin": "observed",
        })
    return sorted(assets, key=lambda item: item["asset_id"])


def _knowledge(query_text: str, equipment_type_code: str = TARGET_EQUIPMENT_TYPE, limit: int = 5) -> list[dict]:
    """Return approved English chunks only; an unconfigured KB is a valid empty state."""
    if not KNOWLEDGE_TABLE:
        return []
    from google.cloud import bigquery

    table = _table_name(KNOWLEDGE_TABLE, "BQ_KNOWLEDGE_TABLE")
    client = bigquery.Client(project=PROJECT or None, location=LOCATION)
    terms = list(dict.fromkeys(re.findall(r"[a-z0-9]+", query_text.lower())))[:12]
    if not terms:
        return []
    sql = f"""
        SELECT chunk_id, title, content, source_id, source_uri,
               source_locator, source_version, translation_status,
               equipment_type_code, tags
        FROM `{table}`
        WHERE status = 'approved'
          AND language = 'en'
          AND translation_status = 'reviewed'
          AND equipment_type_code IN (@equipment_type_code, 'all')
          AND EXISTS (
              SELECT 1 FROM UNNEST(@terms) AS term
              WHERE STRPOS(LOWER(CONCAT(title, ' ', content, ' ', ARRAY_TO_STRING(tags, ' '))), term) > 0
          )
        LIMIT @row_limit
    """
    config = bigquery.QueryJobConfig(query_parameters=[
        bigquery.ScalarQueryParameter("equipment_type_code", "STRING", equipment_type_code),
        bigquery.ArrayQueryParameter("terms", "STRING", terms),
        bigquery.ScalarQueryParameter("row_limit", "INT64", limit),
    ])
    return [dict(row) for row in client.query(sql, job_config=config).result()]


def _build_evidence(asset_id: str) -> dict | None:
    rows = [row for row in _target_rows() if row.get("psr_id") == asset_id]
    if not rows:
        return None
    snapshots = [row for row in rows if row.get("dataset_type") == "snapshot"]
    history = sorted(
        (row for row in rows if row.get("dataset_type") == "history"),
        key=lambda row: (row.get("observed_at") or "", row.get("measurement_type_code") or ""),
    )
    latest_by_code = {}
    for row in snapshots:
        code = row.get("measurement_type_code") or ""
        previous = latest_by_code.get(code)
        if previous is None or (row.get("observed_at") or "") > (previous.get("observed_at") or ""):
            latest_by_code[code] = row

    observed_codes = set(latest_by_code)
    missing_codes = sorted(REQUIRED_TRANSFORMER_CODES - observed_codes)
    phase_currents = [
        row.get("measurement_value") for code, row in latest_by_code.items()
        if code in {"A_phsA", "A_phsB", "A_phsC"} and row.get("measurement_value") is not None
    ]
    phase_spread = None
    if len(phase_currents) == 3 and sum(phase_currents) != 0:
        mean_current = sum(phase_currents) / 3
        phase_spread = round((max(phase_currents) - min(phase_currents)) / abs(mean_current) * 100, 2)

    timestamps = sorted({row.get("observed_at", "") for row in rows if row.get("observed_at")})
    history_by_code = defaultdict(list)
    for row in history:
        history_by_code[row.get("measurement_type_code") or "unknown"].append(row)
    history_summary = {}
    for code, points in history_by_code.items():
        numeric_points = [point for point in points if point.get("measurement_value") is not None]
        first_point = points[0]
        last_point = points[-1]
        summary = {
            "measurement_name": last_point.get("measurement_name"),
            "observation_count": len(points),
            "first_observed_at": first_point.get("observed_at"),
            "last_observed_at": last_point.get("observed_at"),
            "first_value": first_point.get("measurement_value"),
            "last_value": last_point.get("measurement_value"),
            "minimum": None,
            "maximum": None,
            "mean": None,
            "supporting_evidence_ids": [first_point["evidence_id"], last_point["evidence_id"]],
        }
        if numeric_points:
            low = min(numeric_points, key=lambda point: point["measurement_value"])
            high = max(numeric_points, key=lambda point: point["measurement_value"])
            summary.update({
                "minimum": low["measurement_value"],
                "maximum": high["measurement_value"],
                "mean": round(sum(point["measurement_value"] for point in numeric_points) / len(numeric_points), 6),
                "supporting_evidence_ids": list(dict.fromkeys([
                    first_point["evidence_id"], low["evidence_id"], high["evidence_id"], last_point["evidence_id"],
                ])),
            })
        history_summary[code] = summary
    history_dates = sorted({row.get("observed_at", "")[:10] for row in history if row.get("observed_at")})
    warnings = [
        "Measurement units and scaling have not been confirmed.",
        "Oil-immersed construction is not confirmed by the telemetry export.",
        "No confirmed failure or maintenance outcome labels are present; this is not a failure prediction.",
    ]
    if not history:
        warnings.append("No historical observations are available for this asset.")
    if missing_codes:
        warnings.append("One or more expected transformer measurement codes are missing from the snapshot.")

    evidence = {
        "asset_id": asset_id,
        "equipment_type_code": TARGET_EQUIPMENT_TYPE,
        "asset_type_label": "Distribution transformer (oil-immersed status unconfirmed)",
        "oil_immersed_confirmed": False,
        "data_origin": "observed",
        "latest_observation": timestamps[-1] if timestamps else "",
        "history_start": timestamps[0] if timestamps else "",
        "history_end": timestamps[-1] if timestamps else "",
        "snapshot_measurements": sorted(latest_by_code.values(), key=lambda row: row.get("measurement_type_code") or ""),
        "history": history,
        "history_summary": history_summary,
        "history_coverage_days": len(history_dates),
        "history_observation_count": len(history),
        "observation_count": len(rows),
        "measurement_point_count": len({row.get("measurement_point_id") for row in rows}),
        "phase_current_spread_pct": phase_spread,
        "missing_measurement_codes": missing_codes,
        "quality_warnings": warnings,
        "source_files": sorted({row.get("source_file_id", "") for row in rows}),
        "evidence_ids": sorted({row["evidence_id"] for row in rows}),
        "assessment_status": "descriptive_review_only",
    }
    evidence["decision"] = evaluate_data_readiness(evidence)
    return evidence


def _defects(query: str = "") -> list[dict]:
    with DEFECT_CSV.open(encoding="utf-8-sig", newline="") as handle:
        rows = list(csv.DictReader(handle))
    normalized_query = query.strip().lower()
    if normalized_query:
        rows = [row for row in rows if normalized_query in " ".join(row.values()).lower()]
    return rows[:100]


def _gemini_explanation(evidence: dict, knowledge: list[dict]) -> str:
    from google import genai
    from google.genai.types import HttpOptions

    client = genai.Client(
        vertexai=True, project=PROJECT, location=VERTEX_LOCATION,
        http_options=HttpOptions(api_version="v1"),
    )
    facts = {
        "asset_id": evidence["asset_id"],
        "asset_type": evidence["asset_type_label"],
        "latest_observation": evidence["latest_observation"],
        "history_start": evidence["history_start"],
        "history_end": evidence["history_end"],
        "history_observation_count": evidence["history_observation_count"],
        "history_coverage_days": evidence["history_coverage_days"],
        "history_summary": evidence["history_summary"],
        "snapshot_measurements": evidence["snapshot_measurements"],
        "phase_current_spread_pct": evidence["phase_current_spread_pct"],
        "quality_warnings": evidence["quality_warnings"],
        "snapshot_evidence_ids": [row["evidence_id"] for row in evidence["snapshot_measurements"]],
    }
    prompt = (
        "Write a concise English evidence review for a maintenance analyst. "
        "Use only facts and approved English knowledge passages supplied below. "
        "Cite evidence IDs and knowledge chunk IDs for factual statements. "
        "If no approved guidance applies, say so. Do not diagnose a fault, infer units, "
        "claim failure probability, invent maintenance thresholds, or prescribe repair. "
        "Separate observed evidence from uncertainty.\n\n"
        "OBSERVED_EVIDENCE_JSON:\n" + json.dumps(facts, ensure_ascii=True) +
        "\n\nAPPROVED_KNOWLEDGE_CHUNKS_JSON:\n" + json.dumps(knowledge, ensure_ascii=True)
    )
    response = client.models.generate_content(model=MODEL, contents=prompt)
    return response.text or "No explanation was returned."


@app.get("/")
def index():
    return send_from_directory(ROOT / "static", "index.html")


@app.get("/api/health")
def health():
    return jsonify({
        "status": "ok",
        "data_source": "bigquery" if BQ_TABLE else "bundled_sample",
        "knowledge_source": "bigquery" if KNOWLEDGE_TABLE else "empty",
        "target_equipment_type_code": TARGET_EQUIPMENT_TYPE,
        "decision_component": "data_readiness_rules_v1",
        "laya_enabled": False,
        "gemini_enabled": ENABLE_GEMINI,
        "gemini_review_enabled": ENABLE_GEMINI_REVIEW,
        "agent_draft_enabled": ENABLE_AGENT_DRAFT,
        "asset_mapping_enabled": ENABLE_ASSET_MAPPING,
    })


@app.get("/api/assets")
def assets():
    return jsonify(_assets())


@app.get("/api/assets/<asset_id>")
@app.get("/api/assets/<asset_id>/evidence")
def asset_evidence(asset_id: str):
    evidence = _build_evidence(asset_id)
    if evidence is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    return jsonify(evidence)


@app.get("/api/assets/<asset_id>/knowledge")
def asset_knowledge(asset_id: str):
    evidence = _build_evidence(asset_id)
    if evidence is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    query_text = request.args.get("q", "distribution transformer phase current voltage active reactive power condition").strip()
    chunks = _knowledge(query_text)
    return jsonify({
        "asset_id": asset_id,
        "knowledge_status": "available" if chunks else "empty",
        "retrieval_mode": "approved_english_keyword_search" if KNOWLEDGE_TABLE else "not_configured",
        "query": query_text,
        "items": chunks,
    })


@app.get("/api/knowledge/search")
def knowledge_search():
    query_text = request.args.get("q", "").strip()
    if not query_text:
        return jsonify({"error": "Query parameter q is required"}), 400
    chunks = _knowledge(query_text)
    return jsonify({
        "knowledge_status": "available" if chunks else "empty",
        "retrieval_mode": "approved_english_keyword_search" if KNOWLEDGE_TABLE else "not_configured",
        "query": query_text,
        "items": chunks,
    })


@app.get("/api/assets/<asset_id>/decision")
def asset_decision(asset_id: str):
    evidence = _build_evidence(asset_id)
    if evidence is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    return jsonify({"asset_id": asset_id, "decision": evidence["decision"]})


@app.post("/api/assets/<asset_id>/explain")
def explain(asset_id: str):
    if not ENABLE_GEMINI:
        return jsonify({"error": "Gemini assistance is disabled for this deployment."}), 503
    evidence = _build_evidence(asset_id)
    if evidence is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    query_text = "distribution transformer phase current voltage active reactive power condition"
    knowledge = _knowledge(query_text)
    try:
        explanation = _gemini_explanation(evidence, knowledge)
    except Exception:
        app.logger.exception("Gemini evidence explanation failed")
        return jsonify({"error": "Gemini could not prepare an explanation. The evidence remains available."}), 502
    return jsonify({
        "explanation": explanation,
        "model": MODEL,
        "assessment_status": evidence["assessment_status"],
        "knowledge_status": "available" if knowledge else "empty",
        "knowledge_chunk_ids": [item.get("chunk_id") for item in knowledge],
        "evidence_ids": evidence["evidence_ids"],
        "decision": evidence["decision"],
    })


@app.post("/api/assets/<asset_id>/gemini-review")
def gemini_review(asset_id: str):
    """Context-grounded Gemini review of the validated evidence only.

    Gated by ENABLE_GEMINI_REVIEW (default off). Read-only: it writes nothing,
    opens no review case, and changes no evidence status. The model receives only
    the validated bundle/profile/deterministic review; its structured output is
    validated server-side and fails closed to an abstention.
    """
    if not ENABLE_GEMINI_REVIEW:
        return jsonify({"error": "Gemini evidence review is disabled for this deployment."}), 404
    body = request.get_json(silent=True)
    if body is None:
        body = {}
    if not isinstance(body, dict):
        return jsonify({"error": "Request body must be a JSON object."}), 400
    question = body.get("question")
    if question is not None:
        if not isinstance(question, str) or not question.strip():
            return jsonify({"error": "question must be a non-empty string."}), 400
        if len(question) > gemini_review_module.MAX_USER_QUESTION_CHARS:
            return jsonify({"error": "question exceeds the 500 character limit."}), 400
    try:
        result = gemini_review_module.explain(
            asset_id, root=ROOT, user_question=question.strip() if question else None)
    except agent_contract_module.AgentContractError as exc:
        return jsonify({"error": "Asset evidence contract violation.",
                        "code": exc.code}), 500
    except gemini_review_module.GeminiReviewError as exc:
        return jsonify({"error": "Gemini evidence review failed.",
                        "code": exc.code}), 500
    if result is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    return jsonify(result)


@app.post("/api/reviews")
def save_review():
    if not REVIEW_TABLE:
        return jsonify({"error": "Human-review persistence is not configured for this deployment."}), 503
    body = request.get_json(silent=True) or {}
    asset_id = str(body.get("asset_id", "")).strip()
    outcome = str(body.get("outcome", "")).strip()
    rationale = str(body.get("rationale", "")).strip()
    if not asset_id or outcome not in REVIEW_OUTCOMES or not rationale:
        return jsonify({"error": "asset_id, a valid outcome, and rationale are required"}), 400
    evidence = _build_evidence(asset_id)
    if evidence is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    evidence_ids = body.get("evidence_ids", [])
    if not isinstance(evidence_ids, list) or any(not isinstance(item, str) for item in evidence_ids):
        return jsonify({"error": "evidence_ids must be a list of strings"}), 400
    known_ids = set(evidence["evidence_ids"])
    if not set(evidence_ids).issubset(known_ids):
        return jsonify({"error": "Review includes evidence IDs not belonging to this asset"}), 400
    chunk_ids = body.get("knowledge_chunk_ids", [])
    if not isinstance(chunk_ids, list) or any(not isinstance(item, str) for item in chunk_ids):
        return jsonify({"error": "knowledge_chunk_ids must be a list of strings"}), 400
    row = {
        "review_id": str(uuid.uuid4()),
        "asset_id": asset_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "outcome": outcome,
        "rationale": rationale[:2000],
        "model_name": str(body.get("model_name", "rules_data_readiness_v1"))[:100],
        "evidence_ids": evidence_ids,
        "knowledge_chunk_ids": chunk_ids,
        "data_origin": "observed",
    }
    from google.cloud import bigquery

    table = _table_name(REVIEW_TABLE, "BQ_REVIEW_TABLE")
    client = bigquery.Client(project=PROJECT or None, location=LOCATION)
    errors = client.insert_rows_json(table, [row])
    if errors:
        app.logger.error("BigQuery review insert failed: %s", errors)
        return jsonify({"error": "Review could not be saved"}), 502
    return jsonify({"status": "saved", "review_id": row["review_id"]}), 201


@app.post("/api/monitoring-reviews")
def monitoring_review():
    body = request.get_json(silent=True)
    try:
        asset_id, _window = validate_request(body if body is not None else {})
    except RequestError as exc:
        return jsonify({"error": str(exc)}), exc.status
    evidence = _build_evidence(asset_id)
    if evidence is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    review = build_review(evidence, len(_target_rows()), len(_rows()), ROOT,
                          bool(REVIEW_TABLE), ENABLE_GEMINI)
    review.update(build_response_id())
    return jsonify(review), 200


@app.get("/api/review-cases")
def review_case_list():
    directory = review_store.store_dir(ROOT)
    return jsonify(review_store.list_cases(
        directory,
        asset_id=request.args.get("asset") or None,
        status=request.args.get("status") or None))


@app.post("/api/review-cases")
def review_case_create():
    body = request.get_json(silent=True) or {}
    asset_id = str(body.get("asset_id", "")).strip()
    if not asset_id:
        return jsonify({"error": "asset_id is required as a non-empty string."}), 400
    evidence = _build_evidence(asset_id)
    if evidence is None:
        return jsonify({"error": "Distribution transformer asset not found"}), 404
    monitoring = build_review(evidence, len(_target_rows()), len(_rows()), ROOT,
                              bool(REVIEW_TABLE), ENABLE_GEMINI)
    note = body.get("note")
    if note is not None and (not isinstance(note, str) or not note.strip()):
        return jsonify({"error": "note must be a non-empty string when provided."}), 400
    reviewer = body.get("reviewer")
    if reviewer is not None and (not isinstance(reviewer, str) or not reviewer.strip()):
        return jsonify({"error": "reviewer must be a non-empty string when provided."}), 400
    key = body.get("idempotency_key")
    if key is not None and (not isinstance(key, str) or not key.strip()):
        return jsonify({"error": "idempotency_key must be a non-empty string when provided."}), 400
    try:
        case, created = review_store.create_case(
            review_store.store_dir(ROOT), asset_id, monitoring,
            note=note.strip() if isinstance(note, str) else None,
            reviewer=reviewer.strip() if isinstance(reviewer, str) else None,
            idempotency_key=key.strip() if isinstance(key, str) else None)
    except review_store.StoreError as exc:
        return jsonify({"error": str(exc)}), exc.status
    body_out = dict(case)
    body_out["replayed"] = not created
    return jsonify(body_out), 201 if created else 200


@app.get("/api/review-cases/<case_id>")
def review_case_get(case_id: str):
    try:
        return jsonify(review_store.get_case(review_store.store_dir(ROOT), case_id))
    except review_store.StoreError as exc:
        return jsonify({"error": str(exc)}), exc.status


@app.post("/api/review-cases/<case_id>/notes")
def review_case_note(case_id: str):
    body = request.get_json(silent=True) or {}
    try:
        case = review_store.add_note(
            review_store.store_dir(ROOT), case_id, body.get("from_version"),
            body.get("note"), body.get("reviewer"))
    except review_store.StoreError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return jsonify(case)


@app.post("/api/review-cases/<case_id>/transitions")
def review_case_transition(case_id: str):
    body = request.get_json(silent=True) or {}
    try:
        case = review_store.transition(
            review_store.store_dir(ROOT), case_id, body.get("from_version"),
            body.get("to_status"), body.get("note"), body.get("reviewer"))
    except review_store.StoreError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return jsonify(case)


@app.get("/api/evidence-gaps")
def evidence_gaps():
    """Read-only evidence status. Never promotes or verifies anything."""
    try:
        register = json.loads((ROOT / "docs/evidence_register.json").read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return jsonify({"error": "Evidence register unavailable."}), 500
    items = register.get("items", []) if isinstance(register, dict) else []
    public = [{
        "evidence_id": item.get("id"),
        "question": item.get("question"),
        "status": item.get("status"),
        "applicability": item.get("applicability"),
        "owner_role": item.get("reviewer_required"),
        "evidence_cited": bool(item.get("evidence_location")),
        "blocks": item.get("blocks", []),
    } for item in items if isinstance(item, dict)]
    return jsonify({
        "total": len(public),
        "verified": sum(1 for item in public if item["status"] == "verified"),
        "note": "No engineering claim is verified. Translation completion never "
                "promotes engineering status.",
        "items": public,
    })


@app.post("/api/evidence-submissions")
def evidence_submission_create():
    body = request.get_json(silent=True) or {}
    try:
        submission, created = evidence_intake.register_submission(
            evidence_intake.store_dir(ROOT),
            body.get("evidence_request_id"),
            files=body.get("files") or [],
            idempotency_key=body.get("idempotency_key"),
            submission_source=body.get("submission_source"),
            source_date=body.get("source_date"),
            asset_context=body.get("asset_context"),
            measurement_context=body.get("measurement_context"),
            stated_interpretation=body.get("stated_interpretation"),
            stated_uncertainty=body.get("stated_uncertainty"))
    except evidence_intake.IntakeError as exc:
        return jsonify({"error": str(exc)}), exc.status
    body_out = dict(submission)
    body_out["replayed"] = not created
    return jsonify(body_out), 201 if created else 200


@app.get("/api/evidence-submissions")
def evidence_submission_list():
    return jsonify(evidence_intake.list_submissions(
        evidence_intake.store_dir(ROOT),
        request_id=request.args.get("request_id") or None,
        intake_status=request.args.get("intake_status") or None))


@app.get("/api/evidence-submissions/<submission_id>")
def evidence_submission_get(submission_id: str):
    try:
        return jsonify(evidence_intake.get_submission(
            evidence_intake.store_dir(ROOT), submission_id))
    except evidence_intake.IntakeError as exc:
        return jsonify({"error": str(exc)}), exc.status


@app.post("/api/evidence-submissions/<submission_id>/files")
def evidence_submission_file(submission_id: str):
    body = request.get_json(silent=True) or {}
    try:
        submission = evidence_intake.attach_file(
            evidence_intake.store_dir(ROOT), submission_id,
            body.get("filename"), body.get("content_base64"))
    except evidence_intake.IntakeError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return jsonify(submission)


@app.post("/api/evidence-submissions/<submission_id>/transitions")
def evidence_submission_transition(submission_id: str):
    body = request.get_json(silent=True) or {}
    try:
        submission = evidence_intake.transition(
            evidence_intake.store_dir(ROOT), submission_id,
            body.get("to_status"), body.get("note"))
    except evidence_intake.IntakeError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return jsonify(submission)


@app.get("/api/equipment")
def equipment_list():
    catalog = equipment_directory.load_catalog(ROOT)
    if catalog is None:
        return jsonify({"error": "Equipment catalog is not built for this deployment."}), 503
    telemetry = equipment_directory.telemetry_assets(_rows())
    items, counts = equipment_directory.directory(catalog.get("records", []), telemetry)
    query = (request.args.get("q", "") or "").strip().lower()
    type_filter = (request.args.get("type", "") or "").strip()
    coverage = (request.args.get("coverage", "") or "").strip()
    if coverage not in ("", "all", "ledger only", "telemetry only", "both"):
        return jsonify({"error": "coverage must be all, ledger only, telemetry only, or both"}), 400
    filtered = items
    if query:
        filtered = [i for i in filtered if query in i["asset_id"].lower()]
    if type_filter:
        filtered = [i for i in filtered
                    if type_filter in (i.get("equipment_type_codes") or [])
                    or type_filter == (i.get("ast_type") or "")]
    if coverage and coverage != "all":
        filtered = [i for i in filtered if i["coverage"] == coverage]
    elif not coverage:
        # Default review list: telemetry-backed equipment only. Ledger-only
        # inventory has no readings and is available via coverage=ledger only/all.
        filtered = [i for i in filtered if i["coverage"] != "ledger only"]
    sort = (request.args.get("sort", "") or "").strip()
    if sort not in ("", "asset", "readings"):
        return jsonify({"error": "sort must be asset or readings"}), 400
    if sort == "readings":
        filtered = sorted(filtered,
                          key=lambda i: (-i.get("observation_count", 0),
                                         i["asset_id"]))
    try:
        page = max(1, int(request.args.get("page", "1")))
        per_page = min(100, max(1, int(request.args.get("per_page", "50"))))
    except ValueError:
        return jsonify({"error": "page and per_page must be integers"}), 400
    start = (page - 1) * per_page
    return jsonify({"counts": counts, "total": len(filtered), "page": page,
                    "per_page": per_page, "items": filtered[start:start + per_page]})


@app.get("/api/equipment/<asset_id>")
def equipment_detail(asset_id: str):
    catalog = equipment_directory.load_catalog(ROOT)
    if catalog is None:
        return jsonify({"error": "Equipment catalog is not built for this deployment."}), 503
    key = equipment_directory.normalize_id(asset_id)
    telemetry = equipment_directory.telemetry_assets(_rows())
    items, _ = equipment_directory.directory(catalog.get("records", []), telemetry)
    match = next((i for i in items if i["asset_id"] == key), None)
    if match is None:
        return jsonify({"error": "Equipment not found in ledger or telemetry"}), 404
    ledger = next((r for r in catalog.get("records", [])
                   if equipment_directory.normalize_id(r.get("psr_id")) == key), None)
    rows = [r for r in _rows() if equipment_directory.normalize_id(r.get("psr_id")) == key]
    body = {"asset_id": match["asset_id"], "coverage": match["coverage"],
            "ledger": ledger,
            "value_caveat": equipment_directory.UNIT_TIME_CAVEAT}
    if rows and TARGET_EQUIPMENT_TYPE in {r.get("equipment_type_code") for r in rows}:
        evidence = _build_evidence(match["asset_id"])
        body["telemetry"] = {"equipment_type_codes": match["equipment_type_codes"],
                             "observation_count": match["observation_count"],
                             "latest_observation": match["latest_observation"],
                             "source_files": sorted({r.get("source_file_id", "")
                                                     for r in rows}),
                             "readings_by_code":
                                 equipment_directory.describe_telemetry(rows),
                             "review": evidence["decision"] if evidence else None,
                             "assessment_status": evidence["assessment_status"]
                             if evidence else None}
    elif rows:
        body["telemetry"] = {
            "equipment_type_codes": match["equipment_type_codes"],
            "observation_count": match["observation_count"],
            "latest_observation": match["latest_observation"],
            "source_files": sorted({r.get("source_file_id", "") for r in rows}),
            "readings_by_code": equipment_directory.describe_telemetry(rows)}
        body["analysis_note"] = equipment_directory.NON_TRANSFORMER_NOTE
    else:
        body["telemetry"] = None
        body["note"] = equipment_directory.LEDGER_ONLY_NOTE
    return jsonify(body)


def _mapping_context():
    """Source-record registry plus the set of known equipment IDs, built once."""
    catalog = equipment_directory.load_catalog(ROOT) or {}
    telemetry = equipment_directory.telemetry_assets(_rows())
    items, _counts = equipment_directory.directory(catalog.get("records", []), telemetry)
    equipment_ids = {item["asset_id"] for item in items}
    registry = asset_mapping_module.build_registry(
        ROOT, rows=_rows(), catalog=catalog,
        manifest=agent_context_module.load_manifest(ROOT))
    return registry, equipment_ids


def _mapping_documents(registry, equipment_ids, *, equipment=None,
                       include_removed=False):
    """Read packaged mappings plus local edits, validating packaged state."""
    return asset_mapping_module.effective_mappings(
        asset_mapping_module.store_dir(ROOT),
        ROOT / "data/output/asset_mapping_snapshot.json",
        registry, equipment_ids, equipment=equipment,
        include_removed=include_removed)


@app.get("/api/asset-mappings/source-families")
def asset_mapping_families():
    """Available source families and their records, derived from supplied data."""
    registry, _ids = _mapping_context()
    return jsonify({
        "registry_version": registry["registry_version"],
        "registry_digest_sha256": registry["registry_digest_sha256"],
        "writes_enabled": ENABLE_ASSET_MAPPING,
        "families": registry["families"],
        "note": ("Availability is derived from the records actually supplied right now. "
                 "Unavailable families are reported as unavailable; no placeholder "
                 "record is created."),
    })


@app.get("/api/asset-mappings/candidates")
def asset_mapping_candidates():
    """Currently-unlinked source records offered for assignment (search clues)."""
    registry, _ids = _mapping_context()
    family = (request.args.get("family") or "").strip() or None
    exclude_linked = request.args.get("exclude_linked", "true").lower() != "false"
    try:
        limit = int(request.args.get("limit", "50"))
    except ValueError:
        return jsonify({"error": "limit must be an integer"}), 400
    try:
        registry, equipment_ids = _mapping_context()
        existing_documents = _mapping_documents(
            registry, equipment_ids, include_removed=True)
        result = asset_mapping_module.candidates(
            registry, asset_mapping_module.store_dir(ROOT), family=family,
            q=request.args.get("q", ""), exclude_linked=exclude_linked, limit=limit,
            existing_documents=existing_documents)
    except asset_mapping_module.MappingError as exc:
        return jsonify({"error": str(exc)}), exc.status
    result["writes_enabled"] = ENABLE_ASSET_MAPPING
    return jsonify(result)


@app.get("/api/asset-mappings/equipment/<equipment_id>")
def asset_mapping_equipment(equipment_id: str):
    """One equipment item's links, grouped by source family (exact-ID vs manual)."""
    registry, equipment_ids = _mapping_context()
    key = equipment_directory.normalize_id(equipment_id)
    if key not in equipment_ids:
        return jsonify({"error": "Equipment not found in ledger or telemetry"}), 404
    try:
        documents = [asset_mapping_module.decorate(document, registry)
                     for document in _mapping_documents(registry, equipment_ids,
                                                        equipment=key)]
    except asset_mapping_module.MappingError as exc:
        return jsonify({"error": str(exc)}), exc.status
    result = asset_mapping_module.equipment_links(registry, documents, key)
    result["writes_enabled"] = ENABLE_ASSET_MAPPING
    result["manual_enable_hint"] = (
        "Manual assignment is off unless enabled for local development by setting "
        "ENABLE_ASSET_MAPPING=true. It is not enabled in deployment configuration.")
    return jsonify(result)


@app.get("/api/asset-mappings/<mapping_id>")
def asset_mapping_get(mapping_id: str):
    registry, equipment_ids = _mapping_context()
    try:
        document = next((item for item in _mapping_documents(
            registry, equipment_ids, include_removed=True)
                         if item.get("mapping_id") == mapping_id), None)
        if document is None:
            raise asset_mapping_module.MappingError("Mapping not found.", status=404)
    except asset_mapping_module.MappingError as exc:
        return jsonify({"error": str(exc)}), exc.status
    result = asset_mapping_module.decorate(document, registry)
    result["writes_enabled"] = ENABLE_ASSET_MAPPING
    return jsonify(result)


@app.post("/api/asset-mappings")
def asset_mapping_create():
    if not ENABLE_ASSET_MAPPING:
        return jsonify({"error": "Asset mapping is disabled for this deployment."}), 404
    body = request.get_json(silent=True) or {}
    registry, equipment_ids = _mapping_context()
    try:
        document, created = asset_mapping_module.create_mapping(
            asset_mapping_module.store_dir(ROOT), registry, equipment_ids,
            target_equipment_id=body.get("target_equipment_id"),
            source_family=body.get("source_family"),
            source_record_key=body.get("source_record_key"),
            basis=body.get("basis"),
            actor=body.get("actor"),
            basis_origin=body.get("basis_origin", "user_provided"),
            supporting_reference=body.get("supporting_reference"),
            status=body.get("status"),
            source_sha256=body.get("source_sha256"),
            source_locator=body.get("source_locator"))
    except asset_mapping_module.MappingError as exc:
        return jsonify({"error": str(exc)}), exc.status
    result = asset_mapping_module.decorate(document, registry)
    result["created"] = created
    return jsonify(result), 201 if created else 200


@app.post("/api/asset-mappings/<mapping_id>")
def asset_mapping_update(mapping_id: str):
    if not ENABLE_ASSET_MAPPING:
        return jsonify({"error": "Asset mapping is disabled for this deployment."}), 404
    body = request.get_json(silent=True) or {}
    registry, equipment_ids = _mapping_context()
    try:
        document = asset_mapping_module.update_mapping(
            asset_mapping_module.store_dir(ROOT), registry, equipment_ids, mapping_id,
            body.get("from_version"),
            target_equipment_id=body.get("target_equipment_id"),
            basis=body.get("basis"),
            status=body.get("status"),
            basis_origin=body.get("basis_origin"),
            supporting_reference=body.get("supporting_reference"),
            actor=body.get("actor"))
    except asset_mapping_module.MappingError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return jsonify(asset_mapping_module.decorate(document, registry))


@app.post("/api/asset-mappings/<mapping_id>/remove")
def asset_mapping_remove(mapping_id: str):
    if not ENABLE_ASSET_MAPPING:
        return jsonify({"error": "Asset mapping is disabled for this deployment."}), 404
    body = request.get_json(silent=True) or {}
    registry, _ids = _mapping_context()
    try:
        document = asset_mapping_module.remove_mapping(
            asset_mapping_module.store_dir(ROOT), mapping_id, body.get("from_version"),
            body.get("note"), body.get("actor"))
    except asset_mapping_module.MappingError as exc:
        return jsonify({"error": str(exc)}), exc.status
    return jsonify(asset_mapping_module.decorate(document, registry))


@app.get("/api/lineage")
def lineage_inventory():
    try:
        return jsonify(json.loads(
            (ROOT / "data/output/lineage_inventory.json").read_text(encoding="utf-8")))
    except (OSError, ValueError):
        return jsonify({"error": "Lineage inventory is not built for this deployment."}), 503


@app.get("/api/lineage/equipment/<asset_id>")
def lineage_equipment(asset_id: str):
    """Relationship explorer for telemetry-backed equipment only."""
    key = equipment_directory.normalize_id(asset_id)
    telemetry = equipment_directory.telemetry_assets(_rows())
    if key not in telemetry:
        return jsonify({"error": "No telemetry for this asset; "
                                 "lineage explorer covers telemetry-backed equipment only."}), 404
    catalog = equipment_directory.load_catalog(ROOT) or {}
    ledger = next((r for r in catalog.get("records", [])
                   if equipment_directory.normalize_id(r.get("psr_id")) == key), None)
    tele = telemetry[key]
    rows = [r for r in _rows() if equipment_directory.normalize_id(r.get("psr_id")) == key]
    codes = {}
    for row in rows:
        code = row.get("measurement_type_code") or "unknown"
        entry = codes.setdefault(code, {"observation_count": 0, "timestamps": []})
        entry["observation_count"] += 1
        if row.get("observed_at"):
            entry["timestamps"].append(row.get("observed_at"))
    for entry in codes.values():
        entry["first_observation"] = min(entry["timestamps"], default="")
        entry["last_observation"] = max(entry["timestamps"], default="")
        entry["unique_timestamps"] = len(set(entry["timestamps"]))
        del entry["timestamps"]
    body = {
        "asset_id": key,
        "telemetry": {
            "source_files": tele["source_files"],
            "observation_count": tele["observation_count"],
            "measurement_codes": tele["measurement_codes"],
            "time_coverage": {"first": tele["latest_observation"] and min(
                r.get("observed_at", "") for r in rows),
                "last": tele["latest_observation"]},
            "per_code": codes,
        },
        "ledger_match": ({
            "matched": True,
            "locator": {"workbook": "equipment ledger", "sheet": "hlt_device_ledger_pool",
                        "key": f"psr_id={key}"},
            "profile": {k: ledger.get(k) for k in
                        ("ast_type", "voltage_level_en", "psr_state_en",
                         "manufacturer", "model", "insulation_medium_en",
                         "operate_date_iso", "manufacture_date_iso")},
        } if ledger else {
            "matched": False,
            "note": "No exact ledger ID match; never infer or fuzzy-match a ledger record.",
        }),
        "approved_references": [],
        "references_note": "No approved reference is linked to this specific equipment; "
                           "general references are not shown as equipment evidence.",
    }
    # Read-only evidence profile and review for the review experience. Both are
    # projections of the validated context bundle; the review layers the
    # deterministic cited facts, the approved-reference state, and the
    # register-backed unresolved questions on the asset profile. Neither carries
    # a conclusion, calls a model, or writes anything, and both are ``None`` when
    # the asset has no telemetry evidence in the target class. A contract
    # violation fails closed with the reason code and no projection.
    try:
        bundle = agent_context_module.build_context_bundle(key, root=ROOT)
        profile = asset_evidence_module.build_asset_evidence(
            key, root=ROOT, bundle=bundle)
        body["evidence_profile"] = profile
        body["evidence_review"] = evidence_review_module.build_evidence_review(
            key, root=ROOT, bundle=bundle, profile=profile)
    except agent_contract_module.AgentContractError as exc:
        return jsonify({"error": "Asset evidence contract violation.",
                        "code": exc.code}), 500
    return jsonify(body)


@app.get("/api/agent/context/<asset_id>")
def agent_context_route(asset_id: str):
    """Read-only evidence bundle for the agent. Gated; makes no cloud calls."""
    if not ENABLE_AGENT_DRAFT:
        return jsonify({"error": "Agent draft is disabled for this deployment."}), 404
    bundle = agent_context_module.build_context_bundle(asset_id, root=ROOT)
    if bundle is None:
        return jsonify({"error": "No telemetry evidence for this asset"}), 404
    try:
        agent_contract_module.validate_context(bundle)
    except agent_contract_module.AgentContractError as exc:
        return jsonify({"error": "Context contract violation.", "code": exc.code}), 500
    return jsonify(bundle)


@app.get("/api/agent/draft/<asset_id>")
def agent_draft_route(asset_id: str):
    """Read-only deterministic draft. Gated; fails closed on any contract violation."""
    if not ENABLE_AGENT_DRAFT:
        return jsonify({"error": "Agent draft is disabled for this deployment."}), 404
    bundle = agent_context_module.build_context_bundle(asset_id, root=ROOT)
    if bundle is None:
        return jsonify({"error": "No telemetry evidence for this asset"}), 404
    try:
        draft = agent_draft_module.build_deterministic_draft(bundle)
    except agent_contract_module.AgentContractError as exc:
        return jsonify(agent_draft_module.rejected_result(
            bundle["bundle_digest_sha256"], bundle["asset_id"], exc.code, exc.detail))
    return jsonify(draft)


@app.get("/api/defects")
def defects():
    query = request.args.get("q", "")
    return jsonify({
        "count": len(_defects(query)),
        "items": _defects(query),
        "sample_status": "raw_reference_sample_only",
        "approved_guidance": False,
        "translation_status": "draft_unreviewed",
        "equipment_scope_note": "Mixed-equipment 75-row English sample; not mapped to asset type codes and not approved transformer diagnostic guidance.",
    })


@app.get("/static/<path:path>")
def static_files(path: str):
    return send_from_directory(ROOT / "static", path)


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.getenv("PORT", "8080")))
