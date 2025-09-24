"""
Bot Player - Simple AI that can play the game automatically.
The bot uses the existing game engine methods directly without going through web UI.
"""

import math
import time
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .models import Player
from .game_engine import GameEngine
from .state import GraphState
from .constants import PLAYER_COLOR_SCHEMES, BRIDGE_COST_PER_UNIT_DISTANCE


class BotPlayer:
    """AI bot that can play the game with different difficulty levels."""
    
    def __init__(self, player_id: int = 2, color: str = "#3388ff", difficulty: str = "easy"):
        self.player_id = player_id
        self.color = color
        self.difficulty = difficulty
        self.game_engine: Optional[GameEngine] = None
        self.bot_token = "bot_token_" + str(int(time.time()))
        self.has_acted = False
        self.last_action_time = 0.0
        self.action_cooldown = 2.0  # Minimum seconds between actions
        self.last_bridge_time = 0.0
        self.bridge_cooldown = 8.0  # Extra delay between bridge builds
        self.bridge_gold_reserve = 10.0  # Keep a proportional gold buffer when bridging
        self.websocket = None  # Will be set by the bot game manager
        # Targeting cadence so we don't thrash edges every tick
        self.last_target_update_time = 0.0
        self.target_cooldown = 3.0
    
    def join_game(self, game_engine: GameEngine) -> None:
        """Join the game engine as a bot player."""
        self.game_engine = game_engine
    
    def set_websocket(self, websocket) -> None:
        """Set the websocket for sending frontend responses."""
        self.websocket = websocket
    
    async def _send_frontend_response(self, message: dict) -> None:
        """Send a response message to the frontend."""
        if self.websocket:
            import json
            try:
                await self.websocket.send(json.dumps(message))
            except Exception as e:
                print(f"Failed to send frontend response: {e}")
    
    async def make_move(self) -> bool:
        """
        Make a move based on the current game state and difficulty level.
        Returns True if a move was made, False if no move was possible.
        """
        if not self.game_engine or not self.game_engine.state:
            return False
        
        current_time = time.time()
        
        # Check cooldown
        if current_time - self.last_action_time < self.action_cooldown:
            return False
        
        # If we haven't picked a starting node yet, do that first
        if not self.game_engine.state.players_who_picked.get(self.player_id, False):
            success = self._pick_optimal_starting_node()
            if success:
                self.last_action_time = current_time
            return success
        
        # Game is in playing phase - make moves based on difficulty
        if self.difficulty == "easy":
            success = await self._make_medium_move()
            if success:
                self.last_action_time = current_time
            return success
        elif self.difficulty == "medium":
            success = await self._make_medium_plus_bridge_move()
            if success:
                self.last_action_time = current_time
            return success
        elif self.difficulty == "hard":
            success = await self._make_hard_move()
            if success:
                self.last_action_time = current_time
            return success
        
        return False
    
    def _pick_optimal_starting_node(self) -> bool:
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
            if success:
                self.has_acted = True
                return True
        
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
    
    async def _make_medium_move(self) -> bool:
        """
        Medium bot strategy: edge reversal for expansion + attack opportunities.
        Returns True if a move was made.
        """
        # First, try to find edge reversal opportunities for expansion
        if await self._try_edge_reversal_for_expansion():
            return True
        
        # Then, try to attack opponent nodes
        if self._try_attack_opponent():
            return True
        
        return False
    
    async def _make_medium_plus_bridge_move(self) -> bool:
        """Medium strategy plus opportunistic bridge building."""
        if await self._make_medium_move():
            return True

        if await self._try_bridge_building():
            return True

        return False

    async def _make_hard_move(self) -> bool:
        """
        Hard bot strategy focused on:
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
            await self._send_frontend_response({
                "type": "edgeReversed",
                "edge": {
                    "id": edge.id,
                    "source": edge.source_node_id,
                    "target": edge.target_node_id,
                    "bidirectional": False,
                    "forward": True,
                    "on": edge.on,
                    "flowing": edge.flowing
                }
            })
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
    
    def _calculate_bridge_cost(self, from_node, to_node) -> int:
        """Delegate bridge cost calculation to the game engine for consistency."""
        if not self.game_engine:
            return 0
        return self.game_engine.calculate_bridge_cost(from_node, to_node)

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

                await self._send_frontend_response({
                    "type": "newEdge",
                    "edge": {
                        "id": new_edge.id,
                        "source": new_edge.source_node_id,
                        "target": new_edge.target_node_id,
                        "bidirectional": False,
                        "forward": True,
                        "on": new_edge.on,
                        "flowing": new_edge.flowing
                    }
                    # No cost included - human player shouldn't see bot's spending
                })
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
                    await self._send_frontend_response({
                        "type": "newEdge",
                        "edge": {
                            "id": new_edge.id,
                            "source": new_edge.source_node_id,
                            "target": new_edge.target_node_id,
                            "bidirectional": False,
                            "forward": True,
                            "on": new_edge.on,
                            "flowing": new_edge.flowing
                        }
                        # No cost included - human player shouldn't see bot's spending
                    })
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
    
    def get_game_state(self) -> Optional[Dict]:
        """Get the current game state for debugging/analysis."""
        if not self.game_engine or not self.game_engine.state:
            return None
        
        return {
            "phase": self.game_engine.state.phase,
            "player_id": self.player_id,
            "has_picked": self.game_engine.state.players_who_picked.get(self.player_id, False),
            "auto_expand": self.game_engine.state.player_auto_expand.get(self.player_id, False),
            "gold": self.game_engine.state.player_gold.get(self.player_id, 0.0),
            "nodes_owned": len([n for n in self.game_engine.state.nodes.values() if n.owner == self.player_id])
        }


class BotGameManager:
    """Manages bot vs human games."""
    
    def __init__(self):
        self.game_engine = GameEngine()
        self.bot_player: Optional[BotPlayer] = None
        self.human_token: Optional[str] = None
        self.game_active = False
    
    def start_bot_game(self, human_token: str, difficulty: str = "easy", auto_expand: bool = False, speed_level: int = 3) -> Tuple[bool, Optional[str]]:
        """
        Start a new bot vs human game with specified difficulty.
        Returns: (success, error_message)
        """
        try:
            speed_level = max(1, min(5, speed_level))

            # Create bot player with specified difficulty
            self.bot_player = BotPlayer(player_id=2, color="#3388ff", difficulty=difficulty)
            self.human_token = human_token

            from .message_handlers import PLAYER_COLOR_SCHEMES  # avoid circular import at top level

            player_slots = [
                {
                    "player_id": 1,
                    "token": human_token,
                    "color": PLAYER_COLOR_SCHEMES[0]["color"],
                    "secondary_colors": PLAYER_COLOR_SCHEMES[0]["secondary"],
                    "auto_expand": auto_expand,
                },
                {
                    "player_id": 2,
                    "token": self.bot_player.bot_token,
                    "color": PLAYER_COLOR_SCHEMES[1]["color"],
                    "secondary_colors": PLAYER_COLOR_SCHEMES[1]["secondary"],
                    "auto_expand": True,
                },
            ]

            self.game_engine.start_game(player_slots, speed_level)
            self.bot_player.join_game(self.game_engine)
            self.game_active = True

            return True, None

        except Exception as e:
            return False, str(e)
    
    async def make_bot_move(self) -> bool:
        """Make the bot's move if it's the bot's turn."""
        if not self.bot_player or not self.game_active:
            return False
        
        return await self.bot_player.make_move()
    
    def get_game_engine(self) -> GameEngine:
        """Get the game engine for the bot game."""
        return self.game_engine
    
    def end_game(self) -> None:
        """End the bot game."""
        self.game_active = False
        self.bot_player = None
        self.human_token = None
        self.game_engine._end_game()


# Global bot game manager instance
bot_game_manager = BotGameManager()
