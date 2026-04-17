from __future__ import annotations

from typing import Any
import hashlib
import json
import unicodedata


CANONICALIZATION_VERSION = "gpu-job-c14n-v1"


def normalize_value(value: Any) -> Any:
    if isinstance(value, dict):
        return {str(key): normalize_value(value[key]) for key in sorted(value)}
    if isinstance(value, list):
        return [normalize_value(item) for item in value]
    if isinstance(value, str):
        return unicodedata.normalize("NFC", value)
    if isinstance(value, float):
        if value.is_integer():
            return int(value)
        return float(format(value, ".12g"))
    return value


def canonical_json(value: Any) -> str:
    normalized = normalize_value(value)
    return json.dumps(normalized, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def canonical_hash(value: Any) -> dict[str, str]:
    payload = canonical_json(value)
    return {
        "canonicalization_version": CANONICALIZATION_VERSION,
        "sha256": sha256_text(payload),
    }
