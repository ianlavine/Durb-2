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
    """Simple AI bot that can play the game."""
    
    def __init__(self, player_id: int = 2, color: str = "#3388ff"):
        self.player_id = player_id
        self.color = color
        self.game_engine: Optional[GameEngine] = None
        self.bot_token = "bot_token_" + str(int(time.time()))
        self.has_acted = False
    
    def join_game(self, game_engine: GameEngine) -> None:
        """Join the game engine as a bot player."""
        self.game_engine = game_engine
        
        # Register the bot in the game engine
        self.game_engine.token_to_player_id[self.bot_token] = self.player_id
        self.game_engine.token_to_color[self.bot_token] = self.color
    
    def make_move(self) -> bool:
        """
        Make a move based on the current game state.
        Returns True if a move was made, False if no move was possible.
        """
        if not self.game_engine or not self.game_engine.state:
            return False
        
        # If we haven't picked a starting node yet, do that first
        if not self.game_engine.state.players_who_picked.get(self.player_id, False):
            return self._pick_optimal_starting_node()
        
        # For now, the bot stops after picking the starting node
        # Future: could implement more complex strategies here
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
                print(f"Bot picked starting node {best_node_id}")
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
    
    def start_bot_game(self, human_token: str) -> Tuple[bool, Optional[str]]:
        """
        Start a new bot vs human game.
        Returns: (success, error_message)
        """
        try:
            # Create bot player
            self.bot_player = BotPlayer(player_id=2, color="#3388ff")
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
            
            # Join the bot to the game
            self.bot_player.join_game(self.game_engine)
            
            return True, None
            
        except Exception as e:
            return False, str(e)
    
    def make_bot_move(self) -> bool:
        """Make the bot's move if it's the bot's turn."""
        if not self.bot_player or not self.game_active:
            return False
        
        return self.bot_player.make_move()
    
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
