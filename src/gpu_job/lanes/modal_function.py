from __future__ import annotations

from .base import Lane


LANE = Lane(
    lane_id="modal_function",
    provider="modal",
    resource_kind="function",
    description="Modal function execution lane",
)
