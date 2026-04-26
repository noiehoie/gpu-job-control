from __future__ import annotations

from typing import Any

from .base import Lane
from .modal_function import LANE as MODAL_FUNCTION
from .runpod_pod import LANE as RUNPOD_POD
from .runpod_serverless import LANE as RUNPOD_SERVERLESS
from .vast_instance import LANE as VAST_INSTANCE
from .vast_pyworker_serverless import LANE as VAST_PYWORKER_SERVERLESS


LANES: dict[str, Lane] = {
    lane.lane_id: lane
    for lane in (
        MODAL_FUNCTION,
        RUNPOD_POD,
        RUNPOD_SERVERLESS,
        VAST_INSTANCE,
        VAST_PYWORKER_SERVERLESS,
    )
}

DEFAULT_LANE_BY_PROVIDER = {
    "modal": MODAL_FUNCTION.lane_id,
    "modal_function": MODAL_FUNCTION.lane_id,
    "runpod": RUNPOD_POD.lane_id,
    "runpod_pod": RUNPOD_POD.lane_id,
    "runpod_serverless": RUNPOD_SERVERLESS.lane_id,
    "vast": VAST_INSTANCE.lane_id,
    "vast_instance": VAST_INSTANCE.lane_id,
    "vast_pyworker_serverless": VAST_PYWORKER_SERVERLESS.lane_id,
}


def list_lanes() -> list[dict[str, Any]]:
    return [
        {
            "lane_id": lane.lane_id,
            "provider": lane.provider,
            "resource_kind": lane.resource_kind,
            "description": lane.description,
        }
        for lane in LANES.values()
    ]


def get_lane(lane_id: str) -> Lane:
    return LANES[lane_id]


def resolve_lane_id(provider: str, metadata: dict[str, Any] | None = None) -> str:
    metadata = metadata or {}
    explicit = str(metadata.get("provider_module_id") or "")
    if explicit in LANES:
        return explicit
    return DEFAULT_LANE_BY_PROVIDER.get(str(provider), "")
