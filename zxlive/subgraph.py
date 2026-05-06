"""Subgraph management for zxlive.

The SubgraphManager tracks subgraph annotations on the graph scene,
assigning IDs and enforcing one-subgraph-per-vertex.  The subgraph
data model lives in pyzx (Subgraph, CircuitLike, etc.).
"""

from __future__ import annotations

import uuid
from typing import Optional

from pyzx.circuitlike import Subgraph, CircuitLike

from .common import VT


class SubgraphManager:
    """Manages subgraphs for a graph.

    Provides create/delete/query operations.  Each vertex can belong to
    at most one subgraph.
    """

    def __init__(self) -> None:
        self._subgraphs: dict[str, Subgraph] = {}
        self._vertex_to_subgraph: dict[VT, str] = {}

    def add(self, subgraph: Subgraph) -> str:
        """Register a subgraph.  Returns its assigned ID.

        Raises ValueError if any vertex is already in a subgraph.
        """
        for v in subgraph.vertices:
            if v in self._vertex_to_subgraph:
                existing = self._vertex_to_subgraph[v]
                raise ValueError(
                    f"Vertex {v} already belongs to subgraph {existing}")

        sg_id = str(uuid.uuid4())[:8]
        self._subgraphs[sg_id] = subgraph
        for v in subgraph.vertices:
            self._vertex_to_subgraph[v] = sg_id
        return sg_id

    def delete(self, sg_id: str) -> None:
        """Remove a subgraph."""
        sg = self._subgraphs.pop(sg_id, None)
        if sg is None:
            return
        for v in sg.vertices:
            self._vertex_to_subgraph.pop(v, None)

    def get(self, sg_id: str) -> Optional[Subgraph]:
        return self._subgraphs.get(sg_id)

    def get_by_vertex(self, v: VT) -> Optional[Subgraph]:
        sg_id = self._vertex_to_subgraph.get(v)
        if sg_id is None:
            return None
        return self._subgraphs.get(sg_id)

    def get_id(self, subgraph: Subgraph) -> Optional[str]:
        """Find the ID for a given subgraph instance."""
        for sg_id, sg in self._subgraphs.items():
            if sg is subgraph:
                return sg_id
        return None

    def all_subgraphs(self) -> list[tuple[str, Subgraph]]:
        """Return all (id, subgraph) pairs."""
        return list(self._subgraphs.items())

    def clear(self) -> None:
        self._subgraphs.clear()
        self._vertex_to_subgraph.clear()

    def merge(self, sg_id_a: str, sg_id_b: str) -> str:
        """Merge two subgraphs into one (vertex set union).

        CircuitLike structure is not preserved — the result is a
        plain Subgraph.  Returns the new subgraph's ID.
        Raises ValueError if vertex sets overlap or IDs not found.
        """
        a = self._subgraphs.get(sg_id_a)
        b = self._subgraphs.get(sg_id_b)
        if a is None or b is None:
            raise ValueError("Subgraph not found")

        overlap = a.vertices & b.vertices
        if overlap:
            raise ValueError(f"Vertex sets overlap: {overlap}")

        merged = Subgraph(vertices=a.vertices | b.vertices, g=a.g)
        self.delete(sg_id_a)
        self.delete(sg_id_b)
        return self.add(merged)
