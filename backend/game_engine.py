"""
Game Engine - Core game logic separated from server implementation.
Handles game state management, validation, and game rules.
"""
import math
import time
from collections import deque
from typing import Any, Dict, List, Optional, Set, Tuple
from .constants import (
    BRIDGE_BASE_COST,
    BRIDGE_COST_PER_UNIT_DISTANCE,
    BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE,
    DEFAULT_GAME_MODE,
    DEFAULT_KING_MOVEMENT_MODE,
    DEFAULT_PIPE_COST,
    DEFAULT_BRASS_COST,
    DEFAULT_CROWN_SHOT_COST,
    OVERFLOW_PENDING_GOLD_PAYOUT,
    PRODUCTION_RATE_PER_NODE,
    MAX_TRANSFER_RATIO,
    INTAKE_TRANSFER_RATIO,
    RESERVE_TRANSFER_RATIO,
    STARTING_NODE_JUICE,
    KING_CROWN_MAX_HEALTH,
    KING_CROWN_MIN_TRAVEL_TICKS,
    KING_CROWN_SPIN_TICKS,
    KING_CROWN_TICKS_PER_UNIT_DISTANCE,
    CLASSIC_PRODUCTION_RATE_PER_NODE,
    CLASSIC_MAX_TRANSFER_RATIO,
    CLASSIC_INTAKE_TRANSFER_RATIO,
    CLASSIC_RESERVE_TRANSFER_RATIO,
    CLASSIC_STARTING_NODE_JUICE,
    NODE_MAX_JUICE,
    TICK_INTERVAL_SECONDS,
    UNOWNED_NODE_BASE_JUICE,
    WARP_MARGIN_RATIO_X,
    WARP_MARGIN_RATIO_Y,
    get_bridge_cost_per_unit,
    get_neutral_capture_reward,
    get_node_max_juice,
    get_overflow_juice_to_gold_ratio,
    normalize_game_mode,
    normalize_king_movement_mode,
)
from .graph_generator import graph_generator
from .models import Edge, Node, Player
from .node_movement import resolve_sharp_angles
from .state import GraphState, build_state_from_dict

SANDBOX_INITIAL_GOLD = 1_000_000_000.0
SANDBOX_NODE_JUICE = 50.0


class GameValidationError(Exception):
    """Raised when a game action fails validation."""
    pass


