from typing import Any, Dict, List, Tuple

PLAYER_COLOR_SCHEMES: List[Dict[str, Any]] = [
    {"color": "#FF6B6B", "secondary": ["#FFC6C7", "#C44D58"]},
    {"color": "#4ECDC4", "secondary": ["#D7FFF8", "#177E89"]},
    {"color": "#FFD166", "secondary": ["#FFF3B0", "#E09F3E"]},
    {"color": "#9B5DE5", "secondary": ["#E0C3FC", "#5A189A"]},
]

MIN_FRIEND_PLAYERS = 2
MAX_FRIEND_PLAYERS = 4


# Core timing
TICK_INTERVAL_SECONDS: float = 0.1

# Game modes
GAME_MODES: Tuple[str, ...] = ("passive", "pop")
DEFAULT_GAME_MODE: str = GAME_MODES[0]
POP_NODE_REWARD: float = 10.0


# Gameplay flow tuning
NODE_MIN_JUICE: float = 0.0
NODE_MAX_JUICE: float = 500.0
PRODUCTION_RATE_PER_NODE: float = 0.7
MAX_TRANSFER_RATIO: float = 0.95
INTAKE_TRANSFER_RATIO: float = 0.75
RESERVE_TRANSFER_RATIO: float = 0.007


# Economy tuning
GOLD_REWARD_FOR_NEUTRAL_CAPTURE: float = 10.0
GOLD_REWARD_FOR_ENEMY_CAPTURE: float = 0.0
PASSIVE_INCOME_ENABLED: bool = True
PASSIVE_GOLD_PER_TICK: float = 1.0 / 30.0
PASSIVE_GOLD_PER_SECOND: float = PASSIVE_GOLD_PER_TICK / TICK_INTERVAL_SECONDS
STARTING_GOLD: float = 0.0


# Node sizing
UNOWNED_NODE_BASE_JUICE: float = 50.0


# Bridge costs
BRIDGE_BASE_COST: float = 0.0
BRIDGE_COST_PER_UNIT_DISTANCE: float = 2.0
