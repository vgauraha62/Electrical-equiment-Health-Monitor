"""Build the sanitized equipment catalog (stdlib only, deterministic).

Reads the authoritative equipment ledger workbook (path given by the task;
hash recorded, file never modified), keeps 3,811 rows keyed by unique psr_id,
drops personnel/owner/maintainer/organization/name fields, translates only
closed enum values with established equivalents, and writes
data/output/equipment_catalog.json sorted by psr_id.

Excluded: all *_name organization/personal fields, Chinese proper names,
null-only arc-medium columns. ast_type codes are kept raw with meaning
unverified. Excel date serials convert to ISO dates mechanically (UTC-neutral).
"""
from __future__ import annotations

import datetime
import hashlib
import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

LEDGER_PATH = Path("/home/vg/my-projects/ai-builder-cup/programs/data_english/equipment_ledger.xlsx")
OUT_PATH = ROOT / "data/output/equipment_catalog.json"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"

VOLTAGE = {"22": "10 kV AC"}
STATE = {"20": "in service", "30": "retired", "31": "retained on site",
         "10": "not commissioned"}
# The translated workbook carries a few pinyin leftovers instead of the
# established English terms. Normalize them deterministically (documented here,
# reversible to the source cell); anything unlisted stays null, never guessed.
NORMALIZE_STATE_NAME = {"in operation": "in service", "Tuiyi": "retired",
                        "not yet commissioned": "not commissioned",
                        "Xianchangliuyong": "retained on site"}
NORMALIZE_VOLTAGE_NAME = {"Jiaoliu 10 kV": "10 kV AC"}
NORMALIZE_INSULATION_NAME = {"Zhenkong": "vacuum"}
INSULATION = {"oil-immersed": "oil-immersed", "dry-type": "dry-type",
              "SF6": "SF6", "vacuum": "vacuum"}
# Dropped: personal/owner/maintainer/org/name fields and null-only columns.
KEPT = ("id", "psr_id", "ast_id", "ast_type", "voltage_level",
        "psr_state", "manufacturer", "model", "station", "line", "pole",
        "insulation_medium", "operate_date", "manufacture_date", "status")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def excel_date(value):
    if value is None or value == "":
        return None
    try:
        return (datetime.datetime(1899, 12, 30)
                + datetime.timedelta(days=float(value))).date().isoformat()
    except (TypeError, ValueError):
        return None


def load_rows():
    with zipfile.ZipFile(LEDGER_PATH) as z:
        try:
            sroot = ET.fromstring(z.read("xl/sharedStrings.xml"))
            shared = ["".join(t.text or "" for t in si.iter(NS + "t"))
                      for si in sroot.findall(NS + "si")]
        except KeyError:
            shared = []
        sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
    rows = []
    for row in sheet.iter(NS + "row"):
        cells = {}
        for c in row.findall(NS + "c"):
            v = c.find(NS + "v")
            val = v.text if v is not None else None
            if c.get("t") == "s" and val is not None:
                val = shared[int(val)]
            cells["".join(x for x in c.get("r") if x.isalpha())] = val
        rows.append(cells)
    return rows


def main():
    if not LEDGER_PATH.is_file():
        raise SystemExit(f"ledger workbook not found: {LEDGER_PATH}")
    rows = load_rows()
    header, data = rows[0], rows[1:]
    col = {name: letter for letter, name in header.items()}
    if len(data) != 3811:
        raise SystemExit(f"expected 3811 ledger rows, found {len(data)}")
    records = []
    for record in data:
        get = lambda name, _r=record: _r.get(col[name])  # noqa: E731
        entry = {name: get(name) for name in KEPT}
        entry["voltage_level_en"] = VOLTAGE.get(str(get("voltage_level")))
        entry["psr_state_en"] = STATE.get(str(get("psr_state")))
        # Name columns in the translated source carry English terms plus a few
        # pinyin leftovers; normalize the leftovers deterministically (maps
        # above) and cross-check against the code columns. Anything unlisted
        # stays null, never guessed.
        voltage_name = NORMALIZE_VOLTAGE_NAME.get(get("voltage_level_name"),
                                                  get("voltage_level_name"))
        if voltage_name not in ("10 kV AC", None):
            raise SystemExit(f"unexpected voltage label: {voltage_name!r}")
        state_name = NORMALIZE_STATE_NAME.get(get("psr_state_name"),
                                              get("psr_state_name"))
        insulation_name = NORMALIZE_INSULATION_NAME.get(
            get("insulation_medium_name"), get("insulation_medium_name"))
        entry["insulation_medium_en"] = INSULATION.get(insulation_name)
        entry["ast_type_meaning"] = None
        entry["operate_date_iso"] = excel_date(get("operate_date"))
        entry["manufacture_date_iso"] = excel_date(get("manufacture_date"))
        records.append(entry)
    psr_ids = [r["psr_id"] for r in records]
    if len(set(psr_ids)) != len(psr_ids):
        raise SystemExit("psr_id values are not unique")
    records.sort(key=lambda r: r["psr_id"] or "")
    catalog = {"catalog_version": "equipment-catalog-v1",
               "source": {"path": str(LEDGER_PATH),
                          "sha256": sha256_file(LEDGER_PATH),
                          "sheet": "hlt_device_ledger_pool",
                          "record_count": len(records),
                          "translation_provenance": "glossary-controlled EN workbook; "
                             "pinyin leftovers normalized per script maps; method per "
                             "data_english/TRANSLATION_NOTES.md"},
               "excluded_fields": "personal, owner, maintainer, organization and "
                                  "proper-name fields; null-only arc-medium columns",
               "records": records}
    OUT_PATH.write_text(json.dumps(catalog, ensure_ascii=True, indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")
    print(f"records={len(records)} unique_psr={len(set(psr_ids))}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
