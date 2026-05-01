"""Tests for the merge subdiagram feature.

Level 1: Pure graph logic (no Qt scene required).
Level 2: Scene-level integration (uses qtbot).
"""

from __future__ import annotations

from fractions import Fraction

import pytest
from pyzx import EdgeType, VertexType
from pytestqt.qtbot import QtBot

from zxlive.common import new_graph
from zxlive.graphscene import GraphScene
from zxlive.merge import merge_subdiagram, point_on_segment


# ---------------------------------------------------------------------------
# Level 1: point_on_segment
# ---------------------------------------------------------------------------

class TestPointOnSegment:
    def test_midpoint(self) -> None:
        assert point_on_segment(0.5, 0.0, 0.0, 0.0, 1.0, 0.0)

    def test_quarter(self) -> None:
        assert point_on_segment(0.25, 0.0, 0.0, 0.0, 1.0, 0.0)

    def test_at_endpoint_a(self) -> None:
        assert not point_on_segment(0.0, 0.0, 0.0, 0.0, 1.0, 0.0)

    def test_at_endpoint_b(self) -> None:
        assert not point_on_segment(1.0, 0.0, 0.0, 0.0, 1.0, 0.0)

    def test_off_line(self) -> None:
        assert not point_on_segment(0.5, 1.0, 0.0, 0.0, 1.0, 0.0)

    def test_beyond_segment(self) -> None:
        assert not point_on_segment(2.0, 0.0, 0.0, 0.0, 1.0, 0.0)

    def test_diagonal(self) -> None:
        assert point_on_segment(0.5, 0.5, 0.0, 0.0, 1.0, 1.0)

    def test_degenerate(self) -> None:
        assert not point_on_segment(0.0, 0.0, 0.0, 0.0, 0.0, 0.0)


# ---------------------------------------------------------------------------
# Level 1: merge_subdiagram — pure graph tests
# ---------------------------------------------------------------------------

def _make_pair_graph():
    """Create a graph with bottom vertices C, D connected by a simple edge,
    and top vertices A, B at the same positions connected by a simple edge.

    Layout: C and A at (0, 0), D and B at (1, 0).
    Returns (graph, A, B, C, D).
    """
    g = new_graph()
    c = g.add_vertex(VertexType.Z, qubit=0, row=0)
    d = g.add_vertex(VertexType.Z, qubit=0, row=1)
    g.add_edge((c, d), EdgeType.SIMPLE)

    a = g.add_vertex(VertexType.X, qubit=0, row=0)
    b = g.add_vertex(VertexType.X, qubit=0, row=1)
    g.add_edge((a, b), EdgeType.SIMPLE)

    return g, a, b, c, d


