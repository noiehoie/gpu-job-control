from __future__ import annotations

from .base import Lane


LANE = Lane(
    lane_id="runpod_pod",
    provider="runpod",
    resource_kind="pod",
    description="RunPod bounded Pod HTTP lane",
)
