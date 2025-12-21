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
GAME_DURATION_MINUTES: int = 10
GAME_DURATION_SECONDS: float = float(GAME_DURATION_MINUTES * 60)

# Game modes
GAME_MODES: Tuple[str, ...] = (
    "sparse",
    "warp-old",
    "warp",
    "semi",
    "i-semi",
    "i-warp",
    "i-flat",
    "flat",
    "basic",
    "overflow",
    "nuke",
    "cross",
    "brass-old",
    "brass",
    "go",
    "sandbox",
)
DEFAULT_GAME_MODE: str = "sparse"

# Map layout tuning
NODE_POSITION_LAYOUTS: Tuple[str, ...] = ("grid", "random")
NODE_POSITION_LAYOUT: str = "grid"  # change to "random" to scatter nodes uniformly


# Gameplay flow tuning
NODE_MIN_JUICE: float = 0.0
NODE_MAX_JUICE: float = 300.0
PRODUCTION_RATE_PER_NODE: float = 0.2
MAX_TRANSFER_RATIO: float = 0.95
INTAKE_TRANSFER_RATIO: float = 0.7
RESERVE_TRANSFER_RATIO: float = 0.004

# Overflow tuning
OVERFLOW_JUICE_TO_GOLD_RATIO: float = 10.0  # 10 juice -> 1 pending gold (tunable)
OVERFLOW_PENDING_GOLD_PAYOUT: float = 2.0   # payout after 2 pending gold -> $2


# Economy tuning
GOLD_REWARD_FOR_NEUTRAL_CAPTURE: float = 3.0
GOLD_REWARD_FOR_ENEMY_CAPTURE: float = 0.0
PASSIVE_INCOME_ENABLED: bool = True
PASSIVE_GOLD_PER_TICK: float = 0.09  # 0.90/s at 0.1s tick rate
PASSIVE_GOLD_PER_SECOND: float = PASSIVE_GOLD_PER_TICK / TICK_INTERVAL_SECONDS
STARTING_GOLD: float = 0.0
MONEY_VICTORY_THRESHOLD: float = 300.0  # Win condition when not in gem mode


# Node sizing
UNOWNED_NODE_BASE_JUICE: float = 50.0
STARTING_NODE_JUICE: float = 150.0

# King mode
KING_CROWN_MAX_HEALTH: float = 150.0  # extra damage buffer before the king node itself is vulnerable
KING_MOVEMENT_MODES: Tuple[str, ...] = ("basic", "smash", "weak-smash")
DEFAULT_KING_MOVEMENT_MODE: str = "smash"
KING_CROWN_TICKS_PER_UNIT_DISTANCE: float = 0.15
KING_CROWN_MIN_TRAVEL_TICKS: int = 1
KING_CROWN_SPIN_TICKS: int = 5  # each spin phase lasts exactly 5 ticks regardless of arc length

# Gem distribution defaults (counts per gem type when resources == 'gems')
DEFAULT_GEM_COUNTS: Dict[str, int] = {
    "warp": 3,
    "brass": 7,
    "rage": 4,
    "reverse": 6,
}

# Classic (OG Durb) tuning
CLASSIC_STARTING_NODE_JUICE: float = 50.0
CLASSIC_PRODUCTION_RATE_PER_NODE: float = 0.7
CLASSIC_MAX_TRANSFER_RATIO: float = 0.95
CLASSIC_INTAKE_TRANSFER_RATIO: float = 0.75
CLASSIC_RESERVE_TRANSFER_RATIO: float = 0.01


# Bridge/Pipe costs
BRIDGE_BASE_COST: float = 0.0
BRIDGE_COST_PER_UNIT_DISTANCE_BY_MODE: Dict[str, float] = {
    "basic": 1.5,
    "warp-old": 1.5,
    "warp": 1.0,
    "semi": 1.0,
    "i-semi": 0.7,
    "i-warp": 0.7,
    "i-flat": 0.7,
    "flat": 1.0,
    "sparse": 1.0,
    "overflow": 1.0,
    "nuke": 1.0,
    "cross": 1.0,
    "brass-old": 1.0,
    "brass": 1.0,
    "go": 1.0,
    "sandbox": 0.0,
}
BRIDGE_COST_PER_UNIT_DISTANCE: float = BRIDGE_COST_PER_UNIT_DISTANCE_BY_MODE[DEFAULT_GAME_MODE]

# Cost multipliers (configurable via settings)
DEFAULT_PIPE_COST: float = 1.0      # Multiplier for standard pipe cost (range 0.5-2.5)
DEFAULT_BRASS_COST: float = 2.0     # Multiplier for brass pipe cost (range 0.5-2.5)
DEFAULT_CROWN_SHOT_COST: float = 0.5  # Multiplier for crown shot cost (range 0.5-2.5)

# Bridge build timing (ticks required per unit world distance)
BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE: float = 0.6

# Warp geometry (mirror frontend)
WARP_MARGIN_RATIO_X: float = 0.06
WARP_MARGIN_RATIO_Y: float = 0.10

# Geometry tuning
# Minimum separation angle (in degrees) between bridges meeting at a node before we auto-relax them
MIN_PIPE_JOIN_ANGLE_DEGREES: float = 22.5


def normalize_game_mode(value: str) -> str:
    """Return a supported game mode, treating legacy names as aliases."""
    if not isinstance(value, str):
        return DEFAULT_GAME_MODE
    lowered = value.strip().lower()
    if lowered == "passive":  # legacy alias
        lowered = "basic"
    if lowered == "pop":  # legacy alias now mapped to legacy warp
        lowered = "warp-old"
    if lowered == "xb":  # legacy alias now mapped to modern warp
        lowered = "warp"
    if lowered == "brass":
        lowered = "brass-old"
    return lowered if lowered in GAME_MODES else DEFAULT_GAME_MODE


def get_neutral_capture_reward(mode: str) -> float:
    """Return the gold reward for capturing a neutral node for the given mode."""
    return GOLD_REWARD_FOR_NEUTRAL_CAPTURE


def get_bridge_cost_per_unit(mode: str) -> float:
    """Return the bridge cost per unit distance for the given mode."""
    key = normalize_game_mode(mode)
    return BRIDGE_COST_PER_UNIT_DISTANCE_BY_MODE.get(key, BRIDGE_COST_PER_UNIT_DISTANCE)


def get_node_max_juice(mode: str) -> float:
    """Return the node max juice value for the given mode."""
    return NODE_MAX_JUICE


def get_overflow_juice_to_gold_ratio(mode: str) -> float:
    """Return the juice-to-gold conversion ratio for overflow-style modes."""
    return OVERFLOW_JUICE_TO_GOLD_RATIO


def normalize_king_movement_mode(value: str) -> str:
    """Normalize king movement mode names."""
    if not isinstance(value, str):
        return DEFAULT_KING_MOVEMENT_MODE
    lowered = value.strip().lower()
    if lowered in {"smash"}:
        return "smash"
    if lowered in {"weak-smash", "weaksmash", "weak"}:
        return "weak-smash"
    if lowered in {"standard"}:
        return "basic"
    return "basic"
