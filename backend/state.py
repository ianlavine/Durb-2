import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Tuple

from .models import Edge, Node, Player


EDGE_TOGGLE_PROBABILITY: float = 0.0
NODE_MIN_JUICE: float = 0.0
NODE_MAX_JUICE: float = 120.0

# Flow model
PRODUCTION_RATE_PER_NODE: float = 0.15  # owned nodes generate this per tick (constant growth)
TRANSFER_PERCENT_PER_TICK: float = 0.01  # fraction of source juice transferred per tick (split across outgoing edges)

# Gold economy - no limit, no natural generation
GOLD_REWARD_FOR_CAPTURE: float = 2.0  # gold awarded when capturing unowned nodes
STARTING_GOLD: float = 3.0  # gold each player starts with


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
        # Track capital nodes (double growth rate)
        self.capital_nodes: set = set()
        # Track if game has ended due to capital victory
        self.game_ended: bool = False
        self.winner_id: Optional[int] = None
        

    def add_player(self, player: Player) -> None:
        self.players[player.id] = player
        # Initialize player economy and pick status
        self.player_gold[player.id] = STARTING_GOLD
        self.players_who_picked[player.id] = False


    def check_capital_victory(self) -> Optional[int]:
        """Check if any player has 5 capitals. Returns winner ID or None."""
        if self.game_ended:
            return self.winner_id
            
        capital_counts = {}
        for capital_id in self.capital_nodes:
            node = self.nodes.get(capital_id)
            if node and node.owner is not None:
                capital_counts[node.owner] = capital_counts.get(node.owner, 0) + 1
                
        # Check for victory condition (5 capitals)
        for player_id, count in capital_counts.items():
            if count >= 5:
                self.game_ended = True
                self.winner_id = player_id
                return player_id
                
        return None

    def calculate_win_threshold(self) -> int:
        """Calculate the number of nodes needed to win based on total nodes (2/3 rule)."""
        total_nodes = len(self.nodes)
        if total_nodes % 3 == 0:
            # If divisible by 3, need exactly 2/3
            return (total_nodes * 2) // 3
        else:
            # If not divisible by 3, get as close to 2/3 as possible
            return (total_nodes * 2 + 2) // 3  # This rounds up to get closest to 2/3

    def check_node_count_victory(self) -> Optional[int]:
        """Check if any player has reached the win threshold. Returns winner ID or None."""
        if self.game_ended:
            return self.winner_id
            
        # Only check after picking phase is complete
        if self.phase == "picking":
            return None
            
        win_threshold = self.calculate_win_threshold()
        
        # Count nodes owned by each player
        node_counts = {}
        for node in self.nodes.values():
            if node.owner is not None:
                node_counts[node.owner] = node_counts.get(node.owner, 0) + 1
        
        # Check if any player has reached the win threshold
        for player_id in self.players.keys():
            if node_counts.get(player_id, 0) >= win_threshold:
                self.game_ended = True
                self.winner_id = player_id
                return player_id
                
        return None

    def check_zero_nodes_loss(self) -> Optional[int]:
        """Check if any player has 0 nodes. Returns winner ID (the other player) or None."""
        if self.game_ended:
            return self.winner_id
        
        # Only check for zero nodes loss after picking phase is complete
        if self.phase == "picking":
            return None
            
        # Count nodes owned by each player
        node_counts = {}
        for node in self.nodes.values():
            if node.owner is not None:
                node_counts[node.owner] = node_counts.get(node.owner, 0) + 1
        
        # Check if any player has 0 nodes
        for player_id in self.players.keys():
            if node_counts.get(player_id, 0) == 0:
                # This player has no nodes - they lose
                # Winner is the other player
                for other_player_id in self.players.keys():
                    if other_player_id != player_id:
                        self.game_ended = True
                        self.winner_id = other_player_id
                        return other_player_id
        
        return None

    def to_init_message(self, screen: Dict[str, int], tick_interval: float, current_time: float = 0.0) -> Dict:
        nodes_arr = [
            [nid, round(n.x, 3), round(n.y, 3), round(n.juice, 3), (n.owner if n.owner is not None else None)]
            for nid, n in self.nodes.items()
        ]
        edges_arr = [
            [eid, e.source_node_id, e.target_node_id, 0, 1]  # Always one-way, always forward
            for eid, e in self.edges.items()
        ]
        players_arr = [[pid, p.color] for pid, p in self.players.items()]
        gold_arr = [[pid, round(self.player_gold.get(pid, 0.0), 4)] for pid in self.players.keys()]
        picked_arr = [[pid, bool(self.players_who_picked.get(pid, False))] for pid in self.players.keys()]
        
        # Count capitals by player for init
        capital_counts: Dict[int, int] = {}
        for capital_id in self.capital_nodes:
            node = self.nodes.get(capital_id)
            if node and node.owner is not None:
                capital_counts[node.owner] = capital_counts.get(node.owner, 0) + 1
        capital_arr = [[pid, capital_counts.get(pid, 0)] for pid in self.players.keys()]
        
        # Calculate win threshold for progress bar
        win_threshold = self.calculate_win_threshold()
        
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
            "capitals": capital_arr,
            "winThreshold": win_threshold,
            "totalNodes": len(self.nodes),
        }

    def to_tick_message(self, current_time: float = 0.0) -> Dict:
        edges_arr = [[eid, 1 if e.on else 0, 1 if e.flowing else 0, 1] for eid, e in self.edges.items()]  # Always forward now
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
        
        # Count capitals by player
        capital_counts: Dict[int, int] = {}
        for capital_id in self.capital_nodes:
            node = self.nodes.get(capital_id)
            if node and node.owner is not None:
                capital_counts[node.owner] = capital_counts.get(node.owner, 0) + 1
        capital_arr = [[pid, capital_counts.get(pid, 0)] for pid in self.players.keys()]
        
        # Calculate win threshold for progress bar
        win_threshold = self.calculate_win_threshold()
        
        return {
            "type": "tick",
            "edges": edges_arr,
            "nodes": nodes_arr,
            "counts": counts,
            "totalNodes": len(self.nodes),
            "phase": self.phase,
            "gold": gold_arr,
            "picked": picked_arr,
            "capitals": capital_arr,
            "winThreshold": win_threshold,
        }

    def simulate_tick(self, tick_interval_seconds: float) -> None:
        # Direction/flow toggles are input-driven; no random changes here
        # Compute size deltas from production and flows
        size_delta: Dict[int, float] = {nid: 0.0 for nid in self.nodes.keys()}

        # Production for owned nodes (capitals get double rate)
        for node in self.nodes.values():
            if node.owner is not None:
                base_rate = PRODUCTION_RATE_PER_NODE
                if node.id in self.capital_nodes:
                    base_rate *= 2.0  # Double growth for capitals
                size_delta[node.id] += base_rate

        # Flows using percentage-based transfer per source node, split across its outgoing flowing edges
        pending_ownership: Dict[int, int] = {}  # node_id -> new_owner_id
        outgoing_by_node: Dict[int, List[int]] = {}
        for e in self.edges.values():
            if not e.flowing:
                continue
            src_id = e.source_node_id  # All edges flow from source to target
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
            from_id = edge.source_node_id  # All edges flow from source to target
            to_id = edge.target_node_id
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

        # Apply pending ownership changes and award gold for capturing unowned nodes
        for nid, new_owner in pending_ownership.items():
            node = self.nodes.get(nid)
            if node is None:
                continue
            if node.juice <= NODE_MIN_JUICE:
                # Check if this was an unowned node being captured
                if node.owner is None:
                    # Award gold for capturing unowned node
                    self.player_gold[new_owner] = self.player_gold.get(new_owner, 0.0) + GOLD_REWARD_FOR_CAPTURE
                node.owner = new_owner

        # All edges are now one-way only - no auto-adjustment needed


def load_graph(graph_path: Path) -> Tuple[GraphState, Dict[str, int]]:
    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    screen = data.get("screen", {})
    nodes_raw = data["nodes"]
    edges_raw = data["edges"]
    nodes: List[Node] = [Node(id=n["id"], x=n["x"], y=n["y"], juice=2.0) for n in nodes_raw]
    edges: List[Edge] = [
        Edge(id=e["id"], source_node_id=e["source"], target_node_id=e["target"])
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
        Edge(id=e["id"], source_node_id=e["source"], target_node_id=e["target"])
        for e in edges_raw
    ]
    return GraphState(nodes, edges), screen