class TestMergeNodeOnNode:
    def test_basic_merge_vertices_removed(self) -> None:
        """Selected vertices A, B should be removed after merging onto C, D."""
        g, a, b, c, d = _make_pair_graph()
        result = merge_subdiagram(g, [a, b])
        verts = set(result.vertices())
        assert a not in verts
        assert b not in verts
        assert c in verts
        assert d in verts

    def test_top_type_wins(self) -> None:
        """Target vertices should take the type of the top (selected) vertices."""
        g, a, b, c, d = _make_pair_graph()
        assert g.type(c) == VertexType.Z
        result = merge_subdiagram(g, [a, b])
        assert result.type(c) == VertexType.X
        assert result.type(d) == VertexType.X

    def test_top_phase_wins(self) -> None:
        """Target vertices should take the phase of the top vertices."""
        g, a, b, c, d = _make_pair_graph()
        g.set_phase(a, Fraction(1, 4))
        g.set_phase(c, Fraction(1, 2))
        result = merge_subdiagram(g, [a, b])
        assert result.phase(c) == Fraction(1, 4)

    def test_overlapping_edges_dedup(self) -> None:
        """When top and bottom both have 1 simple edge at curve 0,
        result should have 1 edge (not 2)."""
        g, a, b, c, d = _make_pair_graph()
        result = merge_subdiagram(g, [a, b])
        edges = list(result.edges(c, d))
        assert len(edges) == 1

    def test_different_type_edges_dedup_by_curve(self) -> None:
        """Top has simple edge, bottom has hadamard edge, both at curve 0.
        They overlay, so result should have 1 edge (the top's simple type)."""
        g = new_graph()
        c = g.add_vertex(VertexType.Z, qubit=0, row=0)
        d = g.add_vertex(VertexType.Z, qubit=0, row=1)
        g.add_edge((c, d), EdgeType.HADAMARD)

        a = g.add_vertex(VertexType.X, qubit=0, row=0)
        b = g.add_vertex(VertexType.X, qubit=0, row=1)
        g.add_edge((a, b), EdgeType.SIMPLE)

        result = merge_subdiagram(g, [a, b])
        edges = list(result.edges(c, d))
        assert len(edges) == 1
        assert result.edge_type(edges[0]) == EdgeType.SIMPLE

    def test_non_overlapping_edges_become_parallel(self) -> None:
        """Top and bottom edges at different curves should both survive."""
        g, a, b, c, d = _make_pair_graph()
        # Give top edge a different curve
        e_top = next(g.edges(a, b))
        result = merge_subdiagram(g, [a, b],
                                  edge_curves={
                                      next(g.edges(c, d)): [0.0],
                                      e_top: [0.5],
                                  })
        edges = list(result.edges(c, d))
        assert len(edges) == 2

    def test_external_edges_redirected(self) -> None:
        """Edges from a selected vertex to a non-selected vertex
        should be redirected to the merge target."""
        g = new_graph()
        c = g.add_vertex(VertexType.Z, qubit=0, row=0)
        ext = g.add_vertex(VertexType.Z, qubit=1, row=0)

        a = g.add_vertex(VertexType.X, qubit=0, row=0)
        g.add_edge((a, ext), EdgeType.SIMPLE)

        result = merge_subdiagram(g, [a])
        assert result.connected(c, ext)

    def test_self_loop_avoided(self) -> None:
        """An edge from selected vertex to its merge target
        should not create a self-loop."""
        g = new_graph()
        c = g.add_vertex(VertexType.Z, qubit=0, row=0)
        a = g.add_vertex(VertexType.X, qubit=0, row=0)
        g.add_edge((a, c), EdgeType.SIMPLE)

        result = merge_subdiagram(g, [a])
        assert a not in result.vertices()
        # No self-loop on c
        for e in result.edges():
            s, t = result.edge_st(e)
            assert not (s == c and t == c)

    def test_no_merge_target_vertex_stays(self) -> None:
        """A selected vertex with no unselected vertex at the same position
        should remain in the graph."""
        g = new_graph()
        a = g.add_vertex(VertexType.X, qubit=5, row=5)
        result = merge_subdiagram(g, [a])
        assert a in result.vertices()

    def test_empty_selection(self) -> None:
        """Empty selection should return a copy of the original graph."""
        g = new_graph()
        g.add_vertex(VertexType.Z, qubit=0, row=0)
        result = merge_subdiagram(g, [])
        assert set(result.vertices()) == set(g.vertices())

    def test_original_not_mutated(self) -> None:
        """The original graph should not be modified."""
        g, a, b, c, d = _make_pair_graph()
        orig_verts = set(g.vertices())
        merge_subdiagram(g, [a, b])
        assert set(g.vertices()) == orig_verts


class TestMergeNodeOnEdge:
    def test_snap_onto_simple_edge(self) -> None:
        """A selected vertex sitting on a simple edge should split it."""
        g = new_graph()
        s = g.add_vertex(VertexType.Z, qubit=0, row=0)
        t = g.add_vertex(VertexType.Z, qubit=0, row=2)
        g.add_edge((s, t), EdgeType.SIMPLE)

        # Place a selected vertex at midpoint of the edge
        v = g.add_vertex(VertexType.X, qubit=0, row=1)

        result = merge_subdiagram(g, [v])
        assert v in result.vertices()
        # Original edge should be gone
        assert not result.connected(s, t)
        # Two new edges should connect through v
        assert result.connected(s, v)
        assert result.connected(v, t)

    def test_snap_onto_hadamard_edge(self) -> None:
        """Snapping onto a Hadamard edge: one leg Hadamard, one leg simple."""
        g = new_graph()
        s = g.add_vertex(VertexType.Z, qubit=0, row=0)
        t = g.add_vertex(VertexType.Z, qubit=0, row=2)
        g.add_edge((s, t), EdgeType.HADAMARD)

        v = g.add_vertex(VertexType.X, qubit=0, row=1)

        result = merge_subdiagram(g, [v])
        edges_sv = list(result.edges(s, v))
        edges_vt = list(result.edges(v, t))
        assert len(edges_sv) == 1
        assert len(edges_vt) == 1
        types = {result.edge_type(edges_sv[0]), result.edge_type(edges_vt[0])}
        assert types == {EdgeType.HADAMARD, EdgeType.SIMPLE}

    def test_no_snap_onto_selected_edge(self) -> None:
        """A selected vertex should not snap onto an edge between
        other selected vertices."""
        g = new_graph()
        s = g.add_vertex(VertexType.X, qubit=0, row=0)
        t = g.add_vertex(VertexType.X, qubit=0, row=2)
        g.add_edge((s, t), EdgeType.SIMPLE)

        v = g.add_vertex(VertexType.Z, qubit=0, row=1)

        # All three are selected — v should NOT snap onto s-t edge
        result = merge_subdiagram(g, [s, t, v])
        # s-t edge should still exist
        assert result.connected(s, t)
        # v should not be connected to s or t
        assert not result.connected(s, v)
        assert not result.connected(v, t)

    def test_vertex_not_on_edge_stays_free(self) -> None:
        """A vertex not on any edge should remain unconnected."""
        g = new_graph()
        s = g.add_vertex(VertexType.Z, qubit=0, row=0)
        t = g.add_vertex(VertexType.Z, qubit=0, row=2)
        g.add_edge((s, t), EdgeType.SIMPLE)

        # Place vertex off the edge
        v = g.add_vertex(VertexType.X, qubit=5, row=5)

        result = merge_subdiagram(g, [v])
        assert v in result.vertices()
        assert not result.connected(s, v)
        assert not result.connected(v, t)


