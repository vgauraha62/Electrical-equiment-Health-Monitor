"""Manual asset-mapping store (stdlib only, file-based, local-only).

Purpose
-------
Some supplied source records do not carry a reliable matching equipment ID, so
they cannot be linked to an equipment item by the exact-ID rule the rest of the
application uses. This module lets a person record a *manual* assignment of one
such source record to one equipment item, together with the basis for the
assignment and a full, append-only audit trail. It never edits, regenerates, or
"corrects" the source records themselves.

Boundaries (this milestone)
---------------------------
* Read-only by default. The write API is gated by ``ENABLE_ASSET_MAPPING`` (see
  ``app/main.py``); the read projections work with or without it.
* Local file store only. No BigQuery, Cloud Storage, model, or other cloud call.
* Exact-ID links and manual assignments stay visibly distinct. A manual
  assignment is never rendered as source-confirmed or verified, and the actor
  string is self-asserted (this application has no authentication).
* A record whose scope is not asset-specific (a class-level reference, or a
  record that names more than one asset) can only be recorded as ``unresolved``;
  it is never forced onto a single equipment item.
* Candidate text search returns search *clues* only. No similar name, timestamp,
  row order, nearby equipment, or similar reading is ever treated as a link.

Store layout
------------
One JSON document per source record, named ``<mapping_id>.json`` where
``mapping_id`` is a deterministic digest of ``family + record key``. Keying by
source record makes "one record assigned to two equipment items" a
single-document conflict rather than two silent rows. Writes are atomic (tmp
file + ``os.replace``), every mutation bumps ``version`` and appends an
immutable history event, and removal is a soft, audited ``removed`` status so
the trail survives.
"""
from __future__ import annotations

import csv
import hashlib
import json
import os
from datetime import datetime, timezone
from pathlib import Path

try:
    from . import agent_contract as contract
except ImportError:  # Supports `python app/main.py` during local preview.
    import agent_contract as contract

STORE_VERSION = "asset_mapping_store.v1"
REGISTRY_VERSION = "asset_mapping_registry.v1"
SNAPSHOT_VERSION = "asset_mapping_snapshot.v1"

ACTIVE_STATUSES = ("manually_assigned", "unresolved")
STATUSES = ("manually_assigned", "unresolved", "removed")
METHODS = ("manual_user_assignment", "unresolved_ambiguous_scope")
BASIS_ORIGINS = ("user_provided", "source_document")
SCOPES = ("asset_specific", "multi_asset", "class_level")

EXACT_ID_METHOD = "exact_id_string"
MAX_BASIS = 500
MAX_ACTOR = 200

TELEMETRY_FAMILY = "telemetry_record_set"
DEFECT_REFERENCE_FAMILY = "defect_reference_library"
DEFECT_CSV_REL = "data/input/reference/defect_reference_sample.csv"

LINK_CAVEAT = ("An exact ID-string match is a data link only; it does not prove "
               "physical identity.")
MANUAL_CAVEAT = ("A manual assignment records a person's stated basis for a link; it "
                 "is not confirmed by the source and is not a verified identity.")
SEARCH_CAVEAT = ("Text search returns search clues only. A name, timestamp, row order, "
                 "nearby equipment, or similar reading is never proof of a link.")

# Fields surfaced for a defect-reference row. Kept explicit so a candidate view
# shows the original identifiers before assignment without dumping whole rows.
_DEFECT_FIELDS = (
    "standard_id", "equipment_type", "equipment_subtype", "component_type",
    "defect_location", "defect_description", "classification_basis", "severity",
    "phase", "remediation_work_type", "maintenance_class", "remediation_method",
    "work_order_required",
)

_UNAVAILABLE_FAMILIES = (
    ("defect_record", "Defect records (asset-specific)",
     "Asset-specific defect records are not supplied in this runtime package. Only "
     "a draft, unreviewed translation exists as development material, so this "
     "family is unavailable and no placeholder record is created."),
    ("test_record", "Test records",
     "No supplied source provides asset-specific test records, so this family is "
     "unavailable and no placeholder record is created."),
    ("inspection_record", "Inspection records",
     "No supplied source provides asset-specific inspection records, so this family "
     "is unavailable and no placeholder record is created."),
    ("maintenance_work_order", "Maintenance / work-order records",
     "No supplied source provides asset-specific maintenance or work-order records, "
     "so this family is unavailable and no placeholder record is created."),
)


class MappingError(Exception):
    def __init__(self, message, status=400):
        super().__init__(message)
        self.status = status


# --- small helpers -----------------------------------------------------------

def now_iso():
    return datetime.now(timezone.utc).isoformat()


