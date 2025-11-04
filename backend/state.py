import json
import random
from pathlib import Path
from typing import Any, Dict, List, Optional, Set, Tuple

from .constants import (
    BRIDGE_BASE_COST,
    BRIDGE_COST_PER_UNIT_DISTANCE,
    BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE,
    DEFAULT_GAME_MODE,
    GAME_DURATION_SECONDS,
    GOLD_REWARD_FOR_ENEMY_CAPTURE,
    INTAKE_TRANSFER_RATIO,
    MAX_TRANSFER_RATIO,
    NODE_MIN_JUICE,
    OVERFLOW_PENDING_GOLD_PAYOUT,
    PASSIVE_GOLD_PER_TICK,
    PASSIVE_INCOME_ENABLED,
    PRODUCTION_RATE_PER_NODE,
    RESERVE_TRANSFER_RATIO,
    STARTING_GOLD,
    UNOWNED_NODE_BASE_JUICE,
    get_neutral_capture_reward,
    get_node_max_juice,
    get_overflow_juice_to_gold_ratio,
    normalize_game_mode,
)
from .models import Edge, Node, Player


class GraphState:
    def __init__(self, nodes: List[Node], edges: List[Edge]) -> None:
        self.nodes: Dict[int, Node] = {n.id: n for n in nodes}
        self.edges: Dict[int, Edge] = {e.id: e for e in edges}
        for e in edges:
            self.nodes[e.source_node_id].attached_edge_ids.append(e.id)
            self.nodes[e.target_node_id].attached_edge_ids.append(e.id)

        self.players: Dict[int, Player] = {}
        # Economy and game flow
        self.player_gold: Dict[int, float] = {}
        # Phase: 'picking' (each player picks starting node) or 'playing'
        self.phase: str = "picking"
        # Track which players have completed their starting pick
        self.players_who_picked: Dict[int, bool] = {}
        # Track if game has ended
        self.game_ended: bool = False
        self.winner_id: Optional[int] = None

        # Track eliminated players so they can remain as spectators
        self.eliminated_players: Set[int] = set()
        self.pending_eliminations: List[int] = []
        self.pending_overflow_payouts: List[Dict[str, Any]] = []
        
        # Timer system
        self.game_start_time: Optional[float] = None
        self.game_duration: float = GAME_DURATION_SECONDS
        
        # Auto-expand settings per player
        self.player_auto_expand: Dict[int, bool] = {}
        self.pending_auto_expand_nodes: Dict[int, Set[int]] = {}
        # Auto-attack settings per player (mirrors auto-expand but targets enemy nodes)
        self.player_auto_attack: Dict[int, bool] = {}
        self.pending_auto_attack_nodes: Dict[int, Set[int]] = {}
        
        # Game mode (e.g., 'basic', 'warp')
        self.mode: str = DEFAULT_GAME_MODE
        self.neutral_capture_reward: float = get_neutral_capture_reward(self.mode)
        self.bridge_cost_per_unit: float = BRIDGE_COST_PER_UNIT_DISTANCE
        self.bridge_build_ticks_per_unit: float = BRIDGE_BUILD_TICKS_PER_UNIT_DISTANCE

        # Runtime rule configuration (overridden by game options)
        self.screen_variant: str = "flat"
        self.auto_brass_on_cross: bool = False
        self.manual_brass_selection: bool = False
        self.brass_double_cost: bool = False
        self.allow_brass_start_anywhere: bool = False
        self.mode_settings: Dict[str, Any] = {}

        # Replay helpers
        self.tick_count: int = 0
        self.pending_edge_removals: List[Dict[str, Any]] = []

        # Geometry updates queued for the next tick payload
        self.pending_node_movements: Dict[int, Dict[str, float]] = {}
        
        # Hidden game start state
        self.game_start_mode: str = "open"
        self.hidden_start_active: bool = False
        self.hidden_start_revealed: bool = False
        self.hidden_start_boundary: Optional[float] = None
        self.hidden_start_sides: Dict[int, str] = {}
        self.hidden_start_picks: Dict[int, int] = {}
        self.hidden_start_bounds: Optional[Dict[str, float]] = None
        self.hidden_start_original_sizes: Dict[int, float] = {}
        

    def add_player(self, player: Player) -> None:
        self.players[player.id] = player
        # Initialize player economy and pick status
        self.player_gold[player.id] = STARTING_GOLD
        self.players_who_picked[player.id] = False
        # Initialize auto-expand setting (default: off)
        self.player_auto_expand[player.id] = False
        # Initialize auto-attack setting (default: off)
        self.player_auto_attack[player.id] = False
        self.eliminated_players.discard(player.id)

    def get_player_node_counts(self) -> Dict[int, int]:
        """Return a mapping of player id to number of nodes they currently own."""
        counts: Dict[int, int] = {pid: 0 for pid in self.players.keys()}
        for node in self.nodes.values():
            if node.owner is not None:
                counts[node.owner] = counts.get(node.owner, 0) + 1
        return counts

    def get_active_player_ids(self) -> List[int]:
        """Return the list of players still alive in the match."""
        return [pid for pid in self.players.keys() if pid not in self.eliminated_players]



    def calculate_win_threshold(self) -> int:
        """Calculate the number of nodes needed to win based on total nodes (2/3 rule)."""
        total_nodes = len(self.nodes)
        if total_nodes % 3 == 0:
            # If divisible by 3, need exactly 2/3
            return (total_nodes * 2) // 3
        else:
            # If not divisible by 3, get as close to 2/3 as possible
            return (total_nodes * 2 + 2) // 3  # This rounds up to get closest to 2/3

    def check_node_count_victory(self) -> Optional[int]:
        """Check if any player has reached the win threshold. Returns winner ID or None."""
        if self.game_ended:
            return self.winner_id
            
        # Only check after picking phase is complete
        if self.phase == "picking":
            return None
            
        win_threshold = self.calculate_win_threshold()
        node_counts = self.get_player_node_counts()

        # Collect players meeting or exceeding the threshold who are still active
        candidates = [
            pid for pid, count in node_counts.items()
            if pid not in self.eliminated_players and count >= win_threshold
        ]

        if not candidates:
            return None

        # Determine top candidate and ensure there is no tie on node count
        best_pid = max(candidates, key=lambda pid: node_counts.get(pid, 0))
        best_count = node_counts.get(best_pid, 0)
        if sum(1 for pid in candidates if node_counts.get(pid, 0) == best_count) > 1:
            return None

        self.game_ended = True
        self.winner_id = best_pid
        return best_pid

    def start_game_timer(self, current_time: float) -> None:
        """Start the game timer when the game begins."""
        if self.game_start_time is None:
            self.game_start_time = current_time

    def check_timer_expiration(self, current_time: float) -> Optional[int]:
        """Check if the game timer has expired. Returns winner ID or None."""
        if self.game_ended:
            return self.winner_id
            
        # Only check timer after picking phase is complete
        if self.phase == "picking":
            return None
            
        if self.game_start_time is None:
            return None
            
        elapsed_time = current_time - self.game_start_time
        if elapsed_time >= self.game_duration:
            # Timer expired - determine winner by node count
            node_counts = self.get_player_node_counts()
            active_players = [pid for pid in self.players.keys() if pid not in self.eliminated_players]

            if not active_players:
                return None

            winner_id = max(active_players, key=lambda pid: node_counts.get(pid, 0))
            max_nodes = node_counts.get(winner_id, 0)
            if sum(1 for pid in active_players if node_counts.get(pid, 0) == max_nodes) > 1:
                return None

            self.game_ended = True
            self.winner_id = winner_id
            return winner_id
                
        return None

    def check_zero_nodes_loss(self) -> Optional[int]:
        """Eliminate players with zero nodes. Returns winner ID when only one remains."""
        if self.game_ended:
            return self.winner_id
        
        # Only check for zero nodes loss after picking phase is complete
        if self.phase == "picking":
            return None
            
        node_counts = self.get_player_node_counts()
        newly_eliminated: List[int] = []

        for player_id in self.players.keys():
            if player_id in self.eliminated_players:
                continue
            if node_counts.get(player_id, 0) == 0:
                self.eliminated_players.add(player_id)
                newly_eliminated.append(player_id)

        if newly_eliminated:
            self.pending_eliminations.extend(newly_eliminated)

        active_players = [pid for pid in self.players.keys() if pid not in self.eliminated_players]
        if len(active_players) == 1:
            self.game_ended = True
            self.winner_id = active_players[0]
            return self.winner_id

        return None

    def remove_node_and_edges(self, node_id: int) -> Optional[Dict[str, Any]]:
        """Remove a node and all connected edges, returning snapshot details for messaging."""
        node = self.nodes.get(node_id)
        if not node:
            return None

        snapshot: Dict[str, Any] = {
            "id": node.id,
            "x": node.x,
            "y": node.y,
            "owner": node.owner,
            "juice": node.juice,
        }

        edges_to_remove = list(node.attached_edge_ids)
        for edge_id in edges_to_remove:
            edge = self.edges.pop(edge_id, None)
            if not edge:
                continue
            source_node = self.nodes.get(edge.source_node_id)
            target_node = self.nodes.get(edge.target_node_id)
            if source_node and edge_id in source_node.attached_edge_ids:
                source_node.attached_edge_ids.remove(edge_id)
            if target_node and edge_id in target_node.attached_edge_ids:
                target_node.attached_edge_ids.remove(edge_id)

        for pending_nodes in self.pending_auto_expand_nodes.values():
            if pending_nodes and node_id in pending_nodes:
                pending_nodes.discard(node_id)

        self.nodes.pop(node_id, None)

        return {
            "node": snapshot,
            "removedEdges": edges_to_remove,
        }

    def remove_edges(
        self,
        edge_ids: List[int],
        *,
        record: bool = False,
        reason: Optional[str] = None,
    ) -> List[int]:
        """Remove edges by id, optionally recording them for the next tick payload."""
        if not edge_ids:
            return []

        removed_ids: List[int] = []
        for edge_id in edge_ids:
            edge = self.edges.pop(edge_id, None)
            if not edge:
                continue
            removed_ids.append(edge_id)

            source_node = self.nodes.get(edge.source_node_id)
            if source_node and edge_id in source_node.attached_edge_ids:
                source_node.attached_edge_ids.remove(edge_id)

            target_node = self.nodes.get(edge.target_node_id)
            if target_node and edge_id in target_node.attached_edge_ids:
                target_node.attached_edge_ids.remove(edge_id)

        if record and removed_ids:
            for rid in removed_ids:
                payload: Dict[str, Any] = {"edgeId": rid}
                if reason:
                    payload["reason"] = reason
                self.pending_edge_removals.append(payload)

        return removed_ids

    def record_node_movement(self, node_id: int, x: float, y: float) -> None:
        """Queue a node position update for inclusion in the next tick."""
        self.pending_node_movements[int(node_id)] = {
            "nodeId": int(node_id),
            "x": float(x),
            "y": float(y),
        }

    def pop_pending_node_movements(self) -> List[Dict[str, float]]:
        if not self.pending_node_movements:
            return []
        movements = list(self.pending_node_movements.values())
        self.pending_node_movements = {}
        return movements

    def to_init_message(self, screen: Dict[str, int], tick_interval: float, current_time: float = 0.0) -> Dict:
        node_max = getattr(self, "node_max_juice", get_node_max_juice(self.mode))
        nodes_arr = [
            [
                nid,
                round(n.x, 3),
                round(n.y, 3),
                round(n.juice, 3),
                (n.owner if n.owner is not None else None),
                round(getattr(n, "pending_gold", 0.0), 3),
                1 if getattr(n, "node_type", "normal") == "brass" else 0,
            ]
            for nid, n in self.nodes.items()
        ]
        edges_arr = [
            [
                eid,
                e.source_node_id,
                e.target_node_id,
                0,
                1,
                int(getattr(e, 'build_ticks_required', 0)),
                int(getattr(e, 'build_ticks_elapsed', 0)),
                1 if getattr(e, 'building', False) else 0,
                1 if getattr(e, 'pipe_type', 'normal') == 'gold' else 0,
            ]
            for eid, e in self.edges.items()
        ]
        edge_warp = {}
        for eid, edge in self.edges.items():
            segments = list(getattr(edge, "warp_segments", []) or [])
            if not segments:
                source = self.nodes.get(edge.source_node_id)
                target = self.nodes.get(edge.target_node_id)
                if source and target:
                    segments = [(source.x, source.y, target.x, target.y)]
            edge_warp[eid] = {
                "axis": getattr(edge, "warp_axis", "none"),
                "segments": [[sx, sy, ex, ey] for sx, sy, ex, ey in segments],
            }
        players_arr = [
            {
                "id": pid,
                "color": p.color,
                "secondaryColors": list(getattr(p, "secondary_colors", [])),
                "eliminated": pid in self.eliminated_players,
                "name": getattr(p, "name", ""),
            }
            for pid, p in self.players.items()
        ]
        gold_arr = [[pid, round(self.player_gold.get(pid, 0.0), 4)] for pid in self.players.keys()]
        picked_arr = [[pid, bool(self.players_who_picked.get(pid, False))] for pid in self.players.keys()]
        auto_expand_arr = [[pid, bool(self.player_auto_expand.get(pid, False))] for pid in self.players.keys()]
        auto_attack_arr = [[pid, bool(self.player_auto_attack.get(pid, False))] for pid in self.players.keys()]
        
        # Calculate win threshold for progress bar
        win_threshold = self.calculate_win_threshold()
        timer_remaining = None
        if self.game_start_time is not None:
            elapsed = max(0.0, current_time - self.game_start_time)
            timer_remaining = max(0.0, self.game_duration - elapsed)
        
        neutral_reward = getattr(self, "neutral_capture_reward", get_neutral_capture_reward(self.mode))

        return {
            "type": "init",
            "screen": screen,
            "tickInterval": tick_interval,
            "nodes": nodes_arr,
            "edges": edges_arr,
            "players": players_arr,
            "settings": {
                "nodeMaxJuice": node_max,
                "bridgeBaseCost": BRIDGE_BASE_COST,
                "bridgeCostPerUnit": self.bridge_cost_per_unit,
                "neutralCaptureReward": neutral_reward,
                "overflowPendingGoldPayout": OVERFLOW_PENDING_GOLD_PAYOUT,
            },
            "phase": self.phase,
            "gold": gold_arr,
            "picked": picked_arr,
            "winThreshold": win_threshold,
            "totalNodes": len(self.nodes),
            "autoExpand": auto_expand_arr,
            "autoAttack": auto_attack_arr,
            "eliminatedPlayers": sorted(self.eliminated_players),
            "gameDuration": self.game_duration,
            "timerRemaining": timer_remaining,
            "mode": self.mode,
            "modeSettings": dict(self.mode_settings or {}),
            "edgeWarp": edge_warp,
        }

    def to_tick_message(self, current_time: float = 0.0) -> Dict:
        edges_arr = [[
            eid,
            1 if e.on else 0,
            1 if e.flowing else 0,
            1,
            round(getattr(e, 'last_transfer', 0.0), 3),
            int(getattr(e, 'build_ticks_required', 0)),
            int(getattr(e, 'build_ticks_elapsed', 0)),
            1 if getattr(e, 'building', False) else 0,
            1 if getattr(e, 'pipe_type', 'normal') == 'gold' else 0,
        ] for eid, e in self.edges.items()]  # Always forward now
        nodes_arr = [
            [
                nid,
                round(n.juice, 3),
                (n.owner if n.owner is not None else None),
                round(getattr(n, "pending_gold", 0.0), 3),
                1 if getattr(n, "node_type", "normal") == "brass" else 0,
            ]
            for nid, n in self.nodes.items()
        ]
        counts = self.get_player_node_counts()
        gold_arr = [[pid, round(self.player_gold.get(pid, 0.0), 4)] for pid in self.players.keys()]
        picked_arr = [[pid, bool(self.players_who_picked.get(pid, False))] for pid in self.players.keys()]
        auto_expand_arr = [[pid, bool(self.player_auto_expand.get(pid, False))] for pid in self.players.keys()]
        auto_attack_arr = [[pid, bool(self.player_auto_attack.get(pid, False))] for pid in self.players.keys()]
        
        # Calculate win threshold for progress bar
        win_threshold = self.calculate_win_threshold()
        eliminated_players = sorted(self.eliminated_players)
        recent_eliminations = list(self.pending_eliminations)
        self.pending_eliminations = []
        timer_remaining = None
        if self.game_start_time is not None:
            elapsed = max(0.0, current_time - self.game_start_time)
            timer_remaining = max(0.0, self.game_duration - elapsed)
        
        removed_edge_events = [dict(event) for event in self.pending_edge_removals]
        self.pending_edge_removals = []
        node_movements = self.pop_pending_node_movements()

        message = {
            "type": "tick",
            "edges": edges_arr,
            "nodes": nodes_arr,
            "counts": counts,
            "totalNodes": len(self.nodes),
            "phase": self.phase,
            "gold": gold_arr,
            "picked": picked_arr,
            "winThreshold": win_threshold,
            "autoExpand": auto_expand_arr,
            "autoAttack": auto_attack_arr,
            "eliminatedPlayers": eliminated_players,
            "recentEliminations": recent_eliminations,
            "gameDuration": self.game_duration,
            "timerRemaining": timer_remaining,
            "mode": self.mode,
            "modeSettings": dict(self.mode_settings or {}),
        }

        if node_movements:
            message["nodeMovements"] = [
                [movement["nodeId"], round(movement["x"], 3), round(movement["y"], 3)]
                for movement in node_movements
            ]

        if removed_edge_events:
            message["removedEdges"] = [event.get("edgeId") for event in removed_edge_events]
            message["removedEdgeEvents"] = removed_edge_events

        return message

    def _update_edge_flowing_status(self) -> None:
        """
        Update the flowing status of all edges based on whether their target nodes can receive flow.
        An edge can only flow if:
        1. It is turned on (on = True)
        2. For friendly flows: target node is not full (juice < NODE_MAX_JUICE)
        3. For attacking flows: always flow when on (regardless of target capacity)
        """
        node_max = getattr(self, "node_max_juice", get_node_max_juice(self.mode))
        normalized_mode = normalize_game_mode(self.mode)
        is_overflow_mode = normalized_mode in {"overflow", "nuke", "cross", "brass-old", "go", "warp", "flat", "i-warp", "i-flat"}
        is_go_mode = normalized_mode == "go"
        for edge in self.edges.values():
            # Handle bridge build gating: while building, edge cannot be on/flowing
            building = getattr(edge, 'building', False)
            source_node = self.nodes.get(edge.source_node_id)
            if is_go_mode:
                if building:
                    edge.on = False
                else:
                    edge.on = bool(source_node and source_node.owner is not None)

            if building:
                edge.flowing = False
                continue
            if not edge.on:
                # Edge is not turned on, so it cannot flow
                edge.flowing = False
                continue
            
            # Get source and target nodes
            target_node = self.nodes.get(edge.target_node_id)
            
            if source_node is None or target_node is None:
                edge.flowing = False
                continue
            
            # Check if this is an attacking flow (source and target have different owners)
            if (source_node.owner is not None and 
                target_node.owner is not None and 
                source_node.owner != target_node.owner):
                # This is an attacking flow - it can always flow when on
                edge.flowing = True
            elif (source_node.owner is not None and 
                  (target_node.owner is None or target_node.owner == source_node.owner)):
                # This is a friendly flow - it can only flow if target is not full
                if is_overflow_mode:
                    edge.flowing = True
                elif target_node.juice >= node_max:
                    edge.flowing = False
                else:
                    edge.flowing = True
            else:
                # Edge is on but cannot flow for other reasons
                edge.flowing = False

    def simulate_tick(self, tick_interval_seconds: float) -> None:
        # Progress bridge builds
        for e in list(self.edges.values()):
            if getattr(e, 'building', False):
                e.build_ticks_elapsed = int(getattr(e, 'build_ticks_elapsed', 0)) + 1
                if e.build_ticks_elapsed >= int(getattr(e, 'build_ticks_required', 0)):
                    e.building = False
                    # Edge becomes eligible for being on, but do not auto-on here unless previously intended
                    # If it was intended to be on (e.g., creator owns source), we can turn it on now
                    src = self.nodes.get(e.source_node_id)
                    if src and src.owner is not None:
                        # Leave 'on' state as-is; game logic elsewhere may toggle it
                        pass
        # Handle delayed cross removals tied to bridge construction progress
        for e in list(self.edges.values()):
            pending = list(getattr(e, 'pending_cross_removals', []) or [])
            if not pending:
                continue

            elapsed = int(getattr(e, 'build_ticks_elapsed', 0))
            remaining: List[Tuple[int, int]] = []
            ready_ids: List[int] = []
            for target_edge_id, trigger in pending:
                if elapsed >= trigger:
                    ready_ids.append(target_edge_id)
                else:
                    remaining.append((target_edge_id, trigger))

            if ready_ids:
                self.remove_edges(ready_ids, record=True, reason="bridgeCross")

            e.pending_cross_removals = remaining

        # Update edge build progress and apply post-build on-state
        for e in list(self.edges.values()):
            if getattr(e, 'building', False):
                continue
            if getattr(e, 'post_build_turn_on', False):
                expected_owner = getattr(e, 'post_build_turn_on_owner', None)
                source_node = self.nodes.get(e.source_node_id)
                if expected_owner is None and source_node:
                    expected_owner = source_node.owner
                if source_node and expected_owner is not None and source_node.owner == expected_owner:
                    # Apply intended on-state once if ownership still matches
                    e.on = True
                setattr(e, 'post_build_turn_on', False)
                if hasattr(e, 'post_build_turn_on_owner'):
                    delattr(e, 'post_build_turn_on_owner')

        # Update flowing status for all edges based on target node capacity
        self._update_edge_flowing_status()

        node_max = getattr(self, "node_max_juice", get_node_max_juice(self.mode))
        normalized_mode = normalize_game_mode(self.mode)
        is_overflow_mode = normalized_mode in {"overflow", "nuke", "cross", "brass-old", "go", "warp", "flat", "i-warp", "i-flat"}
        overflow_ratio = get_overflow_juice_to_gold_ratio(self.mode)
        if self.pending_overflow_payouts:
            self.pending_overflow_payouts.clear()

        # Passive gold income for active players ($1 every 3 seconds)
        if (
            normalize_game_mode(self.mode) == "basic"
            and not self.game_ended
            and PASSIVE_INCOME_ENABLED
            and PASSIVE_GOLD_PER_TICK > 0.0
        ):
            passive_income = PASSIVE_GOLD_PER_TICK
            for player_id in self.players.keys():
                if player_id in self.eliminated_players:
                    continue
                self.player_gold[player_id] = self.player_gold.get(player_id, 0.0) + passive_income

        # Direction/flow toggles are input-driven; no random changes here
        # Compute size deltas from production and flows
        size_delta: Dict[int, float] = {nid: 0.0 for nid in self.nodes.keys()}
        
        # Track juice intake for each node (from friendly nodes only)
        intake_tracking: Dict[int, float] = {nid: 0.0 for nid in self.nodes.keys()}

        # Production for owned nodes
        for node in self.nodes.values():
            if node.owner is not None:
                if is_overflow_mode and node.juice >= node_max - 1e-6:
                    continue
                size_delta[node.id] += PRODUCTION_RATE_PER_NODE

        # Reset last_transfer for all edges at the start of the tick
        for e in self.edges.values():
            e.last_transfer = 0.0

        # Flows using intake-influenced transfer amounts
        pending_ownership: Dict[int, int] = {}  # node_id -> new_owner_id
        outgoing_by_node: Dict[int, List[int]] = {}
        for e in self.edges.values():
            if not e.flowing:
                continue
            src_id = e.source_node_id  # All edges flow from source to target
            outgoing_by_node.setdefault(src_id, []).append(e.id)

        # Compute per-edge transfer amounts: 95% of last tick's intake plus 1% of remaining reserves
        per_edge_amount: Dict[int, float] = {}
        remaining_transfer: Dict[int, float] = {}
        for src_id, edge_ids in outgoing_by_node.items():
            src_node = self.nodes.get(src_id)
            if src_node is None:
                continue
            
            prev_intake = max(0.0, src_node.cur_intake)
            reserves = max(0.0, src_node.juice - prev_intake)

            transfer_from_intake = prev_intake * INTAKE_TRANSFER_RATIO
            transfer_from_reserve = reserves * RESERVE_TRANSFER_RATIO

            total_transfer = transfer_from_intake + transfer_from_reserve
            max_transfer_allowed = max(0.0, src_node.juice * MAX_TRANSFER_RATIO)
            total_transfer = min(total_transfer, max_transfer_allowed)
            total_transfer = min(total_transfer, src_node.juice)

            if total_transfer <= 0 or len(edge_ids) == 0:
                continue
            amount_each = total_transfer / len(edge_ids)
            for eid in edge_ids:
                per_edge_amount[eid] = amount_each
            remaining_transfer[src_id] = total_transfer

        # Apply transfers and track friendly intake
        for eid, amount in per_edge_amount.items():
            edge = self.edges.get(eid)
            if edge is None:
                continue
            from_id = edge.source_node_id  # All edges flow from source to target
            to_id = edge.target_node_id
            from_node = self.nodes.get(from_id)
            to_node = self.nodes.get(to_id)
            if from_node is None or to_node is None:
                continue

            remaining = remaining_transfer.get(from_id, amount)
            if remaining <= 0:
                continue

            actual_transfer = min(amount, remaining)

            is_friendly_flow = (from_node.owner is not None and to_node.owner == from_node.owner)
            if is_friendly_flow:
                if not is_overflow_mode:
                    current_target = to_node.juice + size_delta[to_id]
                    current_target = max(NODE_MIN_JUICE, min(node_max, current_target))
                    available_capacity = max(0.0, node_max - current_target)
                    if available_capacity <= 0:
                        actual_transfer = 0.0
                    else:
                        actual_transfer = min(actual_transfer, available_capacity)

            if actual_transfer <= 0:
                continue

            size_delta[from_id] -= actual_transfer
            remaining_transfer[from_id] = max(0.0, remaining - actual_transfer)

            # Record the actual amount that flowed on this edge for UI display
            edge.last_transfer = actual_transfer

            if is_friendly_flow:
                intake_tracking[to_id] += actual_transfer
                size_delta[to_id] += actual_transfer
            elif to_node.owner is None or (from_node.owner is not None and to_node.owner != from_node.owner):
                size_delta[to_id] -= actual_transfer
                projected = max(NODE_MIN_JUICE, to_node.juice + size_delta[to_id])
                if projected <= NODE_MIN_JUICE and from_node.owner is not None:
                    pending_ownership[to_id] = from_node.owner
            else:
                size_delta[to_id] += actual_transfer

        # Update cur_intake for all nodes
        for nid, intake in intake_tracking.items():
            self.nodes[nid].cur_intake = intake

        # Apply deltas and clamp
        for nid, delta in size_delta.items():
            node = self.nodes[nid]
            updated_amount = max(NODE_MIN_JUICE, node.juice + delta)
            if is_overflow_mode and node.owner is not None:
                overflow_amount = max(0.0, updated_amount - node_max)
                if overflow_amount > 0:
                    pending_gold = getattr(node, "pending_gold", 0.0)
                    pending_gold += overflow_amount / overflow_ratio
                    updated_amount -= overflow_amount

                    payout_threshold = OVERFLOW_PENDING_GOLD_PAYOUT
                    payouts = 0
                    epsilon = 1e-6
                    while pending_gold + epsilon >= payout_threshold:
                        pending_gold -= payout_threshold
                        payouts += 1

                    if payouts and node.owner is not None:
                        gold_award = payouts * OVERFLOW_PENDING_GOLD_PAYOUT
                        self.player_gold[node.owner] = self.player_gold.get(node.owner, 0.0) + gold_award
                        self.pending_overflow_payouts.append({
                            "nodeId": nid,
                            "amount": gold_award,
                            "player_id": node.owner,
                        })

                    pending_gold = max(0.0, pending_gold)
                    node.pending_gold = pending_gold

                node.juice = max(NODE_MIN_JUICE, min(node_max, updated_amount))
            else:
                node.juice = max(NODE_MIN_JUICE, min(node_max, updated_amount))

        # Apply pending ownership changes and award gold for capturing unowned nodes
        for nid, new_owner in pending_ownership.items():
            node = self.nodes.get(nid)
            if node is None:
                continue
            if node.juice <= NODE_MIN_JUICE:
                # Determine reward based on the previous owner state
                previous_owner = node.owner
                reward = 0.0
                if previous_owner is None:
                    reward = get_neutral_capture_reward(self.mode)
                    self.neutral_capture_reward = reward
                elif previous_owner != new_owner:
                    reward = GOLD_REWARD_FOR_ENEMY_CAPTURE

                if reward > 0.0:
                    # Award gold for capturing the node
                    self.player_gold[new_owner] = self.player_gold.get(new_owner, 0.0) + reward
                    # Store the capture event for frontend notification
                    if not hasattr(self, 'pending_node_captures'):
                        self.pending_node_captures = []
                    self.pending_node_captures.append({
                        'nodeId': nid,
                        'reward': reward,
                        'player_id': new_owner
                    })

                node.owner = new_owner

        # All edges are now one-way only - no auto-adjustment needed
        
        # Handle auto-expand for newly captured nodes
        for nid, new_owner in pending_ownership.items():
            if self.player_auto_expand.get(new_owner, False):
                self._auto_expand_from_node(nid, new_owner)
            if self.player_auto_attack.get(new_owner, False):
                self._auto_attack_from_node(nid, new_owner)

        self.enforce_eliminated_edges_off()

        # Advance replay tick counter
        self.tick_count += 1

    def process_pending_auto_expands(self) -> None:
        """Run any auto-expand operations that were deferred during the picking phase."""
        if not self.pending_auto_expand_nodes:
            return

        for player_id, node_ids in list(self.pending_auto_expand_nodes.items()):
            if not self.player_auto_expand.get(player_id, False):
                continue

            for node_id in list(node_ids):
                self._apply_auto_expand_from_node(node_id, player_id)

        self.pending_auto_expand_nodes.clear()

    def process_pending_auto_attacks(self) -> None:
        """Run any auto-attack operations that were deferred during the picking phase."""
        if not self.pending_auto_attack_nodes:
            return

        for player_id, node_ids in list(self.pending_auto_attack_nodes.items()):
            if not self.player_auto_attack.get(player_id, False):
                continue

            for node_id in list(node_ids):
                self._apply_auto_attack_from_node(node_id, player_id)

        self.pending_auto_attack_nodes.clear()

    def _auto_expand_from_node(self, node_id: int, player_id: int) -> None:
        """
        Auto-expand from a newly captured node by turning on edges to unowned surrounding nodes.
        This method finds all edges from the captured node to unowned nodes and turns them on.
        """
        if self.phase == "picking":
            pending = self.pending_auto_expand_nodes.setdefault(player_id, set())
            pending.add(node_id)
            return

        self._apply_auto_expand_from_node(node_id, player_id)

    def _auto_attack_from_node(self, node_id: int, player_id: int) -> None:
        """Auto-attack from a captured node by turning on edges to enemy-owned nodes."""
        if self.phase == "picking":
            pending = self.pending_auto_attack_nodes.setdefault(player_id, set())
            pending.add(node_id)
            return

        self._apply_auto_attack_from_node(node_id, player_id)

    def enforce_eliminated_edges_off(self) -> None:
        """Force edges owned by eliminated players to remain off."""
        for edge in self.edges.values():
            source_node = self.nodes.get(edge.source_node_id)
            if source_node and source_node.owner in self.eliminated_players:
                edge.on = False
                edge.flowing = False

    def _apply_auto_expand_from_node(self, node_id: int, player_id: int) -> None:
        captured_node = self.nodes.get(node_id)
        if not captured_node or captured_node.owner != player_id:
            return

        # Find all edges from this node to unowned nodes
        for edge_id in captured_node.attached_edge_ids:
            edge = self.edges.get(edge_id)
            if not edge:
                continue

            # Check if this edge goes from the captured node to an unowned node
            target_node_id = None
            if edge.source_node_id == node_id:
                target_node_id = edge.target_node_id
            elif edge.target_node_id == node_id:
                # This edge goes TO the captured node, so we need to reverse it first
                # But for auto-expand, we only want to turn on edges that already point outward
                continue

            if target_node_id:
                target_node = self.nodes.get(target_node_id)
                if target_node and target_node.owner is None:
                    # This is an unowned node that can be captured - turn on the edge
                    edge.on = True

    def _apply_auto_attack_from_node(self, node_id: int, player_id: int) -> None:
        captured_node = self.nodes.get(node_id)
        if not captured_node or captured_node.owner != player_id:
            return

        for edge_id in captured_node.attached_edge_ids:
            edge = self.edges.get(edge_id)
            if not edge:
                continue

            if edge.source_node_id != node_id:
                continue

            target_node = self.nodes.get(edge.target_node_id)
            if not target_node or target_node.owner is None or target_node.owner == player_id:
                continue

            edge.on = True

    def toggle_auto_expand(self, player_id: int) -> bool:
        """
        Toggle the auto-expand setting for a player.
        Returns the new state of the setting.
        """
        if player_id not in self.player_auto_expand:
            self.player_auto_expand[player_id] = False
        
        self.player_auto_expand[player_id] = not self.player_auto_expand[player_id]
        return self.player_auto_expand[player_id]

    def toggle_auto_attack(self, player_id: int) -> bool:
        """Toggle the auto-attack setting for a player and return the new state."""
        if player_id not in self.player_auto_attack:
            self.player_auto_attack[player_id] = False

        self.player_auto_attack[player_id] = not self.player_auto_attack[player_id]
        return self.player_auto_attack[player_id]

    def _hidden_start_info_for_player(self, player_id: Optional[int]) -> Dict[str, Any]:
        revealed = self.hidden_start_revealed or self.phase != "picking"
        info: Dict[str, Any] = {
            "active": bool(self.hidden_start_active),
            "mode": self.game_start_mode,
            "revealed": bool(revealed),
        }

        if not self.hidden_start_active:
            return info

        info["boundary"] = self.hidden_start_boundary
        if self.hidden_start_bounds:
            info["bounds"] = dict(self.hidden_start_bounds)

        side = self.hidden_start_sides.get(player_id) if player_id is not None else None
        info["side"] = side

        if side and self.hidden_start_bounds and self.hidden_start_boundary is not None:
            min_x = self.hidden_start_bounds.get("minX")
            max_x = self.hidden_start_bounds.get("maxX")
            min_y = self.hidden_start_bounds.get("minY")
            max_y = self.hidden_start_bounds.get("maxY")
            boundary = self.hidden_start_boundary

            if min_x is not None and max_x is not None:
                if side == "left":
                    side_min = min_x
                    side_max = boundary
                else:
                    side_min = boundary
                    side_max = max_x
                if side_min is not None and side_max is not None:
                    info["sideBounds"] = {
                        "minX": side_min,
                        "maxX": side_max,
                        "minY": min_y,
                        "maxY": max_y,
                    }

        return info

    def build_player_view(self, payload: Dict[str, Any], player_id: Optional[int]) -> Dict[str, Any]:
        if not isinstance(payload, dict):
            return payload

        info = self._hidden_start_info_for_player(player_id)
        if info.get("active"):
            payload["hiddenStart"] = info
        elif "hiddenStart" in payload:
            payload.pop("hiddenStart", None)

        should_mask = info.get("active") and not info.get("revealed")
        if not should_mask:
            return payload

        if not isinstance(payload.get("nodes"), list):
            return payload

        # Identify players whose selections should be hidden from this viewer
        mask_player_ids = {
            pid for pid in self.hidden_start_sides.keys()
            if pid is not None and pid != player_id
        }
        if not mask_player_ids:
            return payload

        node_entries = payload.get("nodes")
        payload_type = payload.get("type")
        owner_index = 2 if payload_type == "tick" else 4

        for entry in node_entries:
            if not isinstance(entry, list):
                continue
            if owner_index >= len(entry):
                continue
            owner_id = entry[owner_index]
            try:
                owner_int = int(owner_id) if owner_id is not None else None
            except (TypeError, ValueError):
                owner_int = None
            if owner_int in mask_player_ids:
                entry[owner_index] = None
                node_id = entry[0] if entry else None
                try:
                    node_int = int(node_id)
                except (TypeError, ValueError):
                    node_int = node_id
                size_index = 1 if payload_type == "tick" else 3
                if isinstance(node_int, int) and size_index < len(entry):
                    original_size = self.hidden_start_original_sizes.get(node_int)
                    if original_size is not None:
                        entry[size_index] = round(original_size, 3)

        return payload


