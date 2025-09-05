import json
import math
import random
from dataclasses import dataclass
from pathlib import Path
from typing import List, Optional, Tuple


SEED: Optional[int] = 39
NODE_COUNT: int = 60
DESIRED_EDGE_COUNT: int = 55
ONE_WAY_PERCENT: float = 0.5

SCREEN_WIDTH: Optional[int] = None
SCREEN_HEIGHT: Optional[int] = None
SCREEN_MARGIN: int = 40

OUTPUT_PATH: str = "graph.json"


@dataclass
class Node:
    id: int
    x: float
    y: float


@dataclass
class Edge:
    id: int
    source: int
    target: int


def try_get_screen_size() -> Tuple[int, int]:
    # Deprecated for runtime server usage. Keep for CLI fallback.
    if SCREEN_WIDTH is not None and SCREEN_HEIGHT is not None:
        return SCREEN_WIDTH, SCREEN_HEIGHT
    return 1920, 1080


def generate_node_positions(num_nodes: int, width: int, height: int, margin: int) -> List[Node]:
    usable_w = max(1, width - 2 * margin)
    usable_h = max(1, height - 2 * margin)
    aspect = usable_w / usable_h
    cols = max(1, math.ceil(math.sqrt(num_nodes * aspect)))
    rows = max(1, math.ceil(num_nodes / cols))
    cell_w = usable_w / cols
    cell_h = usable_h / rows
    nodes: List[Node] = []
    index = 0
    random_jitter_w = cell_w * 0.3
    random_jitter_h = cell_h * 0.3
    for r in range(rows):
        for c in range(cols):
            if index >= num_nodes:
                break
            cx = margin + (c + 0.5) * cell_w
            cy = margin + (r + 0.5) * cell_h
            x = cx + random.uniform(-random_jitter_w, random_jitter_w)
            y = cy + random.uniform(-random_jitter_h, random_jitter_h)
            nodes.append(Node(index, x, y))
            index += 1
        if index >= num_nodes:
            break
    return nodes


def _orientation(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> int:
    val = (by - ay) * (cx - bx) - (bx - ax) * (cy - by)
    if abs(val) < 1e-9:
        return 0
    return 1 if val > 0 else 2


def _on_segment(ax: float, ay: float, bx: float, by: float, cx: float, cy: float) -> bool:
    return min(ax, bx) - 1e-9 <= cx <= max(ax, bx) + 1e-9 and min(ay, by) - 1e-9 <= cy <= max(ay, by) + 1e-9


def segments_intersect(p1: Tuple[float, float], p2: Tuple[float, float], q1: Tuple[float, float], q2: Tuple[float, float]) -> bool:
    ax, ay = p1
    bx, by = p2
    cx, cy = q1
    dx, dy = q2
    o1 = _orientation(ax, ay, bx, by, cx, cy)
    o2 = _orientation(ax, ay, bx, by, dx, dy)
    o3 = _orientation(cx, cy, dx, dy, ax, ay)
    o4 = _orientation(cx, cy, dx, dy, bx, by)
    if o1 != o2 and o3 != o4:
        return True
    if o1 == 0 and _on_segment(ax, ay, bx, by, cx, cy):
        return True
    if o2 == 0 and _on_segment(ax, ay, bx, by, dx, dy):
        return True
    if o3 == 0 and _on_segment(cx, cy, dx, dy, ax, ay):
        return True
    if o4 == 0 and _on_segment(cx, cy, dx, dy, bx, by):
        return True
    return False


def generate_planar_edges(nodes: List[Node], desired_edges: int, one_way_percent: float) -> List[Edge]:
    coords = [(n.x, n.y) for n in nodes]
    candidates: List[Tuple[float, int, int]] = []
    num_nodes = len(nodes)
    for i in range(num_nodes):
        xi, yi = coords[i]
        for j in range(i + 1, num_nodes):
            xj, yj = coords[j]
            d2 = (xi - xj) * (xi - xj) + (yi - yj) * (yi - yj)
            candidates.append((d2, i, j))
    candidates.sort(key=lambda t: t[0])
    edges: List[Edge] = []
    existing_segments: List[Tuple[Tuple[float, float], Tuple[float, float]]] = []
    def would_cross(i: int, j: int) -> bool:
        p1 = (nodes[i].x, nodes[i].y)
        p2 = (nodes[j].x, nodes[j].y)
        for (q1, q2) in existing_segments:
            if (q1 == p1) or (q1 == p2) or (q2 == p1) or (q2 == p2):
                continue
            if segments_intersect(p1, p2, q1, q2):
                return True
        return False
    taken_pairs = set()
    for _, i, j in candidates:
        if len(edges) >= desired_edges:
            break
        pair = (i, j) if i < j else (j, i)
        if pair in taken_pairs:
            continue
        if would_cross(i, j):
            continue
        taken_pairs.add(pair)
        is_one_way = random.random() < max(0.0, min(1.0, one_way_percent))
        if is_one_way:
            if random.random() < 0.5:
                source, target = i, j
            else:
                source, target = j, i
            edges.append(Edge(len(edges), source, target))
        else:
            edges.append(Edge(len(edges), i, j))
        existing_segments.append(((nodes[i].x, nodes[i].y), (nodes[j].x, nodes[j].y)))
    return edges


def main() -> None:
    if SEED is not None:
        random.seed(SEED)
    # Default to 100x100 coordinate space; frontend scales to window
    width, height = 100, 100
    nodes = generate_node_positions(NODE_COUNT, width, height, 0)
    edges = generate_planar_edges(nodes, DESIRED_EDGE_COUNT, ONE_WAY_PERCENT)
    data = {
        "screen": {"width": width, "height": height, "margin": 0},
        "nodes": [{"id": n.id, "x": round(n.x, 3), "y": round(n.y, 3)} for n in nodes],
        "edges": [
            {"id": e.id, "source": e.source, "target": e.target, "bidirectional": False}
            for e in edges
        ],
    }
    with open(Path(__file__).resolve().parent.parent / OUTPUT_PATH, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)
    print("Wrote graph.json")


if __name__ == "__main__":
    main()

def generate_graph_to_path(width: int, height: int, output_path: Path) -> None:
    """Generate a graph using the provided screen width/height and write to output_path.

    This avoids using tkinter for screen detection and is safe to call off the main thread.
    """
    if SEED is not None:
        random.seed(SEED)
    nodes = generate_node_positions(NODE_COUNT, width, height, 0)
    edges = generate_planar_edges(nodes, DESIRED_EDGE_COUNT, ONE_WAY_PERCENT)
    data = {
        "screen": {"width": width, "height": height, "margin": 0},
        "nodes": [{"id": n.id, "x": round(n.x, 3), "y": round(n.y, 3)} for n in nodes],
        "edges": [
            {"id": e.id, "source": e.source, "target": e.target, "bidirectional": False}
            for e in edges
        ],
    }
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(data, f, indent=2)



