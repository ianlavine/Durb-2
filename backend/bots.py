"""
Bot implementations: contains `BotPlayer` which encapsulates AI logic/strategies.
"""

import math
import time
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .game_engine import GameEngine
from .constants import BRIDGE_COST_PER_UNIT_DISTANCE, NODE_MAX_JUICE

class BotTemplate:

    def __init__(self, player_id: int = 2, color: str = "#3388ff", difficulty: str = "hard"):
        self.player_id = player_id
        self.color = color
        self.game_engine: Optional[GameEngine] = None
        self.bot_token = "bot_token_" + str(int(time.time()))
        self.last_action_time = 0.0
        self.action_cooldown = 0.5  # Minimum seconds between actions
        self.last_bridge_time = 0.0
        self.bridge_cooldown = 1.0  # Extra delay between bridge builds
        self.bridge_gold_reserve = 5.0  # Keep a proportional gold buffer when bridging
        # Targeting cadence so we don't thrash edges every tick
        self.last_target_update_time = 0.0
        self.target_cooldown = 3.0

    def join_game(self, game_engine: GameEngine) -> None:
        """Join the game engine as a bot player."""
        self.game_engine = game_engine

    async def make_move(self) -> bool:
        """
        Make a move based on the current game state and difficulty level.
        Returns True if a move was made, False if no move was possible.
        """
        if not self.game_engine or not self.game_engine.state:
            return False

        current_time = time.time()

        # # Check cooldown
        # if current_time - self.last_action_time < self.action_cooldown:
        #     return False

        # If we haven't picked a starting node yet, do that first
        if not self.game_engine.state.players_who_picked.get(self.player_id, False):
            success = self._pick_starting_node()
            if success:
                self.last_action_time = current_time
            return success


        success = await self._make_move()
        if success:
            self.last_action_time = current_time
        return success

    def _pick_starting_node(self) -> bool:
        """
        Find and pick the optimal starting node.
        Optimal = can expand to the most nodes without ever having to flip any edges.
        """
        return False

    def _make_move(self) -> bool:
        """
        Make a move based on the current game state and difficulty level.
        Returns True if a move was made, False if no move was possible.
        """
        return False

    def _count_expandable_nodes(self, start_node_id: int) -> int:
        """
        Count how many unowned nodes can be reached from the starting node
        without ever having to flip any edges.
        """
        if not self.game_engine or not self.game_engine.state:
            return 0

        visited = set()
        queue = [start_node_id]
        expandable_count = 0

        while queue:
            current_node_id = queue.pop(0)
            if current_node_id in visited:
                continue

            visited.add(current_node_id)
            current_node = self.game_engine.state.nodes.get(current_node_id)
            if not current_node:
                continue

            # Check all edges from this node
            for edge_id in current_node.attached_edge_ids:
                edge = self.game_engine.state.edges.get(edge_id)
                if not edge:
                    continue

                # Check if this edge goes FROM the current node (no flipping needed)
                if edge.source_node_id == current_node_id:
                    target_node_id = edge.target_node_id
                    target_node = self.game_engine.state.nodes.get(target_node_id)

                    # If target is unowned, we can expand to it
                    if target_node and target_node.owner is None:
                        if target_node_id not in visited:
                            queue.append(target_node_id)
                            expandable_count += 1

        return expandable_count

    def _calculate_bridge_cost(self, from_node, to_node) -> int:
        """Delegate bridge cost calculation to the game engine for consistency."""
        if not self.game_engine:
            return 0
        return self.game_engine.calculate_bridge_cost(from_node, to_node)


