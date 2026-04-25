from __future__ import annotations

from .base import Lane


LANE = Lane(
    lane_id="vast_pyworker_serverless",
    provider="vast",
    resource_kind="endpoint_workergroup",
    description="Vast pyworker serverless lane",
)
