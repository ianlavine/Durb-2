"""
Bot implementations: contains `BotPlayer` which encapsulates AI logic/strategies.
"""

import math
import time
from collections import deque
from typing import Dict, List, Optional, Set, Tuple

from .game_engine import GameEngine
from .generate_graph import NODE_COUNT

class Agent:

    def __init__(self, player_id: int = 1, color: str = "#ff4133"):
        self.player_id = player_id
        self.color = color
        self.game_engine: Optional[GameEngine] = None
        self.bot_token = "bot_token_101"
        self.last_action_time = 0.0
        self.action_cooldown = 0.5  # Minimum seconds between actions
        self.last_bridge_time = 0.0
        self.bridge_cooldown = 1.0  # Extra delay between bridge builds
        self.bridge_gold_reserve = 5.0  # Keep a proportional gold buffer when bridging
        # Targeting cadence so we don't thrash edges every tick
        self.last_target_update_time = 0.0
        self.target_cooldown = 3.0
        self.max_num_nodes = NODE_COUNT
        self._calc_actions_space()

    def join_game(self, game_engine: GameEngine) -> None:
        """Join the game engine as a bot player."""
        self.game_engine = game_engine

    async def step(self, action) -> bool:
        """
        Make a move based on the current game state and difficulty level.
        Returns True if a move was made, False if no move was possible.
        """
        if not self.game_engine or not self.game_engine.state:
            return False

        current_time = time.time()


        success = self._make_move(action)
        if success:
            self.last_action_time = current_time
        return success
    
    def _calc_actions_space(self):
        self.actions_dict = {}
        # Initial Node selection
        ind  = 0
        for i in range(0, self.max_num_nodes):
            self.actions_dict[i] = ("node_selection", i)
        ind += self.max_num_nodes

        # toggle_edge_flow
        for i in range(0, self.max_num_nodes-1):
            for j in range(i+1, self.max_num_nodes):
                if i!=j:
                    self.actions_dict[ind] = ("toggle_edge_flow", (i, j))
                    ind += 1

        # reverse edge
        for i in range(0, self.max_num_nodes-1):
            for j in range(i+1, self.max_num_nodes):
                if i!=j:
                    self.actions_dict[ind] = ("reverse_edge", (i, j))
                    ind += 1

        # build_bridge
        for i in range(0, self.max_num_nodes):
            for j in range(0, self.max_num_nodes):
                if i!=j:
                    self.actions_dict[ind] = ("build_bridge", (i, j))
                    ind += 1

        self.actions_dict[ind] = ("pass", ())
        
        self.ind_2_act= {v:k for k, v in self.actions_dict.items()}
        return self.actions_dict

        

    def _make_move(self, action:Dict) -> bool:
        """
        Make a move based on the current AI selected action.
        Returns True if a move was made, False if no move was possible.
        """
        
        if action[0] == "node_selection":
            return self._execute_node_selection_act(action[1])
        
        elif action[0] == "toggle_edge_flow":
            return self._execute_toggle_flow(*action[1])

        elif action[0] == "reverse_edge":
            return self._execute_reverse_edge(*action[1])

        elif action[0] == "build_bridge":
            return self._execute_build_bridge(*action[1])
        
        elif action[0] == "pass":
            return True

        return False
    
    def _get_edge_from_nodes(self, node_a_id, node_b_id):
        """
        Get edge_id of edge between node A and node B
        """
        nodes = self.game_engine.state.nodes
        node_a_edges = nodes[node_a_id].attached_edge_ids
        node_b_edges = nodes[node_b_id].attached_edge_ids

        edge_id = list(set(node_a_edges) & set(node_b_edges))[0]
        return edge_id
    
    def _execute_node_selection_act(self, node_id) -> bool:
        """
        Execute initial node selection action of AI Agent.
        """
        success = self.game_engine.handle_node_click(self.bot_token, node_id)
        return success
    
    def _execute_toggle_flow(self, src_node, tgt_node) -> bool:
        """
        Execute edge flow toggle action of AI Agent.
        """
        edge_id = self._get_edge_from_nodes(src_node, tgt_node)

        success = self.game_engine.handle_edge_click(self.bot_token, edge_id)
        return success
    
    def _execute_reverse_edge(self, src_node, tgt_node) -> bool:
        """
        Execute edge flow toggle action of AI Agent.
        """
        edge_id = self._get_edge_from_nodes(src_node, tgt_node)

        success = self.game_engine.handle_reverse_edge(self.bot_token, edge_id)
        return success
    
    def _execute_build_bridge(self, src_node, tgt_node) -> bool:
        """
        Execute build bridge action of AI Agent.
        """
        nodes = self.game_engine.state.nodes
        cost = self._calculate_bridge_cost(nodes[src_node], nodes[tgt_node])
        success = self.game_engine.handle_build_bridge(self.bot_token, src_node, tgt_node, cost)
        return success
    
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
    
    def _get_common_edge(self, src_node, tgt_node):
        return list(set(src_node.attached_edge_ids)&set(tgt_node.attached_edge_ids))
    
    def get_valid_actions(self):
        all_valid_actions = []
        if not self.game_engine.state.players_who_picked.get(self.player_id, False):
            all_valid_actions += self._get_valid_node_selection_actions()

        else:
            all_valid_actions += self._get_valid_toggle_edge_actions()
            all_valid_actions += self._get_valid_reverse_edge_actions()
            all_valid_actions += self._get_valid_build_bridge_actions()
            all_valid_actions.append(("pass", ()))
        
        return all_valid_actions

    def _get_valid_node_selection_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for node_id, node in state.nodes.items():
            if node.owner:
                continue
            valid_acts.append(("node_selection", node_id))
        return valid_acts
    
    def _get_valid_toggle_edge_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for edge in state.edges.values():
            source_node = state.nodes.get(edge.source_node_id)
            target_node = state.nodes.get(edge.target_node_id)
            if not source_node or not target_node:
                continue

            # Toggle only if: source is owned by us
            if (source_node.owner == self.player_id):
                if edge.source_node_id < edge.target_node_id:
                    valid_acts.append(("toggle_edge_flow", (edge.source_node_id, edge.target_node_id)))
                else:
                    valid_acts.append(("toggle_edge_flow", (edge.target_node_id, edge.source_node_id)))
        return valid_acts
    
    def _get_valid_reverse_edge_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for edge in state.edges.values():
            source_node = state.nodes.get(edge.source_node_id)
            target_node = state.nodes.get(edge.target_node_id)
            if not source_node or not target_node:
                continue

            # Toggle only if: source is owned by us and cost is less than available gold
            cost = self._calculate_bridge_cost(source_node, target_node)
            player_gold = state.player_gold.get(self.player_id, 0)
            if (target_node.owner == self.player_id and source_node.owner is None) and cost<player_gold:
                if edge.source_node_id < edge.target_node_id:
                    valid_acts.append(("reverse_edge", (edge.source_node_id, edge.target_node_id)))
                else:
                    valid_acts.append(("reverse_edge", (edge.target_node_id, edge.source_node_id)))
        return valid_acts
    
    def _get_valid_build_bridge_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for node_id, node in state.nodes.items():
            if node.owner == self.player_id:
                for tgt_node_id, tgt_node in state.nodes.items():
                    if node_id==tgt_node_id and self._get_common_edge(node, tgt_node):
                        continue

                    cost = self._calculate_bridge_cost(node, tgt_node)
                    player_gold = state.player_gold.get(self.player_id, 0)

                    if cost < player_gold:
                        valid_acts.append(("build_bridge", (node_id, tgt_node_id)))
        return valid_acts
    

