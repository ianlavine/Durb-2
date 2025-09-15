"""
Bot Player - Simple AI that can play the game automatically.
The bot uses the existing game engine methods directly without going through web UI.
"""

import time
from typing import Dict, List, Optional, Tuple
from .models import Player
from .game_engine import GameEngine
from .state import GraphState


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
        self.websocket = None  # Will be set by the bot game manager
    
    def join_game(self, game_engine: GameEngine) -> None:
        """Join the game engine as a bot player."""
        self.game_engine = game_engine
        
        # Register the bot in the game engine
        self.game_engine.token_to_player_id[self.bot_token] = self.player_id
        self.game_engine.token_to_color[self.bot_token] = self.color
    
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
            # Easy bot: only auto-expand (handled automatically)
            return False
        elif self.difficulty == "medium":
            return await self._make_medium_move()
        elif self.difficulty == "hard":
            return await self._make_hard_move()
        
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
    
    async def _make_hard_move(self) -> bool:
        """
        Hard bot strategy: medium strategy + bridge building for expansion.
        Returns True if a move was made.
        """
        # First try medium strategy
        if await self._make_medium_move():
            return True
        
        # Then try bridge building for expansion
        if await self._try_bridge_building():
            return True
        
        return False
    
    async def _try_edge_reversal_for_expansion(self) -> bool:
        """Try to reverse edges to expand to more unowned nodes."""
        if not self.game_engine or not self.game_engine.state:
            return False
        
        # Find edges that could be reversed to reach unowned nodes
        for edge in self.game_engine.state.edges.values():
            # Check if we can reverse this edge to reach an unowned node
            source_node = self.game_engine.state.nodes.get(edge.source_node_id)
            target_node = self.game_engine.state.nodes.get(edge.target_node_id)
            
            if not source_node or not target_node:
                continue
            
            # Check if reversing would give us access to an unowned node
            # We need to own the target node but not the source node
            if (target_node.owner == self.player_id and 
                source_node.owner != self.player_id and 
                source_node.owner is None):  # Source is unowned
                
                # Check if we have enough gold
                cost = 1.0
                if self.game_engine.state.player_gold.get(self.player_id, 0) >= cost:
                    # Try to reverse the edge
                    success = self.game_engine.handle_reverse_edge(self.bot_token, edge.id, cost)
                    if success:
                        # Send frontend response
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
    
    async def _try_bridge_building(self) -> bool:
        """Try to build bridges for expansion opportunities."""
        if not self.game_engine or not self.game_engine.state:
            return False
        
        # Find good bridge building opportunities
        for owned_node_id, owned_node in self.game_engine.state.nodes.items():
            if owned_node.owner != self.player_id:
                continue
            
            # Look for nearby unowned nodes that would be good expansion targets
            for target_node_id, target_node in self.game_engine.state.nodes.items():
                if (target_node.owner is None and 
                    target_node_id != owned_node_id and
                    not self._edge_exists_between_nodes(owned_node_id, target_node_id)):
                    
                    # Check if this would be a good expansion opportunity
                    if self._is_good_bridge_target(target_node_id):
                        # Calculate actual bridge cost based on distance
                        owned_node = self.game_engine.state.nodes.get(owned_node_id)
                        target_node = self.game_engine.state.nodes.get(target_node_id)
                        if owned_node and target_node:
                            # Calculate distance between nodes
                            dx = target_node.x - owned_node.x
                            dy = target_node.y - owned_node.y
                            distance = (dx * dx + dy * dy) ** 0.5
                            
                            # If nodes are the same, cost is 0
                            if distance == 0:
                                cost = 0
                            else:
                                # Base cost of 1 gold, then scales after distance 10
                                # Stays at 1 gold for distances 0-10, then adds 0.1 gold per unit beyond 10
                                # This means a bridge across the full map (distance ~141) would cost ~14.1 gold
                                # Corner to corner (distance ~141) should cost around $14
                                cost = round(1 + max(0, (distance - 8) * 0.1))
                            
                            if self.game_engine.state.player_gold.get(self.player_id, 0) >= cost:
                                # Try to build the bridge
                                success, new_edge, error_msg = self.game_engine.handle_build_bridge(
                                    self.bot_token, owned_node_id, target_node_id, cost
                                )
                                if success and new_edge:
                                    # Send frontend response
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
                                        },
                                        "cost": cost  # Include the cost for frontend animation
                                    })
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
    
    def start_bot_game(self, human_token: str, difficulty: str = "easy", auto_expand: bool = False, speed_level: int = 6) -> Tuple[bool, Optional[str]]:
        """
        Start a new bot vs human game with specified difficulty.
        Returns: (success, error_message)
        """
        try:
            # Create bot player with specified difficulty
            self.bot_player = BotPlayer(player_id=2, color="#3388ff", difficulty=difficulty)
            self.human_token = human_token
            
            # Set up the game with human as player 1, bot as player 2
            p1 = Player(id=1, color="#ff3333")
            p2 = Player(id=2, color="#3388ff")
            
            # Generate new map
            from .graph_generator import graph_generator
            data = graph_generator.generate_game_data_sync()
            from .state import build_state_from_dict
            self.game_engine.state, self.game_engine.screen = build_state_from_dict(data)
            
            # Add players
            self.game_engine.state.add_player(p1)
            self.game_engine.state.add_player(p2)
            
            # Set up game state
            self.game_engine.state.phase = "picking"
            self.game_engine.token_to_player_id = {human_token: 1, self.bot_player.bot_token: 2}
            self.game_engine.token_to_color = {human_token: "#ff3333", self.bot_player.bot_token: "#3388ff"}
            self.game_engine.game_active = True
            self.game_active = True
            
            # Set speed level for the game
            self.game_engine.state.speed_level = speed_level
            
            # Apply auto-expand setting for human player
            self.game_engine.state.player_auto_expand[1] = auto_expand
            
            # Join the bot to the game
            self.bot_player.join_game(self.game_engine)
            
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
