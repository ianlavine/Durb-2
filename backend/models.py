from dataclasses import dataclass, field
from typing import List, Optional

from .constants import UNOWNED_NODE_BASE_JUICE


@dataclass
class Player:
    id: int
    color: str  # hex color string like "#ffcc00"
    secondary_colors: List[str] = field(default_factory=list)


@dataclass
class Node:
    id: int
    x: float
    y: float
    juice: float = UNOWNED_NODE_BASE_JUICE
    owner: Optional[int] = None  # player id
    attached_edge_ids: List[int] = field(default_factory=list)
    cur_intake: float = 0.0  # amount of juice received from friendly nodes in last tick
    


@dataclass
class Edge:
    id: int
    source_node_id: int
    target_node_id: int
    # All edges are now one-way only, from source to target
    on: bool = False
    flowing: bool = False
