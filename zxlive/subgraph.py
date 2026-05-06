"""Subgraph management for typed subdiagram annotations in zxlive.

A Subgraph groups vertices in the graph into named, typed regions
(e.g., "Clifford unitary", "Pauli box") and stores their CircuitLike
metadata.  The SubgraphManager keeps track of all subgraphs for a
given graph scene.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Optional

from pyzx.circuitlike import CircuitLike

from .common import VT, ET, GraphT


@dataclass
class Subgraph:
    """A named, typed subdiagram annotation."""
    id: str
    subgraph_type: Optional[str]  # "clifford", "pauli_box", or None
    circuit_like: CircuitLike
    params: dict = field(default_factory=dict)  # e.g. {"pauli_string": "XZ"}

    @property
    def vertices(self) -> set[VT]:
        return self.circuit_like.vertices


class SubgraphManager:
    """Manages subgraphs for a graph.

    Provides create/delete/query operations.  Each vertex can belong to
    at most one subgraph.
    """

    def __init__(self) -> None:
        self._subgraphs: dict[str, Subgraph] = {}
        self._vertex_to_subgraph: dict[VT, str] = {}

    def create(
        self,
        circuit_like: CircuitLike,
        subgraph_type: Optional[str] = None,
        params: Optional[dict] = None,
    ) -> Subgraph:
        """Create a new subgraph.  Raises if any vertex is already in a subgraph."""
        for v in circuit_like.vertices:
            if v in self._vertex_to_subgraph:
                existing = self._vertex_to_subgraph[v]
                raise ValueError(
                    f"Vertex {v} already belongs to subgraph {existing}")

        sg_id = str(uuid.uuid4())[:8]
        sg = Subgraph(
            id=sg_id,
            subgraph_type=subgraph_type,
            circuit_like=circuit_like,
            params=params or {},
        )
        self._subgraphs[sg_id] = sg
        for v in circuit_like.vertices:
            self._vertex_to_subgraph[v] = sg_id
        return sg

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

    def all_subgraphs(self) -> list[Subgraph]:
        return list(self._subgraphs.values())

    def clear(self) -> None:
        self._subgraphs.clear()
        self._vertex_to_subgraph.clear()