class TestMergeMixed:
    def test_some_merge_some_snap(self) -> None:
        """One selected vertex merges onto a node, another snaps onto an edge."""
        g = new_graph()
        # Bottom: c at (0,0), with edge to d at (0,2)
        c = g.add_vertex(VertexType.Z, qubit=0, row=0)
        d = g.add_vertex(VertexType.Z, qubit=0, row=2)
        g.add_edge((c, d), EdgeType.SIMPLE)

        # Top: a at (0,0) merges onto c; b at (0,1) snaps onto c-d edge
        a = g.add_vertex(VertexType.X, qubit=0, row=0)
        b = g.add_vertex(VertexType.X, qubit=0, row=1)

        result = merge_subdiagram(g, [a, b])
        # a merged into c
        assert a not in result.vertices()
        assert result.type(c) == VertexType.X
        # b snapped onto c-d edge
        assert b in result.vertices()
        assert result.connected(c, b) or result.connected(b, d)


# ---------------------------------------------------------------------------
# Level 2: Scene-level integration
# ---------------------------------------------------------------------------

class TestMergeScene:
    def test_scene_update_after_merge(self, qtbot: QtBot) -> None:
        """Verify the scene reflects the merge: merged vertices gone,
        targets updated."""
        g = new_graph()
        c = g.add_vertex(VertexType.Z, qubit=0, row=0)
        d = g.add_vertex(VertexType.Z, qubit=0, row=1)
        g.add_edge((c, d), EdgeType.SIMPLE)

        a = g.add_vertex(VertexType.X, qubit=0, row=0)
        b = g.add_vertex(VertexType.X, qubit=0, row=1)
        g.add_edge((a, b), EdgeType.SIMPLE)

        scene = GraphScene()
        scene.set_graph(g)

        # Extract curves from scene
        edge_curves = {
            e: [eitems[idx].curve_distance for idx in sorted(eitems)]
            for e, eitems in scene.edge_map.items()
        }

        new_g = merge_subdiagram(g, [a, b], edge_curves)
        scene.update_graph(new_g)

        assert a not in scene.vertex_map
        assert b not in scene.vertex_map
        assert c in scene.vertex_map
        assert d in scene.vertex_map

    def test_scene_edge_count_after_dedup(self, qtbot: QtBot) -> None:
        """Overlapping edges should be deduplicated when curves match."""
        g = new_graph()
        c = g.add_vertex(VertexType.Z, qubit=0, row=0)
        d = g.add_vertex(VertexType.Z, qubit=0, row=1)
        g.add_edge((c, d), EdgeType.SIMPLE)

        a = g.add_vertex(VertexType.X, qubit=0, row=0)
        b = g.add_vertex(VertexType.X, qubit=0, row=1)
        g.add_edge((a, b), EdgeType.SIMPLE)

        scene = GraphScene()
        scene.set_graph(g)

        edge_curves = {
            e: [eitems[idx].curve_distance for idx in sorted(eitems)]
            for e, eitems in scene.edge_map.items()
        }

        new_g = merge_subdiagram(g, [a, b], edge_curves)
        scene.update_graph(new_g)

        # Should be exactly 1 edge between c and d
        edges = list(new_g.edges(c, d))
        assert len(edges) == 1
