"""Utilities for resolving sharp/acute angles when new edges are added."""
from __future__ import annotations

import logging
import math
from typing import Dict, List, Optional, Set, Tuple, TYPE_CHECKING

# Geometry tuning
# Minimum separation angle (in degrees) between bridges meeting at a node before we auto-relax them
MIN_PIPE_JOIN_ANGLE_DEGREES: float = 22.5
# Maximum straight-line distance a node can be auto-moved when resolving sharp angles
MAX_SHARP_ANGLE_ADJUST_DISTANCE: float = 50.0
# Minimum standoff distance (world units) we keep from collisions when rotating pipes
COLLISION_CLEARANCE_DISTANCE: float = 5.0
COLLISION_REASON_LABELS = {
    "edge-path": "path-cross-pipe",
    "node-path": "path-cross-node",
    "final-overlap": "endpoint-overlap",
}
# Maximum angular increment (degrees) when simulating the swept path of moving pipes
PATH_SIMULATION_MAX_STEP_DEGREES: float = 3.0
# Hard upper bound on the number of simulation steps per relaxation
PATH_SIMULATION_MAX_STEPS: int = 32

from .models import Edge

if TYPE_CHECKING:  # pragma: no cover - only used for type hints
    from .game_engine import GameEngine
    from .models import Node


LOGGER = logging.getLogger(__name__)


def _record_node_movement(engine: "GameEngine", node_id: int, x: float, y: float) -> None:
    """Wrapper for recording node movements on the active game state."""
    if engine.state:
        engine.state.record_node_movement(node_id, x, y)


def _compute_candidate_position(
    shared_node: "Node",
    vec_length: float,
    old_angle: float,
    direction: float,
    rotation: float,
) -> Tuple[float, float]:
    new_angle = old_angle + direction * rotation
    new_x = shared_node.x + vec_length * math.cos(new_angle)
    new_y = shared_node.y + vec_length * math.sin(new_angle)
    return new_x, new_y


def _segment_point_distance(
    px: float,
    py: float,
    x1: float,
    y1: float,
    x2: float,
    y2: float,
) -> float:
    """Return the shortest distance between a point and a segment."""
    dx = x2 - x1
    dy = y2 - y1
    seg_len_sq = dx * dx + dy * dy
    if seg_len_sq <= 1e-12:
        return math.hypot(px - x1, py - y1)

    t = ((px - x1) * dx + (py - y1) * dy) / seg_len_sq
    t = max(0.0, min(1.0, t))
    proj_x = x1 + t * dx
    proj_y = y1 + t * dy
    return math.hypot(px - proj_x, py - proj_y)


def _movement_causes_collision(
    engine: "GameEngine",
    node: "Node",
    new_x: float,
    new_y: float,
    ignore_edge_ids: Optional[Set[int]] = None,
    ignore_node_ids: Optional[Set[int]] = None,
) -> Tuple[bool, Optional[str]]:
    state = engine.state
    if not state:
        return False, None

    edges = state.edges
    attached_edges = [edges.get(edge_id) for edge_id in getattr(node, "attached_edge_ids", [])]

    old_x, old_y = node.x, node.y
    node.x = new_x
    node.y = new_y

    collision_detected = False
    collision_reason: Optional[str] = None

    ignore_edge_ids = ignore_edge_ids or set()
    ignore_node_ids = ignore_node_ids or set()

    try:
        for edge in attached_edges:
            if not edge:
                continue
            source_node = state.nodes.get(edge.source_node_id)
            target_node = state.nodes.get(edge.target_node_id)
            if not source_node or not target_node:
                continue
            candidate_segments = [(
                source_node.x,
                source_node.y,
                target_node.x,
                target_node.y,
            )]
            intersecting = engine._find_intersecting_edges(
                source_node,
                target_node,
                candidate_segments,
                ignore_edge_ids=ignore_edge_ids,
            )
            if intersecting:
                collision_detected = True
                collision_reason = "edge-path"
                break

            for node_id, candidate in state.nodes.items():
                if (
                    not candidate
                    or node_id in ignore_node_ids
                    or node_id == source_node.id
                    or node_id == target_node.id
                ):
                    continue
                for seg in candidate_segments:
                    if _segment_point_distance(candidate.x, candidate.y, *seg) <= COLLISION_CLEARANCE_DISTANCE:
                        collision_detected = True
                        collision_reason = "node-path"
                        break
                if collision_detected:
                    break
            if collision_detected:
                break
    finally:
        node.x = old_x
        node.y = old_y

    return collision_detected, collision_reason


