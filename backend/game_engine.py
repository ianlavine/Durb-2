"""
Game Engine - Core game logic separated from server implementation.
Handles game state management, validation, and game rules.
"""
import uuid
import time
from typing import Dict, List, Optional, Tuple, Set
from .models import Player, Node, Edge
from .state import GraphState, build_state_from_dict
from .graph_generator import graph_generator


class GameValidationError(Exception):
    """Raised when a game action fails validation."""
    pass


class GameEngine:
    """Core game engine handling game logic, validation, and state management."""
    
    def __init__(self):
        self.state: Optional[GraphState] = None
        self.screen: Dict[str, int] = {"width": 100, "height": 100, "margin": 0}
        
        # Player management
        self.token_to_player_id: Dict[str, int] = {}
        self.token_to_color: Dict[str, str] = {}
        
        # Lobby state
        self.lobby_waiting: Optional[str] = None  # waiting player token
        self.game_active: bool = False
    
    def is_game_active(self) -> bool:
        """Check if a game is currently active."""
        return self.game_active and self.state is not None and len(self.token_to_player_id) >= 2
    
    def join_lobby(self, token: Optional[str] = None) -> Tuple[str, str]:
        """
        Handle a player joining the lobby.
        Returns: (player_token, status) where status is 'waiting' or 'ready'
        """
        player_token = token or uuid.uuid4().hex
        
        if self.lobby_waiting is None:
            # First player waiting
            self.lobby_waiting = player_token
            self.token_to_color[player_token] = "#ffcc00"
            return player_token, "waiting"
        else:
            # Second player joins - start game
            other_token = self.lobby_waiting
            self.lobby_waiting = None
            self.token_to_color[player_token] = "#66ccff"
            
            # Initialize game with both players
            self._start_new_game(other_token, player_token)
            return player_token, "ready"
    
    def _start_new_game(self, player1_token: str, player2_token: str) -> None:
        """Initialize a new game with two players."""
        # Generate new map
        data = graph_generator.generate_game_data_sync()
        self.state, self.screen = build_state_from_dict(data)
        
        # Create players with fixed colors
        p1 = Player(id=1, color="#ff3333")
        p2 = Player(id=2, color="#3388ff")
        self.state.add_player(p1)
        self.state.add_player(p2)
        
        # Set up game state - start in peace phase immediately
        self.state.phase = "peace"
        self.state.start_peace_period(time.time())
        self.token_to_player_id = {player1_token: 1, player2_token: 2}
        self.game_active = True
    
    def create_new_game(self) -> Tuple[GraphState, Dict[str, int]]:
        """Create a new single-player game for testing/development."""
        data = graph_generator.generate_game_data_sync()
        new_state, screen = build_state_from_dict(data)
        
        # Ensure Player 1 exists
        if 1 not in new_state.players:
            new_state.add_player(Player(id=1, color="#ffcc00"))
        
        self.state = new_state
        self.screen = screen
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
            
            # Check if this is for picking a starting node (node is unowned and player hasn't picked yet)
            if node.owner is None and not self.state.players_who_picked.get(player_id):
                # This is a starting node pick
                node.owner = player_id
                self.state.players_who_picked[player_id] = True
                return True
            
            # For all other cases (already picked, node already owned, etc.), 
            # let the normal node click logic handle it in other handlers
            return False
            
        except GameValidationError:
            return False
    
    def handle_edge_click(self, token: str, edge_id: int) -> bool:
        """
        Handle an edge click to toggle flow.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            
            # Allow edge clicks in both peace and playing phases
            if self.state.phase not in ["peace", "playing"]:
                raise GameValidationError("Game not in active phase")
            
            edge = self.validate_edge_exists(edge_id)
            
            # Toggle behavior
            if edge.on or edge.flowing:
                edge.on = False
                edge.flowing = False
            else:
                # Check if player owns the source node
                source_node = self.validate_node_exists(edge.source_node_id)
                
                if source_node.owner == player_id:
                    # During peace period, check if this would flow into opponent's node
                    if self.state.phase == "peace":
                        target_node = self.validate_node_exists(edge.target_node_id)
                        if target_node.owner is not None and target_node.owner != player_id:
                            raise GameValidationError("Cannot attack during peace period")
                    
                    edge.on = True
                    edge.flowing = True
                else:
                    raise GameValidationError("You must own the source node")
            
            return True
            
        except GameValidationError:
            return False
    
    def handle_reverse_edge(self, token: str, edge_id: int, cost: float = 1.0) -> bool:
        """
        Handle reversing an edge direction.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            
            # Allow reverse edge in playing phase or peace phase
            if self.state.phase not in ["playing", "peace"]:
                raise GameValidationError("Not in playing phase")
                
            edge = self.validate_edge_exists(edge_id)
            
            # Get both nodes
            source_node = self.validate_node_exists(edge.source_node_id)
            target_node = self.validate_node_exists(edge.target_node_id)
            
            # New eligibility rules: must own at least one node AND source can't be opponent's
            source_owner = source_node.owner
            target_owner = target_node.owner
            
            # Must own at least one node
            if source_owner != player_id and target_owner != player_id:
                raise GameValidationError("You must own at least one node")
            
            # Source node cannot belong to opponent
            opponent_ids = [pid for pid in self.state.players.keys() if pid != player_id]
            if source_owner in opponent_ids:
                raise GameValidationError("Pipe controlled by opponent")
            
            # During peace period, check if reversing would create an attack
            if self.state.phase == "peace":
                # After reversal, check if the new source (current target) would flow into opponent's node
                if target_owner is not None and target_owner != player_id:
                    raise GameValidationError("Cannot reverse pipe to attack during peace period")
            
            # Validate gold
            self.validate_sufficient_gold(player_id, cost)
            
            # Reverse the edge by swapping source and target
            edge.source_node_id, edge.target_node_id = edge.target_node_id, edge.source_node_id
            
            # Only start flowing if the new source node is owned by the swapping player
            # AND we're not in peace period (or if it wouldn't attack)
            new_source_node = self.validate_node_exists(edge.source_node_id)
            if new_source_node.owner == player_id:
                if self.state.phase == "peace":
                    # During peace, check if this would attack opponent's node
                    new_target_node = self.validate_node_exists(edge.target_node_id)
                    if new_target_node.owner is not None and new_target_node.owner != player_id:
                        # Would attack during peace - don't start flowing
                        edge.on = False
                        edge.flowing = False
                    else:
                        # Safe to start flowing
                        edge.on = True
                        edge.flowing = True
                else:
                    # Normal behavior outside peace period
                    edge.on = True
                    edge.flowing = True
            else:
                # Edge is swapped but not flowing since player doesn't own new source
                edge.on = False
                edge.flowing = False
            
            # Deduct gold
            self.state.player_gold[player_id] = max(0.0, self.state.player_gold[player_id] - cost)
            
            return True
            
        except GameValidationError:
            return False
    
    def handle_build_bridge(self, token: str, from_node_id: int, to_node_id: int, 
                          cost: float) -> Tuple[bool, Optional[Edge], Optional[str]]:
        """
        Handle building a bridge between two nodes.
        Returns: (success, new_edge, error_message)
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            
            # Allow bridge building in playing phase or peace phase
            if self.state.phase not in ["playing", "peace"]:
                raise GameValidationError("Not in playing phase")
            
            # Validate nodes
            from_node = self.validate_node_exists(from_node_id)
            to_node = self.validate_node_exists(to_node_id)
            
            if from_node_id == to_node_id:
                raise GameValidationError("Cannot connect node to itself")
            
            # Validate ownership
            self.validate_player_owns_node(from_node, player_id)
            
            # During peace period, check if this would attack opponent's node
            if self.state.phase == "peace":
                if to_node.owner is not None and to_node.owner != player_id:
                    raise GameValidationError("Cannot build bridge to attack during peace period")
            
            # Validate gold
            self.validate_sufficient_gold(player_id, cost)
            
            # Check if edge already exists
            if self._edge_exists_between_nodes(from_node_id, to_node_id):
                raise GameValidationError("Edge already exists between these nodes")
            
            # Check for intersections
            if self._edges_would_intersect(from_node, to_node):
                raise GameValidationError("Bridge would intersect existing edge")
            
            # Create the edge (always one-way from source to target)
            new_edge_id = max(self.state.edges.keys(), default=0) + 1
            
            # During peace period, don't start flowing if it would attack
            should_flow = True
            if self.state.phase == "peace" and to_node.owner is not None and to_node.owner != player_id:
                should_flow = False
            
            new_edge = Edge(
                id=new_edge_id,
                source_node_id=from_node_id,
                target_node_id=to_node_id,
                on=should_flow,
                flowing=should_flow
            )
            
            # Add to state
            self.state.edges[new_edge_id] = new_edge
            from_node.attached_edge_ids.append(new_edge_id)
            to_node.attached_edge_ids.append(new_edge_id)
            
            # Deduct gold
            self.state.player_gold[player_id] = max(0.0, self.state.player_gold[player_id] - cost)
            
            return True, new_edge, None
            
        except GameValidationError as e:
            return False, None, str(e)
    
    def handle_create_capital(self, token: str, node_id: int, cost: float) -> Tuple[bool, Optional[str]]:
        """
        Handle creating a capital on a node.
        Returns: (success, error_message)
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_phase("playing")
            
            # Validate node
            node = self.validate_node_exists(node_id)
            self.validate_player_owns_node(node, player_id)
            
            # Validate gold
            self.validate_sufficient_gold(player_id, cost)
            
            # Check if already a capital
            if hasattr(self.state, 'capital_nodes') and node_id in self.state.capital_nodes:
                raise GameValidationError("Node is already a capital")
            
            # Check if any adjacent nodes are capitals
            if hasattr(self.state, 'capital_nodes'):
                for edge_id in node.attached_edge_ids:
                    edge = self.state.edges.get(edge_id)
                    if edge:
                        # Check both directions - if this node is source or target
                        adjacent_node_id = None
                        if edge.source_node_id == node_id:
                            adjacent_node_id = edge.target_node_id
                        elif edge.target_node_id == node_id:
                            adjacent_node_id = edge.source_node_id
                        
                        if adjacent_node_id and adjacent_node_id in self.state.capital_nodes:
                            raise GameValidationError("No Adjacent Capitals")
            
            # Create capital
            if not hasattr(self.state, 'capital_nodes'):
                self.state.capital_nodes = set()
            self.state.capital_nodes.add(node_id)
            
            # Deduct gold
            self.state.player_gold[player_id] = max(0.0, self.state.player_gold[player_id] - cost)
            
            return True, None
            
        except GameValidationError as e:
            return False, str(e)
    
    def handle_quit_game(self, token: str) -> Optional[int]:
        """
        Handle a player quitting the game.
        Returns the winner's player ID, or None if no game active.
        """
        if token not in self.token_to_player_id:
            return None
        
        loser_id = self.token_to_player_id[token]
        winner_id = 1 if loser_id == 2 else 2
        
        # Clear game state
        self._end_game()
        
        return winner_id
    
    def handle_lobby_disconnect(self, token: str) -> None:
        """
        Handle a player disconnecting from the lobby before game starts.
        """
        if self.lobby_waiting == token:
            self.lobby_waiting = None
            # Also clean up any associated color mapping
            if token in self.token_to_color:
                del self.token_to_color[token]

    def handle_disconnect(self, token: str) -> Optional[int]:
        """
        Handle a player disconnect after grace period.
        Returns the winner's player ID, or None if no game active.
        """
        if not self.token_to_player_id or token not in self.token_to_player_id:
            return None
        
        loser_id = self.token_to_player_id[token]
        winner_id = 1 if loser_id == 2 else 2
        
        # Reset state for new game
        self.state = GraphState([], [])
        self._end_game()
        
        return winner_id
    
    def _end_game(self) -> None:
        """End the current game and reset state."""
        self.token_to_player_id.clear()
        self.game_active = False
    
    def _edge_exists_between_nodes(self, node_id1: int, node_id2: int) -> bool:
        """Check if an edge already exists between two nodes."""
        if not self.state:
            return False
        
        for edge in self.state.edges.values():
            if ((edge.source_node_id == node_id1 and edge.target_node_id == node_id2) or
                (edge.source_node_id == node_id2 and edge.target_node_id == node_id1)):
                return True
        return False
    
    def _edges_would_intersect(self, from_node: Node, to_node: Node) -> bool:
        """Check if a new edge would intersect existing edges."""
        if not self.state:
            return False
        
        x1, y1 = from_node.x, from_node.y
        x2, y2 = to_node.x, to_node.y
        
        for edge in self.state.edges.values():
            source_node = self.state.nodes.get(edge.source_node_id)
            target_node = self.state.nodes.get(edge.target_node_id)
            if source_node is None or target_node is None:
                continue
            
            x3, y3 = source_node.x, source_node.y
            x4, y4 = target_node.x, target_node.y
            
            # Skip if edges share a node
            if (from_node.id == source_node.id or from_node.id == target_node.id or 
                to_node.id == source_node.id or to_node.id == target_node.id):
                continue
            
            if self._line_segments_intersect(x1, y1, x2, y2, x3, y3, x4, y4):
                return True
        
        return False
    
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
    
    def simulate_tick(self, tick_interval_seconds: float) -> Optional[int]:
        """
        Simulate one game tick and return winner ID if game ended.
        """
        if not self.state or not self.game_active:
            return None
        
        current_time = time.time()
        
        # Check if peace period should end
        if self.state.phase == "peace" and self.state.check_peace_period(current_time):
            self.state.phase = "playing"
        
        self.state.simulate_tick(tick_interval_seconds)
        
        # Check for capital victory
        winner_id = self.state.check_capital_victory()
        if winner_id is not None:
            self._end_game()
            return winner_id
        
        # Check for zero nodes loss condition (only after peace period ends)
        if self.state.phase == "playing":
            winner_id = self.state.check_zero_nodes_loss()
            if winner_id is not None:
                self._end_game()
                return winner_id
        
        return None
    
    def handle_redirect_energy(self, token: str, target_node_id: int) -> bool:
        """
        Redirect energy flow towards a target node by optimizing edge states.
        This algorithm turns on/off edges to maximize energy flow to the target node.
        Returns True if the action was successful.
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_phase("playing")
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
    
    def handle_destroy_node(self, token: str, node_id: int, cost: float = 3.0) -> Tuple[bool, Optional[str]]:
        """
        Handle destroying a node owned by the player.
        Returns: (success, error_message)
        """
        try:
            self.validate_game_active()
            player_id = self.validate_player(token)
            self.validate_phase("playing")
            
            # Validate node
            node = self.validate_node_exists(node_id)
            self.validate_player_owns_node(node, player_id)
            
            # Validate gold
            self.validate_sufficient_gold(player_id, cost)
            
            # Remove all edges connected to this node
            edges_to_remove = []
            for edge_id, edge in self.state.edges.items():
                if edge.source_node_id == node_id or edge.target_node_id == node_id:
                    edges_to_remove.append(edge_id)
            
            # Remove the edges
            for edge_id in edges_to_remove:
                edge = self.state.edges[edge_id]
                # Remove edge from both connected nodes' attached_edge_ids
                source_node = self.state.nodes.get(edge.source_node_id)
                target_node = self.state.nodes.get(edge.target_node_id)
                if source_node and edge_id in source_node.attached_edge_ids:
                    source_node.attached_edge_ids.remove(edge_id)
                if target_node and edge_id in target_node.attached_edge_ids:
                    target_node.attached_edge_ids.remove(edge_id)
                # Remove the edge from the edges dictionary
                del self.state.edges[edge_id]
            
            # Remove the node from capital nodes if it was a capital
            if hasattr(self.state, 'capital_nodes') and node_id in self.state.capital_nodes:
                self.state.capital_nodes.remove(node_id)
            
            # Remove the node
            del self.state.nodes[node_id]
            
            # Deduct gold
            self.state.player_gold[player_id] = max(0.0, self.state.player_gold[player_id] - cost)
            
            return True, None
            
        except GameValidationError as e:
            return False, str(e)
    
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
                edge.flowing = False
            # If this node has a path to target and this is the optimal edge
            elif (edge.source_node_id in best_next_hop and 
                  best_next_hop[edge.source_node_id] == edge.id):
                # This is the ONE edge this node should use
                edge.on = True
                edge.flowing = True
            else:
                # Turn off all other edges from nodes that can reach target
                if edge.source_node_id in best_next_hop:
                    edge.on = False
                    edge.flowing = False
                # For nodes that can't reach target, leave their edges as-is
                # (they might be defending other areas)