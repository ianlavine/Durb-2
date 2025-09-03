from dataclasses import dataclass, field
from typing import List, Optional


@dataclass
class Player:
    id: int
    color: str  # hex color string like "#ffcc00"


@dataclass
class Node:
    id: int
    x: float
    y: float
    juice: float = 2.0
    owner: Optional[int] = None  # player id
    attached_edge_ids: List[int] = field(default_factory=list)


@dataclass
class Edge:
    id: int
    source_node_id: int
    target_node_id: int
    bidirectional: bool
    # If True, direction is source->target. If False, direction is target->source.
    # For one-way edges, this should remain True (source is the fixed from_node).
    forward: bool = True
    on: bool = False
    flowing: bool = False