def _segment_intersects_wedge(
    p0x: float,
    p0y: float,
    p1x: float,
    p1y: float,
    radius: float,
    start_dir: Tuple[float, float],
    end_dir: Tuple[float, float],
    direction: float,
    eps: float = 1e-9,
) -> bool:
    """Check if a segment crosses the circular sector swept by the moving edge."""

    def clip_ge(t_min: float, t_max: float, a: float, b: float) -> Optional[Tuple[float, float]]:
        delta = b - a
        if abs(delta) <= eps:
            if a < -eps:
                return None
            return t_min, t_max
        boundary = (-eps - a) / delta
        if delta > 0:
            if boundary > t_max + eps:
                return None
            t_min = max(t_min, boundary)
        else:
            if boundary < t_min - eps:
                return None
            t_max = min(t_max, boundary)
        if t_min - t_max > eps:
            return None
        return t_min, t_max

    def clip_le(t_min: float, t_max: float, a: float, b: float) -> Optional[Tuple[float, float]]:
        delta = b - a
        if abs(delta) <= eps:
            if a > eps:
                return None
            return t_min, t_max
        boundary = (eps - a) / delta
        if delta > 0:
            if boundary < t_min - eps:
                return None
            t_max = min(t_max, boundary)
        else:
            if boundary > t_max + eps:
                return None
            t_min = max(t_min, boundary)
        if t_min - t_max > eps:
            return None
        return t_min, t_max

    def clip_circle(t_min: float, t_max: float) -> Optional[Tuple[float, float]]:
        r2 = radius * radius
        d0 = p0x * p0x + p0y * p0y
        d1 = p1x * p1x + p1y * p1y
        inside0 = d0 <= r2 + eps
        inside1 = d1 <= r2 + eps
        if inside0 and inside1:
            return t_min, t_max

        dx = p1x - p0x
        dy = p1y - p0y
        qa = dx * dx + dy * dy
        if qa <= eps:
            return (t_min, t_max) if inside0 else None

        qb = 2.0 * (p0x * dx + p0y * dy)
        qc = d0 - r2
        disc = qb * qb - 4.0 * qa * qc
        if disc < -eps:
            return None
        disc = max(0.0, disc)
        sqrt_disc = math.sqrt(disc)
        t1 = (-qb - sqrt_disc) / (2.0 * qa)
        t2 = (-qb + sqrt_disc) / (2.0 * qa)
        t_enter = min(t1, t2)
        t_exit = max(t1, t2)
        t_min = max(t_min, t_enter)
        t_max = min(t_max, t_exit)
        t_min = max(t_min, 0.0)
        t_max = min(t_max, 1.0)
        if t_min - t_max > eps:
            return None
        return t_min, t_max

    t_range: Optional[Tuple[float, float]] = (0.0, 1.0)

    start_cross_p0 = direction * (start_dir[0] * p0y - start_dir[1] * p0x)
    start_cross_p1 = direction * (start_dir[0] * p1y - start_dir[1] * p1x)
    t_range = clip_ge(t_range[0], t_range[1], start_cross_p0, start_cross_p1)
    if t_range is None:
        return False

    end_cross_p0 = direction * (end_dir[0] * p0y - end_dir[1] * p0x)
    end_cross_p1 = direction * (end_dir[0] * p1y - end_dir[1] * p1x)
    t_range = clip_le(t_range[0], t_range[1], end_cross_p0, end_cross_p1)
    if t_range is None:
        return False

    t_range = clip_circle(t_range[0], t_range[1])
    if t_range is None:
        return False

    return True


