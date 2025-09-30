"""Replay playback support for sending recorded matches back to clients."""

from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, Iterable, List, Optional, Tuple, TYPE_CHECKING

import websockets

from .constants import STARTING_GOLD

if TYPE_CHECKING:  # pragma: no cover - typing only
    from .message_handlers import MessageRouter


class ReplayLoadError(Exception):
    """Raised when a replay payload cannot be loaded."""


@dataclass
class ReplayEvent:
    """Normalized replay event with scheduling metadata."""

    time_ms: int
    tick_hint: int
    seq: int
    player_id: Optional[int]
    event_type: str
    payload: Dict[str, Any]


def _coerce_int(value: Any, default: int = 0) -> int:
    try:
        return int(value)
    except (TypeError, ValueError):
        return default


def _coerce_optional_int(value: Any) -> Optional[int]:
    try:
        if value is None:
            return None
        return int(value)
    except (TypeError, ValueError):
        return None


def _coerce_float(value: Any, default: float = 0.0) -> float:
    try:
        return float(value)
    except (TypeError, ValueError):
        return default


def build_engine_from_replay(
    replay: Dict[str, Any],
    tick_interval: float,
) -> Tuple["GameEngine", Dict[int, str]]:
    """Construct a GameEngine instance populated from a replay bundle."""
    from .game_engine import GameEngine
    from .models import Edge, Node, Player
    from .state import GraphState

    if not isinstance(replay, dict):
        raise ReplayLoadError("Replay payload must be an object")

    graph = replay.get("graph")
    if not isinstance(graph, dict):
        raise ReplayLoadError("Replay missing graph data")

    nodes_payload = graph.get("nodes")
    edges_payload = graph.get("edges")

    if not isinstance(nodes_payload, list) or not isinstance(edges_payload, list):
        raise ReplayLoadError("Replay graph missing nodes or edges array")

    nodes: List[Node] = []
    for raw in nodes_payload:
        if not isinstance(raw, dict):
            continue
        node = Node(
            id=_coerce_int(raw.get("id")),
            x=float(raw.get("x", 0.0)),
            y=float(raw.get("y", 0.0)),
            juice=float(raw.get("juice", 0.0)),
            owner=(raw.get("owner") if raw.get("owner") is not None else None),
        )
        nodes.append(node)

    edges: List[Edge] = []
    for raw in edges_payload:
        if not isinstance(raw, dict):
            continue
        edge = Edge(
            id=_coerce_int(raw.get("id")),
            source_node_id=_coerce_int(raw.get("source")),
            target_node_id=_coerce_int(raw.get("target")),
            on=bool(raw.get("on", False)),
            flowing=bool(raw.get("flowing", False)),
            build_ticks_required=_coerce_int(raw.get("buildTicksRequired", 0)),
            build_ticks_elapsed=_coerce_int(raw.get("buildTicksElapsed", 0)),
            building=bool(raw.get("building", False)),
        )
        edges.append(edge)

    # Build initial state
    state = GraphState(nodes, edges)
    state.phase = "picking"
    state.tick_count = 0
    state.pending_eliminations = []
    state.eliminated_players.clear()
    state.game_duration = _coerce_float(replay.get("durationSeconds", state.game_duration), state.game_duration)
    state.speed_level = _coerce_int(replay.get("speedLevel", 3))

    constants_raw = replay.get("constants")
    constants = constants_raw if isinstance(constants_raw, dict) else {}
    starting_gold_value = _coerce_float(constants.get("STARTING_GOLD", STARTING_GOLD), STARTING_GOLD)

    engine = GameEngine()
    engine.state = state
    engine.screen = dict(graph.get("screen", {}))
    engine.game_active = True

    player_tokens: Dict[int, str] = {}

    players = replay.get("players")
    if not isinstance(players, list):
        raise ReplayLoadError("Replay missing players array")

    for entry in players:
        if isinstance(entry, dict):
            player_id = _coerce_int(entry.get("playerId", entry.get("id")))
            color = entry.get("color", "#ffffff")
            secondary_colors = list(entry.get("secondaryColors", entry.get("secondary_colors", [])))
            auto_expand = bool(entry.get("autoExpand", entry.get("auto_expand", False)))
            name = str(entry.get("name") or entry.get("guestName") or "")
        else:
            player_id = _coerce_int(entry)
            color = "#ffffff"
            secondary_colors = []
            auto_expand = False
            name = ""

        if player_id <= 0:
            raise ReplayLoadError("Replay includes invalid player id")

        player = Player(id=player_id, color=color, secondary_colors=secondary_colors, name=name)
        state.add_player(player)
        state.player_auto_expand[player_id] = auto_expand
        state.player_gold[player_id] = starting_gold_value
        token = f"replay-{player_id}"
        player_tokens[player_id] = token
        engine.token_to_player_id[token] = player_id
        engine.player_id_to_token[player_id] = token
        engine.player_meta[player_id] = {
            "color": color,
            "secondary_colors": secondary_colors,
            "guest_name": name,
        }

    # Ensure gold map exists for all players even if replay recorded different constants
    for pid in player_tokens.keys():
        state.player_gold.setdefault(pid, starting_gold_value)

    # Timer defaults (will be started once we enter playing phase)
    state.game_start_time = None
    state.game_duration = _coerce_float(replay.get("gameDuration", state.game_duration), state.game_duration)
    if state.game_duration <= 0:
        # Fallback to recorded durationTick * tick_interval if provided
        duration_ticks = _coerce_int(replay.get("durationTicks", 0))
        if duration_ticks > 0 and tick_interval > 0:
            state.game_duration = duration_ticks * tick_interval
        else:
            state.game_duration = 7 * 60

    return engine, player_tokens


