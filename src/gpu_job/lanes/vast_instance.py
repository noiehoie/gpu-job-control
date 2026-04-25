from __future__ import annotations

from .base import Lane


LANE = Lane(
    lane_id="vast_instance",
    provider="vast",
    resource_kind="instance",
    description="Vast direct instance lane",
)