def _norm(value):
    if not isinstance(value, str):
        return None
    stripped = value.strip()
    return stripped or None


def store_dir(root):
    """Path of the local mapping store. The directory is created on first write."""
    return Path(os.getenv("ASSET_MAPPING_DIR", root / "data/output/asset_mappings"))


def mapping_id(source_family, source_record_key):
    material = f"{source_family}\x1f{source_record_key}"
    return "map_" + hashlib.sha256(material.encode("utf-8")).hexdigest()[:20]


def _sha256_file(path):
    digest = hashlib.sha256()
    try:
        with Path(path).open("rb") as handle:
            for chunk in iter(lambda: handle.read(65536), b""):
                digest.update(chunk)
    except OSError:
        return None
    return digest.hexdigest()


def _combined_sha(manifest, files):
    """A deterministic digest over the verified per-file hashes, or ``None``."""
    if not files or not manifest:
        return None
    pairs = []
    for name in files:
        sha = manifest.get(name)
        if not isinstance(sha, str):
            return None
        pairs.append([name, sha])
    return contract.content_digest(pairs)


# --- source-record registry (real supplied data only) ------------------------

def _telemetry_records(rows, catalog_records, manifest):
    grouped = {}
    for row in rows or []:
        key = _norm(row.get("psr_id"))
        if key is None:
            continue
        entry = grouped.setdefault(key, {
            "equipment_type_codes": set(), "observation_count": 0,
            "latest_observation": "", "measurement_codes": set(),
            "source_files": set()})
        entry["equipment_type_codes"].add(row.get("equipment_type_code"))
        entry["observation_count"] += 1
        observed = row.get("observed_at") or ""
        if observed > entry["latest_observation"]:
            entry["latest_observation"] = observed
        entry["measurement_codes"].add(row.get("measurement_type_code"))
        entry["source_files"].add(row.get("source_file_id"))
    ledger_keys = {_norm(record.get("psr_id"))
                   for record in (catalog_records or []) if isinstance(record, dict)}
    records = {}
    for key in sorted(grouped):
        entry = grouped[key]
        files = sorted(name for name in entry["source_files"] if name)
        linked = key if key in ledger_keys else None
        if linked is None:
            note = ("This record carries one telemetry asset ID but has no exact ledger "
                    "ID match, so its equipment assignment is not known from the source.")
        else:
            note = ("This record already matches a ledger record by exact ID string, so "
                    "it is an exact-ID link, not a manual assignment.")
        records[key] = {
            "source_family": TELEMETRY_FAMILY,
            "source_record_key": key,
            "scope": "asset_specific",
            "record_label": f"Telemetry asset {key}",
            "original_identifiers": {
                "psr_id": key,
                "equipment_type_codes": sorted(c for c in entry["equipment_type_codes"] if c),
            },
            "fields": {
                "observation_count": entry["observation_count"],
                "latest_observation": entry["latest_observation"],
                "measurement_codes": sorted(c for c in entry["measurement_codes"] if c),
                "source_files": files,
            },
            "source_locator": {"kind": "telemetry_asset", "asset_id": key,
                               "source_files": files},
            "source_sha256": _combined_sha(manifest, files),
            "exact_id_linked_equipment_id": linked,
            "scope_note": note,
        }
    return records


def _defect_reference_records(root):
    path = Path(root) / DEFECT_CSV_REL
    if not path.is_file():
        return {}
    sha = _sha256_file(path)
    try:
        with path.open(encoding="utf-8-sig", newline="") as handle:
            rows = list(csv.DictReader(handle))
    except (OSError, ValueError):
        return {}
    records = {}
    for index, row in enumerate(rows):
        key = _norm(row.get("standard_id"))
        if key is None or key in records:
            continue
        records[key] = {
            "source_family": DEFECT_REFERENCE_FAMILY,
            "source_record_key": key,
            "scope": "class_level",
            "record_label": f"Defect reference {key}",
            "original_identifiers": {
                "standard_id": key,
                "equipment_type": row.get("equipment_type"),
                "equipment_subtype": row.get("equipment_subtype"),
                "component_type": row.get("component_type"),
            },
            "fields": {field: row.get(field) for field in _DEFECT_FIELDS},
            "source_locator": {"kind": "defect_reference_row", "file": path.name,
                               "row_index": index, "standard_id": key},
            "source_sha256": sha,
            "exact_id_linked_equipment_id": None,
            "scope_note": ("This is a class-level defect-reference row: the source names "
                           "an equipment type, not a specific asset, so its scope is not "
                           "asset-specific and it can only be recorded as unresolved."),
        }
    return records


