from __future__ import annotations
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, TYPE_CHECKING

from .constants import (
    DEFAULT_GAME_MODE,
    GOLD_REWARD_FOR_ENEMY_CAPTURE,
    GOLD_REWARD_FOR_NEUTRAL_CAPTURE_BY_MODE,
    NODE_MIN_JUICE,
    PASSIVE_GOLD_PER_SECOND,
    PASSIVE_GOLD_PER_TICK,
    PASSIVE_INCOME_ENABLED,
    PRODUCTION_RATE_PER_NODE,
    STARTING_GOLD,
    get_neutral_capture_reward,
    get_node_max_juice,
)

if TYPE_CHECKING:  # pragma: no cover - import only for type checking
    from .game_engine import GameEngine


class GameReplayRecorder:
    """Capture the essential data needed to replay a completed match."""

    VERSION: int = 3

    def __init__(
        self,
        game_id: str,
        engine: "GameEngine",
        tick_interval: float,
        player_slots: List[Dict[str, Any]],
    ) -> None:
        self.game_id = game_id
        self.tick_interval = float(tick_interval)
        self.created_at = datetime.now(timezone.utc)
        self._events: List[Dict[str, Any]] = []
        self._starting_nodes: Dict[int, int] = {}
        self._token_to_player: Dict[str, int] = {}
        self._sequence: int = 0

        self.player_info: List[Dict[str, Any]] = self._build_player_info(player_slots)
        self.graph_snapshot: Dict[str, Any] = self._snapshot_graph(engine)

    def _build_player_info(self, player_slots: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
        info: List[Dict[str, Any]] = []
        for slot in player_slots:
            player_id = int(slot.get("player_id", 0))
            token = slot.get("token")
            if token:
                self._token_to_player[str(token)] = player_id

            info.append(
                {
                    "playerId": player_id,
                    "color": slot.get("color"),
                    "secondaryColors": list(slot.get("secondary_colors", [])),
                    "autoExpand": bool(slot.get("auto_expand", False)),
                    "autoAttack": bool(slot.get("auto_attack", False)),
                    "name": str(slot.get("guest_name", "") or ""),
                }
            )

        info.sort(key=lambda entry: entry.get("playerId", 0))
        return info

    def _snapshot_graph(self, engine: "GameEngine") -> Dict[str, Any]:
        state = engine.state
        if state is None:
            return {"nodes": [], "edges": [], "screen": dict(engine.screen or {})}

        nodes_payload = []
        for node in state.nodes.values():
            nodes_payload.append(
                {
                    "id": node.id,
                    "x": node.x,
                    "y": node.y,
                    "juice": node.juice,
                    "owner": node.owner,
                    "attachedEdges": list(node.attached_edge_ids),
                    "pendingGold": getattr(node, "pending_gold", 0.0),
                    "nodeType": getattr(node, "node_type", "normal"),
                }
            )

        edges_payload = []
        for edge in state.edges.values():
            edges_payload.append(
                {
                    "id": edge.id,
                    "source": edge.source_node_id,
                    "target": edge.target_node_id,
                    "on": bool(edge.on),
                    "flowing": bool(edge.flowing),
                    "buildTicksRequired": int(getattr(edge, "build_ticks_required", 0)),
                    "buildTicksElapsed": int(getattr(edge, "build_ticks_elapsed", 0)),
                    "building": bool(getattr(edge, "building", False)),
                    "warpAxis": getattr(edge, "warp_axis", "none"),
                    "warpSegments": [
                        [sx, sy, ex, ey]
                        for sx, sy, ex, ey in (getattr(edge, "warp_segments", []) or [])
                    ],
                    "pipeType": getattr(edge, "pipe_type", "normal"),
                }
            )

        return {
            "screen": dict(engine.screen or {}),
            "nodes": nodes_payload,
            "edges": edges_payload,
        }

    def _next_sequence(self) -> int:
        self._sequence += 1
        return self._sequence

    def record_event(
        self,
        *,
        token: str,
        event_type: str,
        payload: Optional[Dict[str, Any]],
        engine: "GameEngine",
    ) -> None:
        """Record a single successful player command."""
        if not token or not event_type:
            return

        player_id = self._token_to_player.get(str(token))
        if player_id is None:
            return

        tick_count = 0
        if engine.state is not None:
            tick_count = int(getattr(engine.state, "tick_count", 0))

        event: Dict[str, Any] = {
            "seq": self._next_sequence(),
            "tick": tick_count,
            "playerId": player_id,
            "type": event_type,
        }
        if payload:
            event["payload"] = payload

        self._events.append(event)

        if event_type == "pickStartingNode" and payload and isinstance(payload.get("nodeId"), int):
            self._starting_nodes[player_id] = int(payload["nodeId"])

    def build_package(self, engine: "GameEngine", winner_id: Optional[int]) -> Dict[str, Any]:
        """Produce a serializable payload that fully describes the replay."""
        duration_ticks = 0
        if engine.state is not None:
            duration_ticks = int(getattr(engine.state, "tick_count", 0))

        current_mode = getattr(engine.state, "mode", DEFAULT_GAME_MODE)
        current_reward = get_neutral_capture_reward(current_mode)

        constants_payload = {
            "NODE_MAX_JUICE": get_node_max_juice(current_mode),
            "NODE_MIN_JUICE": NODE_MIN_JUICE,
            "PASSIVE_GOLD_PER_SECOND": PASSIVE_GOLD_PER_SECOND,
            "PASSIVE_GOLD_PER_TICK": PASSIVE_GOLD_PER_TICK,
            "PASSIVE_INCOME_ENABLED": PASSIVE_INCOME_ENABLED,
            "PRODUCTION_RATE_PER_NODE": PRODUCTION_RATE_PER_NODE,
            "STARTING_GOLD": STARTING_GOLD,
            "GOLD_REWARD_FOR_NEUTRAL_CAPTURE": current_reward,
            "GOLD_REWARD_FOR_NEUTRAL_CAPTURE_BY_MODE": GOLD_REWARD_FOR_NEUTRAL_CAPTURE_BY_MODE,
            "GOLD_REWARD_FOR_ENEMY_CAPTURE": GOLD_REWARD_FOR_ENEMY_CAPTURE,
        }

        starting_nodes_payload = [
            {"playerId": pid, "nodeId": node_id}
            for pid, node_id in sorted(self._starting_nodes.items())
        ]

        if not starting_nodes_payload:
            # Ensure deterministic order even if no picks were recorded yet
            starting_nodes_payload = [
                {"playerId": entry["playerId"], "nodeId": None}
                for entry in self.player_info
            ]

        payload = {
            "version": self.VERSION,
            "gameId": self.game_id,
            "createdAt": self.created_at.isoformat().replace("+00:00", "Z"),
            "tickInterval": self.tick_interval,
            "playerCount": len(self.player_info),
            "players": self.player_info,
            "constants": constants_payload,
            "graph": self.graph_snapshot,
            "events": self._events,
            "startingNodes": starting_nodes_payload,
            "winnerId": winner_id,
            "durationTicks": duration_ticks,
            "mode": getattr(engine.state, "mode", DEFAULT_GAME_MODE),
        }

        if self._events:
            payload["lastEventTick"] = self._events[-1]["tick"]

        return {
            "filename": self._build_filename(),
            "replay": payload,
        }

    def _build_filename(self) -> str:
        date_stamp = self.created_at.strftime("%Y%m%d")
        return f"durb-replay-{date_stamp}.json"
