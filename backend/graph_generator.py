"""
Graph Generator - Centralized graph generation utilities.
Eliminates duplication and provides consistent graph generation.
"""
import asyncio
from typing import Dict, List, Tuple, Any
from . import generate_graph as gen_graph


class GraphGenerator:
    """Handles graph generation with consistent parameters and async support."""
    
    def __init__(self):
        self.default_width = 100
        self.default_height = 100
        self.default_margin = 0
    
    async def generate_async(self, width: int = None, height: int = None, margin: int = None) -> Tuple[List, List]:
        """
        Generate a graph asynchronously to avoid blocking the event loop.
        Returns: (nodes, edges)
        """
        w = width or self.default_width
        h = height or self.default_height
        m = margin or self.default_margin
        
        # Run generation in thread pool to avoid blocking
        nodes, edges = await asyncio.to_thread(self._generate_sync, w, h, m)
        return nodes, edges
    
    def _generate_sync(self, width: int, height: int, margin: int) -> Tuple[List, List]:
        """Synchronous graph generation."""
        nodes = gen_graph.generate_node_positions(gen_graph.NODE_COUNT, width, height, margin)
        edges = gen_graph.generate_planar_edges(nodes, gen_graph.DESIRED_EDGE_COUNT, gen_graph.ONE_WAY_PERCENT)
        
        # Remove isolated nodes after graph generation
        nodes, edges = gen_graph.remove_isolated_nodes(nodes, edges)
        
        return nodes, edges
    
    def generate_sync(self, width: int = None, height: int = None, margin: int = None) -> Tuple[List, List]:
        """
        Generate a graph synchronously.
        Returns: (nodes, edges)
        """
        w = width or self.default_width
        h = height or self.default_height
        m = margin or self.default_margin
        
        return self._generate_sync(w, h, m)
    
    def nodes_edges_to_dict(self, nodes: List, edges: List, width: int = None, height: int = None) -> Dict[str, Any]:
        """
        Convert nodes and edges to the standard dictionary format.
        """
        w = width or self.default_width
        h = height or self.default_height
        
        return {
            "screen": {"width": w, "height": h, "margin": 0},
            "nodes": [{"id": n.id, "x": round(n.x, 3), "y": round(n.y, 3)} for n in nodes],
            "edges": [
                {"id": e.id, "source": e.source_node_id, "target": e.target_node_id, "bidirectional": False}
                for e in edges
            ],
        }
    
    async def generate_game_data_async(self, width: int = None, height: int = None) -> Dict[str, Any]:
        """
        Generate complete game data dictionary asynchronously.
        Returns the full data structure ready for build_state_from_dict.
        """
        nodes, edges = await self.generate_async(width, height)
        return self.nodes_edges_to_dict(nodes, edges, width, height)
    
    def generate_game_data_sync(self, width: int = None, height: int = None) -> Dict[str, Any]:
        """
        Generate complete game data dictionary synchronously.
        Returns the full data structure ready for build_state_from_dict.
        """
        nodes, edges = self.generate_sync(width, height)
        return self.nodes_edges_to_dict(nodes, edges, width, height)


# Global instance for convenience
graph_generator = GraphGenerator()