class ReplaySession:
    """Run a replay for a single websocket client."""

    def __init__(
        self,
        websocket: websockets.WebSocketServerProtocol,
        replay: Dict[str, Any],
        message_router: "MessageRouter",
        tick_interval_default: float,
        on_complete,
    ) -> None:
        self.websocket = websocket
        self.replay = replay
        self.router = message_router
        self.tick_interval = max(0.01, float(replay.get("tickInterval", tick_interval_default or 0.1)))
        self.on_complete = on_complete

        self.engine, self.player_tokens = build_engine_from_replay(replay, self.tick_interval)
        self.events: List[ReplayEvent] = self._normalize_events(replay.get("events", []))
        self.duration_ticks = _coerce_int(replay.get("durationTicks", 0))
        self.recorded_winner = replay.get("winnerId")
        self.speed_multiplier: float = 1.0

        self._task: Optional[asyncio.Task] = None
        self._cancelled = False
        self._start_wall_time = time.time()
        self._winner_announced: Optional[int] = None

    def start(self) -> None:
        if self._task is not None:
            raise RuntimeError("Replay session already started")
        self._task = asyncio.create_task(self._run())

    async def stop(self) -> None:
        self._cancelled = True
        if self._task:
            self._task.cancel()
            try:
                await self._task
            except asyncio.CancelledError:
                pass
            self._task = None

    def _normalize_events(self, raw_events: Iterable[Any]) -> List[ReplayEvent]:
        events: List[ReplayEvent] = []
        if not isinstance(raw_events, Iterable):
            return events
        for raw in raw_events:
            if not isinstance(raw, dict):
                continue
            event_type = raw.get("type")
            if not isinstance(event_type, str):
                continue
            payload = raw.get("payload")
            payload_dict: Dict[str, Any] = payload if isinstance(payload, dict) else {}
            base_time_ms = raw.get("timeMs")
            tick_hint_val = _coerce_int(raw.get("tick", 0))
            if base_time_ms is None:
                base_time_ms = tick_hint_val * self.tick_interval * 1000.0
            time_ms = int(round(_coerce_float(base_time_ms, 0.0)))
            player_id = _coerce_optional_int(raw.get("playerId"))

            events.append(
                ReplayEvent(
                    time_ms=time_ms,
                    tick_hint=tick_hint_val,
                    seq=_coerce_int(raw.get("seq", len(events) + 1)),
                    player_id=player_id,
                    event_type=event_type,
                    payload=payload_dict,
                )
            )
        events.sort(key=lambda e: (e.time_ms, e.seq))
        return events

    async def _run(self) -> None:
        try:
            await self._send_init()
            await self._run_loop()
        except asyncio.CancelledError:
            pass
        except Exception as exc:  # pragma: no cover - defensive
            await self._send_error(f"Replay failed: {exc}")
        finally:
            if callable(self.on_complete):
                try:
                    self.on_complete()
                except Exception:
                    pass

    async def _run_loop(self) -> None:
        state = self.engine.state
        if not state:
            raise ReplayLoadError("Replay engine missing state")

        current_ms = 0.0
        next_tick_ms = self.tick_interval * 1000.0
        event_idx = 0
        epsilon_ms = 0.25

        while not self._cancelled:
            next_event_ms: Optional[float] = None
            if event_idx < len(self.events):
                next_event_ms = float(self.events[event_idx].time_ms)

            targets = [next_tick_ms]
            if next_event_ms is not None:
                targets.append(next_event_ms)
            target_ms = min(targets)
            multiplier = max(0.1, min(10.0, float(self.speed_multiplier)))
            sleep_seconds = max(0.0, (target_ms - current_ms) / 1000.0)
            if multiplier != 1.0:
                sleep_seconds /= multiplier
            if sleep_seconds > 0:
                try:
                    await asyncio.sleep(sleep_seconds)
                except asyncio.CancelledError:
                    raise
            current_ms = target_ms

            # Process all events scheduled up to current time
            while event_idx < len(self.events):
                event = self.events[event_idx]
                if event.time_ms > current_ms + epsilon_ms:
                    break
                await self._apply_event(event)
                event_idx += 1
                if self._cancelled:
                    break
            if self._cancelled:
                break

            # Run simulation tick if due
            if current_ms + epsilon_ms >= next_tick_ms:
                winner = self.engine.simulate_tick(self.tick_interval)
                state_time = self._start_wall_time + (current_ms / 1000.0)
                tick_message = state.to_tick_message(state_time)
                tick_message["replay"] = True
                await self._send_json(tick_message)
                await self._flush_pending_captures()
                next_tick_ms += self.tick_interval * 1000.0
                if winner is not None:
                    await self._announce_winner(winner)
                    break

                if self.duration_ticks and state.tick_count >= self.duration_ticks:
                    # Replay recorded tick count reached - honour recorded winner if present
                    winner_to_use = winner or self.recorded_winner or state.winner_id
                    if winner_to_use is not None:
                        await self._announce_winner(int(winner_to_use))
                    break

            if event_idx >= len(self.events) and (self.duration_ticks == 0 or state.tick_count >= self.duration_ticks):
                # No more scheduled work; announce recorded winner if we have one
                winner_final = state.winner_id or self.recorded_winner
                if winner_final is not None and self._winner_announced is None:
                    await self._announce_winner(int(winner_final))
                break

    async def _apply_event(self, event: ReplayEvent) -> None:
        state = self.engine.state
        if not state:
            return
        token = self.player_tokens.get(event.player_id) if event.player_id is not None else None
        if token is None:
            return

        event_type = event.event_type
        payload = event.payload

        if event_type == "pickStartingNode":
            node_id = _coerce_int(payload.get("nodeId"), -1)
            if node_id >= 0:
                success = self.engine.handle_node_click(token, node_id)
                if success:
                    await self._flush_pending_captures()
        elif event_type == "toggleEdge":
            edge_id = _coerce_int(payload.get("edgeId"), -1)
            if edge_id >= 0:
                self.engine.handle_edge_click(token, edge_id)
        elif event_type == "reverseEdge":
            edge_id = _coerce_int(payload.get("edgeId"), -1)
            cost = _coerce_float(payload.get("cost", 0.0), 0.0)
            if edge_id >= 0:
                success = self.engine.handle_reverse_edge(token, edge_id, cost)
                if success and state:
                    edge = state.edges.get(edge_id)
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
                            "replay": True,
                        }
                        await self._send_json(message)
                    if hasattr(state, "pending_edge_reversal"):
                        state.pending_edge_reversal = None
        elif event_type == "buildBridge":
            from_node = _coerce_int(payload.get("fromNodeId"), -1)
            to_node = _coerce_int(payload.get("toNodeId"), -1)
            cost = _coerce_float(payload.get("cost", 0.0), 0.0)
            if from_node >= 0 and to_node >= 0:
                success, new_edge, _, _ = self.engine.handle_build_bridge(token, from_node, to_node, cost)
                if success and new_edge:
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
                            "building": bool(getattr(new_edge, "building", False)),
                            "buildTicksRequired": int(getattr(new_edge, "build_ticks_required", 0)),
                            "buildTicksElapsed": int(getattr(new_edge, "build_ticks_elapsed", 0)),
                        },
                        "replay": True,
                    }
                    await self._send_json(message)
        elif event_type == "redirectEnergy":
            target_node = _coerce_int(payload.get("targetNodeId"), -1)
            if target_node >= 0:
                success = self.engine.handle_redirect_energy(token, target_node)
                if success and state:
                    updates = []
                    for edge in state.edges.values():
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
                                "replay": True,
                            }
                        )
                    for update in updates:
                        await self._send_json(update)
        elif event_type == "localTargeting":
            target_node = _coerce_int(payload.get("targetNodeId"), -1)
            if target_node >= 0:
                success = self.engine.handle_local_targeting(token, target_node)
                if success and state:
                    updates = []
                    for edge in state.edges.values():
                        if edge.target_node_id == target_node:
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
                                    "replay": True,
                                }
                            )
                    for update in updates:
                        await self._send_json(update)
        elif event_type == "destroyNode":
            node_id = _coerce_int(payload.get("nodeId"), -1)
            cost = _coerce_float(payload.get("cost", 0.0), 0.0)
            if node_id >= 0:
                success, _ = self.engine.handle_destroy_node(token, node_id, cost)
                if success:
                    await self._send_json({"type": "nodeDestroyed", "nodeId": node_id, "replay": True})
        elif event_type == "quitGame":
            winner = self.engine.handle_quit_game(token)
            if winner is not None:
                await self._announce_winner(winner)
                self._cancelled = True
        elif event_type == "toggleAutoExpand":
            self.engine.handle_toggle_auto_expand(token)
        else:
            # Unsupported events are ignored for now
            return

        await self._flush_pending_captures()

    async def _flush_pending_captures(self) -> None:
        state = self.engine.state
        if not state:
            return
        captures = getattr(state, "pending_node_captures", None)
        if not captures:
            return
        for capture in list(captures):
            message = {
                "type": "nodeCaptured",
                "nodeId": capture.get("nodeId"),
                "reward": capture.get("reward"),
                "playerId": capture.get("player_id"),
                "replay": True,
            }
            await self._send_json(message)
        state.pending_node_captures = []

    async def _send_init(self) -> None:
        state = self.engine.state
        if not state:
            raise ReplayLoadError("Replay engine missing state")
        message = state.to_init_message(self.engine.screen, self.tick_interval, self._start_wall_time)
        message["replay"] = True
        message["token"] = None
        message["myPlayerId"] = None
        message["replayMeta"] = {
            "gameId": self.replay.get("gameId"),
            "createdAt": self.replay.get("createdAt"),
            "playerCount": len(self.player_tokens),
        }
        await self._send_json(message)

    async def _announce_winner(self, winner_id: int) -> None:
        if self._winner_announced is not None:
            return
        self._winner_announced = winner_id
        payload = {"type": "gameOver", "winnerId": winner_id, "replay": True}
        await self._send_json(payload)
        await self._send_json({"type": "replayComplete", "winnerId": winner_id, "replay": True})

    async def _send_error(self, message: str) -> None:
        await self._send_json({"type": "replayError", "message": message, "replay": True})

    async def _send_json(self, payload: Dict[str, Any]) -> None:
        await self.router._send_safe(self.websocket, json.dumps(payload))

    def set_speed(self, multiplier: float) -> None:
        try:
            value = float(multiplier)
        except (TypeError, ValueError):
            value = 1.0
        self.speed_multiplier = max(0.5, min(3.0, value))