def _point_inside_wedge(
    px: float,
    py: float,
    radius: float,
    start_dir: Tuple[float, float],
    end_dir: Tuple[float, float],
    direction: float,
    eps: float = 1e-9,
) -> bool:
    start_val = direction * (start_dir[0] * py - start_dir[1] * px)
    if start_val < -eps:
        return False

    end_val = direction * (end_dir[0] * py - end_dir[1] * px)
    if end_val > eps:
        return False

    dist = math.hypot(px, py)
    return dist <= radius + max(eps, COLLISION_CLEARANCE_DISTANCE)


def _path_causes_collision(
    engine: "GameEngine",
    moving_edge: Optional[Edge],
    shared_node: "Node",
    target_node: "Node",
    vec_length: float,
    old_angle: float,
    direction: float,
    rotation: float,
    epsilon: float,
    moving_edge_ids: Optional[Set[int]] = None,
    moving_node_ids: Optional[Set[int]] = None,
) -> Tuple[bool, Optional[str]]:
    state = engine.state
    if not state or rotation <= epsilon or vec_length <= epsilon:
        return False, None

    moving_edge_id = moving_edge.id if moving_edge else None
    forbidden_nodes = {shared_node.id, target_node.id}
    if moving_node_ids:
        forbidden_nodes.update(moving_node_ids)
    forbidden_edges = set()
    if moving_edge_ids:
        forbidden_edges.update(moving_edge_ids)
    if moving_edge_id is not None:
        forbidden_edges.add(moving_edge_id)
    start_dir = (math.cos(old_angle), math.sin(old_angle))
    end_dir = (math.cos(old_angle + direction * rotation), math.sin(old_angle + direction * rotation))

    for edge_id, edge in state.edges.items():
        if not edge:
            continue
        if edge_id in forbidden_edges:
            continue
        if (
            edge.source_node_id in forbidden_nodes
            or edge.target_node_id in forbidden_nodes
        ):
            continue

        source_node = state.nodes.get(edge.source_node_id)
        target_node = state.nodes.get(edge.target_node_id)
        if not source_node or not target_node:
            continue

        rel_sx = source_node.x - shared_node.x
        rel_sy = source_node.y - shared_node.y
        rel_ex = target_node.x - shared_node.x
        rel_ey = target_node.y - shared_node.y
        if _segment_intersects_wedge(
            rel_sx,
            rel_sy,
            rel_ex,
            rel_ey,
            vec_length,
            start_dir,
            end_dir,
            direction,
        ):
            return True, "edge-path"

    for node_id, candidate in state.nodes.items():
        if not candidate or node_id in forbidden_nodes:
            continue
        rel_px = candidate.x - shared_node.x
        rel_py = candidate.y - shared_node.y
        if _point_inside_wedge(rel_px, rel_py, vec_length, start_dir, end_dir, direction):
            return True, "node-path"

    return False, None


def _simulate_path_collisions(
    engine: "GameEngine",
    shared_node: "Node",
    target_node: "Node",
    vec_length: float,
    old_angle: float,
    direction: float,
    rotation: float,
    epsilon: float,
    moving_edge_ids: Set[int],
    moving_node_ids: Set[int],
) -> Tuple[bool, Optional[str]]:
    """Ghost-move the node in small increments and verify the whole star stays clear."""
    state = engine.state
    if not state or rotation <= epsilon or vec_length <= epsilon:
        return False, None

    max_step_radians = math.radians(PATH_SIMULATION_MAX_STEP_DEGREES)
    if max_step_radians <= epsilon:
        max_step_radians = rotation
    steps = max(1, int(math.ceil(rotation / max_step_radians)))
    steps = min(PATH_SIMULATION_MAX_STEPS, steps)
    if steps <= 0:
        steps = 1

    for step_idx in range(1, steps + 1):
        fraction = step_idx / steps
        partial_rotation = rotation * fraction
        candidate_position = _compute_candidate_position(
            shared_node,
            vec_length,
            old_angle,
            direction,
            partial_rotation,
        )
        collision, detail = _movement_causes_collision(
            engine,
            target_node,
            *candidate_position,
            ignore_edge_ids=moving_edge_ids,
            ignore_node_ids=moving_node_ids,
        )
        if collision:
            return True, detail or "edge-path"

    return False, None


