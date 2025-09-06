"""
WebSocket Message Handlers - Handle different types of client messages.
Separates message parsing and response logic from core game logic.
"""
import json
import asyncio
from typing import Dict, Any, Optional, Set
import websockets

from .game_engine import GameEngine, GameValidationError
from .graph_generator import graph_generator


class BaseMessageHandler:
    """Base class for message handlers."""
    
    def __init__(self, game_engine: GameEngine):
        self.game_engine = game_engine
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        """Handle a message. Override in subclasses."""
        raise NotImplementedError


class NodeClickHandler(BaseMessageHandler):
    """Handle node click messages during picking phase."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        node_id = msg.get("nodeId")
        
        if token is None or node_id is None:
            return
        
        success = self.game_engine.handle_node_click(token, node_id)
        # Success/failure is handled implicitly through game state changes
        # Server will broadcast updated state in next tick


class EdgeClickHandler(BaseMessageHandler):
    """Handle edge click messages to toggle flow."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        edge_id = msg.get("edgeId")
        
        if token is None or edge_id is None:
            return
        
        success = self.game_engine.handle_edge_click(token, edge_id)
        # Success/failure is handled implicitly through game state changes


class ReverseEdgeHandler(BaseMessageHandler):
    """Handle edge reversal messages."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        edge_id = msg.get("edgeId")
        cost = msg.get("cost", 1.0)
        
        if edge_id is None:
            return
        
        success = self.game_engine.handle_reverse_edge(token, edge_id, cost)
        
        if success and self.game_engine.state:
            # Broadcast the updated edge immediately so frontend sees the direction change
            edge = self.game_engine.state.edges.get(edge_id)
            if edge:
                edge_data = {
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
                }
                
                game_clients = server_context.get("game_clients", {})
                await self._broadcast_to_game_clients(json.dumps(edge_data), game_clients)


class NewGameHandler(BaseMessageHandler):
    """Handle new game creation requests."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        new_state, screen = self.game_engine.create_new_game()
        
        # Update server context
        server_context["screen"] = screen
        
        # Send init message
        init_msg = new_state.to_init_message(screen, server_context.get("tick_interval", 0.1))
        await websocket.send(json.dumps(init_msg))


class RequestInitHandler(BaseMessageHandler):
    """Handle initialization requests (for reconnections)."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        
        if not self.game_engine.state or not self.game_engine.state.nodes:
            return
        
        if token and token in self.game_engine.token_to_player_id:
            # Reattach this websocket
            game_clients = server_context.get("game_clients", {})
            ws_to_token = server_context.get("ws_to_token", {})
            
            game_clients[token] = websocket
            ws_to_token[websocket] = token
            
            # Send init with player info
            init = self.game_engine.state.to_init_message(
                server_context.get("screen", {}), 
                server_context.get("tick_interval", 0.1)
            )
            init["myPlayerId"] = self.game_engine.token_to_player_id[token]
            init["token"] = token
            await websocket.send(json.dumps(init))


class JoinLobbyHandler(BaseMessageHandler):
    """Handle lobby join requests."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        
        player_token, status = self.game_engine.join_lobby(token)
        
        if status == "waiting":
            # First player waiting
            lobby_waiting = server_context.get("lobby_waiting")
            if lobby_waiting:
                lobby_waiting["token"] = player_token
                lobby_waiting["websocket"] = websocket
            else:
                server_context["lobby_waiting"] = {"token": player_token, "websocket": websocket}
            
            await websocket.send(json.dumps({
                "type": "lobbyJoined", 
                "status": "waiting", 
                "token": player_token
            }))
        
        elif status == "ready":
            # Game starting with two players
            lobby_waiting = server_context.get("lobby_waiting")
            if not lobby_waiting:
                return
            
            other_ws = lobby_waiting["websocket"]
            other_token = lobby_waiting["token"]
            
            # Clear lobby
            server_context["lobby_waiting"] = None
            
            # Set up game client tracking
            game_clients = server_context.setdefault("game_clients", {})
            ws_to_token = server_context.setdefault("ws_to_token", {})
            
            game_clients[other_token] = other_ws
            game_clients[player_token] = websocket
            ws_to_token[other_ws] = other_token
            ws_to_token[websocket] = player_token
            
            # Update server screen
            server_context["screen"] = self.game_engine.screen
            
            # Send init messages to both players
            await self._send_game_start_messages(other_ws, other_token, websocket, player_token, server_context)
    
    async def _send_game_start_messages(self, player1_ws, player1_token, player2_ws, player2_token, server_context):
        """Send game start messages to both players."""
        if not self.game_engine.state:
            return
        
        init_common = self.game_engine.state.to_init_message(
            server_context.get("screen", {}), 
            server_context.get("tick_interval", 0.1)
        )
        
        # Player 1 message
        init1 = dict(init_common)
        init1["type"] = "init"
        init1["myPlayerId"] = 1
        init1["token"] = player1_token
        await player1_ws.send(json.dumps(init1))
        
        # Player 2 message
        init2 = dict(init_common)
        init2["type"] = "init"
        init2["myPlayerId"] = 2
        init2["token"] = player2_token
        await player2_ws.send(json.dumps(init2))


