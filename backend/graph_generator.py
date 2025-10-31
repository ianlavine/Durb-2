"""
Graph Generator - Centralized graph generation utilities.
Eliminates duplication and provides consistent graph generation.
"""
import asyncio
import random
from typing import Dict, List, Tuple, Any

from . import generate_graph as gen_graph
from .constants import DEFAULT_GAME_MODE, normalize_game_mode


class GraphGenerator:
    """Handles graph generation with consistent parameters and async support."""
    
    def __init__(self):
        # Default to a wider aspect so playfields stretch further horizontally than vertically
        self.default_width = 220
        self.default_height = 90
        self.default_margin = 0
        self.mode_configs = {
            "basic": {
                "edge_func": gen_graph.generate_planar_edges,
                "desired_edges": gen_graph.DESIRED_EDGE_COUNT,
            },
            "warp-old": {
                "edge_func": gen_graph.generate_planar_edges,
                "desired_edges": gen_graph.DESIRED_EDGE_COUNT,
            },
            "sparse": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
            "overflow": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
            "nuke": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
            "cross": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
            "brass": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
            "warp": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
            "flat": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
            "go": {
                "edge_func": gen_graph.generate_sparse_edges,
                "desired_edges": None,
            },
        }

    def _resolve_mode(self, mode: str = None) -> str:
        requested_mode = mode or DEFAULT_GAME_MODE
        normalized = normalize_game_mode(requested_mode)
        if normalized not in self.mode_configs:
            return DEFAULT_GAME_MODE
        return normalized

    async def generate_async(
        self,
        width: int = None,
        height: int = None,
        margin: int = None,
        mode: str = None,
    ) -> Tuple[List, List]:
        """
        Generate a graph asynchronously to avoid blocking the event loop.
        Returns: (nodes, edges)
        """
        w = width or self.default_width
        h = height or self.default_height
        m = margin or self.default_margin
        resolved_mode = self._resolve_mode(mode)

        # Run generation in thread pool to avoid blocking
        nodes, edges = await asyncio.to_thread(self._generate_sync, w, h, m, resolved_mode)
        return nodes, edges
    
    def _generate_sync(self, width: int, height: int, margin: int, mode: str) -> Tuple[List, List]:
        """Synchronous graph generation."""
        config = self.mode_configs.get(mode, self.mode_configs[DEFAULT_GAME_MODE])
        nodes = gen_graph.generate_node_positions(gen_graph.NODE_COUNT, width, height, margin)
        edge_func = config["edge_func"]
        desired_edges = config.get("desired_edges")
        if desired_edges is None:
            edges = edge_func(nodes, None, gen_graph.ONE_WAY_PERCENT)
        else:
            edges = edge_func(nodes, desired_edges, gen_graph.ONE_WAY_PERCENT)

        # Remove isolated nodes after graph generation
        nodes, edges = gen_graph.remove_isolated_nodes(nodes, edges)

        if mode == "brass":
            brass_cap = 20
            for node in nodes:
                node.node_type = "normal"
            if nodes:
                brass_count = min(brass_cap, len(nodes))
                brass_pool = random.sample(nodes, brass_count)
                for node in brass_pool:
                    node.node_type = "brass"

        gen_graph.apply_layout_scaling(nodes, width, height)

        print(f"{len(nodes)} , {len(edges)}")

        return nodes, edges

    def generate_sync(
        self,
        width: int = None,
        height: int = None,
        margin: int = None,
        mode: str = None,
    ) -> Tuple[List, List]:
        """
        Generate a graph synchronously.
        Returns: (nodes, edges)
        """
        w = width or self.default_width
        h = height or self.default_height
        m = margin or self.default_margin
        resolved_mode = self._resolve_mode(mode)

        return self._generate_sync(w, h, m, resolved_mode)
    
    def nodes_edges_to_dict(self, nodes: List, edges: List, width: int = None, height: int = None) -> Dict[str, Any]:
        """
        Convert nodes and edges to the standard dictionary format.
        """
        min_x = min((n.x for n in nodes), default=0.0)
        max_x = max((n.x for n in nodes), default=0.0)
        min_y = min((n.y for n in nodes), default=0.0)
        max_y = max((n.y for n in nodes), default=0.0)

        screen = {
            "width": round(max_x - min_x, 3),
            "height": round(max_y - min_y, 3),
            "minX": round(min_x, 3),
            "minY": round(min_y, 3),
            "margin": 0,
        }

        return {
            "screen": screen,
            "nodes": [
                {
                    "id": n.id,
                    "x": round(n.x, 3),
                    "y": round(n.y, 3),
                    "nodeType": getattr(n, "node_type", "normal"),
                }
                for n in nodes
            ],
            "edges": [
                {"id": e.id, "source": e.source_node_id, "target": e.target_node_id, "bidirectional": False}
                for e in edges
            ],
        }
    
    async def generate_game_data_async(
        self,
        width: int = None,
        height: int = None,
        mode: str = None,
    ) -> Dict[str, Any]:
        """
        Generate complete game data dictionary asynchronously.
        Returns the full data structure ready for build_state_from_dict.
        """
        nodes, edges = await self.generate_async(width, height, mode=mode)
        return self.nodes_edges_to_dict(nodes, edges, width, height)
    
    def generate_game_data_sync(
        self,
        width: int = None,
        height: int = None,
        mode: str = None,
    ) -> Dict[str, Any]:
        """
        Generate complete game data dictionary synchronously.
        Returns the full data structure ready for build_state_from_dict.
        """
        nodes, edges = self.generate_sync(width, height, mode=mode)
        return self.nodes_edges_to_dict(nodes, edges, width, height)


# Global instance for convenience
graph_generator = GraphGenerator()