class GameEngine:
    """Core game engine handling game logic, validation, and state management."""
    
    def __init__(self):
        self.state: Optional[GraphState] = None
        self.screen: Dict[str, float] = {"width": 275.0, "height": 108.0, "minX": 0.0, "minY": -18.0, "margin": 0}
        
        # Player management
        self.token_to_player_id: Dict[str, int] = {}
        self.player_id_to_token: Dict[int, str] = {}
        self.player_meta: Dict[int, Dict[str, object]] = {}
        self.game_active: bool = False

    def start_game(
        self,
        player_slots: List[Dict[str, Any]],
        mode: str = DEFAULT_GAME_MODE,
        options: Optional[Dict[str, Any]] = None,
    ) -> None:
        """Initialize a new game with the provided player configuration."""
        normalized_mode = normalize_game_mode(mode)
        data = graph_generator.generate_game_data_sync(mode=normalized_mode)
        self.state, self.screen = build_state_from_dict(data)

        self.token_to_player_id.clear()
        self.player_id_to_token.clear()
        self.player_meta.clear()

        if not self.state:
            raise RuntimeError("Failed to create game state")

        self.state.phase = "picking"
        self.state.mode = normalized_mode
        self.state.node_max_juice = get_node_max_juice(normalized_mode)
        self.state.neutral_capture_reward = get_neutral_capture_reward(normalized_mode)
        self.state.bridge_cost_per_unit = get_bridge_cost_per_unit(normalized_mode)
        self.state.bridge_build_ticks_per_unit = BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE
        self.state.eliminated_players.clear()
        self.state.pending_eliminations = []

        sanitized_options = self._configure_gameplay_options(normalized_mode, options)

        self._refresh_edge_geometry()

        for slot in player_slots:
            player_id = slot["player_id"]
            token = slot["token"]
            color = slot.get("color", "#ffffff")
            secondary_colors = list(slot.get("secondary_colors", []))
            auto_expand = bool(slot.get("auto_expand", False))
            auto_attack = bool(slot.get("auto_attack", False))
            guest_name = str(slot.get("guest_name", "") or "").strip()

            if normalized_mode == "go":
                auto_expand = True

            player = Player(id=player_id, color=color, secondary_colors=secondary_colors, name=guest_name)
            self.state.add_player(player)

            self.token_to_player_id[token] = player_id
            self.player_id_to_token[player_id] = token
            self.player_meta[player_id] = {
                "color": color,
                "secondary_colors": secondary_colors,
                "guest_name": guest_name,
            }
            self.state.player_auto_expand[player_id] = auto_expand
            self.state.player_auto_attack[player_id] = auto_attack

        self.game_active = True

        self._configure_hidden_start_mode(sanitized_options or options or {}, player_slots)
        # Ensure mode settings reflect actual start mode after validation
        if self.state.mode_settings is not None:
            self.state.mode_settings["gameStart"] = self.state.game_start_mode

        if normalized_mode == "sandbox":
            self._apply_sandbox_rules(player_slots)

    def _configure_gameplay_options(
        self,
        normalized_mode: str,
        options: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.state:
            return {}

        # Reset per-game dynamics to defaults before applying mode overrides
        self.state.production_rate_per_node = PRODUCTION_RATE_PER_NODE
        self.state.max_transfer_ratio = MAX_TRANSFER_RATIO
        self.state.intake_transfer_ratio = INTAKE_TRANSFER_RATIO
        self.state.reserve_transfer_ratio = RESERVE_TRANSFER_RATIO
        self.state.starting_node_juice = STARTING_NODE_JUICE
        self.state.king_crown_max_health = KING_CROWN_MAX_HEALTH

        growth_rate_value = PRODUCTION_RATE_PER_NODE
        growth_rate_overridden = False
        starting_flow_ratio = RESERVE_TRANSFER_RATIO
        starting_flow_overridden = False
        secondary_flow_ratio = INTAKE_TRANSFER_RATIO
        secondary_flow_overridden = False

        if normalized_mode in {"warp-old", "warp", "i-warp"}:
            screen_variant = "warp"
        elif normalized_mode in {"semi", "i-semi"}:
            screen_variant = "semi"
        else:
            screen_variant = "flat"
        auto_brass_on_cross = normalized_mode in {"warp", "semi", "flat"}
        manual_brass_selection = normalized_mode in {"i-warp", "i-semi", "i-flat", "cross"}
        allow_pipe_start_anywhere = False
        # Cost multipliers with defaults
        pipe_cost_multiplier = DEFAULT_PIPE_COST
        brass_cost_multiplier = DEFAULT_BRASS_COST
        crown_shot_cost_multiplier = DEFAULT_CROWN_SHOT_COST
        pipe_break_mode = "flowing"
        bridge_cost_override: Optional[float] = None
        game_start_mode = "open"
        passive_income_per_second = 1.0
        neutral_capture_reward = get_neutral_capture_reward(normalized_mode)
        overflow_ratio = get_overflow_juice_to_gold_ratio(normalized_mode)
        overflow_payout = OVERFLOW_PENDING_GOLD_PAYOUT
        starting_node_juice_value = STARTING_NODE_JUICE
        starting_node_juice_overridden = False
        win_condition = "dominate"
        crown_health_value = KING_CROWN_MAX_HEALTH
        lonely_mode = "nothing"

        if isinstance(options, dict):
            screen_option = str(options.get("screen", "")).strip().lower()
            if screen_option in {"warp", "semi", "flat"}:
                screen_variant = screen_option

            brass_option = str(options.get("brass", "")).strip().lower()
            if brass_option in {"cross", "right-click", "rightclick", "right_click"}:
                manual_brass_selection = brass_option in {"right-click", "rightclick", "right_click"}
                auto_brass_on_cross = brass_option == "cross"

            pipe_start_raw = options.get("pipeStart", options.get("brassStart", ""))
            pipe_start_option = str(pipe_start_raw).strip().lower()
            if pipe_start_option in {"owned", "anywhere"}:
                allow_pipe_start_anywhere = pipe_start_option == "anywhere"

            pipe_break_raw = options.get("breakMode", options.get("pipeBreakMode", options.get("break", "")))
            pipe_break_option = str(pipe_break_raw).strip().lower()
            if pipe_break_option in {"brass", "any", "flowing", "double"}:
                if pipe_break_option == "flowing":
                    pipe_break_mode = "flowing"
                elif pipe_break_option == "any":
                    pipe_break_mode = "any"
                elif pipe_break_option == "double":
                    pipe_break_mode = "double"
                else:
                    pipe_break_mode = "brass"

            game_start_option = str(options.get("gameStart", "")).strip().lower()
            if game_start_option in {"hidden", "hidden-split", "hidden_split", "hidden split"}:
                game_start_mode = "hidden-split"

            # Pipe cost (was bridge cost) - range 0.5 to 2.5
            pipe_cost_value = options.get("pipeCost", options.get("bridgeCost"))
            if isinstance(pipe_cost_value, str):
                pipe_cost_value = pipe_cost_value.strip()
            try:
                parsed_pipe_cost = float(pipe_cost_value)
            except (TypeError, ValueError):
                parsed_pipe_cost = None
            if parsed_pipe_cost is not None and parsed_pipe_cost > 0:
                pipe_cost_multiplier = max(0.5, min(2.5, round(parsed_pipe_cost, 1)))
                bridge_cost_override = pipe_cost_multiplier

            # Brass cost - range 0.5 to 2.5
            brass_cost_value = options.get("brassCost")
            if isinstance(brass_cost_value, str):
                brass_cost_value = brass_cost_value.strip()
            try:
                parsed_brass_cost = float(brass_cost_value)
            except (TypeError, ValueError):
                parsed_brass_cost = None
            if parsed_brass_cost is not None and parsed_brass_cost > 0:
                brass_cost_multiplier = max(0.5, min(2.5, round(parsed_brass_cost, 1)))

            # Crown shot cost - range 0.5 to 2.5
            crown_shot_cost_value = options.get("crownShotCost")
            if isinstance(crown_shot_cost_value, str):
                crown_shot_cost_value = crown_shot_cost_value.strip()
            try:
                parsed_crown_shot_cost = float(crown_shot_cost_value)
            except (TypeError, ValueError):
                parsed_crown_shot_cost = None
            if parsed_crown_shot_cost is not None and parsed_crown_shot_cost > 0:
                crown_shot_cost_multiplier = max(0.5, min(2.5, round(parsed_crown_shot_cost, 1)))

            passive_value = options.get("passiveIncome")
            if isinstance(passive_value, str):
                passive_value = passive_value.strip()
            try:
                parsed_passive = float(passive_value)
            except (TypeError, ValueError):
                parsed_passive = None
            if parsed_passive is not None and parsed_passive >= 0:
                snapped = round(parsed_passive * 20.0) / 20.0
                passive_income_per_second = round(max(0.0, min(2.0, snapped)), 2)

            neutral_value = options.get("neutralCaptureGold")
            if isinstance(neutral_value, str):
                neutral_value = neutral_value.strip()
            try:
                parsed_neutral = float(neutral_value)
            except (TypeError, ValueError):
                parsed_neutral = None
            if parsed_neutral is not None and parsed_neutral >= 0:
                neutral_capture_reward = max(0.0, min(20.0, round(parsed_neutral, 3)))

            ratio_value = options.get("ringJuiceToGoldRatio")
            if isinstance(ratio_value, str):
                ratio_value = ratio_value.strip()
            try:
                parsed_ratio = float(ratio_value)
            except (TypeError, ValueError):
                parsed_ratio = None
            if parsed_ratio is not None and parsed_ratio > 0:
                overflow_ratio = max(5.0, min(500.0, round(parsed_ratio, 4)))

            payout_value = options.get("ringPayoutGold")
            if isinstance(payout_value, str):
                payout_value = payout_value.strip()
            try:
                parsed_payout = float(payout_value)
            except (TypeError, ValueError):
                parsed_payout = None
            if parsed_payout is not None and parsed_payout > 0:
                overflow_payout = max(1.0, min(500.0, round(parsed_payout, 4)))

            start_juice_value = options.get("startingNodeJuice")
            if isinstance(start_juice_value, str):
                start_juice_value = start_juice_value.strip()
            try:
                parsed_start_juice = float(start_juice_value)
            except (TypeError, ValueError):
                parsed_start_juice = None
            if parsed_start_juice is not None:
                clamped_start = max(
                    UNOWNED_NODE_BASE_JUICE,
                    min(NODE_MAX_JUICE, parsed_start_juice),
                )
                starting_node_juice_value = round(clamped_start, 2)
                starting_node_juice_overridden = True

            crown_health_raw = options.get("kingCrownHealth")
            if isinstance(crown_health_raw, str):
                crown_health_raw = crown_health_raw.strip()
            try:
                parsed_crown = float(crown_health_raw)
            except (TypeError, ValueError):
                parsed_crown = None
            if parsed_crown is not None and parsed_crown > 0:
                crown_health_value = max(1.0, min(300.0, round(parsed_crown, 3)))

            win_condition_option = options.get("winCondition")
            if isinstance(win_condition_option, str) and win_condition_option.strip().lower() == "king":
                win_condition = "king"

            lonely_option = options.get("lonelyNode")
            if isinstance(lonely_option, str) and lonely_option.strip().lower() == "sinks":
                lonely_mode = "sinks"

            growth_rate_raw = options.get("nodeGrowthRate")
            if isinstance(growth_rate_raw, str):
                growth_rate_raw = growth_rate_raw.strip()
            try:
                parsed_growth = float(growth_rate_raw)
            except (TypeError, ValueError):
                parsed_growth = None
            if parsed_growth is not None and not math.isnan(parsed_growth):
                growth_rate_value = max(0.0, min(1.0, round(parsed_growth, 4)))
                growth_rate_overridden = True

            starting_flow_raw = options.get("startingFlowRate")
            if isinstance(starting_flow_raw, str):
                starting_flow_raw = starting_flow_raw.strip()
            try:
                parsed_starting_flow = float(starting_flow_raw)
            except (TypeError, ValueError):
                parsed_starting_flow = None
            if parsed_starting_flow is not None and not math.isnan(parsed_starting_flow):
                starting_flow_ratio = max(0.0, min(1.0, round(parsed_starting_flow, 4)))
                starting_flow_overridden = True

            secondary_flow_raw = options.get("secondaryFlowRate")
            if isinstance(secondary_flow_raw, str):
                secondary_flow_raw = secondary_flow_raw.strip()
            try:
                parsed_secondary_flow = float(secondary_flow_raw)
            except (TypeError, ValueError):
                parsed_secondary_flow = None
            if parsed_secondary_flow is not None and not math.isnan(parsed_secondary_flow):
                secondary_flow_ratio = max(0.0, min(1.0, round(parsed_secondary_flow, 4)))
                secondary_flow_overridden = True

        if normalized_mode == "basic":
            auto_brass_on_cross = False
            manual_brass_selection = False
            allow_pipe_start_anywhere = False
            self.state.production_rate_per_node = CLASSIC_PRODUCTION_RATE_PER_NODE
            self.state.max_transfer_ratio = CLASSIC_MAX_TRANSFER_RATIO
            self.state.intake_transfer_ratio = CLASSIC_INTAKE_TRANSFER_RATIO
            self.state.reserve_transfer_ratio = CLASSIC_RESERVE_TRANSFER_RATIO
            if not starting_node_juice_overridden:
                starting_node_juice_value = CLASSIC_STARTING_NODE_JUICE
            if not growth_rate_overridden:
                growth_rate_value = CLASSIC_PRODUCTION_RATE_PER_NODE
            if not starting_flow_overridden:
                starting_flow_ratio = CLASSIC_RESERVE_TRANSFER_RATIO
            if not secondary_flow_overridden:
                secondary_flow_ratio = CLASSIC_INTAKE_TRANSFER_RATIO

        if bridge_cost_override is not None:
            clamped_cost = max(0.5, min(2.5, float(bridge_cost_override)))
            self.state.bridge_cost_per_unit = round(clamped_cost, 1)

        self.state.starting_node_juice = starting_node_juice_value

        # Set cost multipliers on state
        self.state.pipe_cost_multiplier = pipe_cost_multiplier
        self.state.brass_cost_multiplier = brass_cost_multiplier
        self.state.crown_shot_cost_multiplier = crown_shot_cost_multiplier

        self.state.production_rate_per_node = growth_rate_value
        self.state.reserve_transfer_ratio = starting_flow_ratio
        self.state.intake_transfer_ratio = secondary_flow_ratio

        king_movement_option = None
        if isinstance(options, dict):
            king_movement_option = options.get("kingMovementMode")
        king_movement_mode = normalize_king_movement_mode(
            king_movement_option if isinstance(king_movement_option, str) else DEFAULT_KING_MOVEMENT_MODE
        )

        sanitized_options: Dict[str, Any] = {
            "screen": screen_variant,
            "brass": "right-click" if manual_brass_selection else "cross",
            "brassStart": "anywhere" if allow_pipe_start_anywhere else "owned",
            "breakMode": pipe_break_mode,
            "pipeCost": pipe_cost_multiplier,
            "brassCost": brass_cost_multiplier,
            "crownShotCost": crown_shot_cost_multiplier,
            "derivedMode": normalized_mode,
            "gameStart": game_start_mode,
            "startingNodeJuice": starting_node_juice_value,
            "passiveIncome": passive_income_per_second,
            "neutralCaptureGold": neutral_capture_reward,
            "ringJuiceToGoldRatio": overflow_ratio,
            "ringPayoutGold": overflow_payout,
            "winCondition": win_condition,
            "kingCrownHealth": crown_health_value,
            "lonelyNode": lonely_mode,
            "nodeGrowthRate": growth_rate_value,
            "startingFlowRate": starting_flow_ratio,
            "secondaryFlowRate": secondary_flow_ratio,
            "kingMovementMode": king_movement_mode,
        }
        sanitized_options["pipeStart"] = sanitized_options["brassStart"]
        if isinstance(options, dict):
            base_mode = options.get("baseMode")
            if isinstance(base_mode, str):
                sanitized_options["baseMode"] = base_mode.strip()

        self.state.screen_variant = screen_variant
        self.state.auto_brass_on_cross = auto_brass_on_cross
        self.state.manual_brass_selection = manual_brass_selection
        self.state.pipe_break_mode = pipe_break_mode
        self.state.allow_pipe_start_anywhere = allow_pipe_start_anywhere
        self.state.lonely_node_mode = "sinks" if lonely_mode == "sinks" else "nothing"
        self.state.mode_settings = sanitized_options
        self.state.passive_income_per_second = passive_income_per_second
        self.state.neutral_capture_reward = neutral_capture_reward
        self.state.overflow_juice_to_gold_ratio = overflow_ratio
        self.state.overflow_pending_gold_payout = overflow_payout
        self.state.win_condition = win_condition
        self.state.king_crown_max_health = crown_health_value
        self.state.king_movement_mode = king_movement_mode
        if win_condition != "king":
            self.state.player_king_nodes.clear()

        return sanitized_options

    def _configure_hidden_start_mode(
        self,
        options: Dict[str, Any],
        player_slots: List[Dict[str, Any]],
    ) -> None:
        if not self.state:
            return

        requested_mode = str(options.get("gameStart", "open")).strip().lower()
        allow_hidden = len(player_slots) == 2 and requested_mode.startswith("hidden")

        if not allow_hidden:
            self.state.game_start_mode = "open"
            self.state.hidden_start_active = False
            self.state.hidden_start_revealed = False
            self.state.hidden_start_boundary = None
            self.state.hidden_start_sides = {}
            self.state.hidden_start_picks = {}
            self.state.hidden_start_bounds = None
            self.state.hidden_start_original_sizes = {}
            return

        self.state.game_start_mode = "hidden-split"
        self.state.hidden_start_active = True
        self.state.hidden_start_revealed = False
        self.state.hidden_start_picks = {}
        self.state.hidden_start_original_sizes = {}

        screen_min_x = float(self.screen.get("minX", 0.0))
        screen_width = float(self.screen.get("width", 0.0))
        screen_min_y = float(self.screen.get("minY", 0.0))
        screen_height = float(self.screen.get("height", 0.0))

        if screen_width > 0:
            min_x = screen_min_x
            max_x = screen_min_x + screen_width
        else:
            nodes = list(self.state.nodes.values())
            if nodes:
                min_x = min(node.x for node in nodes)
                max_x = max(node.x for node in nodes)
            else:
                min_x = screen_min_x
                max_x = screen_min_x

        if screen_height > 0:
            min_y = screen_min_y
            max_y = screen_min_y + screen_height
        else:
            nodes = list(self.state.nodes.values())
            if nodes:
                min_y = min(node.y for node in nodes)
                max_y = max(node.y for node in nodes)
            else:
                min_y = screen_min_y
                max_y = screen_min_y

        boundary = (min_x + max_x) / 2.0
        self.state.hidden_start_boundary = boundary
        self.state.hidden_start_bounds = {
            "minX": min_x,
            "maxX": max_x,
            "minY": min_y,
            "maxY": max_y,
        }

        left_player = player_slots[0]["player_id"] if player_slots else None
        right_player = player_slots[1]["player_id"] if len(player_slots) > 1 else None
        self.state.hidden_start_sides = {}
        if left_player is not None:
            self.state.hidden_start_sides[left_player] = "left"
        if right_player is not None:
            self.state.hidden_start_sides[right_player] = "right"

    def _apply_sandbox_rules(self, player_slots: List[Dict[str, Any]]) -> None:
        """Override state so a solo player can freely build bridges."""
        if not self.state or not player_slots:
            return

        primary_player_id = player_slots[0].get("player_id")
        if primary_player_id is None:
            return

        self.state.sandbox_mode = True
        self.state.allow_pipe_start_anywhere = True
        self.state.production_rate_per_node = 0.0
        self.state.max_transfer_ratio = 0.0
        self.state.intake_transfer_ratio = 0.0
        self.state.reserve_transfer_ratio = 0.0
        self.state.starting_node_juice = 0.0
        self.state.neutral_capture_reward = 0.0
        self.state.bridge_cost_per_unit = 0.0
        self.state.passive_income_per_second = 0.0
        self.state.game_start_mode = "open"
        self.state.hidden_start_active = False
        self.state.hidden_start_revealed = True

        unlimited_gold = SANDBOX_INITIAL_GOLD
        for player_id in self.state.players.keys():
            self.state.player_gold[player_id] = unlimited_gold

        for player_id in self.state.players.keys():
            self.state.players_who_picked[player_id] = True

        for node in self.state.nodes.values():
            node.owner = None
            node.juice = SANDBOX_NODE_JUICE
            node.pending_gold = 0.0

        self.state.phase = "playing"
        self.state.start_game_timer(time.time())

        mode_settings = dict(self.state.mode_settings or {})
        mode_settings.setdefault("screen", "flat")
        mode_settings.setdefault("brass", "cross")
        mode_settings["brassStart"] = "anywhere"
        mode_settings["pipeStart"] = "anywhere"
        mode_settings["bridgeCost"] = 0.0
        mode_settings["startingNodeJuice"] = self.state.starting_node_juice
        mode_settings["nodeGrowthRate"] = self.state.production_rate_per_node
        mode_settings["startingFlowRate"] = self.state.reserve_transfer_ratio
        mode_settings["secondaryFlowRate"] = self.state.intake_transfer_ratio
        mode_settings["gameStart"] = "open"
        mode_settings["derivedMode"] = "sandbox"
        mode_settings["sandbox"] = True
        mode_settings.setdefault(
            "kingMovementMode",
            getattr(self.state, "king_movement_mode", DEFAULT_KING_MOVEMENT_MODE),
        )
        self.state.mode_settings = mode_settings

    def is_game_active(self) -> bool:
        """Check if a game is currently active."""
        return self.game_active and self.state is not None and len(self.token_to_player_id) >= 2
    
    def _try_start_play_phase(self) -> None:
        """Transition from picking to playing once everyone has chosen a starting node."""
        if not self.state or self.state.phase != "picking":
            return

        if not self.state.players:
            return

        all_picked = all(
            self.state.players_who_picked.get(pid, False)
            for pid in self.state.players.keys()
        )
        if not all_picked:
            return

        self.state.phase = "playing"
        if self.state.hidden_start_active:
            self.state.hidden_start_revealed = True
            self.state.hidden_start_original_sizes.clear()
        self.state.start_game_timer(time.time())
        self.state.process_pending_auto_expands()
        self.state.process_pending_auto_attacks()

        for player_id, auto_enabled in self.state.player_auto_expand.items():
            if auto_enabled:
                self._check_auto_expand_opportunities(player_id)
        for player_id, auto_enabled in self.state.player_auto_attack.items():
            if auto_enabled:
                self._check_auto_attack_opportunities(player_id)

    def create_new_game(self) -> Tuple[GraphState, Dict[str, int]]:
        """Create a new single-player game for testing/development."""
        data = graph_generator.generate_game_data_sync(mode=DEFAULT_GAME_MODE)
        new_state, screen = build_state_from_dict(data)
        
        # Ensure Player 1 exists
        if 1 not in new_state.players:
            new_state.add_player(Player(id=1, color="#ffcc00"))

        new_state.eliminated_players.clear()
        new_state.pending_eliminations = []
        new_state.mode = DEFAULT_GAME_MODE
        new_state.win_condition = "dominate"
        new_state.player_king_nodes.clear()
        new_state.node_max_juice = get_node_max_juice(DEFAULT_GAME_MODE)
        new_state.neutral_capture_reward = get_neutral_capture_reward(DEFAULT_GAME_MODE)
        new_state.bridge_cost_per_unit = get_bridge_cost_per_unit(DEFAULT_GAME_MODE)
        new_state.bridge_build_ticks_per_unit = BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE
        for pid in new_state.players.keys():
            new_state.player_auto_expand[pid] = False
            new_state.player_auto_attack[pid] = False

        self.state = new_state
        self.screen = screen
        self.token_to_player_id.clear()
        self.player_id_to_token.clear()
        self.player_meta.clear()
        self._configure_gameplay_options(DEFAULT_GAME_MODE, None)
        self._refresh_edge_geometry()
        return new_state, screen
    
    def get_player_id(self, token: str) -> Optional[int]:
        """Get player ID from token."""
        return self.token_to_player_id.get(token)
    
    def validate_game_active(self) -> None:
        """Validate that a game is currently active."""
        if not self.state:
            raise GameValidationError("No game in progress")
    
    def validate_player(self, token: str) -> int:
        """Validate player token and return player ID."""
        if not token:
            raise GameValidationError("Invalid token")
        
        player_id = self.token_to_player_id.get(token)
        if player_id is None:
            raise GameValidationError("Invalid player")
        
        return player_id
    
    def validate_phase(self, required_phase: str) -> None:
        """Validate that the game is in the required phase."""
        if not self.state:
            raise GameValidationError("No game state")
        
        if getattr(self.state, "phase", "picking") != required_phase:
            raise GameValidationError(f"Not in {required_phase} phase")
    
    def validate_player_can_act(self, player_id: int) -> None:
        """Validate that a player can perform actions (has picked starting node)."""
        if not self.state:
            raise GameValidationError("No game state")
        
        if getattr(self.state, "sandbox_mode", False):
            return

        # Player can act if they have picked their starting node
        if not self.state.players_who_picked.get(player_id, False):
            raise GameValidationError("Must pick starting node first")

        if player_id in self.state.eliminated_players:
            raise GameValidationError("Player eliminated")
    
    def validate_node_exists(self, node_id: int) -> Node:
        """Validate that a node exists and return it."""
        if not self.state:
            raise GameValidationError("No game state")
        
        node = self.state.nodes.get(node_id)
        if node is None:
            raise GameValidationError("Invalid node")
        
        return node
    
    def validate_edge_exists(self, edge_id: int) -> Edge:
        """Validate that an edge exists and return it."""
        if not self.state:
            raise GameValidationError("No game state")
        
        edge = self.state.edges.get(edge_id)
        if edge is None:
            raise GameValidationError("Invalid edge")
        
        return edge
    
    def validate_player_owns_node(self, node: Node, player_id: int) -> None:
        """Validate that a player owns a specific node."""
        if node.owner != player_id:
            raise GameValidationError("You must own this node")
    
    def validate_sufficient_gold(self, player_id: int, cost: float) -> None:
        """Validate that a player has sufficient gold."""
        if not self.state:
            raise GameValidationError("No game state")
        
        player_gold = self.state.player_gold.get(player_id, 0.0)
        if player_gold < cost:
            raise GameValidationError("Not enough gold")
    
    def handle_node_click(self, token: str, node_id: int) -> bool:
        """
        Handle a node click - can be for picking starting node or other purposes.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            node = self.validate_node_exists(node_id)

            if self.state and player_id in self.state.eliminated_players:
                raise GameValidationError("Player eliminated")

            if (
                self.state
                and self.state.hidden_start_active
                and not self.state.hidden_start_revealed
                and self.state.phase == "picking"
            ):
                side = self.state.hidden_start_sides.get(player_id)
                boundary = self.state.hidden_start_boundary
                if side and boundary is not None:
                    tolerance = 1e-6
                    if side == "left" and node.x > boundary + tolerance:
                        raise GameValidationError("Selection outside assigned zone")
                    if side == "right" and node.x < boundary - tolerance:
                        raise GameValidationError("Selection outside assigned zone")

            # Check if this is for picking a starting node (node is unowned and player hasn't picked yet)
            if node.owner is None and not self.state.players_who_picked.get(player_id):
                reward_amount = 0.0
                reward_type = "money"
                reward_key: Optional[str] = None

                reward_amount = getattr(
                    self.state,
                    "neutral_capture_reward",
                    get_neutral_capture_reward(self.state.mode),
                )
                self.state.neutral_capture_reward = reward_amount
                self.state.player_gold[player_id] = self.state.player_gold.get(player_id, 0.0) + reward_amount
                if self.state.hidden_start_active and not self.state.hidden_start_revealed:
                    self.state.hidden_start_original_sizes[node_id] = node.juice
                node.juice = getattr(self.state, "starting_node_juice", STARTING_NODE_JUICE)
                node.owner = player_id
                if getattr(self.state, "win_condition", "dominate") == "king":
                    self.state.player_king_nodes[player_id] = node_id
                    setattr(node, "king_owner_id", player_id)
                    crown_max = getattr(self.state, "king_crown_max_health", KING_CROWN_MAX_HEALTH)
                    setattr(node, "king_crown_health", crown_max)
                    setattr(node, "king_crown_max_health", crown_max)
                self.state.players_who_picked[player_id] = True
                if self.state.hidden_start_active:
                    self.state.hidden_start_picks[player_id] = node_id

                # Check for auto-expand if enabled
                if self.state.player_auto_expand.get(player_id, False):
                    self.state._auto_expand_from_node(node_id, player_id)
                if self.state.player_auto_attack.get(player_id, False):
                    self.state._auto_attack_from_node(node_id, player_id)

                # Store the capture event for frontend notification
                if not hasattr(self.state, 'pending_node_captures'):
                    self.state.pending_node_captures = []
                self.state.pending_node_captures.append({
                    'nodeId': node_id,
                    'reward': reward_amount,
                    'rewardType': reward_type,
                    'rewardKey': reward_key,
                    'player_id': player_id
                })

                # Transition to playing state if everyone has picked
                self._try_start_play_phase()

                return True
            
            # For all other cases (already picked, node already owned, etc.), 
            # let the normal node click logic handle it in other handlers
            return False
            
        except GameValidationError:
            return False
    
    def _validate_king_context(self, player_id: int) -> int:
        """Ensure king movement is available and return the current king node id."""
        if not self.state:
            raise GameValidationError("No game state")
        if getattr(self.state, "win_condition", "dominate") != "king":
            raise GameValidationError("King moves are disabled")
        if player_id in self.state.eliminated_players:
            raise GameValidationError("Player eliminated")
        king_node_id = self.state.player_king_nodes.get(player_id)
        if king_node_id is None:
            raise GameValidationError("No king to move")
        king_node = self.state.nodes.get(king_node_id)
        if king_node is None or king_node.owner != player_id:
            raise GameValidationError("King node not controlled")
        return king_node_id

    def _compute_king_reachable_nodes(self, *, player_id: int, origin_node_id: int) -> Set[int]:
        """Return the set of nodes the king can reach via owned nodes and forward pipes."""
        if not self.state:
            return set()

        visited: Set[int] = {origin_node_id}
        reachable: Set[int] = set()
        queue = deque([origin_node_id])

        while queue:
            current_id = queue.popleft()
            current_node = self.state.nodes.get(current_id)
            if not current_node or current_node.owner != player_id:
                continue

            for edge_id in list(current_node.attached_edge_ids):
                edge = self.state.edges.get(edge_id)
                if not edge:
                    continue
                if getattr(edge, "building", False):
                    continue
                if edge.source_node_id != current_id:
                    continue

                target_id = edge.target_node_id
                if target_id in visited:
                    continue
                target_node = self.state.nodes.get(target_id)
                if not target_node or target_node.owner != player_id:
                    continue

                visited.add(target_id)
                queue.append(target_id)
                if target_id != origin_node_id:
                    reachable.add(target_id)

        return reachable

    def get_king_move_options(
        self,
        token: str,
        origin_node_id: Optional[int] = None,
    ) -> Tuple[bool, List[int], Optional[str], Optional[int], List[Dict[str, Any]]]:
        """Return reachable nodes for the requesting player's king."""
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_player_can_act(player_id)

            current_node_id = self._validate_king_context(player_id)
            if origin_node_id is not None:
                try:
                    origin_int = int(origin_node_id)
                except (TypeError, ValueError):
                    raise GameValidationError("Invalid king node")
                if origin_int != current_node_id:
                    raise GameValidationError("King location has changed")

            movement_mode = normalize_king_movement_mode(
                getattr(self.state, "king_movement_mode", DEFAULT_KING_MOVEMENT_MODE)
            )

            if movement_mode != "basic":
                if not self.state:
                    raise GameValidationError("No game state")
                origin_node = self.state.nodes.get(current_node_id)
                if not origin_node:
                    raise GameValidationError("Invalid king node")
                player_gold = float(self.state.player_gold.get(player_id, 0.0))
                reachable_nodes: List[int] = []
                target_details: List[Dict[str, Any]] = []
                for node in self.state.nodes.values():
                    if not node or node.id == current_node_id or node.owner != player_id:
                        continue
                    try:
                        plan = self._plan_king_smash_move(player_id, origin_node, node, movement_mode)
                    except GameValidationError:
                        continue
                    cost_value = int(plan.get("cost", 0))
                    if cost_value > player_gold:
                        continue
                    reachable_nodes.append(node.id)
                    target_details.append({
                        "nodeId": int(node.id),
                        "cost": cost_value,
                    })
                reachable_nodes.sort()
                target_details.sort(key=lambda entry: entry.get("nodeId", 0))
                return True, reachable_nodes, None, current_node_id, target_details

            reachable = sorted(
                self._compute_king_reachable_nodes(
                    player_id=player_id,
                    origin_node_id=current_node_id,
                )
            )
            return True, reachable, None, current_node_id, []

        except GameValidationError as exc:
            return False, [], str(exc), None, []

    def handle_move_king(
        self,
        token: str,
        destination_node_id: int,
        warp_info: Optional[Dict[str, Any]] = None,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, int]]]:
        """Attempt to move the player's king to a new node."""
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_player_can_act(player_id)
            current_node_id = self._validate_king_context(player_id)

            destination_node = self.validate_node_exists(destination_node_id)
            if destination_node.owner != player_id:
                raise GameValidationError("Must move to a controlled node")
            if current_node_id == destination_node_id:
                raise GameValidationError("King already occupies this node")

            movement_mode = normalize_king_movement_mode(
                getattr(self.state, "king_movement_mode", DEFAULT_KING_MOVEMENT_MODE)
            )

            smash_plan: Optional[Dict[str, Any]] = None
            if movement_mode == "basic":
                reachable = self._compute_king_reachable_nodes(
                    player_id=player_id,
                    origin_node_id=current_node_id,
                )
                if destination_node_id not in reachable:
                    raise GameValidationError("Destination not reachable")
            else:
                origin_node = self.state.nodes.get(current_node_id) if self.state else None
                if not origin_node:
                    raise GameValidationError("Invalid king location")
                smash_plan = self._plan_king_smash_move(
                    player_id,
                    origin_node,
                    destination_node,
                    movement_mode,
                    warp_info=warp_info,
                )
                move_cost = int(smash_plan.get("cost", 0))
                self.validate_sufficient_gold(player_id, move_cost)
                if self.state:
                    current_gold = float(self.state.player_gold.get(player_id, 0.0))
                    self.state.player_gold[player_id] = max(0.0, current_gold - move_cost)
                if smash_plan.get("removals"):
                    self._schedule_king_smash_removals(smash_plan)

            crown_max_default = getattr(self.state, "king_crown_max_health", KING_CROWN_MAX_HEALTH)
            current_health = crown_max_default
            current_max_health = crown_max_default

            current_node = self.state.nodes.get(current_node_id) if self.state else None
            if current_node:
                current_health = float(getattr(current_node, "king_crown_health", current_health))
                current_max_health = float(getattr(current_node, "king_crown_max_health", current_max_health))
                setattr(current_node, "king_owner_id", None)
                setattr(current_node, "king_crown_health", 0.0)
                setattr(current_node, "king_crown_max_health", 0.0)

            if current_max_health <= 0.0:
                current_max_health = crown_max_default
            current_health = max(0.0, min(current_health, current_max_health))

            setattr(destination_node, "king_owner_id", player_id)
            setattr(destination_node, "king_crown_health", current_health)
            setattr(destination_node, "king_crown_max_health", max(current_max_health, crown_max_default))

            if self.state:
                self.state.player_king_nodes[player_id] = destination_node_id

            payload = {
                "playerId": player_id,
                "fromNodeId": current_node_id,
                "toNodeId": destination_node_id,
                "crownHealth": round(current_health, 3),
                "crownMax": round(max(current_max_health, crown_max_default), 3),
            }
            if smash_plan is not None:
                payload["cost"] = int(smash_plan.get("cost", 0))
                # Include edge IDs with distances so frontend can remove them at the right time
                removals = smash_plan.get("removals") or []
                if removals:
                    # Send as list of [edgeId, distance] pairs
                    payload["removedEdges"] = [[int(edge_id), float(dist)] for edge_id, dist in removals]
                    payload["totalDistance"] = float(smash_plan.get("total_distance", 0))
                # Include warp segments for crown flight animation
                segments = smash_plan.get("segments") or []
                warp_axis = smash_plan.get("warp_axis", "none") or "none"
                if segments and warp_axis != "none":
                    payload["warpSegments"] = [[sx, sy, ex, ey] for sx, sy, ex, ey in segments]
                    payload["warpAxis"] = warp_axis
            payload["movementMode"] = movement_mode
            return True, None, payload

        except GameValidationError as exc:
            return False, str(exc), None

    def handle_edge_click(self, token: str, edge_id: int) -> bool:
        """
        Handle an edge click to toggle flow.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            
            # Allow edge clicks if player has picked starting node
            self.validate_player_can_act(player_id)

            current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
            if current_mode == "go":
                raise GameValidationError("Pipes auto-flow in Go mode")
            
            edge = self.validate_edge_exists(edge_id)
            
            # Toggle behavior - only toggle the 'on' property
            # The 'flowing' property will be updated automatically each tick
            if edge.on:
                edge.on = False
            else:
                # Check if player owns the source node
                source_node = self.validate_node_exists(edge.source_node_id)
                
                if source_node.owner == player_id:
                    edge.on = True
                else:
                    raise GameValidationError("You must own the source node")
            
            return True
            
        except GameValidationError:
            return False
    
    def handle_reverse_edge(self, token: str, edge_id: int, cost: Optional[float] = None) -> bool:
        """
        Handle reversing an edge direction.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)

            # Allow reverse edge if player has picked starting node
            self.validate_player_can_act(player_id)

            edge = self.validate_edge_exists(edge_id)
            if getattr(edge, "pipe_type", "normal") != "reverse":
                raise GameValidationError("Edge is not reversible")
            
            # Get both nodes
            source_node = self.validate_node_exists(edge.source_node_id)
            target_node = self.validate_node_exists(edge.target_node_id)

            # Only the player who controls the source node may reverse the pipe
            source_owner = source_node.owner
            if source_owner != player_id:
                raise GameValidationError("Pipe controlled by opponent")
            
            # Reverse the edge by swapping source and target
            edge.source_node_id, edge.target_node_id = edge.target_node_id, edge.source_node_id

            # Only turn on if the new source node is owned by the swapping player
            new_source_node = self.validate_node_exists(edge.source_node_id)
            if new_source_node.owner == player_id:
                edge.on = True
            else:
                # Edge is swapped but not turned on since player doesn't own new source
                edge.on = False

            self._apply_edge_warp_geometry(edge)

            return True

        except GameValidationError:
            return False
    
    def _normalization_scale(self) -> float:
        """Return a uniform scale factor so distance math stays orientation-neutral."""
        width = max(1.0, float(self.screen.get("width", 100)))
        height = max(1.0, float(self.screen.get("height", 100)))
        largest_span = max(width, height)
        return 100.0 / largest_span if largest_span > 0 else 1.0

    def _is_warp_mode_active(self) -> bool:
        if not self.state:
            return False
        if getattr(self.state, "screen_variant", None) in {"warp", "semi"}:
            return True
        current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
        return current_mode in {"warp-old", "warp", "i-warp", "semi", "i-semi", "sparse", "overflow", "nuke", "cross", "brass-old", "go"}

    def _compute_warp_bounds(self) -> Optional[Dict[str, float]]:
        if not self._is_warp_mode_active():
            return None

        width = float(self.screen.get("width", 0.0) or 0.0)
        height = float(self.screen.get("height", 0.0) or 0.0)
        if width <= 0 or height <= 0:
            return None

        min_x = float(self.screen.get("minX", 0.0) or 0.0)
        min_y = float(self.screen.get("minY", 0.0) or 0.0)
        max_x = min_x + width
        max_y = min_y + height

        margin_x = width * WARP_MARGIN_RATIO_X
        margin_y = height * WARP_MARGIN_RATIO_Y

        return {
            "minX": min_x - margin_x,
            "maxX": max_x + margin_x,
            "minY": min_y - margin_y,
            "maxY": max_y + margin_y,
            "width": width + 2.0 * margin_x,
            "height": height + 2.0 * margin_y,
        }

    def _warp_axis_permissions(self) -> Tuple[bool, bool]:
        if not self.state or not self._is_warp_mode_active():
            return False, False
        current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
        screen_variant = getattr(self.state, "screen_variant", None)
        allow_horizontal = True
        if current_mode in {"semi", "i-semi"} or screen_variant == "semi":
            allow_horizontal = False
        return allow_horizontal, True

    def _parse_client_warp_info(
        self,
        warp_info: Dict[str, Any],
        from_node: Node,
        to_node: Node,
    ) -> Tuple[str, List[Tuple[float, float, float, float]], float]:
        if not isinstance(warp_info, dict):
            raise GameValidationError("Invalid warp info payload")

        axis_value = warp_info.get("axis")
        if not isinstance(axis_value, str):
            axis_value = warp_info.get("warpAxis")
        if not isinstance(axis_value, str):
            axis_value = warp_info.get("wrapAxis")

        axis_value = (axis_value or "none").strip().lower()
        if axis_value not in {"none", "horizontal", "vertical"}:
            raise GameValidationError("Invalid warp axis")
        axis = axis_value

        if axis != "none" and not self._is_warp_mode_active():
            raise GameValidationError("Warp bridges only allowed in warp mode")

        if axis != "none":
            allow_horizontal, allow_vertical = self._warp_axis_permissions()
            if axis == "horizontal" and not allow_horizontal:
                raise GameValidationError("Horizontal warping unavailable in this mode")
            if axis == "vertical" and not allow_vertical:
                raise GameValidationError("Vertical warping unavailable in this mode")

        segments_raw = warp_info.get("segments")
        if segments_raw is None:
            segments_raw = warp_info.get("warpSegments")
        if segments_raw is None:
            segments_raw = warp_info.get("warp_segments")
        if segments_raw is None:
            segments_raw = []

        if not isinstance(segments_raw, list):
            raise GameValidationError("Invalid warp segments")

        segments: List[Tuple[float, float, float, float]] = []
        for seg in segments_raw:
            if isinstance(seg, (list, tuple)):
                if len(seg) < 4:
                    raise GameValidationError("Invalid warp segment format")
                sx, sy, ex, ey = seg[0], seg[1], seg[2], seg[3]
            elif isinstance(seg, dict):
                if "start" in seg and "end" in seg:
                    start = seg.get("start") or {}
                    end = seg.get("end") or {}
                    sx = start.get("x")
                    sy = start.get("y")
                    ex = end.get("x")
                    ey = end.get("y")
                else:
                    sx = seg.get("sx")
                    sy = seg.get("sy")
                    ex = seg.get("ex")
                    ey = seg.get("ey")
            else:
                raise GameValidationError("Invalid warp segment format")

            try:
                sx_f = float(sx)
                sy_f = float(sy)
                ex_f = float(ex)
                ey_f = float(ey)
            except (TypeError, ValueError):
                raise GameValidationError("Invalid warp segment coordinates")

            if not all(math.isfinite(val) for val in (sx_f, sy_f, ex_f, ey_f)):
                raise GameValidationError("Invalid warp segment coordinates")

            segments.append((sx_f, sy_f, ex_f, ey_f))

        if not segments:
            if axis != "none":
                raise GameValidationError("Warp segments required for warped bridge")
            segments = [(from_node.x, from_node.y, to_node.x, to_node.y)]

        EPSILON = 1e-3

        # Force exact connections to the declared endpoints so later validation never drifts
        first_sx, first_sy, first_ex, first_ey = segments[0]
        last_sx, last_sy, last_ex, last_ey = segments[-1]

        if math.hypot(first_sx - from_node.x, first_sy - from_node.y) > EPSILON:
            if axis == "none":
                raise GameValidationError("Warp path must begin at source node")
            segments[0] = (from_node.x, from_node.y, first_ex, first_ey)
        else:
            segments[0] = (from_node.x, from_node.y, first_ex, first_ey)

        if math.hypot(last_ex - to_node.x, last_ey - to_node.y) > EPSILON:
            if axis == "none":
                raise GameValidationError("Warp path must end at target node")
            segments[-1] = (last_sx, last_sy, to_node.x, to_node.y)
        else:
            segments[-1] = (last_sx, last_sy, to_node.x, to_node.y)

        for idx in range(1, len(segments)):
            prev = segments[idx - 1]
            curr = segments[idx]
            gap = math.hypot(prev[2] - curr[0], prev[3] - curr[1])
            if axis == "none" and gap > EPSILON:
                raise GameValidationError("Warp path segments must connect")
            # For warped bridges we allow a discontinuity between exit and entry portals.
            # We still enforce the final endpoint correction above.
            if axis == "none" and gap > 0:
                segments[idx] = (prev[2], prev[3], curr[2], curr[3])

        if len(segments) > 2:
            raise GameValidationError("No double warping")
        if axis == "none" and len(segments) > 1:
            raise GameValidationError("No double warping")

        if axis != "none" and len(segments) != 2:
            raise GameValidationError("Warp bridges must include entry and exit segments")

        total_distance = 0.0
        for sx, sy, ex, ey in segments:
            total_distance += math.hypot(ex - sx, ey - sy)

        return axis, segments, total_distance

    def _compute_warp_bridge_path(self, from_node: Node, to_node: Node) -> Dict[str, Any]:
        base_dx = to_node.x - from_node.x
        base_dy = to_node.y - from_node.y
        base_distance = math.hypot(base_dx, base_dy)
        base_segments: List[Tuple[float, float, float, float]] = [
            (from_node.x, from_node.y, to_node.x, to_node.y)
        ]

        bounds = self._compute_warp_bounds()
        if not bounds:
            return {
                "warp_axis": "none",
                "segments": base_segments,
                "total_distance": base_distance,
            }

        width = bounds["width"]
        height = bounds["height"]
        if width <= 0 or height <= 0:
            return {
                "warp_axis": "none",
                "segments": base_segments,
                "total_distance": base_distance,
            }

        dx = base_dx
        dy = base_dy

        best_axis = "none"
        best_segments = list(base_segments)
        best_distance = base_distance

        EPS = 1e-6

        allow_horizontal, allow_vertical = self._warp_axis_permissions()

        # Horizontal wrap candidate
        if allow_horizontal and abs(dx) > width / 2.0 + EPS:
            adjust = -width if dx > 0 else width
            adjusted_target_x = to_node.x + adjust
            dx_wrap = adjusted_target_x - from_node.x
            if abs(dx_wrap) > EPS:
                boundary_x = bounds["maxX"] if dx_wrap > 0 else bounds["minX"]
                t = (boundary_x - from_node.x) / dx_wrap
                if EPS < t < 1.0 - EPS:
                    exit_y = from_node.y + t * dy
                    if bounds["minY"] - EPS <= exit_y <= bounds["maxY"] + EPS:
                        entry_x = bounds["minX"] if dx_wrap > 0 else bounds["maxX"]
                        dist1 = math.hypot(boundary_x - from_node.x, exit_y - from_node.y)
                        dist2 = math.hypot(to_node.x - entry_x, to_node.y - exit_y)
                        total = dist1 + dist2
                        if total + EPS < best_distance or best_distance < EPS:
                            best_axis = "horizontal"
                            best_distance = total
                            best_segments = [
                                (from_node.x, from_node.y, boundary_x, exit_y),
                                (entry_x, exit_y, to_node.x, to_node.y),
                            ]

        # Vertical wrap candidate
        if allow_vertical and abs(dy) > height / 2.0 + EPS:
            adjust = -height if dy > 0 else height
            adjusted_target_y = to_node.y + adjust
            dy_wrap = adjusted_target_y - from_node.y
            if abs(dy_wrap) > EPS:
                boundary_y = bounds["maxY"] if dy_wrap > 0 else bounds["minY"]
                t = (boundary_y - from_node.y) / dy_wrap
                if EPS < t < 1.0 - EPS:
                    exit_x = from_node.x + t * dx
                    if bounds["minX"] - EPS <= exit_x <= bounds["maxX"] + EPS:
                        entry_y = bounds["minY"] if dy_wrap > 0 else bounds["maxY"]
                        dist1 = math.hypot(exit_x - from_node.x, boundary_y - from_node.y)
                        dist2 = math.hypot(to_node.x - exit_x, to_node.y - entry_y)
                        total = dist1 + dist2
                        if total + EPS < best_distance or best_distance < EPS:
                            best_axis = "vertical"
                            best_distance = total
                            best_segments = [
                                (from_node.x, from_node.y, exit_x, boundary_y),
                                (exit_x, entry_y, to_node.x, to_node.y),
                            ]

        return {
            "warp_axis": best_axis,
            "segments": best_segments,
            "total_distance": best_distance,
        }

    def _apply_edge_warp_geometry(self, edge: Edge) -> None:
        if not self.state:
            edge.warp_axis = "none"
            edge.warp_segments = []
            return

        from_node = self.state.nodes.get(edge.source_node_id)
        to_node = self.state.nodes.get(edge.target_node_id)
        if not from_node or not to_node:
            edge.warp_axis = "none"
            edge.warp_segments = []
            return

        EPSILON = 1e-3

        existing_segments = list(edge.warp_segments or [])
        if existing_segments:
            first = existing_segments[0]
            last = existing_segments[-1]
            if (
                math.hypot(first[0] - from_node.x, first[1] - from_node.y) <= EPSILON
                and math.hypot(last[2] - to_node.x, last[3] - to_node.y) <= EPSILON
            ):
                sanitized: List[Tuple[float, float, float, float]] = []
                for sx, sy, ex, ey in existing_segments:
                    sanitized.append((float(sx), float(sy), float(ex), float(ey)))
                edge.warp_segments = sanitized
                if edge.warp_axis not in {"none", "horizontal", "vertical"}:
                    edge.warp_axis = "none"
                return

        path = self._compute_warp_bridge_path(from_node, to_node)
        segments = path.get("segments") or []
        edge.warp_axis = path.get("warp_axis", "none") or "none"
        edge.warp_segments = [(float(sx), float(sy), float(ex), float(ey)) for sx, sy, ex, ey in segments]

    def _edge_segments(self, edge: Edge) -> List[Tuple[float, float, float, float]]:
        if not edge.warp_segments:
            self._apply_edge_warp_geometry(edge)
        return edge.warp_segments

    def _refresh_edge_geometry(self) -> None:
        if not self.state:
            return
        for edge in self.state.edges.values():
            self._apply_edge_warp_geometry(edge)

    def _finalize_auto_reversed_edges(self) -> None:
        if not self.state:
            return

        pending_ids = list(getattr(self.state, "pending_auto_reversed_edge_ids", []) or [])
        self.state.pending_edge_reversal_events = []
        if not pending_ids:
            return

        events: List[Dict[str, Any]] = []
        for edge_id in pending_ids:
            edge = self.state.edges.get(edge_id)
            if not edge:
                continue
            self._apply_edge_warp_geometry(edge)
            warp_payload = {
                "axis": edge.warp_axis,
                "segments": [
                    [sx, sy, ex, ey]
                    for sx, sy, ex, ey in (edge.warp_segments or [])
                ],
            }
            events.append({
                "type": "edgeReversed",
                "edge": {
                    "id": edge.id,
                    "source": edge.source_node_id,
                    "target": edge.target_node_id,
                    "bidirectional": False,
                    "forward": True,
                    "on": edge.on,
                    "flowing": edge.flowing,
                    "pipeType": getattr(edge, "pipe_type", "normal"),
                    "warp": warp_payload,
                    "warpAxis": warp_payload["axis"],
                    "warpSegments": warp_payload["segments"],
                },
            })

        self.state.pending_edge_reversal_events = events
        self.state.pending_auto_reversed_edge_ids = []

    def calculate_bridge_cost(
        self,
        from_node: Node,
        to_node: Node,
        segments_override: Optional[List[Tuple[float, float, float, float]]] = None,
    ) -> float:
        """Calculate the gold cost for a bridge using normalized coordinates."""
        scale = self._normalization_scale()
        segments = segments_override
        if segments is None:
            path = self._compute_warp_bridge_path(from_node, to_node)
            segments = path.get("segments", [])
        if not segments:
            segments = [(from_node.x, from_node.y, to_node.x, to_node.y)]
        normalized_distance = 0.0
        for sx, sy, ex, ey in segments:
            seg_dx = (ex - sx) * scale
            seg_dy = (ey - sy) * scale
            normalized_distance += math.hypot(seg_dx, seg_dy)

        if normalized_distance <= 0:
            return 0

        cost_per_unit = BRIDGE_COST_PER_UNIT_DISTANCE
        if self.state:
            cost_per_unit = getattr(self.state, "bridge_cost_per_unit", BRIDGE_COST_PER_UNIT_DISTANCE)
        total_cost = BRIDGE_BASE_COST + normalized_distance * cost_per_unit
        return int(round(total_cost))

    def handle_build_bridge(
        self,
        token: str,
        from_node_id: int,
        to_node_id: int,
        client_reported_cost: float,
        warp_info: Optional[Dict[str, Any]] = None,
        pipe_type: str = "normal",
    ) -> Tuple[bool, Optional[Edge], float, Optional[str], List[int], List[Dict[str, float]]]:
        """
        Handle building a bridge between two nodes.
        Returns: (success, new_edge, actual_cost, error_message, removed_edges, node_movements)
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)

            # Allow bridge building if player has picked starting node
            self.validate_player_can_act(player_id)

            current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
            is_cross_mode = current_mode == "cross"
            is_brass_mode = current_mode == "brass-old"
            auto_brass_on_cross = bool(getattr(self.state, "auto_brass_on_cross", current_mode in {"warp", "semi", "flat"}))
            manual_brass_selection = bool(getattr(self.state, "manual_brass_selection", current_mode in {"i-warp", "i-semi", "i-flat", "cross"}))
            pipe_cost_multiplier = float(getattr(self.state, "pipe_cost_multiplier", DEFAULT_PIPE_COST))
            brass_cost_multiplier = float(getattr(self.state, "brass_cost_multiplier", DEFAULT_BRASS_COST))
            screen_variant = getattr(
                self.state,
                "screen_variant",
                "warp" if current_mode in {"warp", "i-warp", "warp-old", "semi", "i-semi"} else "flat",
            )
            is_warp_variant_mode = auto_brass_on_cross or screen_variant in {"warp", "semi"} or current_mode in {"warp", "i-warp", "warp-old", "semi", "i-semi"}
            is_cross_like_mode = current_mode in {"cross", "brass-old"}

            # Validate nodes
            from_node = self.validate_node_exists(from_node_id)
            to_node = self.validate_node_exists(to_node_id)

            allowed_pipe_types = {"normal", "gold", "rage", "reverse"}
            requested_pipe_type = (pipe_type or "").strip().lower()
            if requested_pipe_type not in allowed_pipe_types:
                requested_pipe_type = "normal"

            if requested_pipe_type in {"rage", "reverse"}:
                normalized_pipe_type = requested_pipe_type
            elif is_brass_mode:
                normalized_pipe_type = "gold" if getattr(from_node, "node_type", "normal") == "brass" else "normal"
            elif is_cross_mode or manual_brass_selection:
                normalized_pipe_type = "gold" if requested_pipe_type == "gold" else "normal"
            else:
                normalized_pipe_type = "normal"

            allow_pipe_anywhere = bool(getattr(self.state, "allow_pipe_start_anywhere", False))

            if from_node.owner != player_id and not allow_pipe_anywhere:
                raise GameValidationError("Pipes must start from your nodes")

            if from_node_id == to_node_id:
                raise GameValidationError("Cannot connect node to itself")

            candidate_segments: List[Tuple[float, float, float, float]] = []
            warp_axis = "none"
            total_world_distance = 0.0
            removed_edges: List[int] = []
            delayed_cross_removals: List[Tuple[int, float]] = []
            node_movements: List[Dict[str, float]] = []
            destroyed_pipes_during_build = False

            if warp_info:
                warp_axis, candidate_segments, total_world_distance = self._parse_client_warp_info(
                    warp_info, from_node, to_node
                )
            else:
                path_info = self._compute_warp_bridge_path(from_node, to_node)
                candidate_segments = path_info.get("segments") or []
                warp_axis = path_info.get("warp_axis", "none") or "none"
                total_world_distance = float(path_info.get("total_distance") or 0.0)

            if not candidate_segments:
                candidate_segments = [(from_node.x, from_node.y, to_node.x, to_node.y)]
            if warp_axis not in {"none", "horizontal", "vertical"}:
                raise GameValidationError("Invalid warp axis")
            if len(candidate_segments) > 2:
                raise GameValidationError("No double warping")
            if warp_axis != "none" and len(candidate_segments) != 2:
                raise GameValidationError("Warp bridges must include entry and exit segments")

            # Calculate and validate gold using server-side formula with cost multipliers
            base_cost = self.calculate_bridge_cost(from_node, to_node, segments_override=candidate_segments)
            if normalized_pipe_type == "gold":
                actual_cost = int(round(base_cost * brass_cost_multiplier))
            else:
                actual_cost = int(round(base_cost * pipe_cost_multiplier))
            self.validate_sufficient_gold(player_id, actual_cost)

            # Check if edge already exists
            existing_between: List[int] = []
            for edge_id, edge in self.state.edges.items():
                if {edge.source_node_id, edge.target_node_id} == {from_node_id, to_node_id}:
                    existing_between.append(edge_id)

            if existing_between:
                if normalized_pipe_type != "gold" or not is_cross_like_mode:
                    raise GameValidationError("Edge already exists between these nodes")

                removable_between: List[int] = []
                blocking_between: List[int] = []
                for edge_id in existing_between:
                    existing_edge = self.state.edges.get(edge_id)
                    if self._edge_behaves_like_brass(existing_edge):
                        blocking_between.append(edge_id)
                    else:
                        removable_between.append(edge_id)

                if blocking_between:
                    raise GameValidationError("Cannot replace existing brass pipe")

                removed_edges.extend(self._remove_edges(removable_between))
                if removable_between:
                    destroyed_pipes_during_build = True

            pipe_break_setting = str(getattr(self.state, "pipe_break_mode", "brass") or "brass").strip().lower()
            allow_pipe_breaks_without_brass = pipe_break_setting in {"any", "flowing", "double"}
            double_break_requires_owned_target = pipe_break_setting == "double"

            # Check for intersections (cross mode converts them into removals)
            intersecting_edges = self._find_intersecting_edges(from_node, to_node, candidate_segments)
            if intersecting_edges:
                if is_warp_variant_mode:
                    blocked_edges: List[int] = []
                    for intersect_id in intersecting_edges:
                        existing_edge = self.state.edges.get(intersect_id) if self.state else None
                        if self._edge_behaves_like_brass(existing_edge):
                            blocked_edges.append(intersect_id)
                        else:
                            distance = self._distance_to_first_intersection(candidate_segments, existing_edge) or 0.0
                            delayed_cross_removals.append((intersect_id, max(0.0, distance)))

                    if blocked_edges:
                        raise GameValidationError("Cannot cross brass pipe")

                    can_current_pipe_break = normalized_pipe_type == "gold" or allow_pipe_breaks_without_brass
                    if delayed_cross_removals:
                        if not can_current_pipe_break:
                            if auto_brass_on_cross:
                                normalized_pipe_type = "gold"
                                can_current_pipe_break = True
                            else:
                                raise GameValidationError("Only brass pipes can cross others")
                    else:
                        if not is_cross_like_mode and not can_current_pipe_break:
                            raise GameValidationError("Bridge would intersect existing edge")

                        brass_blocking_edges: List[int] = []
                        type_blocking_edges: List[int] = []
                        for intersect_id in intersecting_edges:
                            existing_edge = self.state.edges.get(intersect_id) if self.state else None
                            behaves_like_brass = self._edge_behaves_like_brass(existing_edge)
                            if behaves_like_brass:
                                brass_blocking_edges.append(intersect_id)
                                continue
                            if can_current_pipe_break:
                                distance = self._distance_to_first_intersection(candidate_segments, existing_edge) or 0.0
                                delayed_cross_removals.append((intersect_id, max(0.0, distance)))
                            else:
                                type_blocking_edges.append(intersect_id)

                        if brass_blocking_edges:
                            raise GameValidationError("Cannot cross golden pipe")
                        if type_blocking_edges:
                            raise GameValidationError("Only golden pipes can cross others")

                        if delayed_cross_removals and not can_current_pipe_break:
                            # Should not happen because blocking_edges would have triggered, but guard anyway
                            raise GameValidationError("Only golden pipes can cross others")
                else:
                    can_current_pipe_break = normalized_pipe_type == "gold" or allow_pipe_breaks_without_brass
                    if not is_cross_like_mode:
                        raise GameValidationError("Bridge would intersect existing edge")

                    brass_blocking_edges: List[int] = []
                    type_blocking_edges: List[int] = []
                    for intersect_id in intersecting_edges:
                        existing_edge = self.state.edges.get(intersect_id) if self.state else None
                        behaves_like_brass = self._edge_behaves_like_brass(existing_edge)
                        if behaves_like_brass:
                            brass_blocking_edges.append(intersect_id)
                            continue
                        if can_current_pipe_break:
                            distance = self._distance_to_first_intersection(candidate_segments, existing_edge) or 0.0
                            delayed_cross_removals.append((intersect_id, max(0.0, distance)))
                        else:
                            type_blocking_edges.append(intersect_id)

                    if brass_blocking_edges:
                        raise GameValidationError("Cannot cross golden pipe")
                    if type_blocking_edges:
                        raise GameValidationError("Only golden pipes can cross others")

                    if delayed_cross_removals and not can_current_pipe_break:
                        # Should not happen because blocking_edges would have triggered, but guard anyway
                        raise GameValidationError("Only golden pipes can cross others")

            if double_break_requires_owned_target and delayed_cross_removals:
                target_owner = getattr(to_node, "owner", None)
                if target_owner != player_id:
                    raise GameValidationError("Double breaks must end on your nodes")

            if delayed_cross_removals:
                destroyed_pipes_during_build = True

            if (
                from_node.owner != player_id
                and not allow_pipe_anywhere
            ):
                raise GameValidationError("Pipes must start from your nodes")

            # Create the edge (always one-way from source to target)
            new_edge_id = max(self.state.edges.keys(), default=0) + 1
            
            new_edge_should_be_on = False
            target_owner = getattr(to_node, "owner", None)
            auto_expand_enabled = bool(self.state.player_auto_expand.get(player_id, False)) if self.state else False
            auto_attack_enabled = bool(self.state.player_auto_attack.get(player_id, False)) if self.state else False
            force_off_due_to_flowing_break = (
                pipe_break_setting == "flowing" and destroyed_pipes_during_build
            )
            if not force_off_due_to_flowing_break:
                if target_owner is None:
                    new_edge_should_be_on = auto_expand_enabled
                elif target_owner != player_id:
                    new_edge_should_be_on = auto_attack_enabled

            if total_world_distance <= 0.0:
                total_world_distance = 0.0
                for sx, sy, ex, ey in candidate_segments:
                    total_world_distance += math.hypot(ex - sx, ey - sy)
            if total_world_distance <= 0.0:
                total_world_distance = math.hypot(to_node.x - from_node.x, to_node.y - from_node.y)

            ticks_per_unit = getattr(
                self.state,
                "bridge_build_ticks_per_unit",
                BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE,
            )
            build_ticks_required = max(1, int(total_world_distance * max(0.0, ticks_per_unit)))

            new_edge = Edge(
                id=new_edge_id,
                source_node_id=from_node_id,
                target_node_id=to_node_id,
                pipe_type=normalized_pipe_type,
                on=False,
                flowing=False,  # Will be set to True by _update_edge_flowing_status when built and conditions are met
                build_ticks_required=build_ticks_required,
                build_ticks_elapsed=0,
                building=True,
                warp_axis=warp_axis,
                warp_segments=[(float(sx), float(sy), float(ex), float(ey)) for sx, sy, ex, ey in candidate_segments],
            )

            if delayed_cross_removals and new_edge.build_ticks_required > 0:
                total_distance_for_schedule = total_world_distance
                if total_distance_for_schedule <= 0.0:
                    total_distance_for_schedule = 0.0
                    for sx, sy, ex, ey in candidate_segments:
                        total_distance_for_schedule += math.hypot(ex - sx, ey - sy)
                if total_distance_for_schedule <= 0.0:
                    total_distance_for_schedule = float(new_edge.build_ticks_required)

                distance_per_tick = total_distance_for_schedule / max(1, new_edge.build_ticks_required)
                scheduled: List[Tuple[int, int]] = []
                for target_edge_id, distance in delayed_cross_removals:
                    clamped_distance = max(0.0, min(distance, total_distance_for_schedule))
                    if distance_per_tick <= 0:
                        intersection_tick = new_edge.build_ticks_required
                    else:
                        intersection_tick = math.ceil(clamped_distance / distance_per_tick)
                    removal_tick = max(1, min(new_edge.build_ticks_required, intersection_tick + 1))
                    scheduled.append((target_edge_id, removal_tick))
                new_edge.pending_cross_removals = scheduled
            
            # Add to state
            self.state.edges[new_edge_id] = new_edge
            from_node.attached_edge_ids.append(new_edge_id)
            to_node.attached_edge_ids.append(new_edge_id)

            # Deduct gold using verified cost
            self.state.player_gold[player_id] = max(0.0, self.state.player_gold[player_id] - actual_cost)

            # Record the intended on-state so it can be applied when build completes
            if new_edge_should_be_on:
                # Mark that once building finishes, this edge should turn on, as long as ownership stays the same
                setattr(new_edge, 'post_build_turn_on', True)
                setattr(new_edge, 'post_build_turn_on_owner', player_id)

            if self.state:
                node_movements = resolve_sharp_angles(
                    self.state,
                    new_edge,
                    self._apply_edge_warp_geometry,
                )

            return True, new_edge, actual_cost, None, removed_edges, node_movements
            
        except GameValidationError as e:
            return False, None, 0.0, str(e), [], []
    
    def handle_quit_game(self, token: str) -> Optional[int]:
        """
        Handle a player quitting the game.
        Returns the winner's player ID, or None if no game active.
        """
        if not self.state or token not in self.token_to_player_id:
            return None
        
        player_id = self.token_to_player_id[token]

        self._eliminate_player(player_id)

        active_players = [pid for pid in self.state.players.keys() if pid not in self.state.eliminated_players]
        if len(active_players) == 1:
            winner_id = active_players[0]
            self._end_game()
            return winner_id

        return None

    def handle_disconnect(self, token: str) -> Optional[int]:
        """
        Handle a player disconnect after grace period.
        Returns the winner's player ID, or None if no game active.
        """
        if not self.state or token not in self.token_to_player_id:
            return None
        
        player_id = self.token_to_player_id[token]
        if player_id in self.state.eliminated_players:
            return None

        self._eliminate_player(player_id)

        active_players = [pid for pid in self.state.players.keys() if pid not in self.state.eliminated_players]
        if len(active_players) == 1:
            winner_id = active_players[0]
            self._end_game()
            return winner_id

        return None
    
    def _end_game(self) -> None:
        """End the current game and reset state."""
        self.token_to_player_id.clear()
        self.player_id_to_token.clear()
        self.player_meta.clear()
        self.game_active = False

    def _deactivate_player_edges(self, player_id: int) -> None:
        """Force all edges controlled by the player to remain off."""
        if not self.state:
            return

        for edge in self.state.edges.values():
            source_node = self.state.nodes.get(edge.source_node_id)
            if source_node and source_node.owner == player_id:
                edge.on = False
                edge.flowing = False

    def _eliminate_player(self, player_id: int) -> None:
        """Mark a player as eliminated, disable auto actions, and shut off edges."""
        if not self.state:
            return

        if player_id in self.state.eliminated_players:
            return

        self.state.eliminated_players.add(player_id)
        self.state.pending_eliminations.append(player_id)
        self.state.player_auto_expand[player_id] = False
        if hasattr(self.state, 'pending_auto_expand_nodes'):
            self.state.pending_auto_expand_nodes.pop(player_id, None)
        self.state.player_auto_attack[player_id] = False
        if hasattr(self.state, 'pending_auto_attack_nodes'):
            self.state.pending_auto_attack_nodes.pop(player_id, None)
        self._deactivate_player_edges(player_id)

    def _edge_exists_between_nodes(self, node_id1: int, node_id2: int) -> bool:
        """Check if an edge already exists between two nodes."""
        if not self.state:
            return False
        
        for edge in self.state.edges.values():
            if ((edge.source_node_id == node_id1 and edge.target_node_id == node_id2) or
                (edge.source_node_id == node_id2 and edge.target_node_id == node_id1)):
                return True
        return False

    def _edge_behaves_like_brass(self, edge: Optional[Edge]) -> bool:
        """Determine whether an edge should behave as brass for break/cross logic."""
        if edge is None:
            return False
        if getattr(edge, "pipe_type", "normal") == "gold":
            return True
        if not self.state:
            return False
        pipe_break_mode = str(getattr(self.state, "pipe_break_mode", "") or "").strip().lower()
        if pipe_break_mode != "flowing":
            return False
        return not bool(getattr(edge, "flowing", False))
    
    def _find_intersecting_edges(
        self,
        from_node: Node,
        to_node: Node,
        candidate_segments: List[Tuple[float, float, float, float]],
    ) -> List[int]:
        """Return IDs of edges that would intersect the candidate segments."""
        if not self.state or not candidate_segments:
            return []

        intersecting: List[int] = []

        for edge_id, edge in self.state.edges.items():
            source_node = self.state.nodes.get(edge.source_node_id)
            target_node = self.state.nodes.get(edge.target_node_id)
            if source_node is None or target_node is None:
                continue

            # Skip if edges share a node (touching at endpoints is allowed)
            if (
                from_node.id == source_node.id
                or from_node.id == target_node.id
                or to_node.id == source_node.id
                or to_node.id == target_node.id
            ):
                continue

            existing_segments = self._edge_segments(edge)
            if not existing_segments:
                continue

            for cx1, cy1, cx2, cy2 in candidate_segments:
                for ex1, ey1, ex2, ey2 in existing_segments:
                    if self._line_segments_intersect(cx1, cy1, cx2, cy2, ex1, ey1, ex2, ey2):
                        intersecting.append(edge_id)
                        # No need to record duplicates for the same edge
                        break
                else:
                    continue
                break

        return intersecting

    def _remove_edges(self, edge_ids: List[int]) -> List[int]:
        """Remove edges from the state and detach them from connected nodes."""
        if not self.state or not edge_ids:
            return []

        return self.state.remove_edges(edge_ids, record=False)

    def _edges_would_intersect(
        self,
        from_node: Node,
        to_node: Node,
        candidate_segments: List[Tuple[float, float, float, float]],
    ) -> bool:
        """Check if a new edge would intersect existing edges (warp-aware)."""
        return bool(self._find_intersecting_edges(from_node, to_node, candidate_segments))
    
    def _line_segments_intersect(self, x1, y1, x2, y2, x3, y3, x4, y4) -> bool:
        """Check if two line segments intersect."""
        def orientation(px, py, qx, qy, rx, ry):
            val = (qy - py) * (rx - qx) - (qx - px) * (ry - qy)
            if abs(val) < 1e-10:
                return 0
            return 1 if val > 0 else 2
        
        def on_segment(px, py, qx, qy, rx, ry):
            return (qx <= max(px, rx) and qx >= min(px, rx) and
                    qy <= max(py, ry) and qy >= min(py, ry))
        
        o1 = orientation(x1, y1, x2, y2, x3, y3)
        o2 = orientation(x1, y1, x2, y2, x4, y4)
        o3 = orientation(x3, y3, x4, y4, x1, y1)
        o4 = orientation(x3, y3, x4, y4, x2, y2)
        
        # General case
        if o1 != o2 and o3 != o4:
            return True
        
        # Special cases for collinear points
        if (o1 == 0 and on_segment(x1, y1, x3, y3, x2, y2) or
            o2 == 0 and on_segment(x1, y1, x4, y4, x2, y2) or
            o3 == 0 and on_segment(x3, y3, x1, y1, x4, y4) or
            o4 == 0 and on_segment(x3, y3, x2, y2, x4, y4)):
            return True
        
        return False

    def _segment_intersection_point(
        self,
        x1: float,
        y1: float,
        x2: float,
        y2: float,
        x3: float,
        y3: float,
        x4: float,
        y4: float,
    ) -> Optional[Tuple[float, float, float]]:
        """Return intersection (x,y) and parametric t on first segment if the segments intersect."""
        denom = (x1 - x2) * (y3 - y4) - (y1 - y2) * (x3 - x4)
        if abs(denom) < 1e-9:
            return None

        t = ((x1 - x3) * (y3 - y4) - (y1 - y3) * (x3 - x4)) / denom
        u = ((x1 - x3) * (y1 - y2) - (y1 - y3) * (x1 - x2)) / denom

        if -1e-6 <= t <= 1 + 1e-6 and -1e-6 <= u <= 1 + 1e-6:
            ix = x1 + t * (x2 - x1)
            iy = y1 + t * (y2 - y1)
            return ix, iy, max(0.0, min(1.0, t))

        return None

    def _distance_to_first_intersection(
        self,
        candidate_segments: List[Tuple[float, float, float, float]],
        existing_edge: Optional[Edge],
    ) -> Optional[float]:
        if not candidate_segments or not existing_edge:
            return None

        existing_segments = self._edge_segments(existing_edge)
        if not existing_segments:
            return None

        cumulative = 0.0
        for csx, csy, cex, cey in candidate_segments:
            seg_len = math.hypot(cex - csx, cey - csy)
            if seg_len <= 0:
                continue
            for esx, esy, eex, eey in existing_segments:
                point = self._segment_intersection_point(csx, csy, cex, cey, esx, esy, eex, eey)
                if point is not None:
                    _, _, t = point
                    return cumulative + (t * seg_len)
            cumulative += seg_len

        return None

    def _compute_king_movement_timing(self, total_distance: float) -> Dict[str, int]:
        distance = float(total_distance)
        if not math.isfinite(distance) or distance <= 0.0:
            distance = 1.0
        ticks_per_unit = max(1e-3, float(KING_CROWN_TICKS_PER_UNIT_DISTANCE))
        travel_ticks = int(round(distance * ticks_per_unit))
        travel_ticks = max(KING_CROWN_MIN_TRAVEL_TICKS, travel_ticks)
        # Pre/post spin run for a fixed number of ticks regardless of travel distance
        spin_ticks = max(0, int(KING_CROWN_SPIN_TICKS))
        pre_spin_ticks = spin_ticks
        post_spin_ticks = spin_ticks
        total_ticks = pre_spin_ticks + travel_ticks + post_spin_ticks
        return {
            "preSpinTicks": pre_spin_ticks,
            "travelTicks": max(1, travel_ticks),
            "postSpinTicks": post_spin_ticks,
            "totalTicks": max(1, total_ticks),
        }

    def _plan_king_smash_move(
        self,
        player_id: int,
        origin_node: Node,
        destination_node: Node,
        movement_mode: str,
        warp_info: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        if not self.state:
            raise GameValidationError("No game state")
        if destination_node.owner != player_id:
            raise GameValidationError("Destination not controlled")

        # Use client-provided warp_info if available, otherwise fall back to computing path
        # Note: Without warp_info, the backend computes the shortest path which may warp.
        # The frontend should always send warp_info to ensure the path matches what the user drew.
        if warp_info:
            warp_axis, candidate_segments, total_world_distance = self._parse_client_warp_info(
                warp_info, origin_node, destination_node
            )
        else:
            path_info = self._compute_warp_bridge_path(origin_node, destination_node)
            candidate_segments = path_info.get("segments") or []
            warp_axis = path_info.get("warp_axis", "none") or "none"
            total_world_distance = float(path_info.get("total_distance") or 0.0)

        if not candidate_segments:
            candidate_segments = [(origin_node.x, origin_node.y, destination_node.x, destination_node.y)]
        if total_world_distance <= 0.0:
            total_world_distance = float(math.hypot(destination_node.x - origin_node.x, destination_node.y - origin_node.y))
        if total_world_distance <= 0.0:
            raise GameValidationError("Nodes overlap")

        base_cost = self.calculate_bridge_cost(origin_node, destination_node, segments_override=candidate_segments)
        crown_shot_cost_multiplier = float(getattr(self.state, "crown_shot_cost_multiplier", DEFAULT_CROWN_SHOT_COST))
        cost = int(round(base_cost * crown_shot_cost_multiplier))
        intersecting_edges = self._find_intersecting_edges(origin_node, destination_node, candidate_segments)
        removals: List[Tuple[int, float]] = []
        for edge_id in intersecting_edges:
            edge = self.state.edges.get(edge_id)
            if not edge:
                continue
            behaves_like_brass = self._edge_behaves_like_brass(edge)
            if movement_mode == "weak-smash" and behaves_like_brass:
                raise GameValidationError("Cannot cross brass pipe")
            distance = self._distance_to_first_intersection(candidate_segments, edge) or 0.0
            removals.append((edge_id, max(0.0, distance)))

        timing = self._compute_king_movement_timing(total_world_distance)
        return {
            "cost": int(cost),
            "segments": [(float(sx), float(sy), float(ex), float(ey)) for sx, sy, ex, ey in candidate_segments],
            "warp_axis": warp_axis,
            "total_distance": float(total_world_distance),
            "removals": removals,
            "timing": timing,
            "movement_mode": movement_mode,
        }

    def _schedule_king_smash_removals(self, plan: Dict[str, Any]) -> None:
        if not self.state:
            return
        removals = plan.get("removals") or []
        if not removals:
            return
        total_distance = float(plan.get("total_distance") or 0.0)
        timing = plan.get("timing") or {}
        travel_ticks = max(1, int(timing.get("travelTicks", KING_CROWN_MIN_TRAVEL_TICKS)))
        pre_spin_ticks = max(0, int(timing.get("preSpinTicks", 0)))
        if total_distance <= 0.0:
            total_distance = float(travel_ticks)
        distance_per_tick = total_distance / max(1, travel_ticks)
        current_tick = int(getattr(self.state, "tick_count", 0))
        pending = list(getattr(self.state, "pending_king_smash_removals", []) or [])
        for edge_id, distance in removals:
            try:
                edge_int = int(edge_id)
            except (TypeError, ValueError):
                continue
            dist_val = max(0.0, float(distance))
            if distance_per_tick <= 0:
                travel_offset = travel_ticks
            else:
                # Use floor so edges are removed as crown reaches them during travel
                travel_offset = int(dist_val / max(distance_per_tick, 1e-9))
            # Account for pre-spin phase: crown doesn't start moving until after pre-spin
            trigger_tick = current_tick + pre_spin_ticks + travel_offset
            pending.append((edge_int, trigger_tick))
        self.state.pending_king_smash_removals = pending
    
    def simulate_tick(self, tick_interval_seconds: float) -> Optional[int]:
        """
        Simulate one game tick and return winner ID if game ended.
        """
        if not self.state or not self.game_active:
            return None
        
        # Transition from picking to playing when ready and block simulation until then
        self._try_start_play_phase()
        if self.state.phase != "playing":
            return None

        self.state.simulate_tick(tick_interval_seconds)
        self._finalize_auto_reversed_edges()

        for eliminated_id in list(self.state.eliminated_players):
            self._deactivate_player_edges(eliminated_id)

        if self.state.game_ended and self.state.winner_id is not None:
            self._end_game()
            return self.state.winner_id

        sandbox_mode = bool(getattr(self.state, "sandbox_mode", False))

        if not sandbox_mode:
            if getattr(self.state, "win_condition", "dominate") == "dominate":
                # Check for node count victory (2/3 rule)
                winner_id = self.state.check_node_count_victory()
                if winner_id is not None:
                    self._end_game()
                    return winner_id

            # Check for money victory (300 gold)
            winner_id = self.state.check_money_victory()
            if winner_id is not None:
                self._end_game()
                return winner_id

            # Check for zero nodes loss condition
            winner_id = self.state.check_zero_nodes_loss()
            if winner_id is not None:
                self._end_game()
                return winner_id

        # Check for timer expiration
        winner_id = self.state.check_timer_expiration(time.time())
        if winner_id is not None:
            self._end_game()
            return winner_id
        
        return None
    
    def handle_local_targeting(self, token: str, target_node_id: int) -> bool:
        """
        Handle local targeting - just turn on edges flowing directly into the target node.
        This is the simplified targeting behavior when the targeting toggle is OFF.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_player_can_act(player_id)
            current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
            if current_mode == "go":
                return False
            target_node = self.validate_node_exists(target_node_id)
            
            # Find all edges that flow into the target node and are owned by the player
            edges_activated = False
            for edge in self.state.edges.values():
                if edge.target_node_id == target_node_id:
                    source_node = self.state.nodes.get(edge.source_node_id)
                    if source_node and source_node.owner == player_id:
                        # Turn on this edge
                        edge.on = True
                        edges_activated = True
            
            return edges_activated
            
        except GameValidationError:
            return False
    
    def handle_redirect_energy(self, token: str, target_node_id: int) -> bool:
        """
        Redirect energy flow towards a target node by optimizing edge states.
        This algorithm turns on/off edges to maximize energy flow to the target node.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_player_can_act(player_id)
            current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
            if current_mode == "go":
                return False
            target_node = self.validate_node_exists(target_node_id)
            
            # Get all nodes owned by the player
            player_nodes = [node for node in self.state.nodes.values() 
                          if node.owner == player_id]
            
            if not player_nodes:
                raise GameValidationError("You don't own any nodes")
            
            # Check if the target node can receive flow from any player nodes
            can_reach_target = False
            for edge in self.state.edges.values():
                if edge.target_node_id == target_node_id:
                    source_node = self.state.nodes.get(edge.source_node_id)
                    if source_node and source_node.owner == player_id:
                        can_reach_target = True
                        break
            
            if not can_reach_target:
                raise GameValidationError("No path to target node")
            
            # Algorithm: Maximize flow to target node
            self._optimize_energy_flow_to_target(player_id, target_node_id)
            
            return True
            
        except GameValidationError:
            return False
    
    def handle_destroy_node(
        self,
        token: str,
        node_id: int,
        cost: float = 3.0,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """
        Handle destroying a node owned by the player.
        Returns: (success, error_message)
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_player_can_act(player_id)
            
            # Validate node
            node = self.validate_node_exists(node_id)
            self.validate_player_owns_node(node, player_id)

            if getattr(self.state, "win_condition", "dominate") == "king" and getattr(node, "king_owner_id", None) == player_id:
                raise GameValidationError("Can't destroy your king node")
            
            # Validate gold
            self.validate_sufficient_gold(player_id, cost)
            
            removal_info = self.state.remove_node_and_edges(node_id)
            if not removal_info:
                raise GameValidationError("Invalid node")

            # Deduct gold
            self.state.player_gold[player_id] = max(0.0, self.state.player_gold[player_id] - cost)

            payload = dict(removal_info)
            payload["playerId"] = player_id
            return True, None, payload
            
        except GameValidationError as e:
            return False, str(e), None

    def handle_nuke_node(
        self,
        token: str,
        node_id: int,
    ) -> Tuple[bool, Optional[str], Optional[Dict[str, Any]]]:
        """Handle nuking a node in Nuke mode (removes node and attached edges)."""

        try:
            self.validate_game_active()
            current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
            if current_mode != "nuke":
                raise GameValidationError("Nukes are only available in Nuke mode")

            player_id = self.validate_player(token)
            self.validate_player_can_act(player_id)

            node = self.validate_node_exists(node_id)
            self.validate_player_owns_node(node, player_id)

            if getattr(self.state, "win_condition", "dominate") == "king" and getattr(node, "king_owner_id", None) == player_id:
                raise GameValidationError("Can't nuke your king node")

            removal_info = self.state.remove_node_and_edges(node_id)
            if not removal_info:
                raise GameValidationError("Invalid node")

            payload = dict(removal_info)
            payload["playerId"] = player_id
            return True, None, payload

        except GameValidationError as e:
            return False, str(e), None

    def handle_sandbox_create_node(
        self,
        token: str,
        x: Any,
        y: Any,
    ) -> Optional[Dict[str, Any]]:
        """Create a new neutral node at the given coordinates (sandbox only)."""
        try:
            self.validate_game_active()
            self.validate_player(token)
            if not self.state or not getattr(self.state, "sandbox_mode", False):
                raise GameValidationError("Sandbox only")

            try:
                x_val = float(x)
                y_val = float(y)
            except (TypeError, ValueError):
                raise GameValidationError("Invalid coordinates")

            next_id = (max(self.state.nodes.keys(), default=0) + 1) if self.state.nodes else 1
            node = Node(id=next_id, x=x_val, y=y_val, juice=SANDBOX_NODE_JUICE, owner=None)
            self.state.nodes[node.id] = node

            return {
                "node": {
                    "id": node.id,
                    "x": round(node.x, 3),
                    "y": round(node.y, 3),
                    "size": round(node.juice, 3),
                    "owner": node.owner,
                    "pendingGold": round(getattr(node, "pending_gold", 0.0), 3),
                    "isBrass": getattr(node, "node_type", "normal") == "brass",
                },
                "totalNodes": len(self.state.nodes),
                "winThreshold": self.state.calculate_win_threshold(),
                "winCondition": getattr(self.state, "win_condition", "dominate"),
            }
        except GameValidationError:
            return None

    def handle_sandbox_clear_board(self, token: str) -> Optional[Dict[str, Any]]:
        """Remove all nodes and edges while staying within the same sandbox session."""
        try:
            self.validate_game_active()
            self.validate_player(token)
            if not self.state or not getattr(self.state, "sandbox_mode", False):
                raise GameValidationError("Sandbox only")

            removed_nodes = list(self.state.nodes.keys()) if self.state.nodes else []
            removed_edges = list(self.state.edges.keys()) if self.state.edges else []

            for node_id in removed_nodes:
                self.state.remove_node_and_edges(node_id)

            self.state.pending_node_movements = {}
            self.state.pending_edge_removals = []
            self.state.pending_auto_expand_nodes = {}
            self.state.pending_auto_attack_nodes = {}
            if hasattr(self.state, "pending_node_captures"):
                self.state.pending_node_captures = []
            self.state.player_king_nodes.clear()

            return {
                "removedNodes": removed_nodes,
                "removedEdges": removed_edges,
                "totalNodes": len(self.state.nodes),
                "winThreshold": self.state.calculate_win_threshold(),
                "winCondition": getattr(self.state, "win_condition", "dominate"),
            }
        except GameValidationError:
            return None

    def _optimize_energy_flow_to_target(self, player_id: int, target_node_id: int) -> None:
        """
        Optimize energy flow to maximize flow towards the target node using shortest paths.
        Algorithm:
        1. Start from target node and work backwards
        2. For each node, find the shortest path to target through player-owned edges
        3. Each node should only send energy through ONE optimal outgoing edge
        4. Turn off all other outgoing edges from that node
        """
        from collections import deque, defaultdict
        
        # Build reverse adjacency list (who can send TO each node)
        incoming_edges = defaultdict(list)
        outgoing_edges = defaultdict(list)
        
        for edge in self.state.edges.values():
            source_node = self.state.nodes.get(edge.source_node_id)
            if source_node and source_node.owner == player_id:
                incoming_edges[edge.target_node_id].append(edge)
                outgoing_edges[edge.source_node_id].append(edge)
        
        # BFS backwards from target to find shortest paths
        distances = {target_node_id: 0}
        best_next_hop = {}  # node_id -> edge_id (the ONE edge this node should use)
        queue = deque([target_node_id])
        
        while queue:
            current_node_id = queue.popleft()
            current_distance = distances[current_node_id]
            
            # Look at all edges that can send TO the current node
            for edge in incoming_edges[current_node_id]:
                source_node_id = edge.source_node_id
                
                # If we haven't visited this source node, or we found a shorter path
                if source_node_id not in distances or distances[source_node_id] > current_distance + 1:
                    distances[source_node_id] = current_distance + 1
                    best_next_hop[source_node_id] = edge.id
                    queue.append(source_node_id)
        
        # Now set edge states based on the optimal paths
        for edge in self.state.edges.values():
            source_node = self.state.nodes.get(edge.source_node_id)
            
            # Only modify edges where player owns the source node
            if not source_node or source_node.owner != player_id:
                continue
            
            # Special case: Turn off ALL outgoing edges from the target node
            # (we want energy flowing INTO the target, not OUT of it)
            if edge.source_node_id == target_node_id:
                edge.on = False
            # If this node has a path to target and this is the optimal edge
            elif (edge.source_node_id in best_next_hop and 
                  best_next_hop[edge.source_node_id] == edge.id):
                # This is the ONE edge this node should use
                edge.on = True
            else:
                # Turn off all other edges from nodes that can reach target
                if edge.source_node_id in best_next_hop:
                    edge.on = False
                # For nodes that can't reach target, leave their edges as-is
                # (they might be defending other areas)
    
    def handle_toggle_auto_expand(self, token: str) -> bool:
        """
        Handle toggling the auto-expand setting for a player.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)

            if not self.state:
                raise GameValidationError("No game state")

            if player_id in self.state.eliminated_players:
                raise GameValidationError("Player eliminated")

            current_mode = normalize_game_mode(getattr(self.state, "mode", DEFAULT_GAME_MODE))
            if current_mode == "go":
                was_disabled = not self.state.player_auto_expand.get(player_id, False)
                self.state.player_auto_expand[player_id] = True
                if was_disabled:
                    self._check_auto_expand_opportunities(player_id)
                return True

            # Toggle the setting
            new_state = self.state.toggle_auto_expand(player_id)

            # If auto-expand was just turned ON, immediately check for expansion opportunities
            if new_state:
                self._check_auto_expand_opportunities(player_id)

            return True
            
        except GameValidationError:
            return False

    def handle_toggle_auto_attack(self, token: str) -> bool:
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_player_can_act(player_id)
            if not self.state:
                raise GameValidationError("No game state")

            if player_id in self.state.eliminated_players:
                raise GameValidationError("Player eliminated")

            new_state = self.state.toggle_auto_attack(player_id)
            if new_state:
                self._check_auto_attack_opportunities(player_id)

            return True

        except GameValidationError:
            return False

    def _check_auto_expand_opportunities(self, player_id: int) -> None:
        """
        Check all owned nodes for auto-expand opportunities and turn on edges to unowned nodes.
        This is called when auto-expand is turned on to immediately check existing owned nodes.
        """
        if not self.state:
            return
        
        # Find all nodes owned by this player
        owned_nodes = [node_id for node_id, node in self.state.nodes.items() 
                      if node.owner == player_id]
        
        # For each owned node, check for auto-expand opportunities
        for node_id in owned_nodes:
            self.state._auto_expand_from_node(node_id, player_id)

    def _check_auto_attack_opportunities(self, player_id: int) -> None:
        """Check all owned nodes and turn on edges to enemy neighbors when auto-attack is enabled."""
        if not self.state:
            return

        owned_nodes = [node_id for node_id, node in self.state.nodes.items() if node.owner == player_id]

        for node_id in owned_nodes:
            self.state._auto_attack_from_node(node_id, player_id)