def build_registry(root, *, rows=None, catalog=None, manifest=None):
    """Enumerate the source families and records actually supplied right now.

    Availability is derived from the supplied data, never assumed: a family with
    no records is reported unavailable, and unavailable families carry no record
    placeholders. ``rows`` are the same telemetry rows the rest of the app serves.
    """
    root = Path(root)
    rows = rows or []
    if isinstance(catalog, dict):
        catalog_records = catalog.get("records", [])
    else:
        catalog_records = catalog or []
    manifest = manifest or {}

    telemetry = _telemetry_records(rows, catalog_records, manifest)
    telemetry_files = sorted({name for record in telemetry.values()
                              for name in record["source_locator"]["source_files"]})
    defect = _defect_reference_records(root)
    defect_files = sorted({record["source_locator"]["file"] for record in defect.values()})

    families = [
        {
            "family_id": TELEMETRY_FAMILY,
            "label": "Telemetry asset records",
            "scope": "asset_specific",
            "availability": "available" if rows else "unavailable",
            "record_count": len(telemetry),
            "source_files": telemetry_files,
            "source_sha256": _combined_sha(manifest, telemetry_files),
            "note": ("Individual telemetry assets, each with its own recorded asset ID. "
                     "Assets already matching a ledger record by exact ID are shown as "
                     "exact-ID links and are not manually assignable."),
        },
        {
            "family_id": DEFECT_REFERENCE_FAMILY,
            "label": "Defect reference library (standard sample)",
            "scope": "class_level",
            "availability": "available" if defect else "unavailable",
            "record_count": len(defect),
            "source_files": defect_files,
            "source_sha256": next(iter({record["source_sha256"]
                                        for record in defect.values()}), None),
            "note": ("Class-level defect-reference rows (equipment type, not a specific "
                     "asset), draft translation, excluded from retrieval. Their scope is "
                     "not asset-specific, so they can only be recorded as unresolved."),
        },
    ]
    for family_id, label, note in _UNAVAILABLE_FAMILIES:
        families.append({
            "family_id": family_id,
            "label": label,
            "scope": "asset_specific",
            "availability": "unavailable",
            "record_count": None,
            "source_files": [],
            "source_sha256": None,
            "note": note,
        })

    digest_material = [
        [family["family_id"], family["availability"], family["record_count"],
         [[key, record["source_sha256"]] for key, record in sorted(records.items())]]
        for family, records in ((families[0], telemetry), (families[1], defect))
    ]
    return {
        "registry_version": REGISTRY_VERSION,
        "families": families,
        "records": {TELEMETRY_FAMILY: telemetry, DEFECT_REFERENCE_FAMILY: defect},
        "registry_digest_sha256": contract.content_digest(digest_material),
    }


def _family(registry, family_id):
    return next((family for family in registry.get("families", [])
                 if family["family_id"] == family_id), None)


def _record(registry, family_id, record_key):
    return registry.get("records", {}).get(family_id, {}).get(record_key)


# --- validation --------------------------------------------------------------

def _normalize_actor(actor):
    label = actor.get("label") if isinstance(actor, dict) else actor
    if label is not None:
        if not isinstance(label, str) or not label.strip():
            raise MappingError("actor label must be a non-empty string when provided.")
        label = label.strip()[:MAX_ACTOR]
    return {"label": label, "verified": False}


def _validate_basis(basis):
    if not isinstance(basis, str) or not basis.strip():
        raise MappingError("A short basis for the assignment is required.")
    basis = basis.strip()
    if len(basis) > MAX_BASIS:
        raise MappingError(f"basis must be at most {MAX_BASIS} characters.")
    return basis


def _resolve_record(registry, family_id, record_key, source_sha256=None,
                    source_locator=None):
    if not isinstance(family_id, str) or not family_id.strip():
        raise MappingError("source_family is required as a non-empty string.")
    if not isinstance(record_key, str) or not record_key.strip():
        raise MappingError("source_record_key is required as a non-empty string.")
    family = _family(registry, family_id)
    if family is None:
        raise MappingError(f"Unknown source family {family_id!r}.", status=404)
    if family["availability"] != "available":
        raise MappingError(f"Source family {family_id!r} is not available in this "
                           "runtime package, so nothing can be assigned to it.",
                           status=422)
    record = _record(registry, family_id, record_key)
    if record is None:
        raise MappingError(f"Unknown source record {record_key!r} in {family_id!r}.",
                           status=404)
    if source_sha256 is not None and source_sha256 != record["source_sha256"]:
        raise MappingError("Declared source hash does not match the current source "
                           "record; refusing to record a mapping against changed or "
                           "unverified source content.", status=422)
    if source_locator is not None and source_locator != record["source_locator"]:
        raise MappingError("Declared source locator does not match the current source "
                           "record.", status=422)
    return record


