import json
import random
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

from .models import Edge, Node, Player


EDGE_TOGGLE_PROBABILITY: float = 0.0
NODE_MIN_JUICE: float = 0.0
NODE_MAX_JUICE: float = 120.0

# Flow model
PRODUCTION_RATE_PER_NODE: float = 0.15  # owned nodes generate this per tick (constant growth)
TRANSFER_PERCENT_PER_TICK: float = 0.01  # fraction of source juice transferred per tick (split across outgoing edges)
INTAKE_BONUS_DIVISOR: float = 100.0  # divisor for intake bonus calculation in transfer rate

# Gold economy - no limit, no natural generation
GOLD_REWARD_FOR_CAPTURE: float = 2.0  # gold awarded when capturing unowned nodes
STARTING_GOLD: float = 0.0  # players now start empty and earn gold from captures

# Unowned node sizing
UNOWNED_NODE_BASE_SIZE: float = 2.0  # base juice amount for unowned nodes (back to original)
UNOWNED_NODE_BASE_JUICE: float = 2.0  # base juice amount for unowned nodes


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
        # Track if game has ended
        self.game_ended: bool = False
        self.winner_id: Optional[int] = None

        # Track eliminated players so they can remain as spectators
        self.eliminated_players: Set[int] = set()
        self.pending_eliminations: List[int] = []
        
        # Timer system
        self.game_start_time: Optional[float] = None
        self.game_duration: float = 5 * 60  # 5 minutes in seconds
        
        # Auto-expand settings per player
        self.player_auto_expand: Dict[int, bool] = {}
        self.pending_auto_expand_nodes: Dict[int, Set[int]] = {}
        
        # Game speed level (1-10, default 6 for 1x speed)
        self.speed_level: int = 6
        

    def add_player(self, player: Player) -> None:
        self.players[player.id] = player
        # Initialize player economy and pick status
        self.player_gold[player.id] = STARTING_GOLD
        self.players_who_picked[player.id] = False
        # Initialize auto-expand setting (default: off)
        self.player_auto_expand[player.id] = False
        self.eliminated_players.discard(player.id)

    def get_speed_multiplier(self) -> float:
        """Get speed multiplier based on speed level (1-10)."""
        speed_multipliers = [0.1, 0.2, 0.4, 0.6, 0.8, 1.0, 1.25, 1.5, 1.75, 2.0]
        # Speed levels are 1-10, but array is 0-9 indexed
        level_index = max(0, min(9, self.speed_level - 1))
        return speed_multipliers[level_index]

    def get_scaled_production_rate(self) -> float:
        """Get production rate scaled by speed level."""
        speed_multiplier = self.get_speed_multiplier()
        return PRODUCTION_RATE_PER_NODE * speed_multiplier

    def get_scaled_transfer_percent(self) -> float:
        """Get transfer percentage scaled by speed level."""
        speed_multiplier = self.get_speed_multiplier()
        return TRANSFER_PERCENT_PER_TICK * speed_multiplier

    def get_scaled_intake_divisor(self) -> float:
        """Get intake bonus divisor scaled by speed level."""
        speed_multiplier = self.get_speed_multiplier()
        return INTAKE_BONUS_DIVISOR / speed_multiplier

    def get_player_node_counts(self) -> Dict[int, int]:
        """Return a mapping of player id to number of nodes they currently own."""
        counts: Dict[int, int] = {pid: 0 for pid in self.players.keys()}
        for node in self.nodes.values():
            if node.owner is not None:
                counts[node.owner] = counts.get(node.owner, 0) + 1
        return counts

    def get_active_player_ids(self) -> List[int]:
        """Return the list of players still alive in the match."""
        return [pid for pid in self.players.keys() if pid not in self.eliminated_players]



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
        node_counts = self.get_player_node_counts()

        # Collect players meeting or exceeding the threshold who are still active
        candidates = [
            pid for pid, count in node_counts.items()
            if pid not in self.eliminated_players and count >= win_threshold
        ]

        if not candidates:
            return None

        # Determine top candidate and ensure there is no tie on node count
        best_pid = max(candidates, key=lambda pid: node_counts.get(pid, 0))
        best_count = node_counts.get(best_pid, 0)
        if sum(1 for pid in candidates if node_counts.get(pid, 0) == best_count) > 1:
            return None

        self.game_ended = True
        self.winner_id = best_pid
        return best_pid

    def start_game_timer(self, current_time: float) -> None:
        """Start the game timer when the game begins."""
        if self.game_start_time is None:
            self.game_start_time = current_time

    def check_timer_expiration(self, current_time: float) -> Optional[int]:
        """Check if the game timer has expired. Returns winner ID or None."""
        if self.game_ended:
            return self.winner_id
            
        # Only check timer after picking phase is complete
        if self.phase == "picking":
            return None
            
        if self.game_start_time is None:
            return None
            
        elapsed_time = current_time - self.game_start_time
        if elapsed_time >= self.game_duration:
            # Timer expired - determine winner by node count
            node_counts = self.get_player_node_counts()
            active_players = [pid for pid in self.players.keys() if pid not in self.eliminated_players]

            if not active_players:
                return None

            winner_id = max(active_players, key=lambda pid: node_counts.get(pid, 0))
            max_nodes = node_counts.get(winner_id, 0)
            if sum(1 for pid in active_players if node_counts.get(pid, 0) == max_nodes) > 1:
                return None

            self.game_ended = True
            self.winner_id = winner_id
            return winner_id
                
        return None

    def check_zero_nodes_loss(self) -> Optional[int]:
        """Eliminate players with zero nodes. Returns winner ID when only one remains."""
        if self.game_ended:
            return self.winner_id
        
        # Only check for zero nodes loss after picking phase is complete
        if self.phase == "picking":
            return None
            
        node_counts = self.get_player_node_counts()
        newly_eliminated: List[int] = []

        for player_id in self.players.keys():
            if player_id in self.eliminated_players:
                continue
            if node_counts.get(player_id, 0) == 0:
                self.eliminated_players.add(player_id)
                newly_eliminated.append(player_id)

        if newly_eliminated:
            self.pending_eliminations.extend(newly_eliminated)

        active_players = [pid for pid in self.players.keys() if pid not in self.eliminated_players]
        if len(active_players) == 1:
            self.game_ended = True
            self.winner_id = active_players[0]
            return self.winner_id

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
        players_arr = [
            {
                "id": pid,
                "color": p.color,
                "secondaryColors": list(getattr(p, "secondary_colors", [])),
                "eliminated": pid in self.eliminated_players,
            }
            for pid, p in self.players.items()
        ]
        gold_arr = [[pid, round(self.player_gold.get(pid, 0.0), 4)] for pid in self.players.keys()]
        picked_arr = [[pid, bool(self.players_who_picked.get(pid, False))] for pid in self.players.keys()]
        auto_expand_arr = [[pid, bool(self.player_auto_expand.get(pid, False))] for pid in self.players.keys()]
        
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
            "winThreshold": win_threshold,
            "totalNodes": len(self.nodes),
            "autoExpand": auto_expand_arr,
            "eliminatedPlayers": sorted(self.eliminated_players),
        }

    def to_tick_message(self, current_time: float = 0.0) -> Dict:
        edges_arr = [[eid, 1 if e.on else 0, 1 if e.flowing else 0, 1] for eid, e in self.edges.items()]  # Always forward now
        nodes_arr = [
            [nid, round(n.juice, 3), (n.owner if n.owner is not None else None)]
            for nid, n in self.nodes.items()
        ]
        counts = self.get_player_node_counts()
        gold_arr = [[pid, round(self.player_gold.get(pid, 0.0), 4)] for pid in self.players.keys()]
        picked_arr = [[pid, bool(self.players_who_picked.get(pid, False))] for pid in self.players.keys()]
        auto_expand_arr = [[pid, bool(self.player_auto_expand.get(pid, False))] for pid in self.players.keys()]
        
        # Calculate win threshold for progress bar
        win_threshold = self.calculate_win_threshold()
        eliminated_players = sorted(self.eliminated_players)
        recent_eliminations = list(self.pending_eliminations)
        self.pending_eliminations = []
        
        return {
            "type": "tick",
            "edges": edges_arr,
            "nodes": nodes_arr,
            "counts": counts,
            "totalNodes": len(self.nodes),
            "phase": self.phase,
            "gold": gold_arr,
            "picked": picked_arr,
            "winThreshold": win_threshold,
            "autoExpand": auto_expand_arr,
            "eliminatedPlayers": eliminated_players,
            "recentEliminations": recent_eliminations,
        }

    def _update_edge_flowing_status(self) -> None:
        """
        Update the flowing status of all edges based on whether their target nodes can receive flow.
        An edge can only flow if:
        1. It is turned on (on = True)
        2. For friendly flows: target node is not full (juice < NODE_MAX_JUICE)
        3. For attacking flows: always flow when on (regardless of target capacity)
        """
        for edge in self.edges.values():
            if not edge.on:
                # Edge is not turned on, so it cannot flow
                edge.flowing = False
                continue
            
            # Get source and target nodes
            source_node = self.nodes.get(edge.source_node_id)
            target_node = self.nodes.get(edge.target_node_id)
            
            if source_node is None or target_node is None:
                edge.flowing = False
                continue
            
            # Check if this is an attacking flow (source and target have different owners)
            if (source_node.owner is not None and 
                target_node.owner is not None and 
                source_node.owner != target_node.owner):
                # This is an attacking flow - it can always flow when on
                edge.flowing = True
            elif (source_node.owner is not None and 
                  (target_node.owner is None or target_node.owner == source_node.owner)):
                # This is a friendly flow - it can only flow if target is not full
                if target_node.juice >= NODE_MAX_JUICE:
                    edge.flowing = False
                else:
                    edge.flowing = True
            else:
                # Edge is on but cannot flow for other reasons
                edge.flowing = False

    def simulate_tick(self, tick_interval_seconds: float) -> None:
        # Update flowing status for all edges based on target node capacity
        self._update_edge_flowing_status()
        
        # Direction/flow toggles are input-driven; no random changes here
        # Compute size deltas from production and flows
        size_delta: Dict[int, float] = {nid: 0.0 for nid in self.nodes.keys()}
        
        # Track juice intake for each node (from friendly nodes only)
        intake_tracking: Dict[int, float] = {nid: 0.0 for nid in self.nodes.keys()}

        # Production for owned nodes
        scaled_production_rate = self.get_scaled_production_rate()
        for node in self.nodes.values():
            if node.owner is not None:
                base_rate = scaled_production_rate
                size_delta[node.id] += base_rate

        # Flows using variable percentage based on cur_intake
        pending_ownership: Dict[int, int] = {}  # node_id -> new_owner_id
        outgoing_by_node: Dict[int, List[int]] = {}
        for e in self.edges.values():
            if not e.flowing:
                continue
            src_id = e.source_node_id  # All edges flow from source to target
            outgoing_by_node.setdefault(src_id, []).append(e.id)

        # Compute per-edge transfer amounts using variable percentage
        per_edge_amount: Dict[int, float] = {}
        for src_id, edge_ids in outgoing_by_node.items():
            src_node = self.nodes.get(src_id)
            if src_node is None:
                continue
            
            # Calculate variable transfer percentage based on cur_intake
            # Base percentage is 1%, plus bonus based on intake
            # X = cur_intake / INTAKE_BONUS_DIVISOR, so output = (1 + cur_intake/INTAKE_BONUS_DIVISOR)%
            base_percentage = self.get_scaled_transfer_percent()  # Scaled transfer percentage
            intake_bonus = src_node.cur_intake / self.get_scaled_intake_divisor()  # Scaled intake bonus
            variable_percentage = base_percentage + intake_bonus
            
            total_transfer = src_node.juice * variable_percentage
            if total_transfer <= 0 or len(edge_ids) == 0:
                continue
            amount_each = total_transfer / len(edge_ids)
            for eid in edge_ids:
                per_edge_amount[eid] = amount_each

        # Apply transfers and track friendly intake
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
            
            # Track intake for target node (only from friendly nodes)
            if from_node.owner is not None and to_node.owner == from_node.owner:
                intake_tracking[to_id] += amount
            
            if to_node.owner is None or (from_node.owner is not None and to_node.owner != from_node.owner):
                size_delta[to_id] -= amount
                projected = max(NODE_MIN_JUICE, to_node.juice + size_delta[to_id])
                if projected <= NODE_MIN_JUICE and from_node.owner is not None:
                    pending_ownership[to_id] = from_node.owner
            else:
                size_delta[to_id] += amount

        # Update cur_intake for all nodes
        for nid, intake in intake_tracking.items():
            self.nodes[nid].cur_intake = intake

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
                    # Store the capture event for frontend notification
                    if not hasattr(self, 'pending_node_captures'):
                        self.pending_node_captures = []
                    self.pending_node_captures.append({
                        'nodeId': nid,
                        'reward': GOLD_REWARD_FOR_CAPTURE
                    })
                node.owner = new_owner

        # All edges are now one-way only - no auto-adjustment needed
        
        # Handle auto-expand for newly captured nodes
        for nid, new_owner in pending_ownership.items():
            if self.player_auto_expand.get(new_owner, False):
                self._auto_expand_from_node(nid, new_owner)

        self.enforce_eliminated_edges_off()

    def process_pending_auto_expands(self) -> None:
        """Run any auto-expand operations that were deferred during the picking phase."""
        if not self.pending_auto_expand_nodes:
            return

        for player_id, node_ids in list(self.pending_auto_expand_nodes.items()):
            if not self.player_auto_expand.get(player_id, False):
                continue

            for node_id in list(node_ids):
                self._apply_auto_expand_from_node(node_id, player_id)

        self.pending_auto_expand_nodes.clear()

    def _auto_expand_from_node(self, node_id: int, player_id: int) -> None:
        """
        Auto-expand from a newly captured node by turning on edges to unowned surrounding nodes.
        This method finds all edges from the captured node to unowned nodes and turns them on.
        """
        if self.phase == "picking":
            pending = self.pending_auto_expand_nodes.setdefault(player_id, set())
            pending.add(node_id)
            return

        self._apply_auto_expand_from_node(node_id, player_id)

    def enforce_eliminated_edges_off(self) -> None:
        """Force edges owned by eliminated players to remain off."""
        for edge in self.edges.values():
            source_node = self.nodes.get(edge.source_node_id)
            if source_node and source_node.owner in self.eliminated_players:
                edge.on = False
                edge.flowing = False

    def _apply_auto_expand_from_node(self, node_id: int, player_id: int) -> None:
        captured_node = self.nodes.get(node_id)
        if not captured_node or captured_node.owner != player_id:
            return

        # Find all edges from this node to unowned nodes
        for edge_id in captured_node.attached_edge_ids:
            edge = self.edges.get(edge_id)
            if not edge:
                continue

            # Check if this edge goes from the captured node to an unowned node
            target_node_id = None
            if edge.source_node_id == node_id:
                target_node_id = edge.target_node_id
            elif edge.target_node_id == node_id:
                # This edge goes TO the captured node, so we need to reverse it first
                # But for auto-expand, we only want to turn on edges that already point outward
                continue

            if target_node_id:
                target_node = self.nodes.get(target_node_id)
                if target_node and target_node.owner is None:
                    # This is an unowned node that can be captured - turn on the edge
                    edge.on = True

    def toggle_auto_expand(self, player_id: int) -> bool:
        """
        Toggle the auto-expand setting for a player.
        Returns the new state of the setting.
        """
        if player_id not in self.player_auto_expand:
            self.player_auto_expand[player_id] = False
        
        self.player_auto_expand[player_id] = not self.player_auto_expand[player_id]
        return self.player_auto_expand[player_id]


def load_graph(graph_path: Path) -> Tuple[GraphState, Dict[str, int]]:
    with open(graph_path, "r", encoding="utf-8") as f:
        data = json.load(f)
    screen = data.get("screen", {})
    nodes_raw = data["nodes"]
    edges_raw = data["edges"]
    nodes: List[Node] = [Node(id=n["id"], x=n["x"], y=n["y"], juice=2.0, cur_intake=0.0) for n in nodes_raw]
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
    nodes: List[Node] = [Node(id=n["id"], x=n["x"], y=n["y"], juice=2.0, cur_intake=0.0) for n in nodes_raw]
    edges: List[Edge] = [
        Edge(id=e["id"], source_node_id=e["source"], target_node_id=e["target"])
        for e in edges_raw
    ]
    return GraphState(nodes, edges), screen