class Bot1(BotTemplate):

    def _pick_starting_node(self) -> bool:
        """
        Find and pick the optimal starting node.
        Optimal = can expand to the most nodes without ever having to flip any edges.
        """
        if not self.game_engine or not self.game_engine.state:
            return False

        best_node_id = self._find_optimal_starting_node()
        if best_node_id is not None:
            # Enable auto-expand for the bot
            self.game_engine.state.player_auto_expand[self.player_id] = True

            # Pick the starting node
            success = self.game_engine.handle_node_click(self.bot_token, best_node_id)
            return success

        return False

    def _find_optimal_starting_node(self) -> Optional[int]:
        """
        Find the node that can expand to the most unowned nodes without flipping edges.
        Returns the node ID of the best starting position.
        """
        if not self.game_engine or not self.game_engine.state:
            return None

        best_node_id = None
        best_expansion_count = -1

        # Check each unowned node
        for node_id, node in self.game_engine.state.nodes.items():
            if node.owner is not None:
                continue  # Skip owned nodes

            # Count how many unowned nodes can be reached without flipping edges
            expansion_count = self._count_expandable_nodes(node_id)

            if expansion_count > best_expansion_count:
                best_expansion_count = expansion_count
                best_node_id = node_id

        return best_node_id

    async def _make_move(self) -> bool:
        """
        Strategy focused on:
        - Directing flow to one strong target at a time
        - Avoiding wasteful reversals into dead-end side branches
        - Building/bridging only when cost-effective and high-reach
        Returns True if a move was made.
        """
        # 1) Prioritize retargeting flow smartly
        if self._try_target_best_node():
            return True

        # 2) Opportunistic but filtered reversal for expansion
        if await self._try_edge_reversal_for_expansion():
            return True

        # 3) Build cheap, high-reach neutral bridges
        if await self._try_bridge_building():
            return True

        # 4) Bridge into weak, high-reach enemy weakpoints
        if await self._try_offensive_bridge_building():
            return True

        return False

    async def _try_edge_reversal_for_expansion(self) -> bool:
        """Try to reverse edges to expand to more unowned nodes, avoiding dead-end side branches."""
        if not self.game_engine or not self.game_engine.state:
            return False

        # Gather reversal candidates and score them by downstream expansion potential per cost
        candidates: List[Tuple[float, int]] = []  # (negative score for sorting, edge_id)
        state = self.game_engine.state
        for edge in state.edges.values():
            source_node = state.nodes.get(edge.source_node_id)
            target_node = state.nodes.get(edge.target_node_id)
            if not source_node or not target_node:
                continue

            # Reverse only if: target owned by us, source is neutral (unowned), so reversal would let us capture source
            if (target_node.owner == self.player_id and 
                source_node.owner is None):
                # Estimate expansion if we owned 'source_node' after reversal/capture
                expansion = self._count_expandable_nodes(source_node.id)
                if expansion < 2:
                    # Skip dead-end or tiny side branches
                    continue

                cost = self._calculate_bridge_cost(source_node, target_node)
                player_gold = state.player_gold.get(self.player_id, 0)
                if player_gold < cost:
                    continue

                # Higher expansion, lower cost is better
                score = expansion / max(1.0, float(cost))
                candidates.append((-score, edge.id))

        if not candidates:
            return False

        candidates.sort()

        # Try best candidate
        _, best_edge_id = candidates[0]
        edge = state.edges.get(best_edge_id)
        if not edge:
            return False
        from_node = state.nodes.get(edge.source_node_id)
        to_node = state.nodes.get(edge.target_node_id)
        if not from_node or not to_node:
            return False
        cost = self._calculate_bridge_cost(from_node, to_node)
        if state.player_gold.get(self.player_id, 0) < cost:
            return False

        success = self.game_engine.handle_reverse_edge(self.bot_token, edge.id, cost)
        if success:
            return True

        return False

    def _try_attack_opponent(self) -> bool:
        """Try to attack opponent nodes by turning on edges."""
        if not self.game_engine or not self.game_engine.state:
            return False

        # Find edges from our nodes to opponent nodes
        for edge in self.game_engine.state.edges.values():
            source_node = self.game_engine.state.nodes.get(edge.source_node_id)
            target_node = self.game_engine.state.nodes.get(edge.target_node_id)

            if not source_node or not target_node:
                continue

            # Check if this is an attack opportunity
            if (source_node.owner == self.player_id and 
                target_node.owner is not None and 
                target_node.owner != self.player_id and
                not edge.on):  # Edge is not currently on

                # Turn on the edge to attack
                success = self.game_engine.handle_edge_click(self.bot_token, edge.id)
                if success:
                    return True

        return False

    def _count_downstream_reach(self, start_node_id: int, include_enemy: bool = True, max_depth: int = 16) -> int:
        """Count how many nodes are reachable following edge directions from a start node.
        Includes neutral and optionally enemy nodes. Caps traversal depth to avoid long scans.
        """
        if not self.game_engine or not self.game_engine.state:
            return 0
        state = self.game_engine.state
        visited: Set[int] = set([start_node_id])
        queue: deque[Tuple[int, int]] = deque([(start_node_id, 0)])
        count = 0
        while queue:
            node_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            node = state.nodes.get(node_id)
            if not node:
                continue
            for edge_id in node.attached_edge_ids:
                edge = state.edges.get(edge_id)
                if not edge or edge.source_node_id != node_id:
                    continue
                nxt_id = edge.target_node_id
                nxt = state.nodes.get(nxt_id)
                if not nxt:
                    continue
                # Filter by ownership if include_enemy is False
                if nxt.owner is not None and nxt.owner != self.player_id and not include_enemy:
                    continue
                if nxt_id not in visited:
                    visited.add(nxt_id)
                    count += 1
                    queue.append((nxt_id, depth + 1))
        return count

    def _has_direct_owned_incoming_edge(self, target_node_id: int) -> bool:
        if not self.game_engine or not self.game_engine.state:
            return False
        state = self.game_engine.state
        for edge in state.edges.values():
            if edge.target_node_id != target_node_id:
                continue
            src = state.nodes.get(edge.source_node_id)
            if src and src.owner == self.player_id:
                return True
        return False

    def _try_target_best_node(self) -> bool:
        """Choose a single high-value target and redirect energy to it using server-side optimizer.
        Prefers weak enemy nodes with high downstream reach; falls back to neutral gateways.
        """
        if not self.game_engine or not self.game_engine.state:
            return False

        now = time.time()
        if now - self.last_target_update_time < self.target_cooldown:
            return False

        state = self.game_engine.state

        best_target_id = None
        best_score = -1.0

        # Consider enemy targets that we can attack directly (we have a direct edge into them)
        for node in state.nodes.values():
            if node.owner is None or node.owner == self.player_id:
                continue
            if not self._has_direct_owned_incoming_edge(node.id):
                continue
            out_edges = 0
            for eid in node.attached_edge_ids:
                e = state.edges.get(eid)
                if e and e.source_node_id == node.id:
                    out_edges += 1
            if out_edges == 0:
                continue
            inflow = max(0.0, node.cur_intake)
            pressure = out_edges / (1.0 + inflow)
            reach = self._count_downstream_reach(node.id, include_enemy=True)
            # Composite value: prioritize reach, tempered by pressure (weakness)
            value = reach * 0.7 + pressure * 3.0
            if value > best_score:
                best_score = value
                best_target_id = node.id

        # If no enemy target available, choose a neutral gateway we can directly flow into
        if best_target_id is None:
            for node in state.nodes.values():
                if node.owner is not None:
                    continue
                if not self._has_direct_owned_incoming_edge(node.id):
                    continue
                expansion = self._count_expandable_nodes(node.id)
                if expansion <= 0:
                    continue
                # Slightly prefer nodes with more downstream reach too
                reach = self._count_downstream_reach(node.id, include_enemy=False)
                value = expansion * 1.0 + reach * 0.2
                if value > best_score:
                    best_score = value
                    best_target_id = node.id

        if best_target_id is None:
            return False

        # Use redirectEnergy which optimizes the entire owned subgraph towards the target
        success = self.game_engine.handle_redirect_energy(self.bot_token, best_target_id)
        if success:
            self.last_target_update_time = now
            return True
        # If redirect fails (e.g., validation), try local targeting as a fallback
        success = self.game_engine.handle_local_targeting(self.bot_token, best_target_id)
        if success:
            self.last_target_update_time = now
            return True
        return False

    async def _try_bridge_building(self) -> bool:
        """Try to build bridges for expansion opportunities."""
        if not self.game_engine or not self.game_engine.state:
            return False

        current_time = time.time()
        if current_time - self.last_bridge_time < self.bridge_cooldown:
            return False

        available_gold = self.game_engine.state.player_gold.get(self.player_id, 0)
        if available_gold <= self.bridge_gold_reserve:
            return False

        reachable_without_bridges = self._compute_reachable_nodes(include_enemy=False)
        reasonable_cost = self._estimate_reasonable_bridge_cost()
        candidates: List[Tuple[float, int, float, int, int]] = []

        # Find good bridge building opportunities
        for owned_node_id, owned_node in self.game_engine.state.nodes.items():
            if owned_node.owner != self.player_id:
                continue

            if not self._source_has_flow_capacity(owned_node):
                continue

            # Look for nearby unowned nodes that would be good expansion targets
            for target_node_id, target_node in self.game_engine.state.nodes.items():
                if (target_node.owner is None and 
                    target_node_id != owned_node_id and
                    not self._edge_exists_between_nodes(owned_node_id, target_node_id)):

                    if target_node_id in reachable_without_bridges:
                        # We can already get here via natural expansion; skip
                        continue

                    # Check if this would be a good expansion opportunity
                    if self._is_good_bridge_target(target_node_id):
                        # Calculate actual bridge cost based on distance
                        owned_node = self.game_engine.state.nodes.get(owned_node_id)
                        target_node = self.game_engine.state.nodes.get(target_node_id)
                        if owned_node and target_node:
                            cost = self._calculate_bridge_cost(owned_node, target_node)

                            if cost > reasonable_cost:
                                continue

                            current_gold = self.game_engine.state.player_gold.get(self.player_id, 0)
                            if current_gold < cost:
                                continue

                            if current_gold - cost < self.bridge_gold_reserve:
                                continue

                            expansion_score = self._count_expandable_nodes(target_node_id)
                            if expansion_score <= 0:
                                continue

                            dx = target_node.x - owned_node.x
                            dy = target_node.y - owned_node.y
                            distance = math.hypot(dx, dy)

                            # Lower expansion_score (negative) so larger counts are considered earlier when sorting
                            candidates.append((cost, -expansion_score, distance, owned_node_id, target_node_id))

        if not candidates:
            return False

        candidates.sort()

        for cost, _, _, owned_node_id, target_node_id in candidates:
            current_gold = self.game_engine.state.player_gold.get(self.player_id, 0)
            if current_gold < cost or current_gold - cost < self.bridge_gold_reserve:
                continue

            success, new_edge, actual_cost, error_msg = self.game_engine.handle_build_bridge(
                self.bot_token, owned_node_id, target_node_id, cost
            )
            if success and new_edge:
                # Ensure the new bridge is turned on to immediately send flow
                if not new_edge.on:
                    self.game_engine.handle_edge_click(self.bot_token, new_edge.id)
                self.last_bridge_time = current_time
                return True

        return False

    async def _try_offensive_bridge_building(self) -> bool:
        """Bridge to high-value opponent nodes that are weak and unlock downstream reach."""
        if not self.game_engine or not self.game_engine.state:
            return False

        current_time = time.time()
        if current_time - self.last_bridge_time < self.bridge_cooldown:
            return False

        available_gold = self.game_engine.state.player_gold.get(self.player_id, 0)
        if available_gold <= self.bridge_gold_reserve:
            return False

        state = self.game_engine.state
        reachable_with_attack = self._compute_reachable_nodes(include_enemy=True)
        reasonable_cost = self._estimate_reasonable_bridge_cost()
        enemy_candidates = []  # list of (composite_score, pressure_score, reach, target_node_id)
        for target_node_id, target_node in state.nodes.items():
            if target_node.owner is None or target_node.owner == self.player_id:
                continue

            if target_node_id in reachable_with_attack:
                # Already on a path we can reach without new bridge
                continue

            outflow_edges = 0
            for edge_id in target_node.attached_edge_ids:
                edge = state.edges.get(edge_id)
                if not edge:
                    continue
                if edge.source_node_id == target_node_id:
                    outflow_edges += 1

            if outflow_edges == 0:
                continue

            inflow_intake = max(0.0, target_node.cur_intake)
            pressure_score = outflow_edges / (1.0 + inflow_intake)
            if pressure_score < 0.4:
                continue

            reach = self._count_downstream_reach(target_node_id, include_enemy=True)
            composite = reach * 0.6 + pressure_score * 3.0
            enemy_candidates.append((composite, pressure_score, reach, target_node_id))

        if not enemy_candidates:
            return False

        enemy_candidates.sort(key=lambda item: item[0], reverse=True)

        for _, _, _, target_node_id in enemy_candidates:
            target_node = state.nodes.get(target_node_id)
            if not target_node:
                continue

            candidate_sources = []
            for owned_node_id, owned_node in state.nodes.items():
                if owned_node.owner != self.player_id or owned_node_id == target_node_id:
                    continue
                if self._edge_exists_between_nodes(owned_node_id, target_node_id):
                    continue

                if not self._source_has_flow_capacity(owned_node):
                    continue

                dx = target_node.x - owned_node.x
                dy = target_node.y - owned_node.y
                distance = math.hypot(dx, dy)
                cost = self._calculate_bridge_cost(owned_node, target_node)

                if cost > reasonable_cost:
                    continue
                candidate_sources.append((cost, distance, owned_node_id))

            candidate_sources.sort(key=lambda item: (item[0], item[1]))

            for cost, distance, owned_node_id in candidate_sources:
                current_gold = state.player_gold.get(self.player_id, 0)
                if current_gold < cost or current_gold - cost < self.bridge_gold_reserve:
                    continue

                success, new_edge, actual_cost, error_msg = self.game_engine.handle_build_bridge(
                    self.bot_token, owned_node_id, target_node_id, cost
                )
                if success and new_edge:
                    self.last_bridge_time = current_time
                    return True

        return False

    def _is_good_bridge_target(self, target_node_id: int) -> bool:
        """Check if a node would be a good target for bridge building."""
        if not self.game_engine or not self.game_engine.state:
            return False

        # Check how many unowned nodes this target could reach
        expansion_count = self._count_expandable_nodes(target_node_id)

        # Only build bridges to nodes that can expand to at least 2 other nodes
        return expansion_count >= 2

    def _compute_reachable_nodes(self, include_enemy: bool) -> Set[int]:
        if not self.game_engine or not self.game_engine.state:
            return set()

        state = self.game_engine.state
        start_nodes = [n.id for n in state.nodes.values() if n.owner == self.player_id]
        if not start_nodes:
            return set()

        visited: Set[int] = set(start_nodes)
        queue: deque[int] = deque(start_nodes)

        while queue:
            current_id = queue.popleft()
            current_node = state.nodes.get(current_id)
            if not current_node:
                continue

            for edge_id in current_node.attached_edge_ids:
                edge = state.edges.get(edge_id)
                if not edge or edge.source_node_id != current_id:
                    continue

                target_id = edge.target_node_id
                target_node = state.nodes.get(target_id)
                if not target_node:
                    continue

                target_owner = target_node.owner
                if target_owner == self.player_id:
                    pass
                elif target_owner is None:
                    pass
                elif not include_enemy:
                    continue

                if target_id not in visited:
                    visited.add(target_id)
                    queue.append(target_id)

        return visited

    def _estimate_reasonable_bridge_cost(self) -> float:
        if not self.game_engine or not self.game_engine.state:
            return 60.0

        state = self.game_engine.state
        sample_costs: List[float] = []
        for edge in state.edges.values():
            from_node = state.nodes.get(edge.source_node_id)
            to_node = state.nodes.get(edge.target_node_id)
            if not from_node or not to_node:
                continue
            sample_costs.append(self._calculate_bridge_cost(from_node, to_node))
            if len(sample_costs) >= 80:
                break

        if sample_costs:
            sample_costs.sort()
            median_cost = sample_costs[len(sample_costs) // 2]
            return max(40.0, median_cost * 1.3)

        # Fallback: base on map scale (100 normalized units)
        return max(40.0, BRIDGE_COST_PER_UNIT_DISTANCE * 45.0)

    def _source_has_flow_capacity(self, node) -> bool:
        if node is None:
            return False

        # Prefer nodes that have either substantial juice reserves or intake
        juice_ok = node.juice >= 12.0
        intake_ok = node.cur_intake >= 2.5

        outgoing_edges = 0
        if self.game_engine and self.game_engine.state:
            state = self.game_engine.state
            for edge_id in node.attached_edge_ids:
                edge = state.edges.get(edge_id)
                if edge and edge.source_node_id == node.id:
                    outgoing_edges += 1

        capacity_ok = outgoing_edges < 4  # avoid overloading nodes already feeding many paths

        return (juice_ok or intake_ok) and capacity_ok

    def _edge_exists_between_nodes(self, node_id1: int, node_id2: int) -> bool:
        """Check if an edge already exists between two nodes."""
        if not self.game_engine or not self.game_engine.state:
            return False

        for edge in self.game_engine.state.edges.values():
            if ((edge.source_node_id == node_id1 and edge.target_node_id == node_id2) or
                (edge.source_node_id == node_id2 and edge.target_node_id == node_id1)):
                return True
        return False




class Bot2(Bot1):

    def __init__(self, player_id: int = 2, color: str = "#66bb6a", difficulty: str = "hard"):
        super().__init__(player_id=player_id, color=color, difficulty=difficulty)
        # Slightly slower targeting to prioritize structural fixes first
        self.target_cooldown = 3.5
        # Stronger reserve to avoid overspending while reconnecting
        self.bridge_gold_reserve = 14.0

    # ---------- Improved Start Selection ----------
    def _find_optimal_starting_node(self) -> Optional[int]:
        """
        Choose start by combining natural expansion potential with distance from board center.
        We prefer nodes farther from center (safer roots near map edge) while keeping strong expansion.
        score = expansion_count * (1 + distance_norm * center_bias)
        """
        if not self.game_engine or not self.game_engine.state:
            return None

        center_x, center_y, max_dist = self._compute_board_center()
        if max_dist <= 0.0:
            max_dist = 1.0

        best_node_id: Optional[int] = None
        best_score: float = -1.0

        # Bias controls how much distance-from-center matters relative to expansion
        center_bias = 1.0

        for node_id, node in self.game_engine.state.nodes.items():
            if node.owner is not None:
                continue

            expansion = self._count_expandable_nodes(node_id)
            # Distance from center, normalized by half-diagonal
            dx = node.x - center_x
            dy = node.y - center_y
            dist = math.hypot(dx, dy)
            distance_norm = min(1.0, max(0.0, dist / max_dist))

            score = float(expansion) * (1.0 + distance_norm * center_bias)
            if score > best_score:
                best_score = score
                best_node_id = node_id

        return best_node_id

    # ---------- Move Strategy Overrides ----------
    async def _make_move(self) -> bool:
        """
        Bot2 priorities:
        1) Reconnect dead-end owned branches back into active rivers (within-island allowed)
        2) Prefer cheap, high-yield single-edge reversals over bridges
        3) Focus energy on a single high-value target (inherited targeting)
        4) Offensive bridges to weak, high-reach enemy targets
        5) Neutral bridges almost exclusively for new islands (not same-island expansion)
        """
        # 1) Reconnect branches first
        if await self._try_reconnect_branches():
            return True

        # 2) Prefer edge reversals early with broadened conditions
        if await self._try_edge_reversal_for_expansion_bot2():
            return True

        # 3) Targeting to concentrate pressure
        if self._try_target_best_node():
            return True

        # 4) Offensive bridge (to enemy)
        if await self._try_offensive_bridge_building():
            return True

        # 5) Neutral bridge for new islands only
        if await self._try_bridge_building_new_islands_only():
            return True

        return False


    # ---------- Reconnection Logic ----------
    async def _try_reconnect_branches(self) -> bool:
        """Find owned dead-end branches and connect them to active rivers or attack if not viable."""
        if not self.game_engine or not self.game_engine.state:
            return False

        current_time = time.time()
        if current_time - self.last_bridge_time < self.bridge_cooldown:
            return False

        state = self.game_engine.state
        available_gold = state.player_gold.get(self.player_id, 0)
        if available_gold <= self.bridge_gold_reserve:
            return False

        dead_end_nodes = self._find_dead_end_owned_nodes()
        if not dead_end_nodes:
            return False

        reasonable_cost = self._estimate_reasonable_bridge_cost()

        # Try to reconnect each dead-end to an active owned node first
        for leaf in dead_end_nodes:
            reconnect_target, reconnect_cost = self._find_best_reconnect_bridge(leaf, cost_ceiling=reasonable_cost)
            if reconnect_target is not None and reconnect_cost is not None:
                current_gold = state.player_gold.get(self.player_id, 0)
                if current_gold >= reconnect_cost and current_gold - reconnect_cost >= self.bridge_gold_reserve:
                    success, new_edge, actual_cost, error_msg = self.game_engine.handle_build_bridge(
                        self.bot_token, leaf.id, reconnect_target.id, reconnect_cost
                    )
                    if success and new_edge:
                        # Turn it on to immediately route flow
                        if not new_edge.on:
                            self.game_engine.handle_edge_click(self.bot_token, new_edge.id)
                        self.last_bridge_time = current_time
                        return True

        # If no cheap reconnection, use the dead-end as an attack spearhead
        for leaf in dead_end_nodes:
            enemy_target, attack_cost = self._find_best_enemy_attack_from_source(leaf, cost_ceiling=reasonable_cost)
            if enemy_target is not None and attack_cost is not None:
                current_gold = state.player_gold.get(self.player_id, 0)
                if current_gold >= attack_cost and current_gold - attack_cost >= self.bridge_gold_reserve:
                    success, new_edge, actual_cost, error_msg = self.game_engine.handle_build_bridge(
                        self.bot_token, leaf.id, enemy_target.id, attack_cost
                    )
                    if success and new_edge:
                        self.last_bridge_time = current_time
                        return True

        return False

    def _find_dead_end_owned_nodes(self) -> List[object]:
        """Identify owned nodes that currently pool with no downstream to neutral/enemy.
        Prefer nodes with some capacity (juice/intake) and zero outward edges.
        """
        if not self.game_engine or not self.game_engine.state:
            return []
        state = self.game_engine.state
        dead_ends: List[object] = []
        for node in state.nodes.values():
            if node.owner != self.player_id:
                continue
            # No directed outgoing edges
            out_edges = 0
            for eid in node.attached_edge_ids:
                e = state.edges.get(eid)
                if e and e.source_node_id == node.id:
                    out_edges += 1

            # Lacks any path to a non-owned node
            if out_edges == 0 and not self._has_path_to_non_owned(node.id, max_depth=20):
                # Ensure capacity so reconnecting is useful
                if self._source_has_flow_capacity(node):
                    dead_ends.append(node)
        return dead_ends

    def _has_path_to_non_owned(self, start_node_id: int, max_depth: int = 20) -> bool:
        if not self.game_engine or not self.game_engine.state:
            return False
        state = self.game_engine.state
        visited: Set[int] = set([start_node_id])
        queue: deque[Tuple[int, int]] = deque([(start_node_id, 0)])
        while queue:
            node_id, depth = queue.popleft()
            if depth >= max_depth:
                continue
            node = state.nodes.get(node_id)
            if not node:
                continue
            for eid in node.attached_edge_ids:
                e = state.edges.get(eid)
                if not e or e.source_node_id != node_id:
                    continue
                nxt_id = e.target_node_id
                if nxt_id in visited:
                    continue
                nxt = state.nodes.get(nxt_id)
                if not nxt:
                    continue
                if nxt.owner is None or nxt.owner != self.player_id:
                    return True
                visited.add(nxt_id)
                queue.append((nxt_id, depth + 1))
        return False

    def _find_best_reconnect_bridge(self, leaf_node, cost_ceiling: float) -> Tuple[Optional[object], Optional[float]]:
        """Pick cheapest owned target that has a path to non-owned, avoiding self.
        Returns (target_node, cost).
        """
        if not self.game_engine or not self.game_engine.state:
            return (None, None)
        state = self.game_engine.state
        best: Tuple[Optional[object], Optional[float]] = (None, None)
        for candidate in state.nodes.values():
            if candidate.id == leaf_node.id:
                continue
            if candidate.owner != self.player_id:
                continue
            # Prefer targets that actually lead somewhere
            if not self._has_path_to_non_owned(candidate.id, max_depth=20):
                continue
            # Skip if edge already exists
            if self._edge_exists_between_nodes(leaf_node.id, candidate.id):
                continue
            cost = float(self._calculate_bridge_cost(leaf_node, candidate))
            if cost > cost_ceiling:
                continue
            if best[1] is None or cost < best[1]:
                best = (candidate, cost)
        return best

    def _find_best_enemy_attack_from_source(self, source_node, cost_ceiling: float) -> Tuple[Optional[object], Optional[float]]:
        """From a given source, select an enemy node to attack using a composite value/cost heuristic."""
        if not self.game_engine or not self.game_engine.state:
            return (None, None)
        state = self.game_engine.state
        candidates: List[Tuple[float, object, float]] = []  # (-score, node, cost)
        for node in state.nodes.values():
            if node.owner is None or node.owner == self.player_id:
                continue
            # Value estimate like Bot1
            out_edges = 0
            for eid in node.attached_edge_ids:
                e = state.edges.get(eid)
                if e and e.source_node_id == node.id:
                    out_edges += 1
            if out_edges == 0:
                continue
            inflow = max(0.0, node.cur_intake)
            pressure = out_edges / (1.0 + inflow)
            if pressure < 0.4:
                continue
            reach = self._count_downstream_reach(node.id, include_enemy=True)
            value = reach * 0.6 + pressure * 3.0

            if self._edge_exists_between_nodes(source_node.id, node.id):
                continue
            cost = float(self._calculate_bridge_cost(source_node, node))
            if cost > cost_ceiling:
                continue
            candidates.append((-value / max(1.0, cost), node, cost))

        if not candidates:
            return (None, None)
        candidates.sort()
        _, best_node, best_cost = candidates[0]
        return (best_node, best_cost)

    # ---------- Edge Reversal Tweaks ----------
    async def _try_edge_reversal_for_expansion_bot2(self) -> bool:
        """Broadened reversal: allow single-step neutral captures even for small branches.
        Strongly prefer reversal when cost-effective compared to typical bridge costs.
        """
        if not self.game_engine or not self.game_engine.state:
            return False
        state = self.game_engine.state

        reasonable_cost = self._estimate_reasonable_bridge_cost()
        candidates: List[Tuple[float, int]] = []  # (negative score, edge_id)
        for edge in state.edges.values():
            src = state.nodes.get(edge.source_node_id)
            tgt = state.nodes.get(edge.target_node_id)
            if not src or not tgt:
                continue

            # If edge points from neutral into our owned node, reversing lets us flow outward
            if tgt.owner == self.player_id and src.owner is None:
                expansion = self._count_expandable_nodes(src.id)
                # Allow even small expansions; we'll filter by cost
                if expansion <= 0:
                    continue
                cost = float(self._calculate_bridge_cost(src, tgt))
                if state.player_gold.get(self.player_id, 0) < cost:
                    continue
                # Encourage reversal when clearly cheaper than typical bridge
                if cost > reasonable_cost * 0.9:
                    # Still allow but downweight
                    score = (expansion / max(1.0, cost)) * 0.6
                else:
                    score = expansion / max(1.0, cost)
                candidates.append((-score, edge.id))

        if not candidates:
            return False
        candidates.sort()
        _, best_edge_id = candidates[0]
        e = state.edges.get(best_edge_id)
        if not e:
            return False
        from_node = state.nodes.get(e.source_node_id)
        to_node = state.nodes.get(e.target_node_id)
        if not from_node or not to_node:
            return False
        cost = float(self._calculate_bridge_cost(from_node, to_node))
        if state.player_gold.get(self.player_id, 0) < cost:
            return False
        success = self.game_engine.handle_reverse_edge(self.bot_token, e.id, cost)
        return bool(success)

    # ---------- Neutral Bridge Policy: New Islands Only ----------
    async def _try_bridge_building_new_islands_only(self) -> bool:
        """Only build neutral bridges to nodes on different undirected islands; skip same-island expansion."""
        if not self.game_engine or not self.game_engine.state:
            return False

        current_time = time.time()
        if current_time - self.last_bridge_time < self.bridge_cooldown:
            return False

        state = self.game_engine.state
        available_gold = state.player_gold.get(self.player_id, 0)
        if available_gold <= self.bridge_gold_reserve:
            return False

        reachable_without_bridges = self._compute_reachable_nodes(include_enemy=False)
        reasonable_cost = self._estimate_reasonable_bridge_cost()

        candidates: List[Tuple[float, int, float, int, int]] = []
        for owned_node_id, owned_node in state.nodes.items():
            if owned_node.owner != self.player_id:
                continue
            if not self._source_has_flow_capacity(owned_node):
                continue
            for target_node_id, target_node in state.nodes.items():
                if target_node.owner is not None:
                    continue
                if target_node_id == owned_node_id:
                    continue
                if self._edge_exists_between_nodes(owned_node_id, target_node_id):
                    continue
                # Skip if already reachable by directed flow
                if target_node_id in reachable_without_bridges:
                    continue
                # Require different undirected island to prioritize true new-land grabs
                if self._is_same_island(owned_node_id, target_node_id):
                    continue

                cost = float(self._calculate_bridge_cost(owned_node, target_node))
                if cost > reasonable_cost:
                    continue
                current_gold = state.player_gold.get(self.player_id, 0)
                if current_gold < cost or current_gold - cost < self.bridge_gold_reserve:
                    continue
                expansion_score = self._count_expandable_nodes(target_node_id)
                if expansion_score <= 0:
                    continue

                dx = target_node.x - owned_node.x
                dy = target_node.y - owned_node.y
                distance = math.hypot(dx, dy)
                candidates.append((cost, -float(expansion_score), distance, owned_node_id, target_node_id))

        if not candidates:
            return False

        candidates.sort()
        for cost, _, _, owned_node_id, target_node_id in candidates:
            current_gold = state.player_gold.get(self.player_id, 0)
            if current_gold < cost or current_gold - cost < self.bridge_gold_reserve:
                continue
            success, new_edge, actual_cost, error_msg = self.game_engine.handle_build_bridge(
                self.bot_token, owned_node_id, target_node_id, cost
            )
            if success and new_edge:
                if not new_edge.on:
                    self.game_engine.handle_edge_click(self.bot_token, new_edge.id)
                self.last_bridge_time = current_time
                return True

        return False

    # ---------- Graph Helpers ----------
    def _compute_board_center(self) -> Tuple[float, float, float]:
        """Return (center_x, center_y, max_radius) where max_radius is half of bbox diagonal."""
        if not self.game_engine or not self.game_engine.state or not self.game_engine.state.nodes:
            return (0.0, 0.0, 1.0)
        xs: List[float] = []
        ys: List[float] = []
        for n in self.game_engine.state.nodes.values():
            xs.append(float(n.x))
            ys.append(float(n.y))
        min_x, max_x = min(xs), max(xs)
        min_y, max_y = min(ys), max(ys)
        center_x = (min_x + max_x) / 2.0
        center_y = (min_y + max_y) / 2.0
        half_w = (max_x - min_x) / 2.0
        half_h = (max_y - min_y) / 2.0
        max_radius = math.hypot(half_w, half_h)
        return (center_x, center_y, max_radius if max_radius > 0.0 else 1.0)

    def _is_same_island(self, node_id_a: int, node_id_b: int) -> bool:
        """Undirected connectivity via existing edges indicates same island."""
        if not self.game_engine or not self.game_engine.state:
            return True
        state = self.game_engine.state
        visited: Set[int] = set([node_id_a])
        queue: deque[int] = deque([node_id_a])
        while queue:
            nid = queue.popleft()
            if nid == node_id_b:
                return True
            node = state.nodes.get(nid)
            if not node:
                continue
            for eid in node.attached_edge_ids:
                e = state.edges.get(eid)
                if not e:
                    continue
                # Traverse undirected: both directions
                for nxt_id in (e.source_node_id, e.target_node_id):
                    if nxt_id not in visited:
                        visited.add(nxt_id)
                        queue.append(nxt_id)
        return False


class BotPop1(Bot1):

    POP_READY_MARGIN = 0.4
    SHORT_ATTACK_COST_RATIO = 0.55
    SHORT_ATTACK_MIN_COST = 28.0
    DEAD_PATH_SCAN_DEPTH = 10

    async def _make_move(self) -> bool:
        """Pop dead-end reservoirs before falling back to standard Bot1 play."""
        if self._try_pop_dead_end_node():
            return True

        return await super()._make_move()

    def _try_pop_dead_end_node(self) -> bool:
        if not self.game_engine or not self.game_engine.state:
            return False

        state = self.game_engine.state
        if getattr(state, "mode", "passive") != "pop":
            return False

        candidates = self._find_pop_candidates()
        if not candidates:
            return False

        for _, node in candidates:
            success, _, _ = self.game_engine.handle_pop_node(self.bot_token, node.id)
            if success:
                return True
        return False

    def _find_pop_candidates(self) -> List[Tuple[float, object]]:
        state = self.game_engine.state
        threshold = NODE_MAX_JUICE - self.POP_READY_MARGIN
        ready: List[Tuple[float, object]] = []

        for node in state.nodes.values():
            if node.owner != self.player_id:
                continue
            if node.juice < threshold:
                continue
            if not self._is_dead_end_node(node):
                continue

            support_score = node.cur_intake + self._count_owned_incoming_edges(node.id) * 0.6
            ready.append((support_score, node))

        ready.sort(key=lambda item: item[0])
        return ready

    def _count_owned_incoming_edges(self, node_id: int) -> int:
        state = self.game_engine.state
        count = 0
        for edge in state.edges.values():
            if edge.target_node_id != node_id:
                continue
            src = state.nodes.get(edge.source_node_id)
            if src and src.owner == self.player_id:
                count += 1
        return count

    def _is_dead_end_node(self, node) -> bool:
        state = self.game_engine.state

        has_outgoing = False
        for edge_id in node.attached_edge_ids:
            edge = state.edges.get(edge_id)
            if not edge or edge.source_node_id != node.id:
                continue
            has_outgoing = True
            target = state.nodes.get(edge.target_node_id)
            if target and (target.owner is None or target.owner != self.player_id):
                return False

        if has_outgoing and self._has_path_to_non_owned(node.id):
            return False

        if self._has_short_enemy_bridge_option(node):
            return False

        return True

    def _has_path_to_non_owned(self, start_node_id: int) -> bool:
        state = self.game_engine.state
        visited: Set[int] = set([start_node_id])
        queue: deque[Tuple[int, int]] = deque([(start_node_id, 0)])

        while queue:
            node_id, depth = queue.popleft()
            if depth >= self.DEAD_PATH_SCAN_DEPTH:
                continue
            current = state.nodes.get(node_id)
            if not current:
                continue

            for edge_id in current.attached_edge_ids:
                edge = state.edges.get(edge_id)
                if not edge or edge.source_node_id != node_id:
                    continue
                nxt_id = edge.target_node_id
                if nxt_id in visited:
                    continue
                nxt = state.nodes.get(nxt_id)
                if not nxt:
                    continue
                if nxt.owner is None or nxt.owner != self.player_id:
                    return True
                visited.add(nxt_id)
                queue.append((nxt_id, depth + 1))

        return False

    def _has_short_enemy_bridge_option(self, source_node) -> bool:
        state = self.game_engine.state
        reasonable_cost = self._estimate_reasonable_bridge_cost()
        cost_limit = max(self.SHORT_ATTACK_MIN_COST, reasonable_cost * self.SHORT_ATTACK_COST_RATIO)

        for node in state.nodes.values():
            if node.owner is None or node.owner == self.player_id:
                continue
            if self._edge_exists_between_nodes(source_node.id, node.id):
                continue

            cost = float(self._calculate_bridge_cost(source_node, node))
            if cost <= cost_limit:
                return True

        return False