def _validate_target(equipment_ids, target):
    if not isinstance(target, str) or not target.strip():
        raise MappingError("target_equipment_id is required as a non-empty string.")
    target = target.strip()
    if target not in equipment_ids:
        raise MappingError("Equipment not found in ledger or telemetry.", status=404)
    return target


def _resolve_status(record, status, target):
    if status is None:
        status = "manually_assigned" if record["scope"] == "asset_specific" else "unresolved"
    if status not in ACTIVE_STATUSES:
        raise MappingError(f"status must be one of {', '.join(ACTIVE_STATUSES)}.")
    if status == "manually_assigned":
        if record["scope"] != "asset_specific":
            raise MappingError(
                f"This record's scope is {record['scope']!r}, not asset-specific; it "
                "cannot be forced onto one equipment item. Record it as unresolved "
                "instead.")
        linked = record.get("exact_id_linked_equipment_id")
        if linked is not None:
            raise MappingError(f"This record is already linked to {linked} by exact ID; "
                               "an exact-ID link is not a manual assignment.")
        if target == record["source_record_key"]:
            raise MappingError("A record cannot be manually assigned to its own ID.")
    method = ("manual_user_assignment" if status == "manually_assigned"
              else "unresolved_ambiguous_scope")
    return status, method


# --- store -------------------------------------------------------------------

def _path(directory, document_id):
    if not document_id or "/" in document_id or "\\" in document_id \
            or document_id.startswith("."):
        raise MappingError("Invalid mapping ID.", status=404)
    return directory / f"{document_id}.json"


def _write_atomic(path, document):
    tmp = path.with_suffix(".tmp")
    tmp.write_text(json.dumps(document, ensure_ascii=True, indent=2, sort_keys=True) + "\n",
                   encoding="utf-8")
    os.replace(tmp, path)


