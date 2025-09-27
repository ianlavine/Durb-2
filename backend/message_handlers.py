import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import websockets

from .constants import MAX_FRIEND_PLAYERS, MIN_FRIEND_PLAYERS, PLAYER_COLOR_SCHEMES
from .game_engine import GameEngine
from .bot_manager import bot_game_manager


class MessageRouter:
    """Routes websocket messages to the appropriate handlers."""

    def __init__(self) -> None:
        self.handlers = {
            "joinLobby": self.handle_join_lobby,
            "leaveLobby": self.handle_leave_lobby,
            "requestInit": self.handle_request_init,
            "clickNode": self.handle_click_node,
            "clickEdge": self.handle_click_edge,
            "reverseEdge": self.handle_reverse_edge,
            "buildBridge": self.handle_build_bridge,
            "redirectEnergy": self.handle_redirect_energy,
            "localTargeting": self.handle_local_targeting,
            "destroyNode": self.handle_destroy_node,
            "quitGame": self.handle_quit_game,
            "toggleAutoExpand": self.handle_toggle_auto_expand,
            "newGame": self.handle_new_game,
            "nodeCaptured": self.handle_node_captured_flush,
        }

    async def route_message(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        msg_type = msg.get("type")

        # Bot game messages are handled separately (except startBotGame)
        if msg_type != "startBotGame" and bot_game_manager.game_active:
            human_token = bot_game_manager.human_token
            if human_token and human_token in server_context.get("bot_game_clients", {}):
                await self._route_to_bot_game(websocket, msg, server_context)
                return

        if msg_type == "startBotGame":
            await self.handle_start_bot_game(websocket, msg, server_context)
            return

        handler = self.handlers.get(msg_type)
        if handler:
            await handler(websocket, msg, server_context)

    # ------------------------------------------------------------------
    # Lobby / game setup helpers
    # ------------------------------------------------------------------

    async def handle_join_lobby(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        player_count = int(msg.get("playerCount", MIN_FRIEND_PLAYERS))
        player_count = max(MIN_FRIEND_PLAYERS, min(MAX_FRIEND_PLAYERS, player_count))
        auto_expand = bool(msg.get("autoExpand", False))
        speed_level = int(msg.get("speedLevel", 3))
        speed_level = max(1, min(5, speed_level))
        token = msg.get("token") or uuid.uuid4().hex

        lobbies: Dict[int, List[Dict[str, Any]]] = server_context.setdefault("lobbies", {})
        lobby_queue = lobbies.setdefault(player_count, [])

        # Remove any stale entries for this websocket
        lobby_queue[:] = [entry for entry in lobby_queue if entry.get("websocket") is not websocket]

        lobby_queue.append(
            {
                "token": token,
                "websocket": websocket,
                "auto_expand": auto_expand,
                "speed_level": speed_level,
                "joined_at": time.time(),
            }
        )

        server_context.setdefault("ws_to_token", {})[websocket] = token

        await websocket.send(
            json.dumps(
                {
                    "type": "lobbyJoined",
                    "status": "waiting",
                    "token": token,
                    "playerCount": player_count,
                }
            )
        )

        if len(lobby_queue) >= player_count:
            players = [lobby_queue.pop(0) for _ in range(player_count)]
            await self._start_friend_game(players, player_count, server_context)

    async def handle_leave_lobby(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        """Explicitly remove a client from any lobby queues, similar to disconnect behavior."""
        # Remove this websocket from all lobby queues
        lobbies: Dict[int, List[Dict[str, Any]]] = server_context.setdefault("lobbies", {})
        for queue in lobbies.values():
            if not queue:
                continue
            queue[:] = [entry for entry in queue if entry.get("websocket") is not websocket]

        # Mirror disconnect behavior by clearing the ws->token mapping
        ws_to_token = server_context.setdefault("ws_to_token", {})
        ws_to_token.pop(websocket, None)

        # Acknowledge (optional for frontend UX)
        await self._send_safe(websocket, json.dumps({"type": "lobbyLeft"}))

    async def _start_friend_game(
        self,
        players: List[Dict[str, Any]],
        player_count: int,
        server_context: Dict[str, Any],
    ) -> None:
        speed_level = int(players[0].get("speed_level", 3))
        speed_level = max(1, min(5, speed_level))
        engine = GameEngine()
        color_pool = PLAYER_COLOR_SCHEMES[:player_count]

        player_slots = []
        for idx, player in enumerate(players, start=1):
            color_info = color_pool[idx - 1]
            player_slots.append(
                {
                    "player_id": idx,
                    "token": player["token"],
                    "color": color_info["color"],
                    "secondary_colors": color_info["secondary"],
                    "auto_expand": bool(player.get("auto_expand", False)),
                }
            )

        engine.start_game(player_slots, speed_level)
        game_id = uuid.uuid4().hex
        game_info = {
            "engine": engine,
            "clients": {},
            "screen": engine.screen,
            "player_count": player_count,
            "speed_level": speed_level,
            "created_at": time.time(),
            "disconnect_deadlines": {},
        }

        games = server_context.setdefault("games", {})
        games[game_id] = game_info

        token_to_game = server_context.setdefault("token_to_game", {})
        ws_to_token = server_context.setdefault("ws_to_token", {})

        for player in players:
            token = player["token"]
            websocket = player["websocket"]
            token_to_game[token] = game_id
            ws_to_token[websocket] = token
            game_info["clients"][token] = websocket
            await self._send_init_message(engine, game_info, websocket, token, server_context)

    # ------------------------------------------------------------------
    # Core gameplay handlers
    # ------------------------------------------------------------------

    async def handle_click_node(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        node_id = msg.get("nodeId")
        if token is None or node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success = engine.handle_node_click(token, int(node_id))
        if success and engine.state and getattr(engine.state, "pending_node_captures", None):
            player_id = engine.get_player_id(token)
            for capture_data in engine.state.pending_node_captures:
                # Only send notification to the player who captured the node
                if capture_data.get("player_id") == player_id:
                    capture_msg = {
                        "type": "nodeCaptured",
                        "nodeId": capture_data["nodeId"],
                        "reward": capture_data["reward"],
                    }
                    await self._send_safe(websocket, json.dumps(capture_msg))
            engine.state.pending_node_captures = []

    async def handle_click_edge(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        edge_id = msg.get("edgeId")
        if token is None or edge_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        engine.handle_edge_click(token, int(edge_id))

    async def handle_reverse_edge(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        edge_id = msg.get("edgeId")
        cost = float(msg.get("cost", 1.0))
        if token is None or edge_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success = engine.handle_reverse_edge(token, int(edge_id), cost)
        if not success:
            # Derive a more specific error message
            error_message = "Can't reverse this pipe!"
            try:
                if engine.state:
                    player_id = engine.get_player_id(token)
                    edge = engine.state.edges.get(int(edge_id)) if player_id is not None else None
                    if edge:
                        source_node = engine.state.nodes.get(edge.source_node_id)
                        target_node = engine.state.nodes.get(edge.target_node_id)
                        # Check opponent control first
                        if source_node and source_node.owner is not None and source_node.owner != player_id:
                            error_message = "Pipe controlled by opponent"
                        else:
                            # Compare required cost vs available gold
                            if source_node and target_node:
                                actual_cost = engine.calculate_bridge_cost(source_node, target_node)
                                player_gold = engine.state.player_gold.get(player_id, 0.0)
                                if player_gold < actual_cost:
                                    error_message = "Not enough gold"
            except Exception:
                pass

            await self._send_safe(websocket, json.dumps({"type": "reverseEdgeError", "message": error_message}))
            return

        actual_cost = cost
        if engine.state and getattr(engine.state, "pending_edge_reversal", None):
            actual_cost = engine.state.pending_edge_reversal.get("cost", cost)

        if engine.state:
            edge = engine.state.edges.get(int(edge_id))
            if edge:
                # Send edge state update to all players (without cost indicator for others)
                edge_update_message = {
                    "type": "edgeReversed",
                    "edge": {
                        "id": edge.id,
                        "source": edge.source_node_id,
                        "target": edge.target_node_id,
                        "bidirectional": False,
                        "forward": True,
                        "on": edge.on,
                        "flowing": edge.flowing,
                    }
                }
                
                # Send to all players, but include cost only for the acting player
                for token_key, client_websocket in game_info.get("clients", {}).items():
                    if not client_websocket:
                        continue
                    
                    message_to_send = edge_update_message.copy()
                    # Only include cost for the player who performed the action
                    if token_key == token:
                        message_to_send["cost"] = actual_cost
                    
                    await self._send_safe(client_websocket, json.dumps(message_to_send))

            if hasattr(engine.state, "pending_edge_reversal"):
                engine.state.pending_edge_reversal = None

    async def handle_build_bridge(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        from_node_id = msg.get("fromNodeId")
        to_node_id = msg.get("toNodeId")
        cost = float(msg.get("cost", 0))
        if token is None or from_node_id is None or to_node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success, new_edge, actual_cost, error_msg = engine.handle_build_bridge(
            token, int(from_node_id), int(to_node_id), cost
        )

        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "bridgeError", "message": error_msg or "Failed to build bridge"}),
            )
            return

        if new_edge:
            # Send edge state update to all players (without cost)
            edge_update_message = {
                "type": "newEdge",
                "edge": {
                    "id": new_edge.id,
                    "source": new_edge.source_node_id,
                    "target": new_edge.target_node_id,
                    "bidirectional": False,
                    "forward": True,
                        "on": new_edge.on,
                        "flowing": new_edge.flowing,
                        "building": bool(getattr(new_edge, 'building', False)),
                        "buildTicksRequired": int(getattr(new_edge, 'build_ticks_required', 0)),
                        "buildTicksElapsed": int(getattr(new_edge, 'build_ticks_elapsed', 0)),
                }
            }
            
            # Send to all players, but include cost only for the acting player
            for token_key, client_websocket in game_info.get("clients", {}).items():
                if not client_websocket:
                    continue
                
                message_to_send = edge_update_message.copy()
                # Only include cost for the player who built the bridge
                if token_key == token:
                    message_to_send["cost"] = actual_cost
                
                await self._send_safe(client_websocket, json.dumps(message_to_send))

    async def handle_redirect_energy(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        target_node_id = msg.get("targetNodeId")
        if token is None or target_node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success = engine.handle_redirect_energy(token, int(target_node_id))
        if success and engine.state:
            updates = []
            for edge in engine.state.edges.values():
                updates.append(
                    {
                        "type": "edgeUpdated",
                        "edge": {
                            "id": edge.id,
                            "source": edge.source_node_id,
                            "target": edge.target_node_id,
                            "bidirectional": False,
                            "forward": True,
                            "on": edge.on,
                            "flowing": edge.flowing,
                        },
                    }
                )
            for update in updates:
                await self._broadcast_to_game(game_info, update)

    async def handle_local_targeting(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        target_node_id = msg.get("targetNodeId")
        if token is None or target_node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success = engine.handle_local_targeting(token, int(target_node_id))
        if success and engine.state:
            updates = []
            # Only send updates for edges that were actually changed
            for edge in engine.state.edges.values():
                source_node = engine.state.nodes.get(edge.source_node_id)
                if source_node and source_node.owner == engine.get_player_id(token) and edge.target_node_id == int(target_node_id):
                    updates.append(
                        {
                            "type": "edgeUpdated",
                            "edge": {
                                "id": edge.id,
                                "source": edge.source_node_id,
                                "target": edge.target_node_id,
                                "bidirectional": False,
                                "forward": True,
                                "on": edge.on,
                                "flowing": edge.flowing,
                            },
                        }
                    )
            for update in updates:
                await self._broadcast_to_game(game_info, update)

    async def handle_destroy_node(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        node_id = msg.get("nodeId")
        cost = float(msg.get("cost", 3.0))
        if token is None or node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success, error_msg = engine.handle_destroy_node(token, int(node_id), cost)
        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "destroyError", "message": error_msg or "Failed to destroy node"}),
            )
            return

        message = {"type": "nodeDestroyed", "nodeId": int(node_id)}
        await self._send_safe(websocket, json.dumps(message))

    async def handle_quit_game(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        if token is None:
            return

        game_id, game_info = self._get_game_info_with_id(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        winner_id = engine.handle_quit_game(token)
        if winner_id is not None:
            await self._announce_winner(game_id, game_info, winner_id, server_context)
            return

        if engine.state:
            player_id = engine.token_to_player_id.get(token)
            if player_id is not None and player_id in engine.state.eliminated_players:
                game_info.setdefault("disconnect_deadlines", {}).pop(token, None)
                server_context.get("token_to_game", {}).pop(token, None)
                server_context.get("ws_to_token", {}).pop(websocket, None)

    async def handle_toggle_auto_expand(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        if token is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        engine.handle_toggle_auto_expand(token)

    # ------------------------------------------------------------------
    # Utility handlers
    # ------------------------------------------------------------------

    async def handle_request_init(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        if not token:
            return

        game_id, game_info = self._get_game_info_with_id(token, server_context)
        if not game_info:
            return

        ws_to_token = server_context.setdefault("ws_to_token", {})
        ws_to_token[websocket] = token
        game_info["clients"][token] = websocket
        game_info.setdefault("disconnect_deadlines", {}).pop(token, None)
        await self._send_init_message(game_info["engine"], game_info, websocket, token, server_context)

    async def handle_new_game(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        # Development helper: create a single-player sandbox state
        engine = GameEngine()
        state, screen = engine.create_new_game()
        message = state.to_init_message(
            screen,
            server_context.get("tick_interval", 0.1),
            time.time(),
        )
        await self._send_safe(websocket, json.dumps(message))

    async def handle_node_captured_flush(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        # No-op in the new architecture; retained for backwards compatibility
        return

    # ------------------------------------------------------------------
    # Bot game handlers
    # ------------------------------------------------------------------

    async def handle_start_bot_game(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token") or uuid.uuid4().hex
        difficulty = msg.get("difficulty", "easy")
        auto_expand = bool(msg.get("autoExpand", False))
        speed_level = int(msg.get("speedLevel", 3))
        speed_level = max(1, min(5, speed_level))

        success, error_msg = bot_game_manager.start_bot_game(token, difficulty, auto_expand, speed_level)
        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "botGameError", "message": error_msg or "Failed to start bot game"}),
            )
            return

        bot_game_engine = bot_game_manager.get_game_engine()

        server_context.setdefault("bot_game_clients", {})[token] = websocket
        server_context.setdefault("ws_to_token", {})[websocket] = token

        if bot_game_engine.state:
            message = bot_game_engine.state.to_init_message(
                bot_game_engine.screen,
                server_context.get("tick_interval", 0.1),
                time.time(),
            )
            message["type"] = "init"
            message["myPlayerId"] = 1
            message["token"] = token
            await self._send_safe(websocket, json.dumps(message))

        if bot_game_manager.bot_player:
            await bot_game_manager.make_bot_move()

    async def _route_to_bot_game(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        if not token:
            return

        bot_game_engine = bot_game_manager.get_game_engine()
        msg_type = msg.get("type")

        if msg_type == "clickNode":
            node_id = msg.get("nodeId")
            if node_id is not None:
                success = bot_game_engine.handle_node_click(token, int(node_id))
                if success and bot_game_engine.state and getattr(bot_game_engine.state, "pending_node_captures", None):
                    player_id = bot_game_engine.get_player_id(token)
                    for capture_data in bot_game_engine.state.pending_node_captures:
                        # Only send notification to the player who captured the node
                        if capture_data.get("player_id") == player_id:
                            capture_msg = {
                                "type": "nodeCaptured",
                                "nodeId": capture_data["nodeId"],
                                "reward": capture_data["reward"],
                            }
                            await self._send_safe(websocket, json.dumps(capture_msg))
                    bot_game_engine.state.pending_node_captures = []

        elif msg_type == "clickEdge":
            edge_id = msg.get("edgeId")
            if edge_id is not None:
                bot_game_engine.handle_edge_click(token, int(edge_id))

        elif msg_type == "reverseEdge":
            edge_id = msg.get("edgeId")
            cost = float(msg.get("cost", 1.0))
            if edge_id is not None:
                success = bot_game_engine.handle_reverse_edge(token, int(edge_id), cost)
                if not success:
                    # Derive a more specific error message for bot game
                    error_message = "Can't reverse this pipe!"
                    try:
                        if bot_game_engine.state:
                            player_id = bot_game_engine.get_player_id(token)
                            edge = bot_game_engine.state.edges.get(int(edge_id)) if player_id is not None else None
                            if edge:
                                source_node = bot_game_engine.state.nodes.get(edge.source_node_id)
                                target_node = bot_game_engine.state.nodes.get(edge.target_node_id)
                                # Opponent control check
                                if source_node and source_node.owner is not None and source_node.owner != player_id:
                                    error_message = "Pipe controlled by opponent"
                                else:
                                    if source_node and target_node:
                                        actual_cost = bot_game_engine.calculate_bridge_cost(source_node, target_node)
                                        player_gold = bot_game_engine.state.player_gold.get(player_id, 0.0)
                                        if player_gold < actual_cost:
                                            error_message = "Not enough gold"
                    except Exception:
                        pass

                    await self._send_safe(websocket, json.dumps({"type": "reverseEdgeError", "message": error_message}))
                else:
                    # Send response for human player moves only (bot moves are handled by bot_player.py)
                    if not bot_game_manager.bot_player or token != bot_game_manager.bot_player.bot_token:
                        edge = bot_game_engine.state.edges.get(int(edge_id)) if bot_game_engine.state else None
                        if edge:
                            message = {
                                "type": "edgeReversed",
                                "edge": {
                                    "id": edge.id,
                                    "source": edge.source_node_id,
                                    "target": edge.target_node_id,
                                    "bidirectional": False,
                                    "forward": True,
                                    "on": edge.on,
                                    "flowing": edge.flowing,
                                },
                                "cost": cost,
                            }
                            await self._send_safe(websocket, json.dumps(message))

        elif msg_type == "buildBridge":
            from_node_id = msg.get("fromNodeId")
            to_node_id = msg.get("toNodeId")
            cost = float(msg.get("cost", 1.0))
            if from_node_id is not None and to_node_id is not None:
                success, new_edge, actual_cost, error_msg = bot_game_engine.handle_build_bridge(
                    token, int(from_node_id), int(to_node_id), cost
                )
                if not success:
                    await self._send_safe(
                        websocket,
                        json.dumps({"type": "bridgeError", "message": error_msg or "Failed to build bridge"}),
                    )
                elif new_edge:
                    message = {
                        "type": "newEdge",
                        "edge": {
                            "id": new_edge.id,
                            "source": new_edge.source_node_id,
                            "target": new_edge.target_node_id,
                            "bidirectional": False,
                            "forward": True,
                            "on": new_edge.on,
                            "flowing": new_edge.flowing,
                        },
                        "cost": actual_cost,
                    }
                    await self._send_safe(websocket, json.dumps(message))

        elif msg_type == "redirectEnergy":
            target_node_id = msg.get("targetNodeId")
            if target_node_id is not None:
                bot_game_engine.handle_redirect_energy(token, int(target_node_id))

        elif msg_type == "localTargeting":
            target_node_id = msg.get("targetNodeId")
            if target_node_id is not None:
                bot_game_engine.handle_local_targeting(token, int(target_node_id))

        elif msg_type == "destroyNode":
            node_id = msg.get("nodeId")
            cost = float(msg.get("cost", 3.0))
            if node_id is not None:
                success, error_msg = bot_game_engine.handle_destroy_node(token, int(node_id), cost)
                if not success:
                    await self._send_safe(
                        websocket,
                        json.dumps({"type": "destroyError", "message": error_msg or "Failed to destroy node"}),
                    )
                else:
                    await self._send_safe(websocket, json.dumps({"type": "nodeDestroyed", "nodeId": int(node_id)}))

        elif msg_type == "quitGame":
            winner_id = bot_game_engine.handle_quit_game(token)
            if winner_id is not None:
                await self._send_safe(websocket, json.dumps({"type": "gameOver", "winnerId": winner_id}))
                bot_game_manager.end_game()
                server_context.get("bot_game_clients", {}).pop(token, None)

        elif msg_type == "toggleAutoExpand":
            bot_game_engine.handle_toggle_auto_expand(token)

        elif msg_type == "requestInit":
            if bot_game_engine.state:
                message = bot_game_engine.state.to_init_message(
                    bot_game_engine.screen,
                    server_context.get("tick_interval", 0.1),
                    time.time(),
                )
                message["type"] = "init"
                message["myPlayerId"] = 1
                message["token"] = token
                await self._send_safe(websocket, json.dumps(message))

    # ------------------------------------------------------------------
    # Helper utilities
    # ------------------------------------------------------------------

    def _get_game_info(self, token: str, server_context: Dict[str, Any]) -> Optional[Dict[str, Any]]:
        _, game_info = self._get_game_info_with_id(token, server_context)
        return game_info

    def _get_game_info_with_id(
        self, token: str, server_context: Dict[str, Any]
    ) -> Tuple[Optional[str], Optional[Dict[str, Any]]]:
        if not token:
            return None, None
        token_to_game = server_context.get("token_to_game", {})
        game_id = token_to_game.get(token)
        if not game_id:
            return None, None
        games = server_context.get("games", {})
        game_info = games.get(game_id)
        return game_id, game_info

    async def _send_init_message(
        self,
        engine: GameEngine,
        game_info: Dict[str, Any],
        websocket: websockets.WebSocketServerProtocol,
        token: str,
        server_context: Dict[str, Any],
    ) -> None:
        if not engine.state:
            return
        message = engine.state.to_init_message(
            game_info.get("screen", {}),
            server_context.get("tick_interval", 0.1),
            time.time(),
        )
        message["type"] = "init"
        player_id = engine.token_to_player_id.get(token)
        if player_id is not None:
            message["myPlayerId"] = player_id
        message["token"] = token
        await self._send_safe(websocket, json.dumps(message))

    async def _broadcast_to_game(self, game_info: Dict[str, Any], message: Dict[str, Any]) -> None:
        payload = json.dumps(message)
        for token, websocket in list(game_info.get("clients", {}).items()):
            if not websocket:
                continue
            await self._send_safe(websocket, payload)

    async def _announce_winner(
        self,
        game_id: str,
        game_info: Dict[str, Any],
        winner_id: int,
        server_context: Dict[str, Any],
    ) -> None:
        payload = json.dumps({"type": "gameOver", "winnerId": winner_id})
        for websocket in list(game_info.get("clients", {}).values()):
            if websocket:
                await self._send_safe(websocket, payload)

        # Clean up mappings
        token_to_game = server_context.get("token_to_game", {})
        ws_to_token = server_context.get("ws_to_token", {})
        for token, websocket in list(game_info.get("clients", {}).items()):
            token_to_game.pop(token, None)
            if websocket:
                ws_to_token.pop(websocket, None)
        server_context.get("games", {}).pop(game_id, None)

    async def _send_safe(self, websocket: Optional[websockets.WebSocketServerProtocol], payload: str) -> None:
        if not websocket:
            return
        try:
            await websocket.send(payload)
        except Exception:
            pass