def _find_max_safe_rotation(
    engine: "GameEngine",
    shared_node: "Node",
    target_node: "Node",
    vec_length: float,
    old_angle: float,
    direction: float,
    max_rotation: float,
    epsilon: float,
    moving_edge: Edge,
    moving_edge_ids: Set[int],
    moving_node_ids: Set[int],
) -> Tuple[float, Optional[Tuple[float, float]], bool, Optional[str]]:
    if max_rotation <= epsilon:
        return 0.0, None, False, None

    candidate_position = _compute_candidate_position(shared_node, vec_length, old_angle, direction, max_rotation)
    path_collision, path_detail = _path_causes_collision(
        engine,
        moving_edge,
        shared_node,
        target_node,
        vec_length,
        old_angle,
        direction,
        max_rotation,
        epsilon,
        moving_edge_ids,
        moving_node_ids,
    )
    sampled_collision, sampled_detail = _simulate_path_collisions(
        engine,
        shared_node,
        target_node,
        vec_length,
        old_angle,
        direction,
        max_rotation,
        epsilon,
        moving_edge_ids,
        moving_node_ids,
    )
    if sampled_collision:
        path_collision = True
        if not path_detail:
            path_detail = sampled_detail
    final_collision, final_detail = _movement_causes_collision(
        engine,
        target_node,
        *candidate_position,
        ignore_edge_ids=moving_edge_ids,
        ignore_node_ids=moving_node_ids,
    )
    if not path_collision and not final_collision:
        return max_rotation, candidate_position, False, None

    limited_by_collision = True
    low = 0.0
    high = max_rotation
    best_rotation = 0.0
    best_position: Optional[Tuple[float, float]] = None
    max_iterations = 18
    min_delta = 1e-4
    collision_detail: Optional[str] = path_detail
    path_collision_detected = path_collision
    final_collision_detected = final_collision
    final_collision_detail = final_detail

    for _ in range(max_iterations):
        if high - low <= min_delta:
            break
        mid = (low + high) / 2.0
        if mid <= epsilon:
            break
        candidate_position = _compute_candidate_position(shared_node, vec_length, old_angle, direction, mid)
        path_hit, path_reason = _path_causes_collision(
            engine,
            moving_edge,
            shared_node,
            target_node,
            vec_length,
            old_angle,
            direction,
            mid,
            epsilon,
            moving_edge_ids,
            moving_node_ids,
        )
        if not path_hit:
            sampled_hit, sampled_reason = _simulate_path_collisions(
                engine,
                shared_node,
                target_node,
                vec_length,
                old_angle,
                direction,
                mid,
                epsilon,
                moving_edge_ids,
                moving_node_ids,
            )
            if sampled_hit:
                path_hit = True
                path_reason = sampled_reason

        if path_hit:
            path_collision_detected = True
            if path_reason and collision_detail not in {"edge-path", "node-path"}:
                collision_detail = path_reason
            elif not collision_detail:
                collision_detail = path_reason
            high = mid
            continue

        final_hit, final_reason = _movement_causes_collision(
            engine,
            target_node,
            *candidate_position,
            ignore_edge_ids=moving_edge_ids,
            ignore_node_ids=moving_node_ids,
        )
        if final_hit:
            final_collision_detected = True
            if not collision_detail:
                collision_detail = final_reason or "final-overlap"
            high = mid
        else:
            best_rotation = mid
            best_position = candidate_position
            low = mid

    if collision_detail is None:
        if path_collision_detected:
            collision_detail = "edge-path"
        elif final_collision_detected:
            collision_detail = final_collision_detail or "final-overlap"

    if limited_by_collision and best_rotation > 0.0:
        clearance_rotation = COLLISION_CLEARANCE_DISTANCE / max(vec_length, epsilon)
        if clearance_rotation > 0.0:
            if best_rotation <= clearance_rotation + epsilon:
                best_rotation = 0.0
                best_position = None
            else:
                best_rotation -= clearance_rotation
                best_position = _compute_candidate_position(
                    shared_node,
                    vec_length,
                    old_angle,
                    direction,
                    best_rotation,
                )

    return best_rotation, best_position, limited_by_collision, collision_detail