class AgentLite:

    def __init__(self, player_id: int = 1, color: str = "#ff4133"):
        self.player_id = player_id
        self.color = color
        self.game_engine: Optional[GameEngine] = None
        self.bot_token = "bot_token_101"
        self.last_action_time = 0.0
        self.action_cooldown = 0.5  # Minimum seconds between actions
        self.last_bridge_time = 0.0
        self.bridge_cooldown = 1.0  # Extra delay between bridge builds
        self.bridge_gold_reserve = 5.0  # Keep a proportional gold buffer when bridging
        # Targeting cadence so we don't thrash edges every tick
        self.last_target_update_time = 0.0
        self.target_cooldown = 3.0
        self.max_num_nodes = NODE_COUNT
        self._calc_actions_space()

    def join_game(self, game_engine: GameEngine) -> None:
        """Join the game engine as a bot player."""
        self.game_engine = game_engine

    async def step(self, action) -> bool:
        """
        Make a move based on the current game state and difficulty level.
        Returns True if a move was made, False if no move was possible.
        """
        if not self.game_engine or not self.game_engine.state:
            return False

        current_time = time.time()


        success = self._make_move(action)
        if success:
            self.last_action_time = current_time
        return success
    
    def _calc_actions_space(self):
        self.actions_dict = {}
        # Initial Node selection
        ind  = 0
        for i in range(0, self.max_num_nodes):
            self.actions_dict[i] = ("node_selection", i)
        ind += self.max_num_nodes

        # toggle_edge_flow
        for i in range(0, self.max_num_nodes-1):
            for j in range(i+1, self.max_num_nodes):
                if i!=j:
                    self.actions_dict[ind] = ("toggle_edge_flow", (i, j))
                    ind += 1

        # reverse edge
        for i in range(0, self.max_num_nodes-1):
            for j in range(i+1, self.max_num_nodes):
                if i!=j:
                    self.actions_dict[ind] = ("reverse_edge", (i, j))
                    ind += 1

        self.actions_dict[ind] = ("pass", ())
        
        self.ind_2_act= {v:k for k, v in self.actions_dict.items()}
        return self.actions_dict

        

    def _make_move(self, action:Dict) -> bool:
        """
        Make a move based on the current AI selected action.
        Returns True if a move was made, False if no move was possible.
        """
        
        if action[0] == "node_selection":
            return self._execute_node_selection_act(action[1])
        
        elif action[0] == "toggle_edge_flow":
            return self._execute_toggle_flow(*action[1])

        elif action[0] == "reverse_edge":
            return self._execute_reverse_edge(*action[1])

        elif action[0] == "build_bridge":
            return self._execute_build_bridge(*action[1])
        
        elif action[0] == "pass":
            return True

        return False
    
    def _get_edge_from_nodes(self, node_a_id, node_b_id):
        """
        Get edge_id of edge between node A and node B
        """
        nodes = self.game_engine.state.nodes
        node_a_edges = nodes[node_a_id].attached_edge_ids
        node_b_edges = nodes[node_b_id].attached_edge_ids

        edge_id = list(set(node_a_edges) & set(node_b_edges))[0]
        return edge_id
    
    def _execute_node_selection_act(self, node_id) -> bool:
        """
        Execute initial node selection action of AI Agent.
        """
        success = self.game_engine.handle_node_click(self.bot_token, node_id)
        return success
    
    def _execute_toggle_flow(self, src_node, tgt_node) -> bool:
        """
        Execute edge flow toggle action of AI Agent.
        """
        edge_id = self._get_edge_from_nodes(src_node, tgt_node)

        success = self.game_engine.handle_edge_click(self.bot_token, edge_id)
        return success
    
    def _execute_reverse_edge(self, src_node, tgt_node) -> bool:
        """
        Execute edge flow toggle action of AI Agent.
        """
        edge_id = self._get_edge_from_nodes(src_node, tgt_node)

        success = self.game_engine.handle_reverse_edge(self.bot_token, edge_id)
        return success
    
    def _execute_build_bridge(self, src_node, tgt_node) -> bool:
        """
        Execute build bridge action of AI Agent.
        """
        nodes = self.game_engine.state.nodes
        cost = self._calculate_bridge_cost(nodes[src_node], nodes[tgt_node])
        success = self.game_engine.handle_build_bridge(self.bot_token, src_node, tgt_node, cost)
        return success
    
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
    
    def _get_common_edge(self, src_node, tgt_node):
        return list(set(src_node.attached_edge_ids)&set(tgt_node.attached_edge_ids))
    
    def get_valid_actions(self):
        all_valid_actions = []
        if not self.game_engine.state.players_who_picked.get(self.player_id, False):
            all_valid_actions += self._get_valid_node_selection_actions()

        else:
            all_valid_actions += self._get_valid_toggle_edge_actions()
            all_valid_actions += self._get_valid_reverse_edge_actions()
            #all_valid_actions += self._get_valid_build_bridge_actions()
            all_valid_actions.append(("pass", ()))
        
        return all_valid_actions

    def _get_valid_node_selection_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for node_id, node in state.nodes.items():
            if node.owner:
                continue
            valid_acts.append(("node_selection", node_id))
        return valid_acts
    
    def _get_valid_toggle_edge_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for edge in state.edges.values():
            source_node = state.nodes.get(edge.source_node_id)
            target_node = state.nodes.get(edge.target_node_id)
            if not source_node or not target_node:
                continue

            # Toggle only if: source is owned by us
            if (source_node.owner == self.player_id):
                if edge.source_node_id < edge.target_node_id:
                    valid_acts.append(("toggle_edge_flow", (edge.source_node_id, edge.target_node_id)))
                else:
                    valid_acts.append(("toggle_edge_flow", (edge.target_node_id, edge.source_node_id)))
        return valid_acts
    
    def _get_valid_reverse_edge_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for edge in state.edges.values():
            source_node = state.nodes.get(edge.source_node_id)
            target_node = state.nodes.get(edge.target_node_id)
            if not source_node or not target_node:
                continue

            # Toggle only if: source is owned by us and cost is less than available gold
            cost = self._calculate_bridge_cost(source_node, target_node)
            player_gold = state.player_gold.get(self.player_id, 0)
            if (target_node.owner == self.player_id and source_node.owner is None) and cost<player_gold:
                if edge.source_node_id < edge.target_node_id:
                    valid_acts.append(("reverse_edge", (edge.source_node_id, edge.target_node_id)))
                else:
                    valid_acts.append(("reverse_edge", (edge.target_node_id, edge.source_node_id)))
        return valid_acts
    
    def _get_valid_build_bridge_actions(self):
        """
        Get only those edges that are valid in current state.
        """
        valid_acts = []
        state = self.game_engine.state
        for node_id, node in state.nodes.items():
            if node.owner == self.player_id:
                for tgt_node_id, tgt_node in state.nodes.items():
                    if node_id==tgt_node_id and self._get_common_edge(node, tgt_node):
                        continue

                    cost = self._calculate_bridge_cost(node, tgt_node)
                    player_gold = state.player_gold.get(self.player_id, 0)

                    if cost < player_gold:
                        valid_acts.append(("build_bridge", (node_id, tgt_node_id)))
        return valid_acts
