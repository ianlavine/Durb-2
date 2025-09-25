import asyncio
import json
import os
import time
from pathlib import Path
from typing import Dict, List, Optional, Set

import websockets

from .message_handlers import MessageRouter
from .bot_manager import bot_game_manager


GRAPH_PATH: Path = Path(__file__).resolve().parent.parent / "graph.json"
TICK_INTERVAL_SECONDS: float = 0.1
WEBSOCKET_HOST: str = "0.0.0.0"
WEBSOCKET_PORT: int = int(os.environ.get("PORT", 8765))


class WebSocketServer:
    def __init__(self) -> None:
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.broadcast_task: Optional[asyncio.Task] = None
        
        self.message_router = MessageRouter()

        # Server context shared with message handlers
        self.server_context = {
            "tick_interval": TICK_INTERVAL_SECONDS,
            "games": {},              # game_id -> {engine, clients, ...}
            "token_to_game": {},      # token -> game_id
            "ws_to_token": {},        # websocket -> token
            "lobbies": {              # player_count -> waiting entries
                2: [],
                3: [],
                4: [],
            },
            "bot_game_clients": {},   # token -> websocket
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
        
        ws_to_token = self.server_context.get("ws_to_token", {})
        token = ws_to_token.pop(websocket, None)

        # Remove from all lobby queues
        for queue in self.server_context.get("lobbies", {}).values():
            queue[:] = [entry for entry in queue if entry.get("websocket") is not websocket]

        # Remove from bot game client mapping if present
        bot_clients = self.server_context.get("bot_game_clients", {})
        if token and token in bot_clients:
            bot_clients.pop(token, None)

        if not token:
            return

        token_to_game = self.server_context.get("token_to_game", {})
        game_id = token_to_game.get(token)
        if not game_id:
            return

        game_info = self.server_context.get("games", {}).get(game_id)
        if not game_info:
            return

        # Mark player as temporarily disconnected and allow a grace period
        game_info["clients"][token] = None
        disconnect_deadlines = game_info.setdefault("disconnect_deadlines", {})
        deadline = time.time() + 2.0
        disconnect_deadlines[token] = deadline
        asyncio.create_task(self._handle_disconnect_grace(game_id, token, deadline))

    async def _handle_disconnect_grace(self, game_id: str, token: str, deadline: float) -> None:
        """Give client a grace period to reconnect before declaring forfeit."""
        await asyncio.sleep(2.0)

        games = self.server_context.get("games", {})
        game_info = games.get(game_id)
        if not game_info:
            return

        disconnect_deadlines = game_info.get("disconnect_deadlines", {})
        current_deadline = disconnect_deadlines.get(token)
        if current_deadline != deadline:
            return  # Player reconnected or deadline updated

        engine = game_info.get("engine")
        if not engine:
            return

        winner_id = engine.handle_disconnect(token)
        if winner_id is not None:
            await self.message_router._announce_winner(game_id, game_info, winner_id, self.server_context)


    async def start(self) -> None:
        async with websockets.serve(self.handler, WEBSOCKET_HOST, WEBSOCKET_PORT, ping_interval=20, ping_timeout=20):
            self.broadcast_task = asyncio.create_task(self._broadcast_loop())
            await asyncio.Future()

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            await self._expire_lobbies()
            now = time.time()

            # Friend games
            games = list(self.server_context.get("games", {}).items())
            for game_id, game_info in games:
                engine = game_info.get("engine")
                if not engine or not engine.game_active or not engine.state:
                    continue

                winner_id = engine.simulate_tick(TICK_INTERVAL_SECONDS)

                state = engine.state
                if hasattr(state, "pending_node_captures") and state.pending_node_captures:
                    for capture_data in state.pending_node_captures:
                        # Send node capture notification only to the player who captured it
                        capturing_player_id = capture_data.get("player_id")
                        if capturing_player_id is not None:
                            # Find the token and websocket for this player
                            capturing_token = engine.player_id_to_token.get(capturing_player_id)
                            if capturing_token:
                                capturing_websocket = game_info.get("clients", {}).get(capturing_token)
                                if capturing_websocket:
                                    capture_msg = {
                                        "type": "nodeCaptured",
                                        "nodeId": capture_data["nodeId"],
                                        "reward": capture_data["reward"],
                                    }
                                    await self.message_router._send_safe(capturing_websocket, json.dumps(capture_msg))
                    state.pending_node_captures = []

                tick_msg = state.to_tick_message(now)
                await self.message_router._broadcast_to_game(game_info, tick_msg)

                if winner_id is not None:
                    await self.message_router._announce_winner(game_id, game_info, winner_id, self.server_context)

            # Bot game
            if bot_game_manager.game_active:
                bot_game_engine = bot_game_manager.get_game_engine()
                await bot_game_manager.make_bot_move()

                # If the manager recorded a last_client_event, broadcast it now
                if bot_game_manager.last_client_event:
                    payload = json.dumps(bot_game_manager.last_client_event)
                    await self._broadcast_to_specific(list(self.server_context.get("bot_game_clients", {}).values()), payload)
                    # Clear after broadcasting to avoid repeats
                    bot_game_manager.last_client_event = None

                winner_id = bot_game_engine.simulate_tick(TICK_INTERVAL_SECONDS)

                bot_clients = list(self.server_context.get("bot_game_clients", {}).values())

                state = bot_game_engine.state
                if state and hasattr(state, "pending_node_captures") and state.pending_node_captures:
                    for capture_data in state.pending_node_captures:
                        # Send node capture notification only to the player who captured it
                        capturing_player_id = capture_data.get("player_id")
                        if capturing_player_id is not None:
                            # Find the websocket for this player
                            capturing_token = bot_game_engine.player_id_to_token.get(capturing_player_id)
                            if capturing_token:
                                capturing_websocket = self.server_context.get("bot_game_clients", {}).get(capturing_token)
                                if capturing_websocket:
                                    capture_msg = json.dumps(
                                        {
                                            "type": "nodeCaptured",
                                            "nodeId": capture_data["nodeId"],
                                            "reward": capture_data["reward"],
                                        }
                                    )
                                    await self._broadcast_to_specific([capturing_websocket], capture_msg)
                    state.pending_node_captures = []

                if state:
                    tick_payload = json.dumps(state.to_tick_message(now))
                    await self._broadcast_to_specific(bot_clients, tick_payload)

                if winner_id is not None:
                    victory_payload = json.dumps({"type": "gameOver", "winnerId": winner_id})
                    await self._broadcast_to_specific(bot_clients, victory_payload)
                    bot_game_manager.end_game()
                    self.server_context["bot_game_clients"] = {}


    async def _broadcast_to_specific(
        self,
        clients: List[Optional[websockets.WebSocketServerProtocol]],
        message: str,
    ) -> None:
        for ws in clients:
            if not ws:
                continue
            try:
                await ws.send(message)
            except Exception:
                self.clients.discard(ws)

    async def _expire_lobbies(self) -> None:
        lobbies = self.server_context.get("lobbies", {})
        ws_to_token = self.server_context.get("ws_to_token", {})
        now = time.time()
        timeout_seconds = 180.0

        for queue in lobbies.values():
            if not queue:
                continue

            remaining = []
            for entry in queue:
                joined_at = entry.get("joined_at", now)
                websocket = entry.get("websocket")
                token = entry.get("token")

                if now - joined_at >= timeout_seconds:
                    if websocket:
                        await self.message_router._send_safe(websocket, json.dumps({"type": "lobbyTimeout"}))
                        ws_to_token.pop(websocket, None)
                else:
                    remaining.append(entry)

            queue[:] = remaining


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