def resolve_sharp_angles(engine: "GameEngine", new_edge: Edge) -> List[Dict[str, float]]:
    """Shift attached nodes to avoid pipes meeting at an angle smaller than allowed."""
    state = engine.state
    if not state:
        return []

    if getattr(state, "screen_variant", "flat") != "flat":
        return []

    min_angle_deg = max(0.0, float(MIN_PIPE_JOIN_ANGLE_DEGREES))
    if min_angle_deg <= 0.0:
        return []

    min_angle_rad = math.radians(min_angle_deg)
    epsilon = 1e-6
    movement_reports: List[Dict[str, float]] = []
    max_move_distance = max(0.0, float(MAX_SHARP_ANGLE_ADJUST_DISTANCE))

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

            # Track every edge touching the moving node so we can ignore their mutual collisions
            moving_edge_ids: Set[int] = set()
            for attached_edge_id in getattr(target_node, "attached_edge_ids", []):
                edge_obj = edges.get(attached_edge_id)
                if edge_obj:
                    moving_edge_ids.add(edge_obj.id)

            moving_node_ids: Set[int] = {target_node.id}

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

            angle_shortfall = min_angle_rad - angle
            if angle_shortfall <= epsilon:
                continue

            cross = base_dx * vec_dy - base_dy * vec_dx
            direction = 1.0 if abs(cross) <= epsilon else (1.0 if cross > 0 else -1.0)

            bounded_shortfall = angle_shortfall
            if max_move_distance > 0.0:
                chord_ratio = max_move_distance / max(epsilon, 2.0 * vec_length)
                chord_ratio = max(0.0, min(1.0, chord_ratio))
                allowed_shortfall = 2.0 * math.asin(chord_ratio)
                if allowed_shortfall <= epsilon:
                    continue
                if bounded_shortfall > allowed_shortfall:
                    bounded_shortfall = allowed_shortfall

            old_angle = math.atan2(vec_dy, vec_dx)
            applied_rotation, candidate_position, collision_limited, collision_detail = _find_max_safe_rotation(
                engine,
                shared_node,
                target_node,
                vec_length,
                old_angle,
                direction,
                bounded_shortfall,
                epsilon,
                neighbor_edge,
                moving_edge_ids,
                moving_node_ids,
            )

            reasons: List[str] = []
            if bounded_shortfall + epsilon < angle_shortfall:
                reasons.append("distance-cap")

            if collision_limited and applied_rotation + epsilon < bounded_shortfall:
                reasons.append(COLLISION_REASON_LABELS.get(collision_detail or "final-overlap", "collision-limit"))

            if applied_rotation <= epsilon or not candidate_position:
                if collision_limited:
                    LOGGER.warning(
                        "Node %s movement blocked by collision when relaxing sharp angle (%s)",
                        target_id,
                        (collision_detail or "collision-limit"),
                    )
                    movement_reports.append(
                        {
                            "nodeId": target_id,
                            "x": target_node.x,
                            "y": target_node.y,
                            "moved": False,
                            "limited": True,
                            "limitReasons": reasons
                            or [
                                COLLISION_REASON_LABELS.get(
                                    collision_detail or "final-overlap",
                                    "collision-limit",
                                )
                            ],
                        }
                    )
                continue

            new_x, new_y = candidate_position

            old_x, old_y = target_node.x, target_node.y
            target_node.x = new_x
            target_node.y = new_y
            _record_node_movement(engine, target_id, new_x, new_y)

            move_distance = math.hypot(new_x - old_x, new_y - old_y)
            report: Dict[str, float] = {
                "nodeId": target_id,
                "x": new_x,
                "y": new_y,
                "moved": True,
            }
            if reasons:
                report["limited"] = True
                report["limitReasons"] = reasons
                LOGGER.warning(
                    "Node %s rotation limited by collision (applied %.3f of %.3f) [%s]",
                    target_id,
                    applied_rotation,
                    bounded_shortfall,
                    ",".join(reasons),
                )
            print(
                f"[resolve_sharp_angles] node {target_id} moved {move_distance:.3f} units "
                f"(allowed max {max_move_distance:.3f})"
            )
            movement_reports.append(report)

    return movement_reports
