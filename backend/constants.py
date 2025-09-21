from typing import Any, Dict, List

PLAYER_COLOR_SCHEMES: List[Dict[str, Any]] = [
    {"color": "#FF6B6B", "secondary": ["#FFC6C7", "#C44D58"]},
    {"color": "#4ECDC4", "secondary": ["#D7FFF8", "#177E89"]},
    {"color": "#FFD166", "secondary": ["#FFF3B0", "#E09F3E"]},
    {"color": "#9B5DE5", "secondary": ["#E0C3FC", "#5A189A"]},
]

MIN_FRIEND_PLAYERS = 2
MAX_FRIEND_PLAYERS = 4


# Gameplay flow tuning
EDGE_TOGGLE_PROBABILITY: float = 0.0
NODE_MIN_JUICE: float = 0.0
NODE_MAX_JUICE: float = 120.0
PRODUCTION_RATE_PER_NODE: float = 0.15
MAX_TRANSFER_RATIO: float = 0.95
INTAKE_TRANSFER_RATIO: float = 0.75
RESERVE_TRANSFER_RATIO: float = 0.01


# Economy tuning
GOLD_REWARD_FOR_NEUTRAL_CAPTURE: float = 10.0
GOLD_REWARD_FOR_ENEMY_CAPTURE: float = 0.0
PASSIVE_GOLD_PER_SECOND: float = 1.0 / 3.0
STARTING_GOLD: float = 0.0


# Node sizing
UNOWNED_NODE_BASE_SIZE: float = 8.0
UNOWNED_NODE_BASE_JUICE: float = 8.0


# Bridge costs
BRIDGE_BASE_COST: float = 0.0
BRIDGE_COST_PER_UNIT_DISTANCE: float = 2.0
