"""Standalone HTTP service that exposes `resolve_sharp_angles` for sandbox testing."""
from __future__ import annotations

import argparse
import json
import logging
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Any, Dict, List, Tuple

try:  # pragma: no cover - allow running as module or script
    from .game_engine import GameEngine
    from .graph_generator import graph_generator
    from .models import Edge, Node
    from .sharp_angle_handler import resolve_sharp_angles
    from .state import GraphState
except ImportError:  # pragma: no cover
    import pathlib
    import sys

    ROOT = pathlib.Path(__file__).resolve().parent.parent
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    from backend.game_engine import GameEngine
    from backend.graph_generator import graph_generator
    from backend.models import Edge, Node
    from backend.sharp_angle_handler import resolve_sharp_angles
    from backend.state import GraphState

LOGGER = logging.getLogger("sharp_angle_service")


def _parse_nodes(payload_nodes: List[Dict[str, Any]]) -> List[Node]:
    nodes: List[Node] = []
    for entry in payload_nodes:
        node = Node(
            id=int(entry["id"]),
            x=float(entry["x"]),
            y=float(entry["y"]),
        )
        nodes.append(node)
    return nodes


def _parse_edges(payload_edges: List[Dict[str, Any]]) -> List[Edge]:
    edges: List[Edge] = []
    for entry in payload_edges:
        edge = Edge(
            id=int(entry["id"]),
            source_node_id=int(entry["sourceId"]),
            target_node_id=int(entry["targetId"]),
        )
        segments = entry.get("warpSegments")
        if isinstance(segments, list):
            sanitized: List[Tuple[float, float, float, float]] = []
            for seg in segments:
                if not isinstance(seg, (list, tuple)) or len(seg) != 4:
                    continue
                sx, sy, ex, ey = seg
                sanitized.append((float(sx), float(sy), float(ex), float(ey)))
            if sanitized:
                edge.warp_segments = sanitized
        edges.append(edge)
    return edges


def _build_engine_state(nodes_data: List[Dict[str, Any]], edges_data: List[Dict[str, Any]]) -> Tuple[GameEngine, GraphState]:
    nodes = _parse_nodes(nodes_data)
    edges = _parse_edges(edges_data)
    state = GraphState(nodes, edges)
    engine = GameEngine()
    engine.state = state
    return engine, state


def _run_resolver(payload: Dict[str, Any]) -> Dict[str, Any]:
    nodes_data = payload.get("nodes")
    edges_data = payload.get("edges")
    new_edge_id = payload.get("newEdgeId")

    if not isinstance(nodes_data, list) or not isinstance(edges_data, list):
        raise ValueError("Payload must include 'nodes' and 'edges' arrays")
    if new_edge_id is None:
        raise ValueError("Payload must include 'newEdgeId'")

    engine, state = _build_engine_state(nodes_data, edges_data)
    new_edge = state.edges.get(int(new_edge_id))
    if not new_edge:
        raise ValueError("Specified newEdgeId was not found in edges list")

    movements = resolve_sharp_angles(engine, new_edge)

    response = {
        "movements": movements,
        "nodes": [
            {"id": node.id, "x": node.x, "y": node.y}
            for node in state.nodes.values()
        ],
    }
    return response


def _generate_graph(payload: Dict[str, Any]) -> Dict[str, Any]:
    mode = payload.get("mode") if isinstance(payload, dict) else None
    if not isinstance(mode, str) or not mode.strip():
        mode = "sparse"

    data = graph_generator.generate_game_data_sync(mode=mode)
    nodes_payload = [
        {
            "id": int(node_info.get("id", idx)),
            "x": float(node_info.get("x", 0.0)),
            "y": float(node_info.get("y", 0.0)),
        }
        for idx, node_info in enumerate(data.get("nodes") or [])
    ]
    edges_payload = []
    for idx, edge_info in enumerate(data.get("edges") or []):
        edge_id = edge_info.get("id", idx)
        source = edge_info.get("source")
        target = edge_info.get("target")
        if source is None or target is None:
            continue
        edges_payload.append(
            {
                "id": int(edge_id),
                "sourceId": int(source),
                "targetId": int(target),
                "warpSegments": edge_info.get("warpSegments", []),
            }
        )

    return {
        "screen": data.get("screen", {}),
        "nodes": nodes_payload,
        "edges": edges_payload,
        "mode": mode,
    }


class SharpAngleRequestHandler(BaseHTTPRequestHandler):
    server_version = "SharpAngleService/1.0"

    def _set_common_headers(self, status: int = 200, content_type: str = "application/json") -> None:
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Access-Control-Allow-Origin", "*")
        self.send_header("Access-Control-Allow-Methods", "POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type")

    def do_OPTIONS(self) -> None:  # noqa: N802 (HTTP verb naming)
        self._set_common_headers()
        self.end_headers()

    def _handle_resolve(self, payload: Dict[str, Any]) -> None:
        response = _run_resolver(payload)
        body = json.dumps(response).encode("utf-8")
        self._set_common_headers(200)
        self.end_headers()
        self.wfile.write(body)

    def _handle_generate(self, payload: Dict[str, Any]) -> None:
        response = _generate_graph(payload)
        body = json.dumps(response).encode("utf-8")
        self._set_common_headers(200)
        self.end_headers()
        self.wfile.write(body)

    def do_POST(self) -> None:  # noqa: N802
        if self.path not in {"/resolve", "/generate"}:
            self._set_common_headers(404)
            self.end_headers()
            return

        content_length = int(self.headers.get("Content-Length", 0))
        raw_body = self.rfile.read(content_length)

        try:
            payload = json.loads(raw_body.decode("utf-8"))
            if self.path == "/resolve":
                self._handle_resolve(payload)
            else:
                self._handle_generate(payload)
        except ValueError as err:
            LOGGER.exception("Bad request: %s", err)
            self._set_common_headers(400)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(err)}).encode("utf-8"))
        except Exception as exc:  # pragma: no cover - debugging convenience
            LOGGER.exception("Resolver failed")
            self._set_common_headers(500)
            self.end_headers()
            self.wfile.write(json.dumps({"error": str(exc)}).encode("utf-8"))

    def log_message(self, format: str, *args: object) -> None:  # noqa: D401
        """Suppress default HTTP server logging to keep console output clean."""
        return


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the sharp-angle resolver HTTP service.")
    parser.add_argument("--host", default="127.0.0.1", help="Host/interface to bind (default: 127.0.0.1)")
    parser.add_argument("--port", type=int, default=5051, help="Port to listen on (default: 5051)")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    server = HTTPServer((args.host, args.port), SharpAngleRequestHandler)
    LOGGER.info("Sharp angle service listening on http://%s:%s", args.host, args.port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:  # pragma: no cover - manual shutdown
        LOGGER.info("Shutting down service")
        server.server_close()


if __name__ == "__main__":
    main()