def load_graph(graph_path: Path) -> Tuple[GraphState, Dict[str, int]]:
    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    screen = data.get("screen", {})
    nodes_raw = data["nodes"]
    edges_raw = data["edges"]
    nodes: List[Node] = []
    for n in nodes_raw:
        node_type_val = n.get("nodeType") if isinstance(n, dict) else None
        node_type = "brass" if isinstance(node_type_val, str) and node_type_val.lower() == "brass" else "normal"
        nodes.append(
            Node(
                id=n["id"],
                x=n["x"],
                y=n["y"],
                juice=UNOWNED_NODE_BASE_JUICE,
                cur_intake=0.0,
                node_type=node_type,
            )
        )
    edges: List[Edge] = []
    for e in edges_raw:
        warp_axis = e.get("warpAxis") if isinstance(e, dict) else None
        warp_segments = e.get("warpSegments") if isinstance(e, dict) else None
        edge = Edge(id=e["id"], source_node_id=e["source"], target_node_id=e["target"])
        pipe_type = e.get("pipeType") if isinstance(e, dict) else None
        if isinstance(pipe_type, str) and pipe_type.lower() == "gold":
            edge.pipe_type = "gold"
        if isinstance(warp_axis, str):
            edge.warp_axis = warp_axis
        if isinstance(warp_segments, list):
            parsed_segments: List[Tuple[float, float, float, float]] = []
            for seg in warp_segments:
                if isinstance(seg, (list, tuple)) and len(seg) >= 4:
                    sx, sy, ex, ey = seg[:4]
                    try:
                        parsed_segments.append((float(sx), float(sy), float(ex), float(ey)))
                    except (TypeError, ValueError):
                        continue
            edge.warp_segments = parsed_segments
        edges.append(edge)
    return GraphState(nodes, edges), screen


def build_state_from_dict(data: Dict) -> Tuple[GraphState, Dict[str, int]]:
    screen = data.get("screen", {"width": 100, "height": 100, "margin": 0})
    nodes_raw = data["nodes"]
    edges_raw = data["edges"]
    # Start nodes very small (juice units)
    nodes: List[Node] = []
    for n in nodes_raw:
        node_type_val = n.get("nodeType") if isinstance(n, dict) else None
        node_type = "brass" if isinstance(node_type_val, str) and node_type_val.lower() == "brass" else "normal"
        nodes.append(
            Node(
                id=n["id"],
                x=n["x"],
                y=n["y"],
                juice=UNOWNED_NODE_BASE_JUICE,
                cur_intake=0.0,
                node_type=node_type,
            )
        )
    edges: List[Edge] = []
    for e in edges_raw:
        edge = Edge(id=e["id"], source_node_id=e["source"], target_node_id=e["target"])
        pipe_type = e.get("pipeType") if isinstance(e, dict) else None
        if isinstance(pipe_type, str) and pipe_type.lower() == "gold":
            edge.pipe_type = "gold"
        edges.append(edge)
    return GraphState(nodes, edges), screen
