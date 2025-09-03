import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Edge, Node, Player


EDGE_TOGGLE_PROBABILITY: float = 0.0
NODE_MIN_JUICE: float = 0.0
NODE_MAX_JUICE: float = 120.0

# Flow model
PRODUCTION_RATE_PER_NODE: float = 0.1  # owned nodes generate this per tick (constant growth)
TRANSFER_PERCENT_PER_TICK: float = 0.01  # fraction of source juice transferred per tick (split across outgoing edges)

# Gold economy (displayed as 5 sections filling like elixir)
GOLD_MAX_SECTIONS: float = 5.0
GOLD_SECTION_FILL_SECONDS: float = 4.0  # each section fills in 4 seconds


class GraphState:
    def __init__(self, nodes: List[Node], edges: List[Edge]) -> None:
        self.nodes: Dict[int, Node] = {n.id: n for n in nodes}
        self.edges: Dict[int, Edge] = {e.id: e for e in edges}
        for e in edges:
            self.nodes[e.source_node_id].attached_edge_ids.append(e.id)
            self.nodes[e.target_node_id].attached_edge_ids.append(e.id)

        self.players: Dict[int, Player] = {}
        # Economy and game flow
        self.player_gold: Dict[int, float] = {}
        # Phase: 'picking' (each player picks starting node) or 'playing'
        self.phase: str = "picking"
        # Track which players have completed their starting pick
        self.players_who_picked: Dict[int, bool] = {}

    def add_player(self, player: Player) -> None:
        self.players[player.id] = player
        # Initialize player economy and pick status
        self.player_gold[player.id] = 0.0
        self.players_who_picked[player.id] = False

    def to_init_message(self, screen: Dict[str, int], tick_interval: float) -> Dict:
        nodes_arr = [
            [nid, round(n.x, 3), round(n.y, 3), round(n.juice, 3), (n.owner if n.owner is not None else None)]
            for nid, n in self.nodes.items()
        ]
        edges_arr = [
            [eid, e.source_node_id, e.target_node_id, 1 if e.bidirectional else 0, 1 if e.forward else 0]
            for eid, e in self.edges.items()
        ]
        players_arr = [[pid, p.color] for pid, p in self.players.items()]
        gold_arr = [[pid, round(self.player_gold.get(pid, 0.0), 4)] for pid in self.players.keys()]
        picked_arr = [[pid, bool(self.players_who_picked.get(pid, False))] for pid in self.players.keys()]
        return {
            "type": "init",
            "screen": screen,
            "tickInterval": tick_interval,
            "nodes": nodes_arr,
            "edges": edges_arr,
            "players": players_arr,
            "settings": {"nodeMaxJuice": NODE_MAX_JUICE},
            "phase": self.phase,
            "gold": gold_arr,
            "picked": picked_arr,
        }

    def to_tick_message(self) -> Dict:
        edges_arr = [[eid, 1 if e.on else 0, 1 if e.flowing else 0, 1 if e.forward else 0] for eid, e in self.edges.items()]
        nodes_arr = [
            [nid, round(n.juice, 3), (n.owner if n.owner is not None else None)]
            for nid, n in self.nodes.items()
        ]
        counts: Dict[int, int] = {}
        for n in self.nodes.values():
            if n.owner is not None:
                counts[n.owner] = counts.get(n.owner, 0) + 1
        gold_arr = [[pid, round(self.player_gold.get(pid, 0.0), 4)] for pid in self.players.keys()]
        picked_arr = [[pid, bool(self.players_who_picked.get(pid, False))] for pid in self.players.keys()]
        return {
            "type": "tick",
            "edges": edges_arr,
            "nodes": nodes_arr,
            "counts": counts,
            "totalNodes": len(self.nodes),
            "phase": self.phase,
            "gold": gold_arr,
            "picked": picked_arr,
        }

    def simulate_tick(self, tick_interval_seconds: float) -> None:
        # Direction/flow toggles are input-driven; no random changes here
        # Compute size deltas from production and flows
        size_delta: Dict[int, float] = {nid: 0.0 for nid in self.nodes.keys()}

        # Production for owned nodes
        for node in self.nodes.values():
            if node.owner is not None:
                size_delta[node.id] += PRODUCTION_RATE_PER_NODE

        # Flows using percentage-based transfer per source node, split across its outgoing flowing edges
        pending_ownership: Dict[int, int] = {}  # node_id -> new_owner_id
        outgoing_by_node: Dict[int, List[int]] = {}
        for e in self.edges.values():
            if not e.flowing:
                continue
            src_id = e.source_node_id if e.forward else e.target_node_id
            outgoing_by_node.setdefault(src_id, []).append(e.id)

        # Compute per-edge transfer amounts
        per_edge_amount: Dict[int, float] = {}
        for src_id, edge_ids in outgoing_by_node.items():
            src_node = self.nodes.get(src_id)
            if src_node is None:
                continue
            total_transfer = src_node.juice * TRANSFER_PERCENT_PER_TICK
            if total_transfer <= 0 or len(edge_ids) == 0:
                continue
            amount_each = total_transfer / len(edge_ids)
            for eid in edge_ids:
                per_edge_amount[eid] = amount_each

        # Apply transfers
        for eid, amount in per_edge_amount.items():
            edge = self.edges.get(eid)
            if edge is None:
                continue
            from_id = edge.source_node_id if edge.forward else edge.target_node_id
            to_id = edge.target_node_id if edge.forward else edge.source_node_id
            from_node = self.nodes.get(from_id)
            to_node = self.nodes.get(to_id)
            if from_node is None or to_node is None:
                continue
            size_delta[from_id] -= amount
            if to_node.owner is None or (from_node.owner is not None and to_node.owner != from_node.owner):
                size_delta[to_id] -= amount
                projected = max(NODE_MIN_JUICE, to_node.juice + size_delta[to_id])
                if projected <= NODE_MIN_JUICE and from_node.owner is not None:
                    pending_ownership[to_id] = from_node.owner
            else:
                size_delta[to_id] += amount

        # Apply deltas and clamp
        for nid, delta in size_delta.items():
            node = self.nodes[nid]
            node.juice = max(NODE_MIN_JUICE, min(NODE_MAX_JUICE, node.juice + delta))

        # Apply pending ownership changes
        for nid, new_owner in pending_ownership.items():
            node = self.nodes.get(nid)
            if node is None:
                continue
            if node.juice <= NODE_MIN_JUICE:
                node.owner = new_owner

        # Economy: increment gold each tick, capped at max sections
        if GOLD_SECTION_FILL_SECONDS > 0:
            increment = tick_interval_seconds / GOLD_SECTION_FILL_SECONDS
            for pid in list(self.players.keys()):
                current = self.player_gold.get(pid, 0.0)
                new_val = min(GOLD_MAX_SECTIONS, current + increment)
                self.player_gold[pid] = new_val

        # Auto-adjust bidirectional edge directions based on ownership and node sizes
        for e in self.edges.values():
            if not e.bidirectional:
                continue
            a = self.nodes.get(e.source_node_id)
            b = self.nodes.get(e.target_node_id)
            if a is None or b is None:
                continue
            a_owner = a.owner
            b_owner = b.owner
            desired_forward: Optional[bool] = None
            # If both endpoints owned by the same player, leave direction as-is (manual control allowed)
            if a_owner is not None and a_owner == b_owner:
                desired_forward = e.forward
            # If opposing owners, face toward the smaller node (by juice)
            elif (a_owner is not None) and (b_owner is not None) and a_owner != b_owner:
                if (a.juice or 0) <= (b.juice or 0):
                    desired_forward = False  # b -> a (toward smaller 'a')
                else:
                    desired_forward = True  # a -> b (toward smaller 'b')
            # If one side owned and the other unowned, face toward the unowned
            elif a_owner is not None and b_owner is None:
                desired_forward = True  # a -> b
            elif a_owner is None and b_owner is not None:
                desired_forward = False  # b -> a
            else:
                desired_forward = e.forward  # both unowned, no change

            # Apply desired direction and turn off if it changed (auto-swap)
            if desired_forward is not None and desired_forward != e.forward:
                e.forward = desired_forward
                e.on = False
                e.flowing = False


def load_graph(graph_path: Path) -> Tuple[GraphState, Dict[str, int]]:
    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    screen = data.get("screen", {})
    nodes_raw = data["nodes"]
    edges_raw = data["edges"]
    nodes: List[Node] = [Node(id=n["id"], x=n["x"], y=n["y"], juice=2.0) for n in nodes_raw]
    edges: List[Edge] = [
        Edge(id=e["id"], source_node_id=e["source"], target_node_id=e["target"], bidirectional=bool(e["bidirectional"]))
        for e in edges_raw
    ]
    return GraphState(nodes, edges), screen


def build_state_from_dict(data: Dict) -> Tuple[GraphState, Dict[str, int]]:
    screen = data.get("screen", {"width": 100, "height": 100, "margin": 0})
    nodes_raw = data["nodes"]
    edges_raw = data["edges"]
    # Start nodes very small (juice units)
    nodes: List[Node] = [Node(id=n["id"], x=n["x"], y=n["y"], juice=2.0) for n in nodes_raw]
    edges: List[Edge] = [
        Edge(id=e["id"], source_node_id=e["source"], target_node_id=e["target"], bidirectional=bool(e["bidirectional"]))
        for e in edges_raw
    ]
    return GraphState(nodes, edges), screen


