import asyncio
import json
import uuid
from pathlib import Path
from typing import Dict, List, Optional, Set, Tuple

import websockets

from .models import Player
from .state import GraphState, load_graph, build_state_from_dict
from . import generate_graph as gen_graph


GRAPH_PATH: Path = Path(__file__).resolve().parent.parent / "graph.json"
TICK_INTERVAL_SECONDS: float = 0.1
WEBSOCKET_HOST: str = "0.0.0.0"
WEBSOCKET_PORT: int = 8765


class WebSocketServer:
    def __init__(self, state: GraphState, screen: Dict[str, int]) -> None:
        self.state = state
        self.screen = screen
        self.clients: Set[websockets.WebSocketServerProtocol] = set()
        self.broadcast_task: Optional[asyncio.Task] = None
        # Lobby and game management (single lobby/game for now)
        self.lobby_waiting: Optional[Tuple[str, websockets.WebSocketServerProtocol]] = None  # (token, ws)
        self.game_clients: Dict[str, websockets.WebSocketServerProtocol] = {}  # token -> ws
        self.token_to_player_id: Dict[str, int] = {}
        self.token_to_color: Dict[str, str] = {}
        self.ws_to_token: Dict[websockets.WebSocketServerProtocol, str] = {}

    async def handler(self, websocket: websockets.WebSocketServerProtocol) -> None:
        self.clients.add(websocket)
        try:
            # If a game is active, immediately send init so reloads rejoin
            if self.state.nodes and self.game_clients:
                # Already in a game; try to reattach if token later provided
                pass

            async for raw in websocket:
                try:
                    msg = json.loads(raw)
                except Exception:
                    continue
                await self._handle_client_message(websocket, msg)
        finally:
            # Handle disconnect: if part of lobby, clear waiting; if part of game, schedule grace for reconnect
            # Remove ws from mappings
            tok_to_remove = None
            for tok, ws in list(self.game_clients.items()):
                if ws is websocket:
                    tok_to_remove = tok
                    break
            if self.lobby_waiting and self.lobby_waiting[1] is websocket:
                self.lobby_waiting = None
            if tok_to_remove:
                self.ws_to_token.pop(websocket, None)
                # Temporarily remove mapping; allow grace period to reconnect before declaring forfeit
                self.game_clients.pop(tok_to_remove, None)
                asyncio.create_task(self._handle_disconnect_grace(tok_to_remove))
            self.clients.discard(websocket)

    async def _handle_client_message(self, websocket: websockets.WebSocketServerProtocol, msg: Dict) -> None:
        mtype = msg.get("type")
        token = msg.get("token")
        if mtype == "clickNode":
            node_id = msg.get("nodeId")
            if token is None or node_id is None:
                return
            player_id = self.token_to_player_id.get(token)
            if player_id is None:
                return
            # Only allow during picking phase and only once per player
            if getattr(self.state, "phase", "picking") != "picking":
                return
            if self.state.players_who_picked.get(player_id):
                return
            node = self.state.nodes.get(node_id)
            if node is None:
                return
            # Verify unowned
            if node.owner is None:
                node.owner = player_id
                self.state.players_who_picked[player_id] = True
                # If all players picked, transition to playing phase
                if all(self.state.players_who_picked.get(pid, False) for pid in self.state.players.keys()):
                    self.state.phase = "playing"
        elif mtype == "clickEdge":
            # Left click to start flow if from-node is owned by player
            edge_id = msg.get("edgeId")
            if token is None or edge_id is None:
                return
            player_id = self.token_to_player_id.get(token)
            if player_id is None:
                return
            # Only allow edge interactions during playing phase
            if getattr(self.state, "phase", "picking") != "playing":
                return
            edge = self.state.edges.get(edge_id)
            if edge is None:
                return
            # Toggle behavior: if already on, turn off; else attempt to turn on
            if edge.on or edge.flowing:
                edge.on = False
                edge.flowing = False
            else:
                from_id = edge.source_node_id if edge.forward else edge.target_node_id
                from_node = self.state.nodes.get(from_id)
                if from_node and from_node.owner == player_id:
                    edge.on = True
                    edge.flowing = True
        elif mtype == "toggleEdgeDirection":
            # Right click for bidirectional edges: flip direction
            edge_id = msg.get("edgeId")
            if edge_id is None:
                return
            # Only allow during playing phase
            if getattr(self.state, "phase", "picking") != "playing":
                return
            edge = self.state.edges.get(edge_id)
            if edge is None or not edge.bidirectional:
                return
            # Determine enforced direction rules based on ownership
            token = msg.get("token")
            player_id = self.token_to_player_id.get(token) if token else None
            from_id = edge.source_node_id if edge.forward else edge.target_node_id
            to_id = edge.target_node_id if edge.forward else edge.source_node_id
            a_id = edge.source_node_id
            b_id = edge.target_node_id
            a = self.state.nodes.get(a_id)
            b = self.state.nodes.get(b_id)
            if a is None or b is None:
                return
            a_owner = a.owner
            b_owner = b.owner
            # If a single player owns both sides, allow manual swap only if requester owns both
            if a_owner is not None and a_owner == b_owner:
                if player_id is not None and player_id == a_owner:
                    # Manual swap allowed; turn edge on and set flowing
                    edge.forward = not edge.forward
                    edge.on = True
                    edge.flowing = True
                return
            # If opposing owners on ends, force direction toward the smaller node (by juice)
            if (a_owner is not None) and (b_owner is not None) and a_owner != b_owner:
                if (a.juice or 0) <= (b.juice or 0):
                    edge.forward = False  # b -> a (toward smaller 'a')
                else:
                    edge.forward = True  # a -> b (toward smaller 'b')
                edge.on = False
                edge.flowing = False
                return
            # If one side owned and the other unowned, face toward the unowned
            if (a_owner is not None and b_owner is None):
                edge.forward = True  # a -> b (toward unowned b)
                edge.on = False
                edge.flowing = False
                return
            if (a_owner is None and b_owner is not None):
                edge.forward = False  # b -> a (toward unowned a)
                edge.on = False
                edge.flowing = False
                return
            # If both unowned, allow no manual change
            return
        elif mtype == "newGame":
            # Generate a fresh graph in 100x100 logical space; frontend will scale
            def _make():
                return gen_graph.generate_node_positions(gen_graph.NODE_COUNT, 100, 100, 0), gen_graph.generate_planar_edges(
                    gen_graph.generate_node_positions(gen_graph.NODE_COUNT, 100, 100, 0), gen_graph.DESIRED_EDGE_COUNT, gen_graph.ONE_WAY_PERCENT
                )

            # Ensure deterministic single generation of nodes for edges
            def _make_once():
                nodes = gen_graph.generate_node_positions(gen_graph.NODE_COUNT, 100, 100, 0)
                edges = gen_graph.generate_planar_edges(nodes, gen_graph.DESIRED_EDGE_COUNT, gen_graph.ONE_WAY_PERCENT)
                return nodes, edges

            nodes, edges = await asyncio.to_thread(_make_once)
            data = {
                "screen": {"width": 100, "height": 100, "margin": 0},
                "nodes": [{"id": n.id, "x": round(n.x, 3), "y": round(n.y, 3)} for n in nodes],
                "edges": [
                    {"id": e.id, "source": e.source, "target": e.target, "bidirectional": e.bidirectional}
                    for e in edges
                ],
            }
            new_state, screen = build_state_from_dict(data)
            # Preserve players (ensure Player 1 exists)
            self.state = new_state
            self.screen = screen
            if 1 not in self.state.players:
                self.state.add_player(Player(id=1, color="#ffcc00"))
            await websocket.send(json.dumps(self.state.to_init_message(self.screen, TICK_INTERVAL_SECONDS)))
        elif mtype == "requestInit":
            # Optional token for rejoin
            if token and token in self.token_to_player_id and self.state.nodes:
                # Attach this websocket for broadcasts
                self.game_clients[token] = websocket
                self.ws_to_token[websocket] = token
                init = self.state.to_init_message(self.screen, TICK_INTERVAL_SECONDS)
                init["myPlayerId"] = self.token_to_player_id[token]
                init["token"] = token
                await websocket.send(json.dumps(init))
        elif mtype == "joinLobby":
            # Assign token and possibly start game when two players are present
            tok = token or uuid.uuid4().hex
            color = "#ffcc00" if self.lobby_waiting is None else "#66ccff"
            self.token_to_color[tok] = color
            if self.lobby_waiting is None:
                self.lobby_waiting = (tok, websocket)
                await websocket.send(json.dumps({"type": "lobbyJoined", "status": "waiting", "token": tok}))
            else:
                # Start game with two players
                other_tok, other_ws = self.lobby_waiting
                self.lobby_waiting = None
                # Generate map
                def _make_once():
                    nodes = gen_graph.generate_node_positions(gen_graph.NODE_COUNT, 100, 100, 0)
                    edges = gen_graph.generate_planar_edges(nodes, gen_graph.DESIRED_EDGE_COUNT, gen_graph.ONE_WAY_PERCENT)
                    return nodes, edges
                nodes, edges = await asyncio.to_thread(_make_once)
                data = {
                    "screen": {"width": 100, "height": 100, "margin": 0},
                    "nodes": [{"id": n.id, "x": round(n.x, 3), "y": round(n.y, 3)} for n in nodes],
                    "edges": [
                        {"id": e.id, "source": e.source, "target": e.target, "bidirectional": e.bidirectional}
                        for e in edges
                    ],
                }
                new_state, screen = build_state_from_dict(data)
                self.state = new_state
                self.screen = screen
                # Create players
                # Force colors: Player 1 = red, Player 2 = blue
                p1 = Player(id=1, color="#ff3333")
                p2 = Player(id=2, color="#3388ff")
                self.state.add_player(p1)
                self.state.add_player(p2)
                # Ensure phase starts at picking for fresh game
                self.state.phase = "picking"
                self.token_to_player_id = {other_tok: 1, tok: 2}
                self.game_clients = {other_tok: other_ws, tok: websocket}
                self.ws_to_token[other_ws] = other_tok
                self.ws_to_token[websocket] = tok
                # Send start/init with myPlayerId and token
                init_common = self.state.to_init_message(self.screen, TICK_INTERVAL_SECONDS)
                init1 = dict(init_common)
                init1["type"] = "init"
                init1["myPlayerId"] = 1
                init1["token"] = other_tok
                await other_ws.send(json.dumps(init1))
                init2 = dict(init_common)
                init2["type"] = "init"
                init2["myPlayerId"] = 2
                init2["token"] = tok
                await websocket.send(json.dumps(init2))
        elif mtype == "quitGame":
            if token not in self.token_to_player_id:
                return
            loser_id = self.token_to_player_id[token]
            winner_id = 1 if loser_id == 2 else 2
            # Notify both players of game over but do NOT clear state; stop ticking by clearing client map
            msg = json.dumps({"type": "gameOver", "winnerId": winner_id})
            for tkn, ws in list(self.game_clients.items()):
                try:
                    await ws.send(msg)
                except Exception:
                    pass
            # Keep last state; just stop further broadcasts
            self.token_to_player_id.clear()
            self.game_clients.clear()
            self.ws_to_token.clear()

    async def _handle_disconnect_grace(self, token: str) -> None:
        # Give the client a short window to reconnect before declaring forfeit
        await asyncio.sleep(2.0)
        # If game already ended or client rejoined, do nothing
        if not self.token_to_player_id:
            return
        if token in self.game_clients:
            return
        if token not in self.token_to_player_id:
            return
        # Treat as quit
        loser_id = self.token_to_player_id[token]
        winner_id = 1 if loser_id == 2 else 2
        msg = json.dumps({"type": "gameOver", "winnerId": winner_id})
        for tkn, ws in list(self.game_clients.items()):
            try:
                await ws.send(msg)
            except Exception:
                pass
        self.state = GraphState([], [])
        self.token_to_player_id.clear()
        self.game_clients.clear()
        self.ws_to_token.clear()

    async def start(self) -> None:
        async with websockets.serve(self.handler, WEBSOCKET_HOST, WEBSOCKET_PORT, ping_interval=20, ping_timeout=20):
            self.broadcast_task = asyncio.create_task(self._broadcast_loop())
            await asyncio.Future()

    async def _broadcast_loop(self) -> None:
        while True:
            await asyncio.sleep(TICK_INTERVAL_SECONDS)
            # Only tick/broadcast when a game is active (two players assigned)
            if not self.game_clients:
                continue
            self.state.simulate_tick(TICK_INTERVAL_SECONDS)
            msg = json.dumps(self.state.to_tick_message())
            await self._broadcast(msg)

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
    # Start with empty state; client will request new game
    state = GraphState([], [])
    screen = {"width": 100, "height": 100, "margin": 0}
    server = WebSocketServer(state, screen)
    print(f"WebSocket server starting on ws://{WEBSOCKET_HOST}:{WEBSOCKET_PORT}")
    await server.start()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        print("Shutting down.")


