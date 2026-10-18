"""Provenance helpers: checksums and source references."""
from __future__ import annotations

import hashlib
from pathlib import Path


def sha256_file(path):
    digest = hashlib.sha256()
    with Path(path).open("rb") as handle:
        for chunk in iter(lambda: handle.read(65536), b""):
            digest.update(chunk)
    return digest.hexdigest()


def source_ref(source_file_id, record_index=None, source_uri=None, checksum=None):
    return {
        "source_file_id": source_file_id,
        "source_record_index": record_index,
        "source_uri": source_uri,
        "checksum_sha256": checksum,
        "extraction_method": "telemetry_json_flatten_v1",
    }
