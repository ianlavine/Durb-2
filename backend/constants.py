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
GAME_MODES: Tuple[str, ...] = ("sparse", "brass")
DEFAULT_GAME_MODE: str = GAME_MODES[0]


# Gameplay flow tuning
NODE_MIN_JUICE: float = 0.0
NODE_MAX_JUICE_BY_MODE: Dict[str, float] = {
    "sparse": 500.0,
    "brass": 200.0,
}
NODE_MAX_JUICE: float = NODE_MAX_JUICE_BY_MODE[DEFAULT_GAME_MODE]
PRODUCTION_RATE_PER_NODE: float = 0.4
MAX_TRANSFER_RATIO: float = 0.95
INTAKE_TRANSFER_RATIO: float = 0.7
RESERVE_TRANSFER_RATIO: float = 0.006

# Overflow tuning
OVERFLOW_JUICE_TO_GOLD_RATIO: float = 15.0  # 15 juice -> 1 pending gold
OVERFLOW_PENDING_GOLD_PAYOUT: float = 10.0   # payout after 10 pending gold -> $10


# Economy tuning
GOLD_REWARD_FOR_NEUTRAL_CAPTURE_BY_MODE: Dict[str, float] = {
    "sparse": 10.0,
    "brass": 10.0,
}
GOLD_REWARD_FOR_NEUTRAL_CAPTURE: float = GOLD_REWARD_FOR_NEUTRAL_CAPTURE_BY_MODE[DEFAULT_GAME_MODE]
GOLD_REWARD_FOR_ENEMY_CAPTURE: float = 0.0
PASSIVE_INCOME_ENABLED: bool = False
PASSIVE_GOLD_PER_TICK: float = 1.0 / 30.0
PASSIVE_GOLD_PER_SECOND: float = PASSIVE_GOLD_PER_TICK / TICK_INTERVAL_SECONDS
STARTING_GOLD: float = 0.0


# Node sizing
UNOWNED_NODE_BASE_JUICE: float = 50.0


# Bridge costs
BRIDGE_BASE_COST: float = 0.0
BRIDGE_COST_PER_UNIT_DISTANCE_BY_MODE: Dict[str, float] = {
    "sparse": 1.0,
    "brass": 1.0,
}
BRIDGE_COST_PER_UNIT_DISTANCE: float = BRIDGE_COST_PER_UNIT_DISTANCE_BY_MODE[DEFAULT_GAME_MODE]

# Bridge build timing (ticks required per unit world distance)
BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE_BY_MODE: Dict[str, float] = {
    "sparse": 0.3,
    "brass": 0.3,
}
BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE: float = BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE_BY_MODE[DEFAULT_GAME_MODE]

# Warp geometry (mirror frontend)
WARP_MARGIN_RATIO_X: float = 0.06
WARP_MARGIN_RATIO_Y: float = 0.10


def normalize_game_mode(value: str) -> str:
    """Return a supported game mode, treating legacy names as aliases."""
    if not isinstance(value, str):
        return DEFAULT_GAME_MODE
    lowered = value.strip().lower()
    if lowered == "passive":  # legacy alias
        lowered = "basic"
    if lowered == "pop":  # legacy alias now mapped to warp
        lowered = "warp"
    return lowered if lowered in GAME_MODES else DEFAULT_GAME_MODE


def get_neutral_capture_reward(mode: str) -> float:
    """Return the gold reward for capturing a neutral node for the given mode."""
    key = normalize_game_mode(mode)
    return GOLD_REWARD_FOR_NEUTRAL_CAPTURE_BY_MODE.get(key, GOLD_REWARD_FOR_NEUTRAL_CAPTURE)


def get_bridge_cost_per_unit(mode: str) -> float:
    """Return the bridge cost per unit distance for the given mode."""
    key = normalize_game_mode(mode)
    return BRIDGE_COST_PER_UNIT_DISTANCE_BY_MODE.get(key, BRIDGE_COST_PER_UNIT_DISTANCE)


def get_bridge_build_ticks_per_unit(mode: str) -> float:
    """Return the bridge build ticks per unit world distance for the given mode."""
    key = normalize_game_mode(mode)
    return BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE_BY_MODE.get(key, BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE)


def get_node_max_juice(mode: str) -> float:
    """Return the node max juice value for the given mode."""
    key = normalize_game_mode(mode)
    return NODE_MAX_JUICE_BY_MODE.get(key, NODE_MAX_JUICE)