def _read(directory, document_id):
    try:
        return json.loads(_path(directory, document_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        raise MappingError("Mapping not found.", status=404)
    except ValueError:
        raise MappingError("Mapping record is corrupt.", status=500)


def _load_existing(directory, document_id):
    try:
        return json.loads(_path(directory, document_id).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return None
    except ValueError:
        raise MappingError("Mapping record is corrupt.", status=500)


def create_mapping(directory, registry, equipment_ids, *, target_equipment_id,
                   source_family, source_record_key, basis, actor=None,
                   basis_origin="user_provided", supporting_reference=None,
                   status=None, source_sha256=None, source_locator=None):
    """Record a manual assignment (or an unresolved entry) for one source record.

    Returns ``(document, created)``. An identical active mapping is idempotent; a
    different active mapping for the same source record is a conflict; and a
    soft-removed mapping is reactivated (audited) rather than duplicated.
    """
    if basis_origin not in BASIS_ORIGINS:
        raise MappingError(f"basis_origin must be one of {', '.join(BASIS_ORIGINS)}.")
    if supporting_reference is not None:
        if not isinstance(supporting_reference, str) or not supporting_reference.strip():
            raise MappingError("supporting_reference must be a non-empty string when "
                               "provided.")
        supporting_reference = supporting_reference.strip()[:MAX_BASIS]

    record = _resolve_record(registry, source_family, source_record_key,
                             source_sha256, source_locator)
    target = _validate_target(equipment_ids, target_equipment_id)
    basis = _validate_basis(basis)
    status, method = _resolve_status(record, status, target)
    actor_block = _normalize_actor(actor)
    document_id = mapping_id(record["source_family"], record["source_record_key"])
    Path(directory).mkdir(parents=True, exist_ok=True)

    existing = _load_existing(directory, document_id)
    stamp = now_iso()
    if existing is not None and existing.get("status") != "removed":
        identical = (existing.get("target_equipment_id") == target
                     and existing.get("status") == status
                     and existing.get("basis") == basis
                     and existing.get("basis_origin") == basis_origin
                     and existing.get("supporting_reference") == supporting_reference)
        if identical:
            return existing, False
        raise MappingError(
            f"This source record is already mapped to "
            f"{existing.get('target_equipment_id')!r} (mapping {document_id}). Edit or "
            "remove that mapping instead of creating a conflicting one.", status=409)

    base_history = existing.get("history", []) if existing is not None else []
    version = (existing.get("version", 0) if existing is not None else 0) + 1
    document = _document(document_id, record, target, status, method, basis,
                         basis_origin, supporting_reference, actor_block, version,
                         stamp, base_history)
    _write_atomic(_path(directory, document_id), document)
    return document, True


def _document(document_id, record, target, status, method, basis, basis_origin,
              supporting_reference, actor, version, stamp, history):
    event = ("recreated" if history and history[-1].get("event") == "removed"
             else "created")
    new_history = list(history)
    new_history.append({"seq": len(new_history) + 1, "at": stamp, "event": event,
                        "status": status, "target_equipment_id": target,
                        "actor": actor})
    return {
        "mapping_id": document_id,
        "store_version": STORE_VERSION,
        "source_family": record["source_family"],
        "source_record_key": record["source_record_key"],
        "source_record_label": record["record_label"],
        "source_scope": record["scope"],
        "source_locator": record["source_locator"],
        "source_sha256": record["source_sha256"],
        "target_equipment_id": target,
        "method": method,
        "status": status,
        "basis": basis,
        "basis_origin": basis_origin,
        "supporting_reference": supporting_reference,
        "actor": actor,
        "version": version,
        "created_at": history[0]["at"] if history else stamp,
        "updated_at": stamp,
        "history": new_history,
    }


def update_mapping(directory, registry, equipment_ids, document_id, from_version, *,
                   target_equipment_id=None, basis=None, status=None,
                   basis_origin=None, supporting_reference=None, actor=None):
    current = _read(directory, document_id)
    if from_version != current["version"]:
        raise MappingError(f"Stale version: mapping is at v{current['version']}; "
                           "nothing was overwritten.", status=409)
    if current["status"] == "removed":
        raise MappingError("A removed mapping cannot be edited; create a new "
                           "assignment instead.", status=422)

    record = _record(registry, current["source_family"], current["source_record_key"])
    if record is None:
        raise MappingError("The source record is no longer available, so its mapping "
                           "cannot be edited.", status=422)
    target = (_validate_target(equipment_ids, target_equipment_id)
              if target_equipment_id is not None else current["target_equipment_id"])
    new_basis = _validate_basis(basis) if basis is not None else current["basis"]
    new_origin = basis_origin if basis_origin is not None else current["basis_origin"]
    if new_origin not in BASIS_ORIGINS:
        raise MappingError(f"basis_origin must be one of {', '.join(BASIS_ORIGINS)}.")
    if supporting_reference is None:
        supporting_reference = current["supporting_reference"]
    elif supporting_reference == "":
        supporting_reference = None
    else:
        if not isinstance(supporting_reference, str) or not supporting_reference.strip():
            raise MappingError("supporting_reference must be a non-empty string when "
                               "provided.")
        supporting_reference = supporting_reference.strip()[:MAX_BASIS]
    new_status, new_method = _resolve_status(record, status, target)

    changed = {key: {"from": current[key], "to": value}
               for key, value in (("target_equipment_id", target), ("basis", new_basis),
                                  ("status", new_status), ("basis_origin", new_origin),
                                  ("supporting_reference", supporting_reference))
               if current[key] != value}
    if not changed:
        return current
    stamp = now_iso()
    actor_block = _normalize_actor(actor) if actor is not None else current["actor"]
    history = list(current["history"])
    history.append({"seq": len(history) + 1, "at": stamp, "event": "updated",
                    "changed": sorted(changed), "actor": actor_block})
    updated = dict(current)
    updated.update({"target_equipment_id": target, "basis": new_basis,
                    "basis_origin": new_origin, "method": new_method,
                    "status": new_status, "supporting_reference": supporting_reference,
                    "actor": actor_block, "version": current["version"] + 1,
                    "updated_at": stamp, "history": history,
                    "source_scope": record["scope"],
                    "source_locator": record["source_locator"],
                    "source_sha256": record["source_sha256"]})
    _write_atomic(_path(directory, document_id), updated)
    return updated


def remove_mapping(directory, document_id, from_version, note=None, actor=None):
    current = _read(directory, document_id)
    if from_version != current["version"]:
        raise MappingError(f"Stale version: mapping is at v{current['version']}; "
                           "nothing was overwritten.", status=409)
    if current["status"] == "removed":
        raise MappingError("This mapping is already removed.", status=422)
    if note is not None and (not isinstance(note, str) or not note.strip()):
        raise MappingError("note must be a non-empty string when provided.")
    stamp = now_iso()
    actor_block = _normalize_actor(actor) if actor is not None else current["actor"]
    history = list(current["history"])
    history.append({"seq": len(history) + 1, "at": stamp, "event": "removed",
                    "from_status": current["status"], "note": note.strip() if note else None,
                    "actor": actor_block})
    updated = dict(current)
    updated.update({"status": "removed", "version": current["version"] + 1,
                    "updated_at": stamp, "history": history})
    _write_atomic(_path(directory, document_id), updated)
    return updated


def get_mapping(directory, document_id):
    return _read(directory, document_id)


def list_mappings(directory, *, equipment=None, family=None, include_removed=False):
    found = []
    for path in sorted(Path(directory).glob("*.json")):
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except ValueError:
            continue
        if equipment is not None and document.get("target_equipment_id") != equipment:
            continue
        if family is not None and document.get("source_family") != family:
            continue
        if not include_removed and document.get("status") == "removed":
            continue
        found.append(document)
    return found


def _validate_manual_document(document, registry, equipment_ids):
    """Validate a saved manual assignment against current source and equipment."""
    if not isinstance(document, dict):
        raise MappingError("Mapping snapshot contains a non-object record.", status=503)
    family = document.get("source_family")
    key = document.get("source_record_key")
    if document.get("mapping_id") != mapping_id(family, key):
        raise MappingError("Mapping ID does not match its source record.", status=503)
    if document.get("store_version") != STORE_VERSION:
        raise MappingError("Mapping uses an unsupported store version.", status=503)
    if document.get("status") != "manually_assigned" or \
            document.get("method") != "manual_user_assignment":
        raise MappingError("Only active manual assignments can be published.", status=503)
    record = _resolve_record(registry, family, key, document.get("source_sha256"),
                             document.get("source_locator"))
    source_hash = document.get("source_sha256")
    if (not isinstance(source_hash, str) or len(source_hash) != 64
            or any(char not in "0123456789abcdef" for char in source_hash)
            or source_hash != record.get("source_sha256")):
        raise MappingError("Mapping source hash is absent, malformed, or stale.",
                           status=503)
    target = _validate_target(equipment_ids, document.get("target_equipment_id"))
    status, method = _resolve_status(record, document.get("status"), target)
    if status != "manually_assigned" or method != document.get("method"):
        raise MappingError("Mapping status or method is inconsistent.", status=503)
    if document.get("source_record_label") != record["record_label"] or \
            document.get("source_scope") != record["scope"]:
        raise MappingError("Mapping label or source scope differs from current source.",
                           status=503)
    _validate_basis(document.get("basis"))
    if document.get("basis_origin") not in BASIS_ORIGINS:
        raise MappingError("Mapping basis origin is invalid.", status=503)
    history = document.get("history")
    version = document.get("version")
    if not isinstance(history, list) or not history or \
            not isinstance(version, int) or isinstance(version, bool) or version < 1:
        raise MappingError("Mapping audit history or version is invalid.", status=503)
    if len(history) != version or any(
            not isinstance(event, dict) or event.get("seq") != index
            for index, event in enumerate(history, start=1)):
        raise MappingError("Mapping audit history is inconsistent.", status=503)
    actor = document.get("actor")
    if not isinstance(actor, dict) or actor.get("verified") is not False:
        raise MappingError("Manual assignment actor must remain unverified.", status=503)
    return document


def _snapshot_body(registry, mappings):
    return {
        "snapshot_version": SNAPSHOT_VERSION,
        "registry_digest_sha256": registry["registry_digest_sha256"],
        "mapping_count": len(mappings),
        "mappings": mappings,
    }


def build_mapping_snapshot(directory, registry, equipment_ids):
    """Build a deterministic snapshot of active, valid manual relationships.

    Unresolved and removed records are deliberately omitted: they are not asset
    relationships. Corrupt store files or invalid active mappings fail export.
    """
    mappings = []
    directory = Path(directory)
    for path in sorted(directory.glob("*.json")) if directory.exists() else []:
        try:
            document = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError) as exc:
            raise MappingError(f"Cannot export corrupt mapping file {path.name}.",
                               status=422) from exc
        if not isinstance(document, dict):
            raise MappingError(f"Cannot export non-object mapping file {path.name}.",
                               status=422)
        if document.get("status") in ("removed", "unresolved"):
            continue
        mappings.append(_validate_manual_document(document, registry, equipment_ids))
    mappings.sort(key=lambda item: (item["source_family"], item["source_record_key"]))
    identities = [(item["source_family"], item["source_record_key"])
                  for item in mappings]
    if len(identities) != len(set(identities)):
        raise MappingError("Duplicate source records found in mapping store.", status=422)
    body = _snapshot_body(registry, mappings)
    return {**body, "snapshot_digest_sha256": contract.content_digest(body)}


def load_mapping_snapshot(path, registry, equipment_ids):
    """Load and validate a packaged snapshot; a missing file means no mappings."""
    path = Path(path)
    if not path.is_file():
        return []
    try:
        snapshot = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise MappingError("Packaged mapping snapshot is unreadable.", status=503) from exc
    if not isinstance(snapshot, dict) or \
            snapshot.get("snapshot_version") != SNAPSHOT_VERSION:
        raise MappingError("Packaged mapping snapshot has an unsupported version.",
                           status=503)
    mappings = snapshot.get("mappings")
    if not isinstance(mappings, list) or snapshot.get("mapping_count") != len(mappings):
        raise MappingError("Packaged mapping snapshot count is invalid.", status=503)
    if snapshot.get("registry_digest_sha256") != registry.get("registry_digest_sha256"):
        raise MappingError("Packaged mapping snapshot was built from different source data.",
                           status=503)
    body = _snapshot_body(registry, mappings)
    if snapshot.get("snapshot_digest_sha256") != contract.content_digest(body):
        raise MappingError("Packaged mapping snapshot digest does not match.", status=503)
    seen = set()
    for document in mappings:
        _validate_manual_document(document, registry, equipment_ids)
        identity = (document["source_family"], document["source_record_key"])
        if identity in seen:
            raise MappingError("Packaged mapping snapshot contains duplicate records.",
                               status=503)
        seen.add(identity)
    return mappings


def effective_mappings(directory, snapshot_path, registry, equipment_ids,
                       *, equipment=None, include_removed=False):
    """Combine immutable packaged links with local edits; local state overlays."""
    snapshot = load_mapping_snapshot(snapshot_path, registry, equipment_ids)
    combined = {item["mapping_id"]: item for item in snapshot}
    local = list_mappings(directory, include_removed=True)
    for document in local:
        # Local state is the editable development source of truth. A tombstone
        # overlays the snapshot too, allowing a local removal to take effect.
        combined[document.get("mapping_id")] = document
    result = []
    for document in combined.values():
        if not include_removed and document.get("status") == "removed":
            continue
        if equipment is not None and document.get("target_equipment_id") != equipment:
            continue
        result.append(document)
    return sorted(result, key=lambda item: (item.get("source_family", ""),
                                            item.get("source_record_key", "")))


# --- read projections --------------------------------------------------------

def decorate(document, registry):
    """Add read-time availability/staleness flags. Never asserts identity."""
    record = _record(registry, document.get("source_family"),
                     document.get("source_record_key"))
    family = _family(registry, document.get("source_family"))
    available = (family is not None and family["availability"] == "available"
                 and record is not None)
    current = (available and record.get("source_sha256") is not None
               and record.get("source_sha256") == document.get("source_sha256"))
    status = document.get("status")
    link_kind = status if status in ACTIVE_STATUSES else "removed"
    out = dict(document)
    out.update({
        "link_kind": link_kind,
        "source_available": available,
        "source_current": bool(current),
        "source_changed": bool(available and not current),
        "verification_status": ("manual_user_assignment_not_source_confirmed"
                                if link_kind == "manually_assigned"
                                else "unresolved_not_a_link" if link_kind == "unresolved"
                                else "removed"),
        "identity_caveat": MANUAL_CAVEAT,
    })
    if record is not None:
        out["source_record_label"] = record["record_label"]
        out["source_scope"] = record["scope"]
    return out


def candidates(registry, directory, *, family=None, q="", exclude_linked=True,
               limit=50, existing_documents=None):
    """Available source records, with any existing link/mapping already shown."""
    if family is not None and _family(registry, family) is None:
        raise MappingError(f"Unknown source family {family!r}.", status=404)
    if not isinstance(limit, int) or isinstance(limit, bool) or not 1 <= limit <= 200:
        raise MappingError("limit must be an integer between 1 and 200.")
    needle = (q or "").strip().lower()
    existing = {}
    documents = (existing_documents if existing_documents is not None else
                 list_mappings(directory, include_removed=True))
    for document in documents:
        if document.get("status") in ACTIVE_STATUSES:
            existing[(document.get("source_family"), document.get("source_record_key"))] = \
                {"mapping_id": document["mapping_id"], "status": document["status"],
                 "target_equipment_id": document["target_equipment_id"]}

    items = []
    for fam in registry.get("families", []):
        if fam["availability"] != "available":
            continue
        if family is not None and fam["family_id"] != family:
            continue
        for key, record in sorted(registry.get("records", {}).get(fam["family_id"], {}).items()):
            linked = record.get("exact_id_linked_equipment_id")
            mapping = existing.get((fam["family_id"], key))
            if exclude_linked and (linked is not None or mapping is not None):
                continue
            if needle and needle not in _search_blob(record):
                continue
            items.append({
                "source_family": fam["family_id"],
                "family_label": fam["label"],
                "source_record_key": key,
                "record_label": record["record_label"],
                "scope": record["scope"],
                "scope_note": record["scope_note"],
                "assignable": record["scope"] == "asset_specific" and linked is None,
                "original_identifiers": record["original_identifiers"],
                "fields": record["fields"],
                "source_locator": record["source_locator"],
                "source_sha256": record["source_sha256"],
                "exact_id_linked_equipment_id": linked,
                "existing_mapping": mapping,
            })
    truncated = len(items) > limit
    return {
        "count": min(len(items), limit),
        "total_candidates": len(items),
        "truncated": truncated,
        "search_caveat": SEARCH_CAVEAT,
        "items": items[:limit],
    }


def _search_blob(record):
    parts = [record["source_record_key"], record["record_label"]]
    parts += [str(value) for value in record["original_identifiers"].values()]
    parts += [str(value) for value in record["fields"].values()]
    return " ".join(parts).lower()


def _link_entry(document):
    history = document.get("history", [])
    return {
        "mapping_id": document.get("mapping_id"),
        "source_family": document.get("source_family"),
        "source_record_key": document.get("source_record_key"),
        "source_record_label": document.get("source_record_label"),
        "link_kind": document.get("link_kind"),
        "method": document.get("method"),
        "status": document.get("status"),
        "scope": document.get("source_scope"),
        "basis": document.get("basis"),
        "basis_origin": document.get("basis_origin"),
        "supporting_reference": document.get("supporting_reference"),
        "actor": document.get("actor"),
        "target_equipment_id": document.get("target_equipment_id"),
        "source_locator": document.get("source_locator"),
        "source_sha256": document.get("source_sha256"),
        "source_available": document.get("source_available"),
        "source_current": document.get("source_current"),
        "source_changed": document.get("source_changed"),
        "verification_status": document.get("verification_status"),
        "identity_caveat": document.get("identity_caveat"),
        "version": document.get("version"),
        "created_at": document.get("created_at"),
        "updated_at": document.get("updated_at"),
        "history_count": len(history),
    }


def equipment_links(registry, documents, equipment_id):
    """Group an equipment item's links by source family, exact-ID vs manual."""
    exact_id = []
    record = _record(registry, TELEMETRY_FAMILY, equipment_id)
    if record is not None and record.get("exact_id_linked_equipment_id") == equipment_id:
        exact_id.append({
            "source_family": TELEMETRY_FAMILY,
            "source_record_key": equipment_id,
            "source_record_label": record["record_label"],
            "link_kind": "exact_id",
            "method": EXACT_ID_METHOD,
            "status": "linked",
            "scope": record["scope"],
            "basis": "Source ID strings match exactly (telemetry asset ID equals ledger psr_id).",
            "basis_origin": "source_document",
            "supporting_reference": None,
            "actor": None,
            "target_equipment_id": equipment_id,
            "source_locator": record["source_locator"],
            "source_sha256": record["source_sha256"],
            "source_available": True,
            "source_current": True,
            "source_changed": False,
            "verification_status": "exact_id_string_match",
            "identity_caveat": LINK_CAVEAT,
            "version": None,
            "created_at": None,
            "updated_at": None,
            "history_count": 0,
            "mapping_id": None,
        })

    manual, unresolved, removed = [], [], []
    for document in documents:
        entry = _link_entry(document)
        if entry["link_kind"] == "manually_assigned":
            manual.append(entry)
        elif entry["link_kind"] == "unresolved":
            unresolved.append(entry)
        else:
            removed.append(entry)

    groups = {}
    for entry in exact_id + manual + unresolved:
        group = groups.setdefault(entry["source_family"], {
            "source_family": entry["source_family"],
            "family_label": _family_label(registry, entry["source_family"]),
            "links": []})
        group["links"].append(entry)
    return {
        "equipment_id": equipment_id,
        "exact_id_links": exact_id,
        "manual_assignments": manual,
        "unresolved": unresolved,
        "removed": removed,
        "groups": [groups[key] for key in sorted(groups)],
        "counts": {"exact_id": len(exact_id), "manually_assigned": len(manual),
                   "unresolved": len(unresolved)},
        "unavailable_families": [
            {"family_id": family["family_id"], "label": family["label"],
             "note": family["note"]}
            for family in registry.get("families", [])
            if family["availability"] != "available"],
        "exact_id_caveat": LINK_CAVEAT,
        "manual_caveat": MANUAL_CAVEAT,
    }


def _family_label(registry, family_id):
    family = _family(registry, family_id)
    return family["label"] if family else family_id
