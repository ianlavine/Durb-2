import json
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import websockets

from .constants import (
    DEFAULT_GAME_MODE,
    GAME_MODES,
    MAX_FRIEND_PLAYERS,
    MIN_FRIEND_PLAYERS,
    PLAYER_COLOR_SCHEMES,
    TICK_INTERVAL_SECONDS,
    normalize_game_mode,
)
from .game_engine import GameEngine
from .bot_manager import bot_game_manager
from .replay import GameReplayRecorder
from .replay_session import ReplayLoadError, ReplaySession


def _clean_guest_name(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    # Strip non-printable characters
    text = "".join(ch for ch in text if ch.isprintable())
    text = text.strip()
    if not text:
        return ""
    # Collapse consecutive whitespace to single spaces
    text = " ".join(text.split())
    max_length = 24
    if len(text) > max_length:
        text = text[:max_length]
    return text


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
            "nukeNode": self.handle_nuke_node,
            "quitGame": self.handle_quit_game,
            "toggleAutoExpand": self.handle_toggle_auto_expand,
            "newGame": self.handle_new_game,
            "nodeCaptured": self.handle_node_captured_flush,
            # Postgame / rematch flow
            "postgameRematch": self.handle_postgame_rematch,
            "postgameQuit": self.handle_postgame_quit,
            "requestReplay": self.handle_request_replay,
            "startReplay": self.handle_start_replay,
            "stopReplay": self.handle_stop_replay,
            "setReplaySpeed": self.handle_set_replay_speed,
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
        token = msg.get("token") or uuid.uuid4().hex
        guest_name = _clean_guest_name(msg.get("guestName"))
        requested_mode = str(msg.get("mode") or DEFAULT_GAME_MODE).strip().lower()
        game_modes = set(GAME_MODES)
        mode = requested_mode if requested_mode in game_modes else DEFAULT_GAME_MODE

        lobbies: Dict[int, Dict[str, List[Dict[str, Any]]]] = server_context.setdefault("lobbies", {})
        lobby_modes = lobbies.setdefault(player_count, {m: [] for m in GAME_MODES})

        for mode_map in lobbies.values():
            for queue in mode_map.values():
                queue[:] = [entry for entry in queue if entry.get("websocket") is not websocket]

        lobby_queue = lobby_modes.setdefault(mode, [])

        lobby_queue.append(
            {
                "token": token,
                "websocket": websocket,
                "auto_expand": auto_expand,
                "joined_at": time.time(),
                "guest_name": guest_name,
                "mode": mode,
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
                    "guestName": guest_name,
                    "mode": mode,
                }
            )
        )

        # Start a game when enough players have queued for this lobby size
        filtered_queue: List[Dict[str, Any]] = []
        for entry in lobby_queue:
            ws = entry.get("websocket")
            if ws is None or getattr(ws, "closed", False):
                continue
            filtered_queue.append(entry)
        lobby_queue[:] = filtered_queue

        if len(lobby_queue) >= player_count:
            players = [lobby_queue.pop(0) for _ in range(player_count)]
            await self._start_friend_game(players, player_count, mode, server_context)

    async def handle_leave_lobby(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        """Explicitly remove a client from any lobby queues, similar to disconnect behavior."""
        # Remove this websocket from all lobby queues
        lobbies: Dict[int, Dict[str, List[Dict[str, Any]]]] = server_context.setdefault("lobbies", {})
        for mode_map in lobbies.values():
            for queue in mode_map.values():
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
        mode: str,
        server_context: Dict[str, Any],
    ) -> None:
        engine = GameEngine()
        color_pool = PLAYER_COLOR_SCHEMES[:player_count]

        sanitized_mode = mode if mode in GAME_MODES else DEFAULT_GAME_MODE

        player_slots: List[Dict[str, Any]] = []
        auto_expand_state: Dict[str, bool] = {}
        guest_names: Dict[str, str] = {}
        for idx, player in enumerate(players, start=1):
            color_info = color_pool[idx - 1]
            auto_expand = bool(player.get("auto_expand", False))
            guest_name = _clean_guest_name(player.get("guest_name"))
            if not guest_name:
                guest_name = f"Guest {idx}"
            player_slots.append(
                {
                    "player_id": idx,
                    "token": player["token"],
                    "color": color_info["color"],
                    "secondary_colors": color_info["secondary"],
                    "auto_expand": auto_expand,
                    "guest_name": guest_name,
                }
            )
            auto_expand_state[player["token"]] = auto_expand
            guest_names[player["token"]] = guest_name

        engine.start_game(player_slots, mode=sanitized_mode)
        game_id = uuid.uuid4().hex

        tick_interval = float(server_context.get("tick_interval", TICK_INTERVAL_SECONDS))
        replay_recorder = GameReplayRecorder(game_id, engine, tick_interval, player_slots)
        game_info = {
            "engine": engine,
            "clients": {},
            "screen": engine.screen,
            "player_count": player_count,
            "created_at": time.time(),
            "disconnect_deadlines": {},
            "replay_recorder": replay_recorder,
            "auto_expand_state": auto_expand_state,
            "guest_names": guest_names,
            "mode": sanitized_mode,
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

    def _record_game_event(
        self,
        game_info: Dict[str, Any],
        token: str,
        event_type: str,
        payload: Optional[Dict[str, Any]],
    ) -> None:
        recorder: Optional[GameReplayRecorder] = game_info.get("replay_recorder")
        engine: Optional[GameEngine] = game_info.get("engine")
        if not recorder or not engine:
            return
        try:
            recorder.record_event(token=token, event_type=event_type, payload=payload, engine=engine)
        except Exception as exc:  # pragma: no cover - defensive guard
            print(f"Replay recording failed for event {event_type}: {exc}")

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
        if success:
            self._record_game_event(
                game_info,
                token,
                "pickStartingNode",
                {"nodeId": int(node_id)},
            )

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
        success = engine.handle_edge_click(token, int(edge_id))
        if success:
            payload = {"edgeId": int(edge_id)}
            if engine.state:
                edge = engine.state.edges.get(int(edge_id))
                if edge:
                    payload["on"] = bool(edge.on)
            self._record_game_event(game_info, token, "toggleEdge", payload)

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

        edge_after = None
        if engine.state:
            edge = engine.state.edges.get(int(edge_id))
            if edge:
                edge_after = edge
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
                        "warp": {
                            "axis": edge.warp_axis,
                            "segments": [[sx, sy, ex, ey] for sx, sy, ex, ey in (edge.warp_segments or [])],
                        },
                        "warpAxis": edge.warp_axis,
                        "warpSegments": [[sx, sy, ex, ey] for sx, sy, ex, ey in (edge.warp_segments or [])],
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

        payload = {"edgeId": int(edge_id), "cost": actual_cost}
        if edge_after:
            payload["source"] = edge_after.source_node_id
            payload["target"] = edge_after.target_node_id
            payload["warpAxis"] = edge_after.warp_axis
            payload["warpSegments"] = [
                [sx, sy, ex, ey]
                for sx, sy, ex, ey in (edge_after.warp_segments or [])
            ]
        self._record_game_event(game_info, token, "reverseEdge", payload)

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
        warp_info = msg.get("warpInfo")
        if token is None or from_node_id is None or to_node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success, new_edge, actual_cost, error_msg, removed_edges = engine.handle_build_bridge(
            token, int(from_node_id), int(to_node_id), cost, warp_info=warp_info
        )

        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "bridgeError", "message": error_msg or "Failed to build bridge"}),
            )
            return

        if new_edge:
            # Send edge state update to all players (without cost)
            warp_payload = {
                "axis": new_edge.warp_axis,
                "segments": [[sx, sy, ex, ey] for sx, sy, ex, ey in (new_edge.warp_segments or [])],
            }

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
                    "warp": warp_payload,
                    "warpAxis": warp_payload["axis"],
                    "warpSegments": warp_payload["segments"],
                },
            }
            if removed_edges:
                edge_update_message["removedEdges"] = removed_edges
            
            # Send to all players, but include cost only for the acting player
            for token_key, client_websocket in game_info.get("clients", {}).items():
                if not client_websocket:
                    continue
                
                message_to_send = edge_update_message.copy()
                # Only include cost for the player who built the bridge
                if token_key == token:
                    message_to_send["cost"] = actual_cost
                
                await self._send_safe(client_websocket, json.dumps(message_to_send))

        event_payload = {
            "fromNodeId": int(from_node_id),
            "toNodeId": int(to_node_id),
            "cost": actual_cost,
        }
        if new_edge:
            event_payload["edgeId"] = new_edge.id
            event_payload["warpAxis"] = new_edge.warp_axis
            event_payload["warpSegments"] = [
                [sx, sy, ex, ey]
                for sx, sy, ex, ey in (new_edge.warp_segments or [])
            ]
        if removed_edges:
            event_payload["removedEdges"] = removed_edges
        self._record_game_event(game_info, token, "buildBridge", event_payload)

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

        if success:
            self._record_game_event(
                game_info,
                token,
                "redirectEnergy",
                {"targetNodeId": int(target_node_id)},
            )

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

        if success:
            self._record_game_event(
                game_info,
                token,
                "localTargeting",
                {"targetNodeId": int(target_node_id)},
            )

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
        success, error_msg, removal_info = engine.handle_destroy_node(token, int(node_id), cost)
        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "destroyError", "message": error_msg or "Failed to destroy node"}),
            )
            return

        player_id = engine.get_player_id(token)
        removal_payload: Dict[str, Any] = {
            "type": "nodeDestroyed",
            "nodeId": int(node_id),
            "playerId": player_id,
            "removedEdges": removal_info.get("removedEdges", []) if removal_info else [],
            "reason": "destroy",
            "cost": cost,
        }
        if removal_info and removal_info.get("node"):
            removal_payload["nodeSnapshot"] = removal_info.get("node")

        await self._broadcast_to_game(game_info, removal_payload)

        self._record_game_event(
            game_info,
            token,
            "destroyNode",
            {
                "nodeId": int(node_id),
                "removedEdges": removal_info.get("removedEdges", []) if removal_info else [],
                "cost": cost,
            },
        )

    async def handle_nuke_node(
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
        success, error_msg, removal_info = engine.handle_nuke_node(token, int(node_id))
        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "nukeError", "message": error_msg or "Can't nuke this node"}),
            )
            return

        player_id = engine.get_player_id(token)
        removal_payload: Dict[str, Any] = {
            "type": "nodeDestroyed",
            "nodeId": int(node_id),
            "playerId": player_id,
            "removedEdges": removal_info.get("removedEdges", []) if removal_info else [],
            "reason": "nuke",
            "cost": 0,
        }
        if removal_info and removal_info.get("node"):
            removal_payload["nodeSnapshot"] = removal_info.get("node")

        await self._broadcast_to_game(game_info, removal_payload)

        self._record_game_event(
            game_info,
            token,
            "nukeNode",
            {
                "nodeId": int(node_id),
                "removedEdges": removal_info.get("removedEdges", []) if removal_info else [],
            },
        )

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

        self._record_game_event(game_info, token, "quitGame", None)
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
        success = engine.handle_toggle_auto_expand(token)
        if success:
            enabled = False
            player_id = engine.get_player_id(token)
            if engine.state and player_id is not None:
                enabled = bool(engine.state.player_auto_expand.get(player_id, False))
            game_info.setdefault("auto_expand_state", {})[token] = enabled
            self._record_game_event(
                game_info,
                token,
                "toggleAutoExpand",
                {"enabled": enabled},
            )

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
            server_context.get("tick_interval", TICK_INTERVAL_SECONDS),
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
        mode = normalize_game_mode(msg.get("mode", DEFAULT_GAME_MODE))

        success, error_msg = bot_game_manager.start_bot_game(
            token,
            difficulty,
            auto_expand,
            mode=mode,
        )
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
                server_context.get("tick_interval", TICK_INTERVAL_SECONDS),
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
                            actual_cost = cost
                            if bot_game_engine.state and getattr(bot_game_engine.state, "pending_edge_reversal", None):
                                actual_cost = bot_game_engine.state.pending_edge_reversal.get("cost", cost)
                            warp_segments = [
                                [sx, sy, ex, ey]
                                for sx, sy, ex, ey in (edge.warp_segments or [])
                            ]
                            warp_payload = {
                                "axis": edge.warp_axis,
                                "segments": warp_segments,
                            }
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
                                    "warp": warp_payload,
                                    "warpAxis": warp_payload["axis"],
                                    "warpSegments": warp_segments,
                                },
                                "cost": actual_cost,
                            }
                            await self._send_safe(websocket, json.dumps(message))

                        if bot_game_engine.state and hasattr(bot_game_engine.state, "pending_edge_reversal"):
                            bot_game_engine.state.pending_edge_reversal = None

        elif msg_type == "buildBridge":
            from_node_id = msg.get("fromNodeId")
            to_node_id = msg.get("toNodeId")
            cost = float(msg.get("cost", 1.0))
            warp_info = msg.get("warpInfo")
            if from_node_id is not None and to_node_id is not None:
                success, new_edge, actual_cost, error_msg, removed_edges = bot_game_engine.handle_build_bridge(
                    token, int(from_node_id), int(to_node_id), cost, warp_info=warp_info
                )
                if not success:
                    await self._send_safe(
                        websocket,
                        json.dumps({"type": "bridgeError", "message": error_msg or "Failed to build bridge"}),
                    )
                elif new_edge:
                    warp_payload = {
                        "axis": new_edge.warp_axis,
                        "segments": [[sx, sy, ex, ey] for sx, sy, ex, ey in (new_edge.warp_segments or [])],
                    }
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
                            "warp": warp_payload,
                            "warpAxis": warp_payload["axis"],
                            "warpSegments": warp_payload["segments"],
                        },
                        "cost": actual_cost,
                    }
                    if removed_edges:
                        message["removedEdges"] = removed_edges
                    await self._send_safe(websocket, json.dumps(message))

        elif msg_type == "redirectEnergy":
            target_node_id = msg.get("targetNodeId")
            if target_node_id is not None:
                bot_game_engine.handle_redirect_energy(token, int(target_node_id))

        elif msg_type == "localTargeting":
            target_node_id = msg.get("targetNodeId")
            if target_node_id is not None:
                bot_game_engine.handle_local_targeting(token, int(target_node_id))

        elif msg_type == "nukeNode":
            node_id = msg.get("nodeId")
            if node_id is not None:
                success, error_msg, removal_info = bot_game_engine.handle_nuke_node(token, int(node_id))
                if not success:
                    await self._send_safe(
                        websocket,
                        json.dumps({"type": "nukeError", "message": error_msg or "Can't nuke this node"}),
                    )
                else:
                    player_id = bot_game_engine.get_player_id(token)
                    payload: Dict[str, Any] = {
                        "type": "nodeDestroyed",
                        "nodeId": int(node_id),
                        "playerId": player_id,
                        "removedEdges": removal_info.get("removedEdges", []) if removal_info else [],
                        "reason": "nuke",
                        "cost": 0,
                    }
                    if removal_info and removal_info.get("node"):
                        payload["nodeSnapshot"] = removal_info.get("node")

                    await self._send_safe(websocket, json.dumps(payload))

        elif msg_type == "destroyNode":
            node_id = msg.get("nodeId")
            cost = float(msg.get("cost", 3.0))
            if node_id is not None:
                success, error_msg, _ = bot_game_engine.handle_destroy_node(token, int(node_id), cost)
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
                    server_context.get("tick_interval", TICK_INTERVAL_SECONDS),
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
            server_context.get("tick_interval", TICK_INTERVAL_SECONDS),
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
        # Announce game over
        payload = json.dumps({"type": "gameOver", "winnerId": winner_id})
        for websocket in list(game_info.get("clients", {}).values()):
            if websocket:
                await self._send_safe(websocket, payload)

        # Establish a postgame group for rematch handling with the same players
        try:
            import uuid  # local import to avoid top-level circulars
            postgame_groups = server_context.setdefault("postgame_groups", {})

            engine: Optional[GameEngine] = game_info.get("engine")
            replay_bundle: Optional[Dict[str, Any]] = None
            recorder: Optional[GameReplayRecorder] = game_info.get("replay_recorder")
            if engine and recorder:
                try:
                    replay_bundle = recorder.build_package(engine, winner_id)
                except Exception as exc:
                    print(f"Failed to finalize replay for game {game_id}: {exc}")

            # Gather players (tokens) and their websockets in original join order
            clients = game_info.get("clients", {})
            tokens = list(clients.keys())

            auto_expand_map: Dict[str, bool] = dict(game_info.get("auto_expand_state", {}))
            if not auto_expand_map and engine and engine.state:
                for player_id, enabled in getattr(engine.state, "player_auto_expand", {}).items():
                    token = engine.player_id_to_token.get(player_id)
                    if token:
                        auto_expand_map[token] = bool(enabled)

            guest_name_map: Dict[str, str] = {}
            raw_guest_names = game_info.get("guest_names", {})
            if isinstance(raw_guest_names, dict):
                guest_name_map = {str(tok): str(name) for tok, name in raw_guest_names.items()}

            if not guest_name_map and engine:
                for player_id, meta in getattr(engine, "player_meta", {}).items():
                    token = engine.player_id_to_token.get(player_id)
                    if token:
                        guest_name_map[token] = str(meta.get("guest_name", ""))

            group_id = uuid.uuid4().hex
            postgame_groups[group_id] = {
                "tokens": tokens,
                "clients": dict(clients),  # token -> websocket (may contain None)
                "created_at": time.time(),
                "rematch_votes": set(),
                "replay_data": replay_bundle,
                "auto_expand": auto_expand_map,
                "guest_names": guest_name_map,
                "mode": game_info.get("mode", DEFAULT_GAME_MODE),
            }

            # Notify clients that postgame rematch is available
            post_payload = json.dumps({"type": "postgame", "groupId": group_id})
            for websocket in list(clients.values()):
                if websocket:
                    await self._send_safe(websocket, post_payload)
        except Exception:
            # If anything goes wrong, proceed with normal cleanup
            pass

        # Clean up game registry but keep ws->token so we can detect postgame disconnects
        token_to_game = server_context.get("token_to_game", {})
        for token in list(game_info.get("clients", {}).keys()):
            token_to_game.pop(token, None)
        server_context.get("games", {}).pop(game_id, None)

    async def handle_postgame_rematch(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        group_id = msg.get("groupId")
        token = msg.get("token")
        if not group_id or not token:
            return

        groups: Dict[str, Dict[str, Any]] = server_context.setdefault("postgame_groups", {})
        group = groups.get(group_id)
        if not group:
            return

        if token not in group.get("tokens", []):
            return

        # Record vote
        group.setdefault("rematch_votes", set()).add(token)

        # Broadcast readiness update (optional UX)
        update_payload = json.dumps({
            "type": "postgameRematchUpdate",
            "groupId": group_id,
            "ready": list(group.get("rematch_votes", set())),
        })
        for tok, ws in list(group.get("clients", {}).items()):
            if ws:
                await self._send_safe(ws, update_payload)

        # If everyone voted, start a new game with the same set of tokens
        tokens = list(group.get("tokens", []))
        votes = group.get("rematch_votes", set())
        if len(tokens) > 0 and len(votes) == len(tokens):
            # Build players array for _start_friend_game
            mode = str(group.get("mode", DEFAULT_GAME_MODE))
            players = []
            for t in tokens:
                players.append({
                    "token": t,
                    "websocket": group.get("clients", {}).get(t),
                    "auto_expand": bool(group.get("auto_expand", {}).get(t, False)),
                    "guest_name": group.get("guest_names", {}).get(t),
                    "mode": mode,
                })
            # Remove group before starting to avoid reentrancy issues
            groups.pop(group_id, None)
            await self._start_friend_game(players, len(players), mode, server_context)

    async def handle_postgame_quit(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        group_id = msg.get("groupId")
        token = msg.get("token")
        if not group_id or not token:
            return

        groups: Dict[str, Dict[str, Any]] = server_context.setdefault("postgame_groups", {})
        group = groups.get(group_id)
        if not group:
            return

        clients = group.setdefault("clients", {})
        tokens = group.setdefault("tokens", [])
        auto_expand_map = group.setdefault("auto_expand", {})

        # Notify remaining participants that an opponent has left
        notice = json.dumps({"type": "postgameOpponentLeft"})
        for tok, ws in list(clients.items()):
            if tok == token:
                continue
            if ws:
                await self._send_safe(ws, notice)

        clients.pop(token, None)
        if token in tokens:
            tokens.remove(token)
        auto_expand_map.pop(token, None)

        if not tokens:
            groups.pop(group_id, None)

    async def handle_request_replay(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        group_id = msg.get("groupId")
        token = msg.get("token")
        if not group_id or not token:
            return

        groups: Dict[str, Dict[str, Any]] = server_context.setdefault("postgame_groups", {})
        group = groups.get(group_id)
        if not group or token not in group.get("tokens", []):
            await self._send_safe(
                websocket,
                json.dumps({"type": "replayError", "message": "Replay unavailable"}),
            )
            return

        replay_bundle = group.get("replay_data")
        if not replay_bundle:
            await self._send_safe(
                websocket,
                json.dumps({"type": "replayError", "message": "Replay not ready"}),
            )
            return

        response = json.dumps(
            {
                "type": "replayData",
                "filename": replay_bundle.get("filename"),
                "replay": replay_bundle.get("replay"),
            }
        )
        await self._send_safe(websocket, response)

    async def handle_start_replay(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        replay_payload = msg.get("replay")
        if not isinstance(replay_payload, dict):
            await self._send_safe(
                websocket,
                json.dumps({"type": "replayError", "message": "Invalid replay data", "replay": True}),
            )
            return

        sessions: Dict[websockets.WebSocketServerProtocol, ReplaySession] = server_context.setdefault(
            "replay_sessions", {}
        )

        existing = sessions.pop(websocket, None)
        if existing:
            await existing.stop()

        tick_interval = float(server_context.get("tick_interval", TICK_INTERVAL_SECONDS))

        def _on_complete() -> None:
            stored = server_context.get("replay_sessions", {})
            if stored.get(websocket) is session:
                stored.pop(websocket, None)

        try:
            session = ReplaySession(
                websocket=websocket,
                replay=replay_payload,
                message_router=self,
                tick_interval_default=tick_interval,
                on_complete=_on_complete,
            )
        except ReplayLoadError as exc:
            await self._send_safe(
                websocket,
                json.dumps({"type": "replayError", "message": str(exc), "replay": True}),
            )
            return

        sessions[websocket] = session
        session.start()
        await self._send_safe(websocket, json.dumps({"type": "replayStarting", "replay": True}))

    async def handle_stop_replay(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        sessions: Dict[websockets.WebSocketServerProtocol, ReplaySession] = server_context.setdefault(
            "replay_sessions", {}
        )
        session = sessions.pop(websocket, None)
        if session:
            await session.stop()
        await self._send_safe(websocket, json.dumps({"type": "replayStopped", "replay": True}))

    async def handle_set_replay_speed(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        multiplier = msg.get("multiplier")
        try:
            multiplier_val = float(multiplier)
        except (TypeError, ValueError):
            multiplier_val = 1.0

        sessions: Dict[websockets.WebSocketServerProtocol, ReplaySession] = server_context.setdefault(
            "replay_sessions", {}
        )
        session = sessions.get(websocket)
        if session:
            session.set_speed(multiplier_val)

    async def _send_safe(self, websocket: Optional[websockets.WebSocketServerProtocol], payload: str) -> None:
        if not websocket:
            return
        try:
            await websocket.send(payload)
        except Exception:
            pass
