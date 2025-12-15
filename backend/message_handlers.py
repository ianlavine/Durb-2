import copy
import json
import math
import time
import uuid
from typing import Any, Dict, List, Optional, Tuple

import websockets

from .constants import (
    DEFAULT_GEM_COUNTS,
    DEFAULT_GAME_MODE,
    GAME_MODES,
    MAX_FRIEND_PLAYERS,
    MIN_FRIEND_PLAYERS,
    NODE_MAX_JUICE,
    PLAYER_COLOR_SCHEMES,
    PRODUCTION_RATE_PER_NODE,
    RESERVE_TRANSFER_RATIO,
    INTAKE_TRANSFER_RATIO,
    TICK_INTERVAL_SECONDS,
    UNOWNED_NODE_BASE_JUICE,
    KING_CROWN_MAX_HEALTH,
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
            "crownSmash": self.handle_crown_smash,
            "kingRequestMoves": self.handle_king_request_moves,
            "kingMove": self.handle_king_move,
            "quitGame": self.handle_quit_game,
            "toggleAutoExpand": self.handle_toggle_auto_expand,
            "toggleAutoAttack": self.handle_toggle_auto_attack,
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

        if msg_type == "sandboxReset":
            await self.handle_sandbox_reset(websocket, msg, server_context)
            return

        # Bot game messages are handled separately (except startBotGame/sandboxReset)
        if msg_type not in {"startBotGame", "sandboxReset"} and bot_game_manager.game_active:
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
        auto_attack = bool(msg.get("autoAttack", False))
        token = msg.get("token") or uuid.uuid4().hex
        guest_name = _clean_guest_name(msg.get("guestName"))
        requested_mode = str(msg.get("mode") or DEFAULT_GAME_MODE).strip().lower()
        game_modes = set(GAME_MODES)
        mode = requested_mode if requested_mode in game_modes else DEFAULT_GAME_MODE
        mode_settings = self._sanitize_mode_settings(msg.get("settings"))

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
                "auto_attack": auto_attack,
                "joined_at": time.time(),
                "guest_name": guest_name,
                "mode": mode,
                "settings": dict(mode_settings),
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
                    "modeSettings": mode_settings,
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
            host_settings = dict(players[0].get("settings", {})) if players else {}
            await self._start_friend_game(players, player_count, mode, server_context, host_settings)

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

    def _sanitize_mode_settings(self, payload: Any) -> Dict[str, Any]:
        settings: Dict[str, Any] = {
            "screen": "warp",
            "brass": "right-click",
            "brassStart": "owned",
            "breakMode": "flowing",
            "bridgeCost": 1.0,
            "gameStart": "open",
            "passiveIncome": 1.0,
            "neutralCaptureGold": 5.0,
            "ringJuiceToGoldRatio": 10.0,
            "ringPayoutGold": 2.0,
            "warpGemCount": DEFAULT_GEM_COUNTS.get("warp", 3),
            "brassGemCount": DEFAULT_GEM_COUNTS.get("brass", 7),
            "rageGemCount": DEFAULT_GEM_COUNTS.get("rage", 4),
            "reverseGemCount": DEFAULT_GEM_COUNTS.get("reverse", 6),
            "startingNodeJuice": 300.0,
            "winCondition": "king",
            "kingCrownHealth": KING_CROWN_MAX_HEALTH,
            "resources": "standard",
            "lonelyNode": "sinks",
            "nodeGrowthRate": PRODUCTION_RATE_PER_NODE,
            "startingFlowRate": RESERVE_TRANSFER_RATIO,
            "secondaryFlowRate": INTAKE_TRANSFER_RATIO,
        }
        if not isinstance(payload, dict):
            settings["pipeStart"] = settings["brassStart"]
            return settings

        def sanitize_gem_count(raw_value: Any, fallback: float) -> int:
            if isinstance(raw_value, str):
                raw_value = raw_value.strip()
            try:
                parsed = float(raw_value)
            except (TypeError, ValueError):
                return int(round(fallback))
            if math.isnan(parsed):
                return int(round(fallback))
            clamped = max(0.0, min(10.0, parsed))
            return int(round(clamped))

        screen_option = str(payload.get("screen", settings["screen"])).strip().lower()
        if screen_option in {"warp", "semi", "flat"}:
            settings["screen"] = screen_option

        brass_option = str(payload.get("brass", settings["brass"])).strip().lower()
        if brass_option in {"cross", "right-click", "rightclick", "right_click"}:
            settings["brass"] = "right-click" if brass_option.startswith("right") else "cross"

        brass_start_option = payload.get("pipeStart", payload.get("brassStart", settings["brassStart"]))
        brass_start_option = str(brass_start_option).strip().lower()
        if brass_start_option in {"owned", "anywhere"}:
            settings["brassStart"] = "anywhere" if brass_start_option == "anywhere" else "owned"

        break_mode_option = payload.get("breakMode", payload.get("pipeBreakMode", payload.get("break", settings["breakMode"])))
        if isinstance(break_mode_option, str):
            normalized_break = break_mode_option.strip().lower()
            if normalized_break in {"any", "flowing", "double"}:
                settings["breakMode"] = normalized_break
            else:
                settings["breakMode"] = "brass"
        else:
            settings["breakMode"] = "brass"

        game_start_option = str(payload.get("gameStart", settings["gameStart"])).strip().lower()
        if game_start_option in {"open", "hidden", "hidden-split", "hidden_split", "hidden split"}:
            settings["gameStart"] = "hidden-split" if game_start_option.startswith("hidden") else "open"

        bridge_cost_value = payload.get("bridgeCost", settings["bridgeCost"])
        if isinstance(bridge_cost_value, str):
            bridge_cost_value = bridge_cost_value.strip()
        try:
            parsed_cost = float(bridge_cost_value)
        except (TypeError, ValueError):
            parsed_cost = None
        if parsed_cost is not None and parsed_cost > 0:
            clamped = max(0.5, min(1.0, parsed_cost))
            settings["bridgeCost"] = round(clamped, 1)

        base_mode = payload.get("baseMode")
        if isinstance(base_mode, str):
            settings["baseMode"] = base_mode.strip()

        derived_mode = payload.get("derivedMode")
        if isinstance(derived_mode, str):
            settings["derivedMode"] = derived_mode.strip()

        passive_value = payload.get("passiveIncome", settings["passiveIncome"])
        if isinstance(passive_value, str):
            passive_value = passive_value.strip()
        try:
            parsed_passive = float(passive_value)
        except (TypeError, ValueError):
            parsed_passive = None
        if parsed_passive is not None and not math.isnan(parsed_passive) and parsed_passive >= 0:
            snapped = round(parsed_passive * 20.0) / 20.0
            settings["passiveIncome"] = round(max(0.0, min(2.0, snapped)), 2)

        neutral_value = payload.get("neutralCaptureGold", settings["neutralCaptureGold"])
        if isinstance(neutral_value, str):
            neutral_value = neutral_value.strip()
        try:
            parsed_neutral = float(neutral_value)
        except (TypeError, ValueError):
            parsed_neutral = None
        if parsed_neutral is not None and parsed_neutral >= 0:
            settings["neutralCaptureGold"] = max(0.0, min(20.0, round(parsed_neutral, 3)))

        ratio_value = payload.get("ringJuiceToGoldRatio", settings["ringJuiceToGoldRatio"])
        if isinstance(ratio_value, str):
            ratio_value = ratio_value.strip()
        try:
            parsed_ratio = float(ratio_value)
        except (TypeError, ValueError):
            parsed_ratio = None
        if parsed_ratio is not None and parsed_ratio > 0:
            settings["ringJuiceToGoldRatio"] = max(5.0, min(500.0, round(parsed_ratio, 4)))

        payout_value = payload.get("ringPayoutGold", settings["ringPayoutGold"])
        if isinstance(payout_value, str):
            payout_value = payout_value.strip()
        try:
            parsed_payout = float(payout_value)
        except (TypeError, ValueError):
            parsed_payout = None
        if parsed_payout is not None and parsed_payout > 0:
            settings["ringPayoutGold"] = max(1.0, min(500.0, round(parsed_payout, 4)))

        start_juice_value = payload.get("startingNodeJuice", settings["startingNodeJuice"])
        if isinstance(start_juice_value, str):
            start_juice_value = start_juice_value.strip()
        try:
            parsed_start = float(start_juice_value)
        except (TypeError, ValueError):
            parsed_start = None
        if parsed_start is not None:
            clamped_start = max(UNOWNED_NODE_BASE_JUICE, min(NODE_MAX_JUICE, parsed_start))
            snapped = round(clamped_start / 10.0) * 10.0
            settings["startingNodeJuice"] = max(UNOWNED_NODE_BASE_JUICE, min(NODE_MAX_JUICE, snapped))

        growth_value = payload.get("nodeGrowthRate", settings["nodeGrowthRate"])
        if isinstance(growth_value, str):
            growth_value = growth_value.strip()
        try:
            parsed_growth = float(growth_value)
        except (TypeError, ValueError):
            parsed_growth = None
        if parsed_growth is not None and not math.isnan(parsed_growth):
            settings["nodeGrowthRate"] = max(0.0, min(1.0, round(parsed_growth, 4)))

        starting_flow_value = payload.get("startingFlowRate", settings["startingFlowRate"])
        if isinstance(starting_flow_value, str):
            starting_flow_value = starting_flow_value.strip()
        try:
            parsed_starting_flow = float(starting_flow_value)
        except (TypeError, ValueError):
            parsed_starting_flow = None
        if parsed_starting_flow is not None and not math.isnan(parsed_starting_flow):
            settings["startingFlowRate"] = max(0.0, min(1.0, round(parsed_starting_flow, 4)))

        secondary_flow_value = payload.get("secondaryFlowRate", settings["secondaryFlowRate"])
        if isinstance(secondary_flow_value, str):
            secondary_flow_value = secondary_flow_value.strip()
        try:
            parsed_secondary_flow = float(secondary_flow_value)
        except (TypeError, ValueError):
            parsed_secondary_flow = None
        if parsed_secondary_flow is not None and not math.isnan(parsed_secondary_flow):
            settings["secondaryFlowRate"] = max(0.0, min(1.0, round(parsed_secondary_flow, 4)))

        crown_health_value = payload.get("kingCrownHealth", settings["kingCrownHealth"])
        if isinstance(crown_health_value, str):
            crown_health_value = crown_health_value.strip()
        try:
            parsed_crown = float(crown_health_value)
        except (TypeError, ValueError):
            parsed_crown = None
        if parsed_crown is not None and parsed_crown > 0:
            settings["kingCrownHealth"] = max(1.0, min(300.0, round(parsed_crown, 3)))

        gem_field_map = (
            ("warpGemCount", "warp"),
            ("brassGemCount", "brass"),
            ("rageGemCount", "rage"),
            ("reverseGemCount", "reverse"),
        )
        for field_name, gem_key in gem_field_map:
            current_value = settings[field_name]
            if field_name in payload:
                settings[field_name] = sanitize_gem_count(payload.get(field_name), current_value)
            else:
                settings[field_name] = int(round(current_value))

        win_condition_value = payload.get("winCondition", settings.get("winCondition"))
        if isinstance(win_condition_value, str) and win_condition_value.strip().lower() == "king":
            settings["winCondition"] = "king"
        else:
            settings["winCondition"] = "dominate"

        resources_value = payload.get("resources", settings["resources"])
        if isinstance(resources_value, str):
            normalized_resources = resources_value.strip().lower()
            if normalized_resources == "gems":
                settings["resources"] = "gems"
            elif normalized_resources == "durbium":
                settings["resources"] = "durbium"
            else:
                settings["resources"] = "standard"

        lonely_value = payload.get("lonelyNode", settings["lonelyNode"])
        if isinstance(lonely_value, str) and lonely_value.strip().lower() == "sinks":
            settings["lonelyNode"] = "sinks"
        else:
            settings["lonelyNode"] = "nothing"

        settings["pipeStart"] = settings["brassStart"]

        return settings

    def _derive_mode_from_settings(self, settings: Dict[str, Any], fallback_mode: str) -> str:
        fallback_normalized = normalize_game_mode(fallback_mode)

        derived_mode = settings.get("derivedMode")
        if isinstance(derived_mode, str):
            normalized = normalize_game_mode(derived_mode)
            if normalized in {"flat", "warp", "semi", "i-flat", "i-warp", "i-semi", "basic", "sandbox"}:
                return normalized

        screen_variant = str(settings.get("screen", "flat")).strip().lower()
        brass_variant = str(settings.get("brass", "cross")).strip().lower()

        if screen_variant == "warp":
            return "i-warp" if brass_variant.startswith("right") else "warp"
        if screen_variant == "semi":
            return "i-semi" if brass_variant.startswith("right") else "semi"
        if screen_variant == "flat":
            return "i-flat" if brass_variant.startswith("right") else "flat"

        if fallback_normalized == "basic":
            return "basic"

        return fallback_normalized

    def _build_lonely_sink_payload(self, sink_data: Any) -> Optional[Dict[str, Any]]:
        if not isinstance(sink_data, dict):
            return None
        node_snapshot = sink_data.get("node")
        node_id = sink_data.get("nodeId")
        if node_id is None and isinstance(node_snapshot, dict):
            node_id = node_snapshot.get("id")
        try:
            node_int = int(node_id)
        except (TypeError, ValueError):
            return None
        payload = {
            "type": "nodeDestroyed",
            "nodeId": node_int,
            "playerId": sink_data.get("playerId"),
            "removedEdges": sink_data.get("removedEdges", []),
            "reason": "lonely",
            "sinking": True,
            "cost": 0,
        }
        if isinstance(node_snapshot, dict):
            payload["nodeSnapshot"] = node_snapshot
        if sink_data.get("kingGraveOwnerId") is not None:
            try:
                payload["kingGraveOwnerId"] = int(sink_data.get("kingGraveOwnerId"))
            except (TypeError, ValueError):
                pass
        return payload

    async def _start_friend_game(
        self,
        players: List[Dict[str, Any]],
        player_count: int,
        mode: str,
        server_context: Dict[str, Any],
        host_settings: Optional[Dict[str, Any]] = None,
    ) -> None:
        engine = GameEngine()
        color_pool = PLAYER_COLOR_SCHEMES[:player_count]

        sanitized_mode = mode if mode in GAME_MODES else DEFAULT_GAME_MODE
        resolved_host_settings = dict(host_settings or {})
        if not resolved_host_settings and players:
            resolved_host_settings = dict(players[0].get("settings", {}))

        actual_mode = self._derive_mode_from_settings(resolved_host_settings, sanitized_mode)
        resolved_host_settings["derivedMode"] = actual_mode

        game_start_option = str(resolved_host_settings.get("gameStart", "open")).strip().lower()
        if player_count != 2:
            resolved_host_settings["gameStart"] = "open"
        elif game_start_option.startswith("hidden"):
            resolved_host_settings["gameStart"] = "hidden-split"
        else:
            resolved_host_settings["gameStart"] = "open"

        player_slots: List[Dict[str, Any]] = []
        auto_expand_state: Dict[str, bool] = {}
        auto_attack_state: Dict[str, bool] = {}
        guest_names: Dict[str, str] = {}
        for idx, player in enumerate(players, start=1):
            color_info = color_pool[idx - 1]
            auto_expand = bool(player.get("auto_expand", False))
            auto_attack = bool(player.get("auto_attack", False))
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
                    "auto_attack": auto_attack,
                    "guest_name": guest_name,
                }
            )
            auto_expand_state[player["token"]] = auto_expand
            auto_attack_state[player["token"]] = auto_attack
            guest_names[player["token"]] = guest_name

        engine.start_game(player_slots, mode=actual_mode, options=resolved_host_settings)
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
            "auto_attack_state": auto_attack_state,
            "guest_names": guest_names,
            "mode": actual_mode,
            "mode_settings": dict(engine.state.mode_settings or {}),
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
        except Exception:  # pragma: no cover - defensive guard
            pass

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
                        "rewardType": capture_data.get("rewardType"),
                        "rewardKey": capture_data.get("rewardKey"),
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
        if token is None or edge_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success = engine.handle_reverse_edge(token, int(edge_id))
        if not success:
            error_message = "Can't reverse this pipe!"
            try:
                if engine.state:
                    player_id = engine.get_player_id(token)
                    edge = engine.state.edges.get(int(edge_id)) if player_id is not None else None
                    if edge:
                        if getattr(edge, "pipe_type", "normal") != "reverse":
                            error_message = "Pipe is not reversible"
                        else:
                            source_node = engine.state.nodes.get(edge.source_node_id)
                            if source_node and source_node.owner is not None and source_node.owner != player_id:
                                error_message = "Pipe controlled by opponent"
            except Exception:
                pass

            await self._send_safe(websocket, json.dumps({"type": "reverseEdgeError", "message": error_message}))
            return

        edge_after = None
        if engine.state:
            edge = engine.state.edges.get(int(edge_id))
            if edge:
                edge_after = edge
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
                        "pipeType": getattr(edge, "pipe_type", "normal"),
                        "warp": {
                            "axis": edge.warp_axis,
                            "segments": [[sx, sy, ex, ey] for sx, sy, ex, ey in (edge.warp_segments or [])],
                        },
                        "warpAxis": edge.warp_axis,
                        "warpSegments": [[sx, sy, ex, ey] for sx, sy, ex, ey in (edge.warp_segments or [])],
                    }
                }

                for token_key, client_websocket in game_info.get("clients", {}).items():
                    if not client_websocket:
                        continue
                    await self._send_safe(client_websocket, json.dumps(edge_update_message))

        payload = {"edgeId": int(edge_id)}
        if edge_after:
            payload["source"] = edge_after.source_node_id
            payload["target"] = edge_after.target_node_id
            payload["warpAxis"] = edge_after.warp_axis
            payload["warpSegments"] = [
                [sx, sy, ex, ey]
                for sx, sy, ex, ey in (edge_after.warp_segments or [])
            ]
            payload["pipeType"] = getattr(edge_after, "pipe_type", "normal")
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
        pipe_type = msg.get("pipeType")
        if token is None or from_node_id is None or to_node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success, new_edge, actual_cost, error_msg, removed_edges, node_movements = engine.handle_build_bridge(
            token,
            int(from_node_id),
            int(to_node_id),
            cost,
            warp_info=warp_info,
            pipe_type=pipe_type if isinstance(pipe_type, str) else "normal",
        )

        movement_payloads: List[Dict[str, float]] = []
        movement_arrays: List[List[float]] = []

        if node_movements:
            for movement in node_movements:
                if not isinstance(movement, dict):
                    continue
                node_id = movement.get("nodeId")
                x = movement.get("x")
                y = movement.get("y")
                try:
                    node_int = int(node_id)
                    x_val = round(float(x), 3)
                    y_val = round(float(y), 3)
                except (TypeError, ValueError):
                    continue
                movement_payloads.append({"nodeId": node_int, "x": x_val, "y": y_val})
                movement_arrays.append([node_int, x_val, y_val])

        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "bridgeError", "message": error_msg or "Failed to build bridge"}),
            )
            return

        if engine.state:
            try:
                gem_counts_payload = engine.state._serialize_gem_counts()
            except Exception:
                gem_counts_payload = []
        else:
            gem_counts_payload = []

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
                    "pipeType": getattr(new_edge, "pipe_type", "normal"),
                },
            }
            if movement_arrays:
                edge_update_message["nodeMovements"] = movement_arrays
            if removed_edges:
                edge_update_message["removedEdges"] = removed_edges
            
            # Send to all players, but include cost only for the acting player
            for token_key, client_websocket in game_info.get("clients", {}).items():
                if not client_websocket:
                    continue
                
                message_to_send = edge_update_message.copy()
                message_to_send["gemCounts"] = gem_counts_payload
                # Only include cost for the player who built the bridge
                if token_key == token:
                    message_to_send["cost"] = actual_cost
                
                await self._send_safe(client_websocket, json.dumps(message_to_send))

        lonely_events = []
        if engine.state and hasattr(engine.state, "pop_pending_lonely_sinks"):
            lonely_events = engine.state.pop_pending_lonely_sinks()
        if lonely_events:
            for sink_event in lonely_events:
                payload = self._build_lonely_sink_payload(sink_event)
                if payload:
                    await self._broadcast_to_game(game_info, payload)

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
            event_payload["pipeType"] = getattr(new_edge, "pipe_type", "normal")
            if movement_payloads:
                event_payload["nodeMovements"] = movement_payloads
        if removed_edges:
            event_payload["removedEdges"] = removed_edges
        event_payload["gemCounts"] = gem_counts_payload
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

    async def handle_crown_smash(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        target_node_id = msg.get("targetNodeId")
        if target_node_id is None:
            target_node_id = msg.get("nodeId")
        if token is None or target_node_id is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success, error_msg, payload = engine.handle_crown_smash(token, int(target_node_id))
        if not success or not payload:
            await self._send_safe(
                websocket,
                json.dumps({"type": "crownSmashError", "message": error_msg or "Unable to use Crown Smash"}),
            )
            return

        broadcast_payload = dict(payload)
        broadcast_payload["type"] = "crownSmashed"
        await self._broadcast_to_game(game_info, broadcast_payload)

        try:
            king_player_id = int(payload.get("playerId", 0))
        except (TypeError, ValueError):
            king_player_id = 0
        try:
            king_from_id = int(payload.get("fromNodeId", 0))
        except (TypeError, ValueError):
            king_from_id = 0
        try:
            king_to_id = int(payload.get("toNodeId", 0))
        except (TypeError, ValueError):
            king_to_id = 0
        king_message = {
            "type": "kingMoved",
            "playerId": king_player_id,
            "fromNodeId": king_from_id,
            "toNodeId": king_to_id,
            "crownHealth": payload.get("crownHealth"),
            "crownMax": payload.get("crownMax"),
        }
        await self._broadcast_to_game(game_info, king_message)

        self._record_game_event(
            game_info,
            token,
            "crownSmash",
            {
                "targetNodeId": int(target_node_id),
                "fromNodeId": int(payload.get("fromNodeId", 0)),
                "durbiumCost": payload.get("durbiumCost"),
                "path": payload.get("path"),
                "edgeHits": payload.get("edgeHits", []),
                "removedEdges": payload.get("removedEdges", []),
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

    async def handle_king_request_moves(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        origin_node_id = msg.get("originNodeId")
        if token is None:
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        origin_arg: Optional[int] = None
        if origin_node_id is not None:
            try:
                origin_arg = int(origin_node_id)
            except (TypeError, ValueError):
                origin_arg = None

        success, targets, error_msg, current_node_id = engine.get_king_move_options(token, origin_arg)
        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "kingMoveError", "message": error_msg or "Unable to calculate king moves"}),
            )
            return

        payload = {
            "type": "kingMoveOptions",
            "originNodeId": current_node_id,
            "targets": [int(t) for t in targets],
        }
        await self._send_safe(websocket, json.dumps(payload))

    async def handle_king_move(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token")
        destination_node_id = msg.get("destinationNodeId")
        if destination_node_id is None:
            destination_node_id = msg.get("targetNodeId")
        if token is None or destination_node_id is None:
            return

        try:
            destination_int = int(destination_node_id)
        except (TypeError, ValueError):
            await self._send_safe(
                websocket,
                json.dumps({"type": "kingMoveError", "message": "Invalid destination node"}),
            )
            return

        game_info = self._get_game_info(token, server_context)
        if not game_info:
            return

        engine = game_info["engine"]
        success, error_msg, payload = engine.handle_move_king(token, destination_int)
        if not success or not payload:
            await self._send_safe(
                websocket,
                json.dumps({"type": "kingMoveError", "message": error_msg or "Unable to move king"}),
            )
            return

        broadcast_payload = {
            "type": "kingMoved",
            "playerId": int(payload["playerId"]),
            "fromNodeId": int(payload["fromNodeId"]),
            "toNodeId": int(payload["toNodeId"]),
        }
        if "crownHealth" in payload:
            try:
                broadcast_payload["crownHealth"] = float(payload["crownHealth"])
            except (TypeError, ValueError):
                pass
        if "crownMax" in payload:
            try:
                broadcast_payload["crownMax"] = float(payload["crownMax"])
            except (TypeError, ValueError):
                pass
        await self._broadcast_to_game(game_info, broadcast_payload)

        event_payload = {
            "playerId": int(payload["playerId"]),
            "fromNodeId": int(payload["fromNodeId"]),
            "toNodeId": int(payload["toNodeId"]),
        }
        if "crownHealth" in payload:
            try:
                event_payload["crownHealth"] = float(payload["crownHealth"])
            except (TypeError, ValueError):
                pass
        if "crownMax" in payload:
            try:
                event_payload["crownMax"] = float(payload["crownMax"])
            except (TypeError, ValueError):
                pass
        self._record_game_event(game_info, token, "moveKing", event_payload)

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
    async def handle_toggle_auto_attack(
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
        success = engine.handle_toggle_auto_attack(token)
        if success:
            enabled = False
            player_id = engine.get_player_id(token)
            if engine.state and player_id is not None:
                enabled = bool(engine.state.player_auto_attack.get(player_id, False))
            game_info.setdefault("auto_attack_state", {})[token] = enabled
            self._record_game_event(
                game_info,
                token,
                "toggleAutoAttack",
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
        auto_attack = bool(msg.get("autoAttack", False))
        raw_mode = msg.get("mode", DEFAULT_GAME_MODE)
        mode_settings = self._sanitize_mode_settings(msg.get("settings"))
        mode = self._derive_mode_from_settings(mode_settings, raw_mode)
        mode_settings["derivedMode"] = mode
        if mode == "sandbox":
            mode_settings["sandbox"] = True
            mode_settings["brassStart"] = "anywhere"
            mode_settings["bridgeCost"] = 0.0
            mode_settings["gameStart"] = "open"

        success, error_msg = bot_game_manager.start_bot_game(
            token,
            difficulty,
            auto_expand,
            auto_attack,
            mode=mode,
            options=mode_settings,
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
            player_id = bot_game_engine.token_to_player_id.get(token)
            message = bot_game_engine.state.build_player_view(message, player_id)
            await self._send_safe(websocket, json.dumps(message))

        if bot_game_manager.bot_player:
            await bot_game_manager.make_bot_move()

    async def handle_sandbox_reset(
        self,
        websocket: websockets.WebSocketServerProtocol,
        msg: Dict[str, Any],
        server_context: Dict[str, Any],
    ) -> None:
        token = msg.get("token") or uuid.uuid4().hex
        if not token:
            return

        auto_expand = bool(msg.get("autoExpand", False))
        auto_attack = bool(msg.get("autoAttack", False))
        mode_settings = self._sanitize_mode_settings(msg.get("settings"))
        mode_settings["derivedMode"] = "sandbox"
        mode_settings["sandbox"] = True
        mode_settings["brassStart"] = "anywhere"
        mode_settings["pipeStart"] = "anywhere"
        mode_settings["bridgeCost"] = 0.0
        mode_settings["gameStart"] = "open"

        success, error_msg = bot_game_manager.start_bot_game(
            token,
            difficulty="sandbox",
            auto_expand=auto_expand,
            auto_attack=auto_attack,
            mode="sandbox",
            options=mode_settings,
        )
        if not success:
            await self._send_safe(
                websocket,
                json.dumps({"type": "botGameError", "message": error_msg or "Failed to reset sandbox"}),
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
            player_id = bot_game_engine.token_to_player_id.get(token)
            message = bot_game_engine.state.build_player_view(message, player_id)
            await self._send_safe(websocket, json.dumps(message))

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
                                "rewardType": capture_data.get("rewardType"),
                                "rewardKey": capture_data.get("rewardKey"),
                            }
                            await self._send_safe(websocket, json.dumps(capture_msg))
                    bot_game_engine.state.pending_node_captures = []

        elif msg_type == "clickEdge":
            edge_id = msg.get("edgeId")
            if edge_id is not None:
                bot_game_engine.handle_edge_click(token, int(edge_id))

        elif msg_type == "reverseEdge":
            edge_id = msg.get("edgeId")
            if edge_id is not None:
                success = bot_game_engine.handle_reverse_edge(token, int(edge_id))
                if not success:
                    # Derive a more specific error message for bot game
                    error_message = "Can't reverse this pipe!"
                    try:
                        if bot_game_engine.state:
                            player_id = bot_game_engine.get_player_id(token)
                            edge = bot_game_engine.state.edges.get(int(edge_id)) if player_id is not None else None
                            if edge:
                                if getattr(edge, "pipe_type", "normal") != "reverse":
                                    error_message = "Pipe is not reversible"
                                else:
                                    source_node = bot_game_engine.state.nodes.get(edge.source_node_id)
                                    if source_node and source_node.owner is not None and source_node.owner != player_id:
                                        error_message = "Pipe controlled by opponent"
                    except Exception:
                        pass

                    await self._send_safe(websocket, json.dumps({"type": "reverseEdgeError", "message": error_message}))
                else:
                    # Send response for human player moves only (bot moves are handled by bot_player.py)
                    if not bot_game_manager.bot_player or token != bot_game_manager.bot_player.bot_token:
                        edge = bot_game_engine.state.edges.get(int(edge_id)) if bot_game_engine.state else None
                        if edge:
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
                                    "pipeType": getattr(edge, "pipe_type", "normal"),
                                    "warp": warp_payload,
                                    "warpAxis": warp_payload["axis"],
                                    "warpSegments": warp_segments,
                                },
                            }
                            await self._send_safe(websocket, json.dumps(message))

        elif msg_type == "buildBridge":
            from_node_id = msg.get("fromNodeId")
            to_node_id = msg.get("toNodeId")
            cost = float(msg.get("cost", 1.0))
            warp_info = msg.get("warpInfo")
            pipe_type = msg.get("pipeType")
            if from_node_id is not None and to_node_id is not None:
                success, new_edge, actual_cost, error_msg, removed_edges, node_movements = bot_game_engine.handle_build_bridge(
                    token,
                    int(from_node_id),
                    int(to_node_id),
                    cost,
                    warp_info=warp_info,
                    pipe_type=pipe_type if isinstance(pipe_type, str) else "normal",
                )
                if not success:
                    await self._send_safe(
                        websocket,
                        json.dumps({"type": "bridgeError", "message": error_msg or "Failed to build bridge"}),
                    )
                elif new_edge:
                    movement_arrays: List[List[float]] = []
                    if node_movements:
                        for movement in node_movements:
                            if not isinstance(movement, dict):
                                continue
                            node_id = movement.get("nodeId")
                            x = movement.get("x")
                            y = movement.get("y")
                            try:
                                node_int = int(node_id)
                                x_val = round(float(x), 3)
                                y_val = round(float(y), 3)
                            except (TypeError, ValueError):
                                continue
                            movement_arrays.append([node_int, x_val, y_val])
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
                            "pipeType": getattr(new_edge, "pipe_type", "normal"),
                        },
                        "cost": actual_cost,
                    }
                    if removed_edges:
                        message["removedEdges"] = removed_edges
                    if movement_arrays:
                        message["nodeMovements"] = movement_arrays
                    await self._send_safe(websocket, json.dumps(message))

        elif msg_type == "redirectEnergy":
            target_node_id = msg.get("targetNodeId")
            if target_node_id is not None:
                bot_game_engine.handle_redirect_energy(token, int(target_node_id))

        elif msg_type == "kingRequestMoves":
            origin_node_id = msg.get("originNodeId")
            origin_arg: Optional[int] = None
            if origin_node_id is not None:
                try:
                    origin_arg = int(origin_node_id)
                except (TypeError, ValueError):
                    origin_arg = None

            success, targets, error_msg, current_node_id = bot_game_engine.get_king_move_options(token, origin_arg)
            if not success:
                await self._send_safe(
                    websocket,
                    json.dumps({"type": "kingMoveError", "message": error_msg or "Unable to calculate king moves"}),
                )
            else:
                payload = {
                    "type": "kingMoveOptions",
                    "originNodeId": current_node_id,
                    "targets": [int(t) for t in targets],
                }
                await self._send_safe(websocket, json.dumps(payload))

        elif msg_type == "kingMove":
            destination_node_id = msg.get("destinationNodeId")
            if destination_node_id is None:
                destination_node_id = msg.get("targetNodeId")
            if destination_node_id is not None:
                try:
                    destination_int = int(destination_node_id)
                except (TypeError, ValueError):
                    await self._send_safe(
                        websocket,
                        json.dumps({"type": "kingMoveError", "message": "Invalid destination node"}),
                    )
                else:
                    success, error_msg, payload = bot_game_engine.handle_move_king(token, destination_int)
                    if not success or not payload:
                        await self._send_safe(
                            websocket,
                            json.dumps({"type": "kingMoveError", "message": error_msg or "Unable to move king"}),
                        )
                    else:
                        message = {
                            "type": "kingMoved",
                            "playerId": int(payload["playerId"]),
                            "fromNodeId": int(payload["fromNodeId"]),
                            "toNodeId": int(payload["toNodeId"]),
                        }
                        await self._send_safe(websocket, json.dumps(message))

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
        elif msg_type == "crownSmash":
            target_node_id = msg.get("targetNodeId")
            if target_node_id is None:
                target_node_id = msg.get("nodeId")
            if target_node_id is not None:
                success, error_msg, payload = bot_game_engine.handle_crown_smash(token, int(target_node_id))
                if not success or not payload:
                    await self._send_safe(
                        websocket,
                        json.dumps({"type": "crownSmashError", "message": error_msg or "Unable to use Crown Smash"}),
                    )
                else:
                    broadcast_payload = dict(payload)
                    broadcast_payload["type"] = "crownSmashed"
                    await self._broadcast_to_specific(
                        server_context.get("bot_game_clients", {}).values(),
                        json.dumps(broadcast_payload),
                    )
                    king_message = {
                        "type": "kingMoved",
                        "playerId": int(payload.get("playerId", 0)),
                        "fromNodeId": int(payload.get("fromNodeId", 0)),
                        "toNodeId": int(payload.get("toNodeId", 0)),
                        "crownHealth": payload.get("crownHealth"),
                        "crownMax": payload.get("crownMax"),
                    }
                    await self._broadcast_to_specific(
                        server_context.get("bot_game_clients", {}).values(),
                        json.dumps(king_message),
                    )

        elif msg_type == "sandboxCreateNode":
            result = bot_game_engine.handle_sandbox_create_node(token, msg.get("x"), msg.get("y"))
            if not result:
                await self._send_safe(
                    websocket,
                    json.dumps({"type": "sandboxError", "message": "Unable to create node"}),
                )
            else:
                payload = {
                    "type": "sandboxNodeCreated",
                    "node": result.get("node", {}),
                    "totalNodes": result.get("totalNodes", 0),
                    "winThreshold": result.get("winThreshold", 0),
                }
                await self._send_safe(websocket, json.dumps(payload))

        elif msg_type == "sandboxClearBoard":
            result = bot_game_engine.handle_sandbox_clear_board(token)
            if not result:
                await self._send_safe(
                    websocket,
                    json.dumps({"type": "sandboxError", "message": "Unable to clear board"}),
                )
            else:
                payload = {
                    "type": "sandboxBoardCleared",
                    "removedNodes": [int(nid) for nid in result.get("removedNodes", [])],
                    "removedEdges": [int(eid) for eid in result.get("removedEdges", [])],
                    "totalNodes": result.get("totalNodes", 0),
                    "winThreshold": result.get("winThreshold", 0),
                }
                await self._send_safe(websocket, json.dumps(payload))

        elif msg_type == "quitGame":
            winner_id = bot_game_engine.handle_quit_game(token)
            if winner_id is not None:
                await self._send_safe(websocket, json.dumps({"type": "gameOver", "winnerId": winner_id}))
                bot_game_manager.end_game()
                server_context.get("bot_game_clients", {}).pop(token, None)

        elif msg_type == "toggleAutoExpand":
            bot_game_engine.handle_toggle_auto_expand(token)

        elif msg_type == "toggleAutoAttack":
            bot_game_engine.handle_toggle_auto_attack(token)

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
                player_id = bot_game_engine.token_to_player_id.get(token)
                message = bot_game_engine.state.build_player_view(message, player_id)
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
        if engine.state:
            message = engine.state.build_player_view(message, player_id)
        await self._send_safe(websocket, json.dumps(message))

    async def _broadcast_to_game(self, game_info: Dict[str, Any], message: Dict[str, Any]) -> None:
        engine: Optional[GameEngine] = game_info.get("engine")
        state = engine.state if engine else None
        if state and state.hidden_start_active:
            for token, websocket in list(game_info.get("clients", {}).items()):
                if not websocket:
                    continue
                player_id = engine.token_to_player_id.get(token) if engine else None
                per_player_message = state.build_player_view(copy.deepcopy(message), player_id)
                await self._send_safe(websocket, json.dumps(per_player_message))
            return

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
                except Exception:
                    pass

            # Gather players (tokens) and their websockets in original join order
            clients = game_info.get("clients", {})
            tokens = list(clients.keys())

            auto_expand_map: Dict[str, bool] = dict(game_info.get("auto_expand_state", {}))
            auto_attack_map: Dict[str, bool] = dict(game_info.get("auto_attack_state", {}))
            if not auto_expand_map and engine and engine.state:
                for player_id, enabled in getattr(engine.state, "player_auto_expand", {}).items():
                    token = engine.player_id_to_token.get(player_id)
                    if token:
                        auto_expand_map[token] = bool(enabled)
            if not auto_attack_map and engine and engine.state:
                for player_id, enabled in getattr(engine.state, "player_auto_attack", {}).items():
                    token = engine.player_id_to_token.get(player_id)
                    if token:
                        auto_attack_map[token] = bool(enabled)

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
                "auto_attack": auto_attack_map,
                "guest_names": guest_name_map,
                "mode": game_info.get("mode", DEFAULT_GAME_MODE),
                "mode_settings": dict(game_info.get("mode_settings") or {}),
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
            host_settings = dict(group.get("mode_settings") or {})
            players = []
            for t in tokens:
                players.append({
                    "token": t,
                    "websocket": group.get("clients", {}).get(t),
                    "auto_expand": bool(group.get("auto_expand", {}).get(t, False)),
                    "auto_attack": bool(group.get("auto_attack", {}).get(t, False)),
                    "guest_name": group.get("guest_names", {}).get(t),
                    "mode": mode,
                })
            # Remove group before starting to avoid reentrancy issues
            groups.pop(group_id, None)
            await self._start_friend_game(players, len(players), mode, server_context, host_settings)

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
        auto_attack_map = group.setdefault("auto_attack", {})

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
        auto_attack_map.pop(token, None)

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
