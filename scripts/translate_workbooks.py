"""Deterministic glossary-based workbook translation (stdlib only).

Reads Chinese workbooks from the vendored source archive, applies
data/en/glossary_technical.json with longest-match segmentation, and writes
English record files with source locators. Draft output pending bilingual and
domain review. Personal-name columns are excluded, never translated.

Unmatched CJK spans are reported by character offset (never pasted), so outputs
stay English-only and every gap is explicit.
"""
from __future__ import annotations

import datetime
import io
import json
import re
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
CJK = re.compile(r"[\u4e00-\u9fff\uf900-\ufaff\u3400-\u4dbf]")
IDENT = re.compile(r"[A-Za-z0-9_.\-/#\u03a9%\u2103:;+()\[\] ]+")
SERIAL = re.compile(r"\d{4,6}(\.\d+)?")

ARCHIVE_DIR = ROOT / "data/source_archive/originals"
GLOSSARY_PATH = ROOT / "data/en/glossary_technical.json"

PERSONAL_COLUMNS = {"discoverer_name", "eliminated_user_name", "creater_name",
                    "maintainer_name", "register_id", "register_name",
                    "reporter_contact",
                    "discoverer_crew_name", "eliminated_crew_name",
                    "register_crew_name", "creater_crew_name",
                    "equip_maintcrew_name", "equip_maintainer_name",
                    "editor_name"}
# Place-specific team names are operational identifiers: withheld by reference
# (never pasted) to avoid inventing transliterations.
TEAM_WITHHOLD_SUFFIX = "班"
DATE_COLUMNS = {"discovery_date", "eliminated_deadline_date", "ctime", "mtime"}

MEMBERS = {
    "defect_records": "defect_records",
    "defect_library": "defect_library",
}
# Vendored filenames are Chinese (see manifest for exact originals). These matching
# literals live in tooling only (scripts/ is outside the English-only scan) and
# are file locators, never translated content.
MEMBER_MATCH = {
    "defect_records": "缺陷记录",
    "defect_library": "缺陷描述标准库",
}


def find_member(logical):
    want = MEMBER_MATCH[logical]
    hits = []
    for archive in sorted(ARCHIVE_DIR.glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if want in name and name.endswith(".xlsx"):
                    hits.append((archive, name))
    if len(hits) != 1:
        raise FileNotFoundError(f"expected 1 archive member for {logical!r}, got {hits}")
    return hits[0]


def load_sheet(logical, sheetfile):
    archive, member = find_member(logical)
    with zipfile.ZipFile(archive) as z:
        blob = z.read(member)
    zz = zipfile.ZipFile(io.BytesIO(blob))
    try:
        sroot = ET.fromstring(zz.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(NS + "t"))
                  for si in sroot.findall(NS + "si")]
    except KeyError:
        shared = []
    sr = ET.fromstring(zz.read("xl/" + sheetfile))
    rows = []
    for row in sr.iter(NS + "row"):
        cells = {}
        for c in row.findall(NS + "c"):
            v = c.find(NS + "v")
            val = v.text if v is not None else None
            if c.get("t") == "s" and val is not None:
                try:
                    val = shared[int(val)]
                except (IndexError, ValueError):
                    pass
            cells["".join(x for x in c.get("r") if x.isalpha())] = val
        rows.append(cells)
    return rows


