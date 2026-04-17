from __future__ import annotations

from pathlib import Path
from typing import Any
import json

from .canonical import canonical_hash
from .models import app_data_dir, now_unix


WORKFLOW_VERSION = "gpu-job-workflow-v1"


def workflow_dir() -> Path:
    path = app_data_dir() / "workflows"
    path.mkdir(parents=True, exist_ok=True)
    return path


def validate_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    nodes = workflow.get("nodes", [])
    edges = workflow.get("edges", [])
    errors = []
    if not isinstance(nodes, list) or not nodes:
        errors.append("nodes must be a non-empty list")
        nodes = []
    node_ids = [str(node.get("node_id") or "") for node in nodes if isinstance(node, dict)]
    if len(node_ids) != len(set(node_ids)):
        errors.append("node_id values must be unique")
    node_set = set(node_ids)
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_set}
    for edge in edges if isinstance(edges, list) else []:
        src = str(edge.get("from") or "")
        dst = str(edge.get("to") or "")
        if src not in node_set or dst not in node_set:
            errors.append(f"edge references missing node: {src}->{dst}")
            continue
        adjacency[src].append(dst)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> None:
        if node_id in visiting:
            errors.append("workflow graph must be acyclic")
            return
        if node_id in visited:
            return
        visiting.add(node_id)
        for child in adjacency.get(node_id, []):
            visit(child)
        visiting.remove(node_id)
        visited.add(node_id)

    for node_id in node_set:
        visit(node_id)
    return {"ok": not errors, "workflow_version": WORKFLOW_VERSION, "errors": errors}


def save_workflow(workflow: dict[str, Any]) -> dict[str, Any]:
    validation = validate_workflow(workflow)
    workflow_id = str(workflow.get("workflow_id") or canonical_hash(workflow)["sha256"][:16])
    data = {
        "workflow_version": WORKFLOW_VERSION,
        "workflow_id": workflow_id,
        "created_at": now_unix(),
        "status": "created" if validation["ok"] else "failed",
        "workflow": workflow,
        "validation": validation,
    }
    path = workflow_dir() / f"{workflow_id}.json"
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"ok": validation["ok"], "workflow_id": workflow_id, "path": str(path), "validation": validation}


def load_workflow(workflow_id: str) -> dict[str, Any]:
    path = workflow_dir() / f"{workflow_id}.json"
    if not path.is_file():
        return {"ok": False, "error": "workflow not found", "workflow_id": workflow_id}
    return {"ok": True, "workflow": json.loads(path.read_text()), "path": str(path)}
