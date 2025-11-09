"""Utilities related to moving nodes when resolving geometry constraints."""
from __future__ import annotations

import math
from typing import Callable, Dict, List, Optional, Tuple

from .models import Edge
from .state import GraphState
from .constants import MIN_PIPE_JOIN_ANGLE_DEGREES


# Global switch in case node movement needs to be fully disabled.
ENABLE_NODE_MOVEMENT = False


NodeMovementCallback = Optional[Callable[[Edge], None]]


def resolve_sharp_angles(
    state: Optional[GraphState],
    new_edge: Edge,
    apply_edge_geometry: NodeMovementCallback = None,
) -> List[Dict[str, float]]:
    """Adjust nearby nodes to maintain a minimum pipe join angle.

    Args:
        state: Active graph state containing nodes/edges.
        new_edge: The newly created edge that may cause sharp joins.
        apply_edge_geometry: Optional callback to refresh edge geometry when
            nodes move (e.g., GameEngine._apply_edge_warp_geometry).

    Returns:
        A list of movement dicts suitable for frontend updates.
    """
    if not state or not ENABLE_NODE_MOVEMENT:
        return []

    min_angle_deg = max(0.0, float(MIN_PIPE_JOIN_ANGLE_DEGREES))
    if min_angle_deg <= 0.0:
        return []

    min_angle_rad = math.radians(min_angle_deg)
    epsilon = 1e-6
    adjusted: Dict[int, Tuple[float, float]] = {}

    nodes = state.nodes
    edges = state.edges

    endpoint_pairs = [
        (new_edge.source_node_id, new_edge.target_node_id),
        (new_edge.target_node_id, new_edge.source_node_id),
    ]

    for shared_id, opposite_id in endpoint_pairs:
        shared_node = nodes.get(shared_id)
        opposite_node = nodes.get(opposite_id)
        if not shared_node or not opposite_node:
            continue

        base_dx = opposite_node.x - shared_node.x
        base_dy = opposite_node.y - shared_node.y
        base_length = math.hypot(base_dx, base_dy)
        if base_length <= epsilon:
            continue

        base_angle = math.atan2(base_dy, base_dx)

        attached_ids = list(shared_node.attached_edge_ids)
        for edge_id in attached_ids:
            if edge_id == new_edge.id:
                continue
            neighbor_edge = edges.get(edge_id)
            if not neighbor_edge:
                continue

            if neighbor_edge.source_node_id == shared_id:
                target_id = neighbor_edge.target_node_id
            elif neighbor_edge.target_node_id == shared_id:
                target_id = neighbor_edge.source_node_id
            else:
                continue

            if target_id == opposite_id:
                continue

            target_node = nodes.get(target_id)
            if not target_node:
                continue

            vec_dx = target_node.x - shared_node.x
            vec_dy = target_node.y - shared_node.y
            vec_length = math.hypot(vec_dx, vec_dy)
            if vec_length <= epsilon:
                continue

            dot = base_dx * vec_dx + base_dy * vec_dy
            denom = base_length * vec_length
            if denom <= epsilon:
                continue
            cos_angle = max(-1.0, min(1.0, dot / denom))
            angle = math.acos(cos_angle)
            if angle >= min_angle_rad:
                continue

            cross = base_dx * vec_dy - base_dy * vec_dx
            if abs(cross) <= epsilon:
                direction = 1.0
            else:
                direction = 1.0 if cross > 0 else -1.0

            desired_offset = direction * min_angle_rad
            new_angle = base_angle + desired_offset
            new_x = shared_node.x + vec_length * math.cos(new_angle)
            new_y = shared_node.y + vec_length * math.sin(new_angle)

            target_node.x = new_x
            target_node.y = new_y
            adjusted[target_id] = (new_x, new_y)
            state.record_node_movement(target_id, new_x, new_y)

            if apply_edge_geometry:
                for attached_id in target_node.attached_edge_ids:
                    attached_edge = edges.get(attached_id)
                    if attached_edge:
                        apply_edge_geometry(attached_edge)

    return [
        {"nodeId": node_id, "x": coords[0], "y": coords[1]}
        for node_id, coords in adjusted.items()
    ]