def excel_datetime(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    base = datetime.datetime(1899, 12, 30)
    return (base + datetime.timedelta(days=number)).isoformat(timespec="seconds")


def segment(text, keys_longest, glossary):
    """Return (english, identifiers, spans). Spans are [start, end) offsets."""
    tokens, identifiers, spans = [], [], []
    i = 0
    while i < len(text):
        match = None
        for key in keys_longest:
            if text.startswith(key, i):
                match = key
                break
        if match:
            tokens.append(glossary[match])
            i += len(match)
            continue
        char = text[i]
        if CJK.match(char):
            start = i
            while i < len(text) and CJK.match(text[i]):
                i += 1
            spans.append([start, i])
            tokens.append(f"[untranslated span {start}-{i}]")
            continue
        ident = IDENT.match(text, i)
        if ident:
            tokens.append(ident.group(0))
            identifiers.append(ident.group(0))
            i = ident.end()
            continue
        tokens.append(char)
        i += 1
    words, pending_space = [], False
    for token in tokens:
        if not token:
            continue
        if re.fullmatch(r"\s+", token):
            pending_space = True
            continue
        needs_space = pending_space or (
            words and not re.match(r"^[.,;:!?%\)\]]", token)
            and not re.search(r"[\(\[]$", words[-1]))
        words.append((" " if needs_space else "") + token)
        pending_space = False
    return "".join(words), sorted(set(identifiers)), spans


def translate_value(value, keys_longest, glossary, withheld_label=None):
    if value is None or value == "":
        return {"value": None, "missing": True}
    text = str(value)
    if withheld_label and text.strip().endswith(TEAM_WITHHOLD_SUFFIX) and \
            text.strip() not in glossary:
        return {"value": f"[{withheld_label}: see source row]",
                "missing": False, "withheld": True}
    if not CJK.search(text):
        return {"value": text, "missing": False}
    english, identifiers, spans = segment(text, keys_longest, glossary)
    return {"value": english, "missing": False,
            "retained_identifiers": identifiers,
            "untranslated_spans": spans,
            "needs_review": bool(spans)}


def main():
    glossary = json.load(GLOSSARY_PATH.open(encoding="utf-8"))["entries"]
    keys_longest = sorted(glossary, key=len, reverse=True)

    rows = load_sheet(MEMBERS["defect_records"], "worksheets/sheet1.xml")
    header = rows[0]
    out_records, gap_rows = [], 0
    for lineno, record in enumerate(rows[1:], start=2):
        entry = {"source": {"workbook": "SRC-DEFECT-RECORDS",
                            "sheet": "hlt_defect_record",
                            "source_row": lineno},
                 "translation_status": "translated_draft"}
        spans_total = 0
        for col, name in header.items():
            if name in PERSONAL_COLUMNS or "联系方式" in str(name):
                continue
            raw = record.get(col)
            if name in DATE_COLUMNS and raw is not None and SERIAL.fullmatch(str(raw)):
                entry[name] = {"value": excel_datetime(raw), "missing": False,
                               "note": "Excel serial wall-clock, no timezone assumed"}
                continue
            if name in ("discoverer_org_name", "container_name"):
                label = ("operational team identifier"
                         if name == "discoverer_org_name"
                         else "equipment display name")
                translated = translate_value(raw, keys_longest, glossary,
                                             withheld_label=label)
            else:
                translated = translate_value(raw, keys_longest, glossary)
            entry[name] = translated
            spans_total += len(translated.get("untranslated_spans", []))
        entry["excluded_personal_columns"] = sorted(
            name for name in header.values() if name in PERSONAL_COLUMNS)
        if spans_total:
            gap_rows += 1
        out_records.append(entry)
    records_path = ROOT / "data/en/defect_records_translated.json"
    records_path.write_text(json.dumps(out_records, ensure_ascii=True, indent=1,
                                       sort_keys=True) + "\n", encoding="utf-8")

    lib_rows = load_sheet(MEMBERS["defect_library"], "worksheets/sheet1.xml")
    lib_header = lib_rows[0]
    code_col = next(k for k, v in lib_header.items() if v == "设备类型")
    lib_out, lib_gaps = [], 0
    for lineno, record in enumerate(lib_rows[1:], start=2):
        if record.get(code_col) != "配电变压器":
            continue
        entry = {"source": {"workbook": "SRC-DEFECT-LIB", "sheet": "content",
                            "source_row": lineno,
                            "standard_id": record.get("D")},
                 "translation_status": "translated_draft"}
        spans_total = 0
        for col, name in lib_header.items():
            if "联系方式" in str(name):
                continue
            translated = translate_value(record.get(col), keys_longest, glossary)
            entry[f"col_{col}"] = translated
            spans_total += len(translated.get("untranslated_spans", []))
        if spans_total:
            lib_gaps += 1
        lib_out.append(entry)
    lib_path = ROOT / "data/en/defect_library_translated.json"
    lib_path.write_text(json.dumps(lib_out, ensure_ascii=True, indent=1,
                                   sort_keys=True) + "\n", encoding="utf-8")
    print(f"defect_records={len(out_records)} rows_with_spans={gap_rows}")
    print(f"defect_library_class_rows={len(lib_out)} rows_with_spans={lib_gaps}")


if __name__ == "__main__":
    main()
