"""Pure graph logic for merging a selected subdiagram onto the graph beneath it.

This module has no dependency on Qt or the scene — it operates entirely on
PyZX graph objects, making it easy to test in isolation.
"""

from __future__ import annotations

import copy
from typing import Optional

from pyzx import EdgeType

from .common import ET, VT, GraphT


def point_on_segment(px: float, py: float,
                     ax: float, ay: float,
                     bx: float, by: float,
                     tol: float = 1e-6) -> bool:
    """Check if point (px, py) lies strictly on the segment from (ax, ay) to (bx, by)."""
    dx, dy = bx - ax, by - ay
    length_sq = dx * dx + dy * dy
    if length_sq < tol * tol:
        return False  # Degenerate segment
    # Cross product for collinearity
    cross = dx * (py - ay) - dy * (px - ax)
    if abs(cross) > tol * (abs(dx) + abs(dy) + 1):
        return False
    # Parameter along segment (0 = at a, 1 = at b)
    param = (dx * (px - ax) + dy * (py - ay)) / length_sq
    return tol < param < 1 - tol


def merge_subdiagram(
    g: GraphT,
    selected: list[VT],
    edge_curves: Optional[dict[ET, list[float]]] = None,
) -> GraphT:
    """Merge a selected subdiagram onto the graph beneath it.

    For each selected vertex:
    - If an unselected vertex is at the same position: merge into it
      (top vertex's type/phase wins, edges redirected)
    - If sitting on an unselected edge: snap into that edge
      (edge split, vertex inserted)
    - Otherwise: leave in place

    For edges between pairs of merged vertices: edges that exactly
    overlay (same curve distance) are deduplicated; non-matching edges
    become parallel edges.

    Args:
        g: The original graph (not modified).
        selected: Vertex IDs of the selected "top" subdiagram.
        edge_curves: Optional mapping from edge tuple to a list of curve
            distances (one per parallel instance). Used for deduplication.
            If None, all edges are assumed to have curve distance 0.0.

    Returns:
        A new graph with the merge applied.
    """
    if not selected:
        return copy.deepcopy(g)

    new_g = copy.deepcopy(g)
    selected_set = set(selected)
    if edge_curves is None:
        edge_curves = {}

    # Build position -> unselected vertex lookup
    pos_to_unselected: dict[tuple[float, float], VT] = {}
    for v in new_g.vertices():
        if v not in selected_set:
            pos = (new_g.row(v), new_g.qubit(v))
            pos_to_unselected[pos] = v

    # Phase 1: Identify node-on-node merges
    merge_map: dict[VT, VT] = {}
    for v in selected:
        pos = (new_g.row(v), new_g.qubit(v))
        if pos in pos_to_unselected:
            merge_map[v] = pos_to_unselected[pos]

    merging_set = set(merge_map.keys())

    # Phase 2: Compute deduplication info from curve distances.
    def _get_edge_curves(v1: VT, v2: VT) -> list[tuple[EdgeType, float]]:
        """Get (EdgeType, curve_distance) for all edges between v1 and v2."""
        result = []
        for e in g.edges(v1, v2):
            curves = edge_curves.get(e, [0.0])
            for c in curves:
                result.append((g.edge_type(e), c))
        return result

    # For each target pair, match top and bottom edges by curve only.
    # When matched, the bottom edge should be removed after redirect (top wins).
    dedup_removals: dict[tuple[VT, VT], list[EdgeType]] = {}
    for v in merging_set:
        for w in merging_set:
            if v >= w:
                continue
            top_curves = _get_edge_curves(v, w)
            if not top_curves:
                continue
            tv, tw = merge_map[v], merge_map[w]
            pair = (min(tv, tw), max(tv, tw))
            bottom_curves = _get_edge_curves(pair[0], pair[1])

            # Match by curve distance only; consume each bottom edge at most once
            bottom_remaining = [(ety, round(c, 6)) for ety, c in bottom_curves]
            for _top_ety, top_c in top_curves:
                top_c_rounded = round(top_c, 6)
                for i, (bot_ety, bot_c) in enumerate(bottom_remaining):
                    if bot_c == top_c_rounded:
                        dedup_removals.setdefault(pair, []).append(bot_ety)
                        bottom_remaining.pop(i)
                        break

    # Phase 3: Apply node-on-node merges
    for v, t in merge_map.items():
        # Top vertex's type and phase win
        new_g.set_type(t, new_g.type(v))
        new_g.set_phase(t, new_g.phase(v))

        # Redirect all edges from v to t
        for e in list(new_g.incident_edges(v)):
            s_end, t_end = new_g.edge_st(e)
            other = t_end if s_end == v else s_end

            # Resolve other endpoint if it's also being merged
            resolved_other = merge_map.get(other, other)

            if resolved_other == t:
                # Would become self-loop, skip
                continue

            ety = new_g.edge_type(e)
            new_g.add_edge((t, resolved_other), ety)

        # Remove v (also removes all remaining incident edges on v)
        new_g.remove_vertex(v)

    # Phase 4: Remove overlaid bottom edges.
    for pair, bot_types_to_remove in dedup_removals.items():
        t1, t2 = pair
        for ety in bot_types_to_remove:
            for e in new_g.edges(t1, t2):
                if new_g.edge_type(e) == ety:
                    new_g.remove_edge(e)
                    break

    # Phase 5: Node-on-edge snaps for non-merged selected vertices
    remaining_set = set(v for v in selected if v not in merge_map and v in new_g.vertices())
    for v in remaining_set:
        vx, vy = new_g.row(v), new_g.qubit(v)
        for e in list(new_g.edges()):
            s, t = new_g.edge_st(e)
            if s in remaining_set or t in remaining_set:
                continue
            sx, sy = new_g.row(s), new_g.qubit(s)
            tx, ty = new_g.row(t), new_g.qubit(t)
            if point_on_segment(vx, vy, sx, sy, tx, ty):
                ety = new_g.edge_type(e)
                new_g.remove_edge(e)
                if ety == EdgeType.HADAMARD:
                    new_g.add_edge((s, v), EdgeType.HADAMARD)
                    new_g.add_edge((t, v), EdgeType.SIMPLE)
                else:
                    new_g.add_edge((s, v), ety)
                    new_g.add_edge((t, v), ety)
                break  # Only snap onto one edge per vertex

    return new_g