class BuildBridgeHandler(BaseMessageHandler):
    """Handle bridge building requests."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        from_node_id = msg.get("fromNodeId")
        to_node_id = msg.get("toNodeId")
        cost = msg.get("cost", 0)
        
        success, new_edge, error_msg = self.game_engine.handle_build_bridge(
            token, from_node_id, to_node_id, cost
        )
        
        if not success:
            await websocket.send(json.dumps({
                "type": "bridgeError", 
                "message": error_msg or "Failed to build bridge"
            }))
            return
        
        if new_edge:
            # Broadcast successful edge creation
            edge_data = {
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
            }
            
            game_clients = server_context.get("game_clients", {})
            await self._broadcast_to_game_clients(json.dumps(edge_data), game_clients)


class CreateCapitalHandler(BaseMessageHandler):
    """Handle capital creation requests."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        node_id = msg.get("nodeId")
        cost = msg.get("cost", 0)
        
        success, error_msg = self.game_engine.handle_create_capital(token, node_id, cost)
        
        if not success:
            await websocket.send(json.dumps({
                "type": "capitalError", 
                "message": error_msg or "Failed to create capital"
            }))
            return
        
        # Broadcast successful capital creation
        capital_data = {
            "type": "newCapital",
            "nodeId": node_id
        }
        
        game_clients = server_context.get("game_clients", {})
        await self._broadcast_to_game_clients(json.dumps(capital_data), game_clients)
        
        # Check for victory condition
        winner_id = self.game_engine.state.check_capital_victory() if self.game_engine.state else None
        if winner_id is not None:
            victory_msg = json.dumps({"type": "gameOver", "winnerId": winner_id})
            await self._broadcast_to_game_clients(victory_msg, game_clients)
            
            # Clear game state
            server_context["game_clients"] = {}
            server_context["ws_to_token"] = {}


class RedirectEnergyHandler(BaseMessageHandler):
    """Handle energy redirection messages."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        target_node_id = msg.get("targetNodeId")
        
        if target_node_id is None:
            return
        
        success = self.game_engine.handle_redirect_energy(token, target_node_id)
        
        if success and self.game_engine.state:
            # Broadcast the updated edge states to all clients
            game_clients = server_context.get("game_clients", {})
            
            # Send all affected edge updates
            for edge in self.game_engine.state.edges.values():
                edge_data = {
                    "type": "edgeUpdated",
                    "edge": {
                        "id": edge.id,
                        "source": edge.source_node_id,
                        "target": edge.target_node_id,
                        "bidirectional": False,
                        "forward": True,
                        "on": edge.on,
                        "flowing": edge.flowing
                    }
                }
                await self._broadcast_to_game_clients(json.dumps(edge_data), game_clients)


class QuitGameHandler(BaseMessageHandler):
    """Handle quit game requests."""
    
    async def handle(self, websocket: websockets.WebSocketServerProtocol, 
                    msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        token = msg.get("token")
        
        winner_id = self.game_engine.handle_quit_game(token)
        if winner_id is None:
            return
        
        # Broadcast game over
        game_clients = server_context.get("game_clients", {})
        victory_msg = json.dumps({"type": "gameOver", "winnerId": winner_id})
        await self._broadcast_to_game_clients(victory_msg, game_clients)
        
        # Clear server state
        server_context["game_clients"] = {}
        server_context["ws_to_token"] = {}

    async def _broadcast_to_game_clients(self, message: str, game_clients: Dict[str, websockets.WebSocketServerProtocol]) -> None:
        """Broadcast a message to all game clients."""
        for client_ws in game_clients.values():
            try:
                await client_ws.send(message)
            except Exception:
                pass


class MessageRouter:
    """Routes messages to appropriate handlers."""
    
    def __init__(self, game_engine: GameEngine):
        self.game_engine = game_engine
        self.handlers = {
            "clickNode": NodeClickHandler(game_engine),
            "clickEdge": EdgeClickHandler(game_engine),
            "reverseEdge": ReverseEdgeHandler(game_engine),
            "newGame": NewGameHandler(game_engine),
            "requestInit": RequestInitHandler(game_engine),
            "joinLobby": JoinLobbyHandler(game_engine),
            "buildBridge": BuildBridgeHandler(game_engine),
            "createCapital": CreateCapitalHandler(game_engine),
            "redirectEnergy": RedirectEnergyHandler(game_engine),
            "quitGame": QuitGameHandler(game_engine),
        }
    
    async def route_message(self, websocket: websockets.WebSocketServerProtocol, 
                           msg: Dict[str, Any], server_context: Dict[str, Any]) -> None:
        """Route a message to the appropriate handler."""
        msg_type = msg.get("type")
        handler = self.handlers.get(msg_type)
        
        if handler:
            await handler.handle(websocket, msg, server_context)


# Add broadcast utility to base handler
BaseMessageHandler._broadcast_to_game_clients = QuitGameHandler._broadcast_to_game_clients
