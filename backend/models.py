from dataclasses import dataclass, field
from typing import List, Optional, Tuple

from .constants import UNOWNED_NODE_BASE_JUICE


@dataclass
class Player:
    id: int
    color: str  # hex color string like "#ffcc00"
    secondary_colors: List[str] = field(default_factory=list)
    name: str = ""


@dataclass
class Node:
    id: int
    x: float
    y: float
    juice: float = UNOWNED_NODE_BASE_JUICE
    owner: Optional[int] = None  # player id
    attached_edge_ids: List[int] = field(default_factory=list)
    cur_intake: float = 0.0  # amount of juice received from friendly nodes in last tick
    pending_gold: float = 0.0  # overflow progress towards gold payout
    


@dataclass
class Edge:
    id: int
    source_node_id: int
    target_node_id: int
    pipe_type: str = "normal"  # 'normal' or 'gold'; gold only exists in cross mode
    # All edges are now one-way only, from source to target
    on: bool = False
    flowing: bool = False
    # Amount of juice that flowed through this edge in the most recent tick
    last_transfer: float = 0.0
    # Bridge build gating
    build_ticks_required: int = 0  # number of ticks required before edge can turn on
    build_ticks_elapsed: int = 0  # ticks that have elapsed since creation
    building: bool = False  # if True, edge cannot be toggled/clicked and will not flow
    warp_axis: str = "none"
    warp_segments: List[Tuple[float, float, float, float]] = field(default_factory=list)
