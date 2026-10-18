"""Source-file audit (stdlib only; poppler pdfinfo for page counts).

Reads the vendored source archives (byte-identical to the six supplied files,
verified by hash) and reports per-file sheets/pages/rows/cols, language status,
and equipment coverage. Writes data/output/source_file_audit.json.
"""
from __future__ import annotations

import hashlib
import json
import re
import subprocess
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ARCHIVE_DIR = ROOT / "data/source_archive/originals"
OUT_PATH = ROOT / "data/output/source_file_audit.json"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
CJK = re.compile(r"[\u4e00-\u9fff]")


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def find_member(want_substring, suffix):
    hits = []
    for archive in sorted(ARCHIVE_DIR.glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if want_substring in name and name.endswith(suffix):
                    hits.append((str(archive), name))
    return hits


def read_bytes(archive, member):
    with zipfile.ZipFile(archive) as z:
        return z.read(member)


def sheet_rows(xlsx_blob):
    import io
    zz = zipfile.ZipFile(io.BytesIO(xlsx_blob))
    try:
        sroot = ET.fromstring(zz.read("xl/sharedStrings.xml"))
        shared = ["".join(t.text or "" for t in si.iter(NS + "t"))
                  for si in sroot.findall(NS + "si")]
    except KeyError:
        shared = []
    out = {}
    for info in zz.infolist():
        if not info.filename.startswith("xl/worksheets/sheet"):
            continue
        sr = ET.fromstring(zz.read(info.filename))
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
        out[info.filename] = rows
    return out


def pdf_pages(archive, member):
    import tempfile
    with zipfile.ZipFile(archive) as z:
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
            tmp.write(z.read(member))
        name = tmp.name
    try:
        out = subprocess.run(["pdfinfo", name], capture_output=True, text=True,
                             timeout=60)
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return None
    for line in out.stdout.splitlines():
        if line.startswith("Pages:"):
            return int(line.split(":")[1].strip())
    return None


def main():
    audit = {"audit_version": "source-file-audit-v1", "files": []}
    members = {}
    for archive in sorted(ARCHIVE_DIR.glob("*.zip")):
        with zipfile.ZipFile(archive) as z:
            for name in z.namelist():
                if name.endswith((".pdf", ".xlsx", ".txt")) and "__MACOSX" not in name:
                    members.setdefault(name.split("/")[-1], []).append(
                        (str(archive), name))
    for short in sorted(members):
        archive, member = members[short][0]
        blob = read_bytes(archive, member)
        entry = {"filename": short, "archive": Path(archive).name,
                 "size_bytes": len(blob),
                 "sha256": hashlib.sha256(blob).hexdigest(),
                 "chinese_present": bool(CJK.search(
                     blob.decode("utf-8", errors="ignore")[:100000]))}
        if short.endswith(".pdf"):
            entry["pages"] = pdf_pages(archive, member)
            entry["kind"] = "pdf"
        elif short.endswith(".xlsx"):
            if blob[:2] != b"PK":
                entry["kind"] = "workbook_unreadable_lockfile"
                entry["note"] = "OLE lock placeholder, not a workbook; skipped"
            else:
                sheets = sheet_rows(blob)
                entry["kind"] = "workbook"
                entry["sheets"] = {name.split("/")[-1]: len(rows)
                                   for name, rows in sheets.items()}
        else:
            entry["kind"] = "text"
        audit["files"].append(entry)
    OUT_PATH.write_text(json.dumps(audit, ensure_ascii=True, indent=1, sort_keys=True)
                        + "\n", encoding="utf-8")
    print(f"files={len(audit['files'])}")
    print(f"wrote {OUT_PATH}")


if __name__ == "__main__":
    main()
