import json
from pathlib import Path
root=Path(__file__).resolve().parents[1]
in_dir=root/"data/input/telemetry"
out=root/"data/output/telemetry_observations.ndjson"
with out.open("w",encoding="utf-8") as target:
    for path in sorted(in_dir.glob("*.json")):
        payload=json.loads(path.read_text(encoding="utf-8"))
        dataset_type="history" if "history" in path.stem else "snapshot"
        for rec_idx,record in enumerate(payload.get("data",{}).get("result",{}).get("records",[])):
            for point in record.get("telemetryValue",[]):
                raw=point.get("measValue")
                try: value=float(raw) if raw is not None else None
                except (TypeError,ValueError): value=None
                row={"source_file_id":path.name,"source_record_index":rec_idx,"psr_id":record.get("psrId") or "unknown","measurement_point_id":record.get("measPointId") or "unknown","equipment_type_code":record.get("psrType") or "unknown","measurement_type_code":record.get("measTypeCode") or "unknown","measurement_name":record.get("measTypeName") or "Unknown measurement","measurement_value":value,"measurement_unit":None,"observed_at":point.get("dataTime") or "","dataset_type":dataset_type,"data_origin":"observed","source_uri":f"gs://INPUT_BUCKET/telemetry/{path.name}","quality_flags":"unit not confirmed"}
                target.write(json.dumps(row,ensure_ascii=True)+"\n")
print(f"Wrote normalized observations to {out}")
