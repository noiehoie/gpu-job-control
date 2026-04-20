from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .config import config_path


IMAGE_CONTRACT_REGISTRY_VERSION = "gpu-job-image-contract-registry-v1"


def default_image_contract_registry_path() -> Path:
    return config_path("GPU_JOB_IMAGE_CONTRACT_REGISTRY", "image-contracts.json")


def load_image_contract_registry(path: Path | None = None) -> dict[str, Any]:
    registry_path = path or default_image_contract_registry_path()
    if not registry_path.exists():
        return {"registry_version": IMAGE_CONTRACT_REGISTRY_VERSION, "image_contracts": {}}
    data = json.loads(registry_path.read_text())
    data.setdefault("registry_version", IMAGE_CONTRACT_REGISTRY_VERSION)
    data.setdefault("image_contracts", {})
    return data


def image_contract_status(
    runtime: dict[str, Any],
    backends: dict[str, str],
    *,
    registry: dict[str, Any] | None = None,
) -> dict[str, Any]:
    registry = registry or load_image_contract_registry()
    contract_id = str(runtime.get("image_contract_id") or "")
    if not contract_id:
        return {
            "ok": False,
            "status": "missing_image_contract_reference",
            "contract_id": "",
            "required_backends": sorted(set(backends.values())),
            "reason": "provider runtime does not declare an image_contract_id",
        }
    contract = dict((registry.get("image_contracts") or {}).get(contract_id) or {})
    if not contract:
        return {
            "ok": False,
            "status": "missing_image_contract",
            "contract_id": contract_id,
            "required_backends": sorted(set(backends.values())),
            "reason": "image contract is not registered",
        }
    required = sorted(set(backends.values()))
    provided = set(str(item) for item in contract.get("provides_backends") or [])
    missing = sorted(set(required) - provided)
    if missing:
        return {
            "ok": False,
            "status": "image_contract_missing_backend",
            "contract_id": contract_id,
            "contract": contract,
            "missing_backends": missing,
            "required_backends": required,
            "reason": "registered image contract does not provide all required backends",
        }
    state = str(contract.get("status") or "unverified")
    if state != "verified":
        return {
            "ok": False,
            "status": state,
            "contract_id": contract_id,
            "contract": contract,
            "required_backends": required,
            "reason": "image contract is not verified",
        }
    return {
        "ok": True,
        "status": "verified",
        "contract_id": contract_id,
        "contract": contract,
        "required_backends": required,
        "reason": "image contract verified",
    }
