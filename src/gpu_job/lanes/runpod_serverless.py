from __future__ import annotations

from .base import Lane


LANE = Lane(
    lane_id="runpod_serverless",
    provider="runpod",
    resource_kind="serverless_endpoint",
    description="RunPod serverless endpoint lane",
)
