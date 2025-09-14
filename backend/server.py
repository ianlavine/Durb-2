import asyncio
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import websockets

from .game_engine import GameEngine
from .message_handlers import MessageRouter
from .state import GraphState
from .bot_player import bot_game_manager


GRAPH_PATH: Path = Path(__file__).resolve().parent.parent / "graph.json"
TICK_INTERVAL_SECONDS: float = 0.1
WEBSOCKET_HOST: str = "0.0.0.0"
WEBSOCKET_PORT: int = int(os.environ.get("PORT", 8765))


class WebSocketServer:
    def __init__(self) -> None:
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.broadcast_task: Optional[asyncio.Task] = None
        
        # Game engine handles all game logic
        self.game_engine = GameEngine()
        self.message_router = MessageRouter(self.game_engine)
        
        # Server context for handlers (WebSocket-specific state)
        self.server_context = {
            "tick_interval": TICK_INTERVAL_SECONDS,
            "screen": {"width": 100, "height": 100, "margin": 0},
            "lobby_waiting": None,  # {"token": str, "websocket": ws}
            "game_clients": {},     # token -> websocket
            "ws_to_token": {},      # websocket -> token
        }

    async def handler(self, websocket: websockets.WebSocketServerProtocol) -> None:
        self.clients.add(websocket)
        try:
            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                    await self.message_router.route_message(websocket, msg, self.server_context)
                except Exception as e:
                    # Log error but continue processing other messages
                    print(f"Error processing message: {e}")
                    continue
        finally:
            await self._handle_disconnect(websocket)

    async def _handle_disconnect(self, websocket: websockets.WebSocketServerProtocol) -> None:
        """Handle client disconnection."""
        # Remove from general clients
        self.clients.discard(websocket)
        
        # Handle game-specific disconnections
        ws_to_token = self.server_context.get("ws_to_token", {})
        game_clients = self.server_context.get("game_clients", {})
        lobby_waiting = self.server_context.get("lobby_waiting")
        
        # Find token for this websocket
        token = ws_to_token.pop(websocket, None)
        
        # If in lobby, clear waiting
        if lobby_waiting and lobby_waiting.get("websocket") is websocket:
            self.server_context["lobby_waiting"] = None
            # Also notify game engine of lobby disconnect
            if lobby_waiting.get("token"):
                self.game_engine.handle_lobby_disconnect(lobby_waiting["token"])
        
        # If in game, handle disconnect with grace period
        if token and token in game_clients:
            game_clients.pop(token, None)
            asyncio.create_task(self._handle_disconnect_grace(token))
    
    async def _handle_disconnect_grace(self, token: str) -> None:
        """Give client a grace period to reconnect before declaring forfeit."""
        await asyncio.sleep(2.0)
        
        # Check if game ended or client reconnected
        game_clients = self.server_context.get("game_clients", {})
        if not self.game_engine.token_to_player_id or token in game_clients:
            return
        
        # Handle as quit
        winner_id = self.game_engine.handle_disconnect(token)
        if winner_id is not None:
            victory_msg = json.dumps({"type": "gameOver", "winnerId": winner_id})
            for client_ws in game_clients.values():
                try:
                    await client_ws.send(victory_msg)
                except Exception:
                    pass
            
            # Clear server state
            self.server_context["game_clients"] = {}
            self.server_context["ws_to_token"] = {}


    async def start(self) -> None:
        async with websockets.serve(self.handler, WEBSOCKET_HOST, WEBSOCKET_PORT, ping_interval=20, ping_timeout=20):
            self.broadcast_task = asyncio.create_task(self._broadcast_loop())
            await asyncio.Future()

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            
            # Check for regular game
            game_clients = self.server_context.get("game_clients", {})
            regular_game_active = game_clients and self.game_engine.is_game_active()
            
            # Check for bot game
            bot_game_active = bot_game_manager.game_active
            
            if not regular_game_active and not bot_game_active:
                continue
            
            # Handle regular game
            if regular_game_active and not bot_game_active:
                # Simulate game tick
                winner_id = self.game_engine.simulate_tick(TICK_INTERVAL_SECONDS)
                
                # Always broadcast the current game state first (including the final state)
                if self.game_engine.state:
                    msg = json.dumps(self.game_engine.state.to_tick_message(time.time()))
                    await self._broadcast(msg)
                
                # Check for game over after broadcasting the final state
                if winner_id is not None:
                    victory_msg = json.dumps({"type": "gameOver", "winnerId": winner_id})
                    for client_ws in game_clients.values():
                        try:
                            await client_ws.send(victory_msg)
                        except Exception:
                            pass
                    
                    # Clear server state
                    self.server_context["game_clients"] = {}
                    self.server_context["ws_to_token"] = {}
            
            # Handle bot game
            if bot_game_active:
                bot_game_engine = bot_game_manager.get_game_engine()
                
                # Make bot move if needed
                await bot_game_manager.make_bot_move()
                
                # Simulate game tick
                winner_id = bot_game_engine.simulate_tick(TICK_INTERVAL_SECONDS)
                
                # Broadcast the current game state
                if bot_game_engine.state:
                    msg = json.dumps(bot_game_engine.state.to_tick_message(time.time()))
                    await self._broadcast(msg)
                
                # Check for game over
                if winner_id is not None:
                    victory_msg = json.dumps({"type": "gameOver", "winnerId": winner_id})
                    for client_ws in game_clients.values():
                        try:
                            await client_ws.send(victory_msg)
                        except Exception:
                            pass
                    
                    # End bot game
                    bot_game_manager.end_game()
                    # Clear server state
                    self.server_context["game_clients"] = {}
                    self.server_context["ws_to_token"] = {}


    async def _broadcast(self, message: str) -> None:
        to_remove: List[websockets.WebSocketServerProtocol] = []
        for ws in self.clients:
            try:
                await ws.send(message)
            except Exception:
                to_remove.append(ws)
        for ws in to_remove:
            self.clients.discard(ws)


async def main() -> None:
    # Create server with game engine
    server = WebSocketServer()
    print(f"WebSocket server starting on ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}")
    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down.")


