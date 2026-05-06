from __future__ import annotations

import copy
import os
import subprocess
import sys
from enum import Enum
from typing import Callable, Iterator, Optional, TypedDict

from PySide6.QtCore import QPoint, QSize, Qt, Signal, QEasingCurve, QParallelAnimationGroup
from PySide6.QtGui import QAction, QColor, QContextMenuEvent, QIcon, QPainter, QPalette, QPen, QPixmap
from PySide6.QtWidgets import (QApplication, QComboBox, QFrame, QGridLayout, QHBoxLayout,
                               QInputDialog, QLabel, QLineEdit, QListView, QListWidget,
                               QListWidgetItem, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
                               QSpacerItem, QSplitter, QToolButton, QVBoxLayout, QWidget)
from pyzx import EdgeType, VertexType
from pyzx.utils import get_w_partner, vertex_is_w, phase_to_s, get_z_box_label
from pyzx.graph.jsonparser import string_to_phase
from zxlive.sfx import SFXEnum

from .base_panel import BasePanel, ToolbarSection
from .commands import (BaseCommand, AddEdge, AddEdges, AddNode, AddNodeSnapped, AddWNode, ChangeEdgeColor, ChangeEdgeCurve,
                       ChangeNodeType, ChangePhase, MergeNodes, MoveNode, SetGraph,
                       UpdateGraph)
from .common import (VT, ET, GraphT, ToolType, get_data,
                     pos_from_view, get_settings_value)
from .merge import merge_subdiagram as _merge_subdiagram
from .subgraph import Subgraph
from .dialogs import import_diagram_from_file, show_error_msg, update_dummy_vertex_text
from .eitem import EItem, HAD_EDGE_BLUE
from .vitem import VItem, BLACK
from .graphscene import EditGraphScene
from .settings import display_setting

from . import animations


class ShapeType(Enum):
    CIRCLE = 1
    SQUARE = 2
    TRIANGLE = 3
    LINE = 4
    DASHED_LINE = 5


class DrawPanelNodeType(TypedDict):
    text: str
    icon: tuple[ShapeType, QColor]


def vertices_data() -> dict[VertexType, DrawPanelNodeType]:
    return {
        VertexType.Z: {"text": "Z spider", "icon": (ShapeType.CIRCLE, display_setting.effective_colors["z_spider"])},
        VertexType.X: {"text": "X spider", "icon": (ShapeType.CIRCLE, display_setting.effective_colors["x_spider"])},
        VertexType.H_BOX: {"text": "H box", "icon": (ShapeType.SQUARE, display_setting.effective_colors["hadamard"])},
        VertexType.Z_BOX: {"text": "Z box", "icon": (ShapeType.SQUARE, display_setting.effective_colors["z_spider"])},
        VertexType.W_OUTPUT: {"text": "W node", "icon": (ShapeType.TRIANGLE, display_setting.effective_colors["w_output"])},
        VertexType.BOUNDARY: {"text": "boundary", "icon": (ShapeType.CIRCLE, display_setting.effective_colors["w_input"])},
        VertexType.DUMMY: {"text": "Dummy", "icon": (ShapeType.CIRCLE, display_setting.effective_colors["dummy"])},
    }


def edges_data() -> dict[EdgeType, DrawPanelNodeType]:
    return {
        EdgeType.SIMPLE: {"text": "Simple", "icon": (ShapeType.LINE, QColor(BLACK))},
        EdgeType.HADAMARD: {"text": "Hadamard", "icon": (ShapeType.DASHED_LINE, QColor(HAD_EDGE_BLUE))},
    }


class EditorBasePanel(BasePanel):
    """Base class implementing the shared functionality of graph edit
    and rule edit panels of ZXLive."""

    graph_scene: EditGraphScene
    start_derivation_signal = Signal(object)
    sidebar: QSplitter

    _curr_ety: EdgeType
    _curr_vty: VertexType
    snap_vertex_edge = True
    patterns_folder: Optional[str] = None

    def __init__(self, *actions: QAction) -> None:
        super().__init__(*actions)
        self._curr_vty = VertexType.Z
        self._curr_ety = EdgeType.SIMPLE
        self.patterns_folder = get_settings_value("patterns-folder", str)

    def _toolbar_sections(self) -> Iterator[ToolbarSection]:
        yield from toolbar_select_node_edge(self)
        yield ToolbarSection(*self.actions())

    def create_side_bar(self) -> None:
        self.sidebar = QSplitter(self)
        self.sidebar.setOrientation(Qt.Orientation.Vertical)

        vertex_container, vertex_layout = create_titled_widget("Vertices")
        self.vertex_list = create_list_widget(self, vertices_data(), self._vty_clicked, self._vty_double_clicked)
        vertex_layout.addWidget(self.vertex_list)
        self.sidebar.addWidget(vertex_container)

        edge_container, edge_layout = create_titled_widget("Edges")
        self.edge_list = create_list_widget(self, edges_data(), self._ety_clicked, self._ety_double_clicked)
        edge_layout.addWidget(self.edge_list)
        self.sidebar.addWidget(edge_container)

        if self.patterns_folder is not None:
            patterns_container, patterns_layout = create_titled_widget(
                "Patterns",
                [("↻", self.refresh_patterns, "Refresh patterns list"),
                 ("📁", self._open_patterns_folder, "Open patterns folder")]
            )
            # Add search box
            self.patterns_search = QLineEdit()
            self.patterns_search.setPlaceholderText("Search patterns...")
            self.patterns_search.textChanged.connect(self._filter_patterns)
            patterns_layout.addWidget(self.patterns_search)

            self.patterns_list = PatternsListWidget(self, self.patterns_folder)
            patterns_layout.addWidget(self.patterns_list)
            self.sidebar.addWidget(patterns_container)

        # Insert subdiagrams section
        insert_container, insert_layout = create_titled_widget("Insert Subdiagram")
        btn_h = QPushButton("H (Hadamard)")
        btn_h.clicked.connect(self.insert_clifford_h)
        insert_layout.addWidget(btn_h)
        btn_s = QPushButton("S gate")
        btn_s.clicked.connect(self.insert_clifford_s)
        insert_layout.addWidget(btn_s)
        btn_cnot = QPushButton("CNOT")
        btn_cnot.clicked.connect(self.insert_clifford_cnot)
        insert_layout.addWidget(btn_cnot)
        btn_pauli = QPushButton("Pauli Box...")
        btn_pauli.clicked.connect(self.insert_pauli_box)
        insert_layout.addWidget(btn_pauli)
        self.sidebar.addWidget(insert_container)

        # Rewrite rules section
        rewrite_container, rewrite_layout = create_titled_widget("Subdiagram Rewrites")
        btn_push = QPushButton("Push Clifford through Pauli Box")
        btn_push.clicked.connect(self.apply_push_clifford)
        rewrite_layout.addWidget(btn_push)
        btn_merge_sg = QPushButton("Merge Subgraphs")
        btn_merge_sg.clicked.connect(self.merge_selected_subgraphs)
        rewrite_layout.addWidget(btn_merge_sg)
        btn_vcomp = QPushButton("V-Compose (⊗)")
        btn_vcomp.clicked.connect(self.vertical_compose_subgraphs)
        rewrite_layout.addWidget(btn_vcomp)
        btn_hcomp = QPushButton("H-Compose (∘)")
        btn_hcomp.clicked.connect(self.horizontal_compose_subgraphs)
        rewrite_layout.addWidget(btn_hcomp)
        self.sidebar.addWidget(rewrite_container)

        self.variable_viewer = VariableViewer(self)
        self.sidebar.addWidget(self.variable_viewer)

    def update_side_bar(self) -> None:
        populate_list_widget(self.vertex_list, vertices_data(), self._vty_clicked, self._vty_double_clicked)
        populate_list_widget(self.edge_list, edges_data(), self._ety_clicked, self._ety_double_clicked)

    def refresh_patterns(self) -> None:
        """Refresh the patterns list if it exists."""
        if hasattr(self, 'patterns_list'):
            self.patterns_search.clear()
            self.patterns_list.refresh_patterns()

    def _filter_patterns(self, text: str) -> None:
        """Filter patterns based on search text."""
        if hasattr(self, 'patterns_list'):
            self.patterns_list.filter_patterns(text)

    def _open_patterns_folder(self) -> None:
        """Open the patterns folder in the system file explorer."""
        if self.patterns_folder and os.path.isdir(self.patterns_folder):
            abs_path = os.path.abspath(self.patterns_folder)
            if sys.platform == "win32":
                os.startfile(abs_path)
            elif sys.platform == "darwin":
                subprocess.run(["open", abs_path])
            else:
                subprocess.run(["xdg-open", abs_path])
        elif self.patterns_folder:
            # Create the folder if it doesn't exist
            os.makedirs(self.patterns_folder, exist_ok=True)
            self._open_patterns_folder()  # Try again

    def update_colors(self) -> None:
        super().update_colors()
        self.update_side_bar()

    def _tool_clicked(self, tool: ToolType) -> None:
        self.graph_scene.curr_tool = tool

    def _snap_vertex_edge_clicked(self) -> None:
        self.snap_vertex_edge = not self.snap_vertex_edge

    def _vty_clicked(self, vty: VertexType) -> None:
        self._curr_vty = vty

    def _vty_double_clicked(self, vty: VertexType) -> None:
        self._curr_vty = vty
        selected = list(self.graph_scene.selected_vertices)
        if len(selected) > 0:
            cmd = ChangeNodeType(self.graph_view, selected, vty)
            self.undo_stack.push(cmd)

    def _ety_clicked(self, ety: EdgeType) -> None:
        self._curr_ety = ety
        self.graph_scene.curr_ety = ety

    def _ety_double_clicked(self, ety: EdgeType) -> None:
        self._curr_ety = ety
        self.graph_scene.curr_ety = ety
        selected = list(self.graph_scene.selected_edges)
        if len(selected) > 0:
            cmd = ChangeEdgeColor(self.graph_view, selected, ety)
            self.undo_stack.push(cmd)

    def paste_graph(self, graph: GraphT) -> None:
        new_g = copy.deepcopy(self.graph_scene.g)
        new_verts, new_edges = new_g.merge(graph.translate(0.5, 0.5))
        cmd = UpdateGraph(self.graph_view, new_g)
        self.undo_stack.push(cmd)
        self.graph_scene.select_vertices(new_verts)
        for name in new_g.var_registry.vars():
            self.variable_viewer.add_item(name)

    def insert_pattern_from_sidebar(self, pattern_path: str) -> None:
        """Insert a pattern into the current graph view."""
        try:
            out = import_diagram_from_file(pattern_path, parent=self)
            if out is not None and hasattr(out, 'g'):
                self.paste_graph(out.g)
        except Exception as e:
            QMessageBox.warning(self, "Pattern Insert Error", str(e))

    def delete_selection(self) -> None:
        selection = list(self.graph_scene.selected_vertices)
        selected_edges = list(self.graph_scene.selected_edges)
        rem_vertices = selection.copy()
        for v in selection:
            if vertex_is_w(self.graph_scene.g.type(v)):
                rem_vertices.append(get_w_partner(self.graph_scene.g, v))
        if not rem_vertices and not selected_edges:
            return
        new_g = copy.deepcopy(self.graph_scene.g)
        new_g.remove_edges(selected_edges)
        new_g.remove_vertices(list(set(rem_vertices)))
        cmd = SetGraph(self.graph_view, new_g) if len(set(rem_vertices)) > 128 \
            else UpdateGraph(self.graph_view, new_g)
        self.undo_stack.push(cmd)

    def merge_vertices(self) -> None:
        """Merge selected vertices"""
        selected = list(self.graph_scene.selected_vertices)
        if len(selected) < 2:
            return
        cmd = MergeNodes(self.graph_view, selected)
        self.undo_stack.push(cmd)

    def merge_subdiagram(self) -> None:
        """Merge selected subdiagram onto the graph beneath it."""
        selected = list(self.graph_scene.selected_vertices)
        if not selected:
            return

        # Extract curve distances from scene EItems (View -> Model boundary)
        edge_curves: dict[ET, list[float]] = {}
        for e, eitems in self.graph_scene.edge_map.items():
            edge_curves[e] = [eitems[idx].curve_distance for idx in sorted(eitems)]

        new_g, edge_remap = _merge_subdiagram(self.graph_scene.g, selected, edge_curves)
        cmd = UpdateGraph(self.graph_view, new_g)
        self.undo_stack.push(cmd)

        # Update CircuitLike metadata in all subgraphs to reflect remapped edges
        if edge_remap:
            self._remap_subgraph_edges(edge_remap, new_g)

    def apply_push_clifford(self) -> None:
        """Push a selected Clifford through a selected Pauli box."""
        from pyzx.rewrite_rules.push_clifford_rule import check_push_clifford, apply_push_clifford

        selected_sgs = self.graph_scene.selected_subgraphs
        from pyzx.circuitlike import CliffordUnitary, PauliBox
        cliffords = [sg for sg in selected_sgs if isinstance(sg, CliffordUnitary)]
        pauli_boxes = [sg for sg in selected_sgs if isinstance(sg, PauliBox)]

        if len(cliffords) != 1 or len(pauli_boxes) != 1:
            show_error_msg("Select subgraphs",
                           "Select exactly one Clifford gate and one Pauli box "
                           "(click their bounding boxes).",
                           parent=self)
            return

        cliff_sg = cliffords[0]
        pauli_sg = pauli_boxes[0]
        g = self.graph_scene.g

        cl_c = cliff_sg  # CliffordUnitary is a CircuitLike
        cl_p = pauli_sg  # PauliBox is a CircuitLike

        match = check_push_clifford(g, cl_c, cl_p)
        if match is None:
            show_error_msg("Cannot push",
                           "The selected Clifford and Pauli box are not "
                           "same-support adjacent.",
                           parent=self)
            return

        # Apply the rewrite on a deep copy
        new_g = copy.deepcopy(g)
        match_copy = check_push_clifford(new_g, cl_c, cl_p)
        if match_copy is None:
            show_error_msg("Cannot push", "Rewrite check failed on copy.", parent=self)
            return
        result = apply_push_clifford(new_g, match_copy)

        # Clear old subgraph markings (stale after rewrite)
        mgr = self.graph_scene.subgraph_manager
        cliff_id = mgr.get_id(cliff_sg)
        pauli_id = mgr.get_id(pauli_sg)
        if cliff_id:
            mgr.delete(cliff_id)
            self.graph_scene.remove_subgraph_box(cliff_id)
        if pauli_id:
            mgr.delete(pauli_id)
            self.graph_scene.remove_subgraph_box(pauli_id)

        cmd = UpdateGraph(self.graph_view, new_g)
        self.undo_stack.push(cmd)

        # Re-create subgraph markings for the new Pauli box and Clifford
        if result.new_pauli_vertices:
            pb = PauliBox(vertices=result.new_pauli_vertices, g=new_g,
                          pauli_string=result.new_pauli_string)
            try:
                sg_id = mgr.add(pb)
                self.graph_scene.add_subgraph_box(sg_id)
            except ValueError:
                pass

        if result.new_clifford_vertices:
            cu = CliffordUnitary(vertices=result.new_clifford_vertices, g=new_g)
            try:
                sg_id = mgr.add(cu)
                self.graph_scene.add_subgraph_box(sg_id)
            except ValueError:
                pass

    def _selected_subgraph_pair(self) -> Optional[tuple]:
        """Return (id_a, sg_a, id_b, sg_b) for exactly 2 selected subgraphs, or None."""
        selected = self.graph_scene.selected_subgraphs
        if len(selected) != 2:
            show_error_msg("Select subgraphs",
                           "Select exactly two subgraph bounding boxes.",
                           parent=self)
            return None
        mgr = self.graph_scene.subgraph_manager
        id_a = mgr.get_id(selected[0])
        id_b = mgr.get_id(selected[1])
        if id_a is None or id_b is None:
            return None
        return id_a, selected[0], id_b, selected[1]

    def merge_selected_subgraphs(self) -> None:
        """Merge two selected subgraphs (vertex set union, drops structure)."""
        pair = self._selected_subgraph_pair()
        if pair is None:
            return
        id_a, _sg_a, id_b, _sg_b = pair
        mgr = self.graph_scene.subgraph_manager
        try:
            self.graph_scene.remove_subgraph_box(id_a)
            self.graph_scene.remove_subgraph_box(id_b)
            new_id = mgr.merge(id_a, id_b)
            self.graph_scene.add_subgraph_box(new_id)
        except ValueError as e:
            show_error_msg("Cannot merge", str(e), parent=self)

    def vertical_compose_subgraphs(self) -> None:
        """Vertical composition (tensor product) of two CircuitLike subgraphs."""
        from pyzx.circuitlike import CircuitLike, CliffordUnitary, vertical_compose
        pair = self._selected_subgraph_pair()
        if pair is None:
            return
        id_a, sg_a, id_b, sg_b = pair
        if not isinstance(sg_a, CircuitLike) or not isinstance(sg_b, CircuitLike):
            show_error_msg("Not CircuitLike",
                           "Both subgraphs must be CircuitLike for vertical composition.",
                           parent=self)
            return
        try:
            composed = vertical_compose(sg_a, sg_b)
            # Preserve CliffordUnitary type if both are Clifford
            if isinstance(sg_a, CliffordUnitary) and isinstance(sg_b, CliffordUnitary):
                composed = CliffordUnitary(**{k: v for k, v in composed.__dict__.items()})
        except ValueError as e:
            show_error_msg("Cannot compose", str(e), parent=self)
            return

        mgr = self.graph_scene.subgraph_manager
        self.graph_scene.remove_subgraph_box(id_a)
        self.graph_scene.remove_subgraph_box(id_b)
        mgr.delete(id_a)
        mgr.delete(id_b)
        try:
            new_id = mgr.add(composed)
            self.graph_scene.add_subgraph_box(new_id)
        except ValueError as e:
            show_error_msg("Cannot compose", str(e), parent=self)

    def horizontal_compose_subgraphs(self) -> None:
        """Horizontal composition (sequential) of two CircuitLike subgraphs."""
        from pyzx.circuitlike import CircuitLike, CliffordUnitary, horizontal_compose
        pair = self._selected_subgraph_pair()
        if pair is None:
            return
        id_a, sg_a, id_b, sg_b = pair
        if not isinstance(sg_a, CircuitLike) or not isinstance(sg_b, CircuitLike):
            show_error_msg("Not CircuitLike",
                           "Both subgraphs must be CircuitLike for horizontal composition.",
                           parent=self)
            return

        # Try both orderings (a∘b and b∘a) since selection order may vary
        composed = horizontal_compose(sg_a, sg_b)
        if composed is None:
            composed = horizontal_compose(sg_b, sg_a)
        if composed is None:
            show_error_msg("Not adjacent",
                           "The two subgraphs are not same-support adjacent.",
                           parent=self)
            return

        # Preserve CliffordUnitary type if both are Clifford
        if isinstance(sg_a, CliffordUnitary) and isinstance(sg_b, CliffordUnitary):
            composed = CliffordUnitary(**{k: v for k, v in composed.__dict__.items()})

        mgr = self.graph_scene.subgraph_manager
        self.graph_scene.remove_subgraph_box(id_a)
        self.graph_scene.remove_subgraph_box(id_b)
        mgr.delete(id_a)
        mgr.delete(id_b)
        try:
            new_id = mgr.add(composed)
            self.graph_scene.add_subgraph_box(new_id)
        except ValueError as e:
            show_error_msg("Cannot compose", str(e), parent=self)

    def insert_pauli_box(self) -> None:
        """Prompt for a Pauli string and insert a tagged Pauli box."""
        from pyzx.paulibox import generate_pauli_box

        try:
            text, ok = QInputDialog.getText(
                self, "Insert Pauli Box",
                "Enter Pauli string (e.g. XZIY):")
            if not ok or not text:
                return
            text = text.upper().strip()
            if not all(c in "XYZI" for c in text) or len(text) == 0:
                show_error_msg("Invalid Pauli string",
                               "Use only X, Y, Z, I characters.",
                               parent=self)
                return

            g, pb = generate_pauli_box(text)
            self._insert_tagged_graph(g, pb)
        except Exception as e:
            import traceback
            traceback.print_exc()
            show_error_msg("Error inserting Pauli box", str(e), parent=self)

    def insert_clifford_h(self) -> None:
        """Insert a tagged Hadamard gate."""
        self._insert_clifford_gate("H")

    def insert_clifford_s(self) -> None:
        """Insert a tagged S gate."""
        self._insert_clifford_gate("S")

    def insert_clifford_cnot(self) -> None:
        """Insert a tagged CNOT gate."""
        self._insert_clifford_gate("CNOT")

    def _insert_clifford_gate(self, gate_name: str) -> None:
        """Generate and insert a tagged Clifford gate."""
        from pyzx.circuitlike import CliffordUnitary
        from pyzx.graph.multigraph import Multigraph

        g = Multigraph()
        g.set_auto_simplify(False)

        if gate_name == "H":
            from pyzx.utils import set_h_box_label
            b_in = g.add_vertex(VertexType.BOUNDARY, qubit=0, row=0)
            h = g.add_vertex(VertexType.H_BOX, qubit=0, row=1)
            set_h_box_label(g, h, -1)
            b_out = g.add_vertex(VertexType.BOUNDARY, qubit=0, row=2)
            g.add_edge((b_in, h), EdgeType.SIMPLE)
            g.add_edge((h, b_out), EdgeType.SIMPLE)
            e_in = next(g.edges(b_in, h))
            e_out = next(g.edges(h, b_out))
            cl = CliffordUnitary(
                vertices={h}, g=g,
                input_edges={0: e_in},
                output_edges={0: e_out},
            )
        elif gate_name == "S":
            from fractions import Fraction
            b_in = g.add_vertex(VertexType.BOUNDARY, qubit=0, row=0)
            s = g.add_vertex(VertexType.Z, qubit=0, row=1, phase=Fraction(1, 2))
            b_out = g.add_vertex(VertexType.BOUNDARY, qubit=0, row=2)
            g.add_edge((b_in, s), EdgeType.SIMPLE)
            g.add_edge((s, b_out), EdgeType.SIMPLE)
            e_in = next(g.edges(b_in, s))
            e_out = next(g.edges(s, b_out))
            cl = CliffordUnitary(
                vertices={s}, g=g,
                input_edges={0: e_in},
                output_edges={0: e_out},
            )
        elif gate_name == "CNOT":
            b_in0 = g.add_vertex(VertexType.BOUNDARY, qubit=0, row=0)
            b_in1 = g.add_vertex(VertexType.BOUNDARY, qubit=1, row=0)
            z = g.add_vertex(VertexType.Z, qubit=0, row=1, phase=0)
            x = g.add_vertex(VertexType.X, qubit=1, row=1, phase=0)
            b_out0 = g.add_vertex(VertexType.BOUNDARY, qubit=0, row=2)
            b_out1 = g.add_vertex(VertexType.BOUNDARY, qubit=1, row=2)
            g.add_edge((b_in0, z), EdgeType.SIMPLE)
            g.add_edge((b_in1, x), EdgeType.SIMPLE)
            g.add_edge((z, x), EdgeType.SIMPLE)
            g.add_edge((z, b_out0), EdgeType.SIMPLE)
            g.add_edge((x, b_out1), EdgeType.SIMPLE)
            e_in0 = next(g.edges(b_in0, z))
            e_in1 = next(g.edges(b_in1, x))
            e_out0 = next(g.edges(z, b_out0))
            e_out1 = next(g.edges(x, b_out1))
            cl = CliffordUnitary(
                vertices={z, x}, g=g,
                input_edges={0: e_in0, 1: e_in1},
                output_edges={0: e_out0, 1: e_out1},
            )
        else:
            return

        self._insert_tagged_graph(g, cl)

    def _insert_tagged_graph(
        self,
        subgraph: GraphT,
        cl: CircuitLike,
    ) -> None:
        """Insert a graph into the scene and tag its vertices as a subgraph."""
        new_g = copy.deepcopy(self.graph_scene.g)
        # Place the subgraph below existing vertices to avoid overlap
        if new_g.num_vertices() > 0:
            max_qubit = max((new_g.qubit(v) for v in new_g.vertices()), default=0)
            offset_qubit = max_qubit + 2
        else:
            offset_qubit = 0
        new_verts, new_edges = new_g.merge(subgraph.translate(0.5, offset_qubit + 0.5))

        cmd = UpdateGraph(self.graph_view, new_g)
        self.undo_stack.push(cmd)
        self.graph_scene.select_vertices(new_verts)

        # Remap vertex and edge IDs from the sub-graph to the main graph
        old_to_new = dict(zip(sorted(subgraph.vertices()), sorted(new_verts)))
        new_cl_vertices = {old_to_new[v] for v in cl.vertices if v in old_to_new}

        def _remap_edge(old_e: ET) -> Optional[ET]:
            s, t = subgraph.edge_st(old_e)
            ns = old_to_new.get(s)
            nt = old_to_new.get(t)
            if ns is None or nt is None:
                return None
            ety = subgraph.edge_type(old_e)
            for e in new_g.edges(ns, nt):
                if new_g.edge_type(e) == ety:
                    return e
            return None

        new_input_edges = {}
        for q, e in cl.input_edges.items():
            ne = _remap_edge(e)
            if ne is not None:
                new_input_edges[q] = ne

        new_output_edges = {}
        for q, e in cl.output_edges.items():
            ne = _remap_edge(e)
            if ne is not None:
                new_output_edges[q] = ne

        new_edge_labels = {}
        for e, q in cl.edge_labels.items():
            ne = _remap_edge(e)
            if ne is not None:
                new_edge_labels[ne] = q

        # Reconstruct the same subclass with remapped IDs
        new_sg = cl.__class__(
            vertices=new_cl_vertices,
            g=new_g,
            input_edges=new_input_edges,
            output_edges=new_output_edges,
            edge_labels=new_edge_labels,
            **{k: v for k, v in cl.__dict__.items()
               if k not in ('vertices', 'g', 'input_edges', 'output_edges', 'edge_labels')},
        )

        sg_id = self.graph_scene.subgraph_manager.add(new_sg)
        self.graph_scene.add_subgraph_box(sg_id)

    def _remap_subgraph_edges(self, edge_remap: dict[ET, ET], new_g: GraphT) -> None:
        """Update subgraph references after a stamp-merge."""
        from pyzx.circuitlike import CircuitLike

        for _sg_id, sg in self.graph_scene.subgraph_manager.all_subgraphs():
            # Remove vertices that no longer exist
            sg.vertices = {v for v in sg.vertices if v in new_g.vertices()}
            sg.g = new_g

            if not isinstance(sg, CircuitLike):
                continue

            # Remap input edges
            sg.input_edges = {q: edge_remap.get(e, e)
                              for q, e in sg.input_edges.items()}
            # Remap output edges
            sg.output_edges = {q: edge_remap.get(e, e)
                               for q, e in sg.output_edges.items()}
            # Remap edge labels
            sg.edge_labels = {edge_remap.get(e, e): q
                              for e, q in sg.edge_labels.items()}

        # Refresh bounding boxes
        self.graph_scene.refresh_subgraph_boxes()

    def add_vert(self, x: float, y: float, edges: list[EItem]) -> None:
        """Add a vertex at point (x,y). `edges` is a list of EItems that are underneath the current position.
        We will try to connect the vertex to an edge.
        """
        cmd: BaseCommand
        if self.snap_vertex_edge and edges and self._curr_vty != VertexType.W_OUTPUT:
            # Trying to snap vertex to an edge
            for it in edges:
                e = it.e
                g = self.graph_scene.g
                if self.graph_scene.g.edge_type(e) not in (EdgeType.SIMPLE, EdgeType.HADAMARD):
                    continue
                cmd = AddNodeSnapped(self.graph_view, x, y, self._curr_vty, e)
                self.play_sound_signal.emit(SFXEnum.THATS_A_SPIDER)
                self.undo_stack.push(cmd)
                g = cmd.g
                group = QParallelAnimationGroup()
                for e in [next(g.edges(cmd.s, cmd.added_vert)), next(g.edges(cmd.t, cmd.added_vert))]:
                    eitem = self.graph_scene.edge_map[e][0]
                    anim = animations.edge_thickness(eitem, 3, 400,
                                                     QEasingCurve(QEasingCurve.Type.InCubic), start=7)
                    group.addAnimation(anim)
                self.undo_stack.set_anim(group)
                return

        if self._curr_vty == VertexType.W_OUTPUT:
            self.undo_stack.push(AddWNode(self.graph_view, x, y))
        else:
            self.undo_stack.push(AddNode(self.graph_view, x, y, self._curr_vty))
        self.play_sound_signal.emit(SFXEnum.THATS_A_SPIDER)

    def _is_invalid_edge(self, graph: GraphT, u: VT, v: VT) -> bool:
        if vertex_is_w(graph.type(u)) and get_w_partner(graph, u) == v:
            return True
        if graph.type(u) == VertexType.W_INPUT and len(graph.neighbors(u)) >= 2 or \
                graph.type(v) == VertexType.W_INPUT and len(graph.neighbors(v)) >= 2:
            return True
        u_is_dummy = graph.type(u) == VertexType.DUMMY
        v_is_dummy = graph.type(v) == VertexType.DUMMY
        return bool(u_is_dummy != v_is_dummy)

    def _build_snap_pairs(self, graph: GraphT, u: VT, v: VT,
                          verts: list[VItem]) -> list[tuple[VT, VT]]:
        # Line was drawn from u to v, we want to order vs with the earlier items first.
        ux, uy = graph.row(u), graph.qubit(u)

        def dist(vitem: VItem) -> float:
            return (graph.row(vitem.v) - ux)**2 + (graph.qubit(vitem.v) - uy)**2  # type: ignore
        verts.sort(key=dist)
        vs = [vitem.v for vitem in verts]
        pairs = [(u, vs[0])]
        for i in range(1, len(vs)):
            pairs.append((vs[i - 1], vs[i]))
        pairs.append((vs[-1], v))
        return pairs

    def add_edge(self, u: VT, v: VT, verts: list[VItem]) -> None:
        """Add an edge between vertices u and v. `verts` is a list of VItems that collide with the edge.
        If self.snap_vertex_edge is true, then we try to connect `u` through all the `vertices` in `verts`, and then to `v`.
        """
        graph = self.graph_view.graph_scene.g
        if self._is_invalid_edge(graph, u, v):
            return None

        # We will try to connect all the vertices together in order
        # First we filter out the vertices that are not compatible with the edge.
        verts = [vitem for vitem in verts if not graph.type(vitem.v) == VertexType.W_INPUT]  # we will be adding two edges, which is not compatible with W_INPUT
        # but first we check if there any vertices that we do want to additionally connect.
        if not self.snap_vertex_edge or not verts:
            self.undo_stack.push(AddEdge(self.graph_view, u, v, self._curr_ety))
            return

        pairs = self._build_snap_pairs(graph, u, v, verts)
        self.undo_stack.push(AddEdges(self.graph_view, pairs, self._curr_ety))
        group = QParallelAnimationGroup()
        for vitem in verts:
            anim = animations.scale(vitem, 1.0, 400, QEasingCurve(QEasingCurve.Type.InCubic), start=1.3)
            group.addAnimation(anim)
        self.undo_stack.set_anim(group)

    def vert_moved(self, vs: list[tuple[VT, float, float]]) -> None:
        self.undo_stack.push(MoveNode(self.graph_view, vs))
        self.graph_scene.refresh_subgraph_boxes()

    def _vertex_dropped_onto(self, v: VT, w: VT) -> None:
        view_pos = self.graph_scene.vertex_map[v].pos()
        pos = pos_from_view(view_pos.x(), view_pos.y())
        self.vert_moved([(v, pos[0], pos[1])])

    def change_edge_curves(self, eitem: EItem, new_distance: float, old_distance: float) -> None:
        self.undo_stack.push(ChangeEdgeCurve(self.graph_view, eitem, new_distance, old_distance))

    def vert_double_clicked(self, v: VT) -> None:
        graph = self.graph
        old_variables = graph.var_registry.vars()
        if graph.type(v) == VertexType.BOUNDARY or vertex_is_w(graph.type(v)):
            return None
        if graph.type(v) == VertexType.DUMMY:
            new_g = update_dummy_vertex_text(self, self.graph_scene.g, v)
            if new_g is not None:
                self.undo_stack.push(SetGraph(self.graph_view, new_g))
            return
        phase_is_complex = (graph.type(v) == VertexType.Z_BOX)
        if phase_is_complex:
            prompt = "Enter desired phase value (complex value):"
            error_msg = "Please enter a valid input (e.g., -1+2j)."
            current_phase = str(get_z_box_label(graph, v))
        else:
            prompt = "Enter desired phase value (non-variables are multiples of pi):"
            error_msg = "Please enter a valid input. (e.g. pi/2, 1/2, 0.25, a+b)."
            current_phase = phase_to_s(graph.phase(v), graph.type(v), limit_denominator=False)

        input_, ok = QInputDialog.getText(
            self, "Change Phase", prompt, text=current_phase
        )
        if not ok:
            return None
        try:
            new_phase = string_to_complex(input_) if phase_is_complex else string_to_phase(input_, graph)
        except ValueError:
            show_error_msg("Invalid Input", error_msg, parent=self)
            return None
        self.undo_stack.push(ChangePhase(self.graph_view, v, new_phase))
        # For some reason it is important we first push to the stack before we do the following.
        if len(graph.var_registry.vars()) != len(old_variables):
            new_vars = graph.var_registry.vars() - old_variables
            for nv in new_vars:
                self.variable_viewer.add_item(nv)


class VariableViewer(QScrollArea):

    def __init__(self, parent: EditorBasePanel) -> None:
        super().__init__()
        self.parent_panel = parent
        self._widget = QWidget()
        lpal = QApplication.palette("QListWidget")  # type: ignore
        palette = QPalette()
        palette.setBrush(QPalette.ColorRole.Window, lpal.base())
        self._widget.setAutoFillBackground(True)
        self._widget.setPalette(palette)
        self._layout = QGridLayout(self._widget)
        self._layout.setColumnStretch(0, 1)
        self._layout.setColumnStretch(1, 0)
        self._layout.setColumnStretch(2, 0)
        cb = QComboBox()
        cb.insertItems(0, ["Parametric", "Boolean"])
        self._layout.setColumnMinimumWidth(2, cb.minimumSizeHint().width())
        self._layout.setContentsMargins(0, 0, 0, 0)
        self._items = 0

        vline = QFrame()
        vline.setFrameShape(QFrame.Shape.VLine)
        vline.setFixedWidth(3)
        vline.setLineWidth(1)
        vline.setSizePolicy(QSizePolicy.Policy.Fixed, QSizePolicy.Policy.Expanding)
        self._layout.addWidget(vline, 0, 1, -1, 1)

        hline = QFrame()
        hline.setFrameShape(QFrame.Shape.HLine)
        hline.setFixedHeight(3)
        hline.setLineWidth(1)
        hline.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(hline, 1, 0, 1, -1)

        vlabel = QLabel("Variable")
        vlabel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(vlabel, 0, 0, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)
        tlabel = QLabel("Type")
        tlabel.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Fixed)
        self._layout.addWidget(tlabel, 0, 2, Qt.AlignmentFlag.AlignBottom | Qt.AlignmentFlag.AlignHCenter)

        self._layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding), 2, 2)

        self._type_boxes: dict[str, QComboBox] = {}
        for name in self.parent_panel.graph.var_registry.vars():
            self.add_item(name)

        self.setWidget(self._widget)
        self.setWidgetResizable(True)

    def minimumSizeHint(self) -> QSize:
        if self._items == 0:
            return QSize(0, 0)
        else:
            return super().minimumSizeHint()

    def sizeHint(self) -> QSize:
        if self._items == 0:
            return QSize(0, 0)
        else:
            return super().sizeHint()

    def add_item(self, name: str) -> None:
        if name in self._type_boxes:
            is_bool = self.parent_panel.graph.var_registry.get_type(name, default=False)
            self._type_boxes[name].setCurrentIndex(1 if is_bool else 0)
            return

        combobox = QComboBox()
        combobox.insertItems(0, ["Parametric", "Boolean"])
        is_bool = self.parent_panel.graph.var_registry.get_type(name, default=False)
        combobox.setCurrentIndex(1 if is_bool else 0)
        combobox.currentTextChanged.connect(lambda text: self._text_changed(name, text))
        item = self._layout.itemAtPosition(2 + self._items, 2)
        assert item is not None  # For mypy
        self._layout.removeItem(item)
        self._layout.addWidget(QLabel(f"<pre>{name}</pre>"), 2 + self._items, 0, Qt.AlignmentFlag.AlignVCenter | Qt.AlignmentFlag.AlignRight)
        self._layout.addWidget(combobox, 2 + self._items, 2, Qt.AlignmentFlag.AlignCenter)
        self._type_boxes[name] = combobox
        self._layout.setRowStretch(2 + self._items, 0)
        self._layout.addItem(QSpacerItem(0, 0, QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding), 3 + self._items, 2)
        self._layout.setRowStretch(3 + self._items, 1)
        self._layout.update()
        self._items += 1
        self._widget.updateGeometry()

        if self._items == 1:
            self.updateGeometry()

    def _text_changed(self, name: str, text: str) -> None:
        from .rule_panel import RulePanel
        if text == "Parametric":
            new_type = False
        elif text == "Boolean":
            new_type = True
        else:
            raise ValueError("Unknown variable type")
        if isinstance(self.parent_panel, RulePanel):
            self.parent_panel.graph_scene_left.g.var_registry.set_type(name, new_type)
            self.parent_panel.graph_scene_right.g.var_registry.set_type(name, new_type)
        else:
            self.parent_panel.graph_scene.g.var_registry.set_type(name, new_type)


class PatternsListWidget(QListWidget):
    """Widget for displaying and selecting pattern files."""

    def __init__(self, parent: EditorBasePanel, patterns_folder: str) -> None:
        super().__init__(parent)
        self.parent_panel = parent
        self.patterns_folder = patterns_folder
        self.all_patterns: list[str] = []  # Store all pattern names for filtering

        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setViewMode(QListView.ViewMode.ListMode)
        self.setMovement(QListView.Movement.Static)
        self.setWordWrap(True)
        self.setSpacing(2)
        self.setStyleSheet("""
            QListWidget {
                padding: 4px;
            }
            QListWidget::item {
                padding: 4px 8px;
                border-radius: 3px;
            }
            QListWidget::item:hover {
                background-color: palette(midlight);
            }
            QListWidget::item:selected {
                background-color: palette(highlight);
                color: palette(highlighted-text);
            }
        """)

        self.itemDoubleClicked.connect(self._pattern_selected)
        self.refresh_patterns()

    def refresh_patterns(self) -> None:
        """Refresh the list of patterns from the patterns folder."""
        self.clear()
        self.all_patterns.clear()

        if not os.path.isdir(self.patterns_folder):
            return

        for fname in os.listdir(self.patterns_folder):
            if fname.endswith(".zxg"):
                # remove the extension for display
                fname = fname[:-4]
                self.all_patterns.append(fname)

        self.all_patterns.sort()
        self._display_patterns(self.all_patterns)

    def filter_patterns(self, search_text: str) -> None:
        """Filter patterns based on search text."""
        search_text = search_text.lower()
        if not search_text:
            self._display_patterns(self.all_patterns)
        else:
            filtered = [p for p in self.all_patterns if search_text in p.lower()]
            self._display_patterns(filtered)

    def _display_patterns(self, patterns: list[str]) -> None:
        """Display the given list of patterns."""
        self.clear()
        if not patterns:
            placeholder = QListWidgetItem("Right-click a selection to save it as a pattern")
            placeholder.setFlags(Qt.ItemFlag.NoItemFlags)
            placeholder.setForeground(QPalette().color(QPalette.ColorRole.PlaceholderText))
            self.addItem(placeholder)
        else:
            for pattern_name in patterns:
                item = QListWidgetItem(pattern_name)
                self.addItem(item)

    def _pattern_selected(self, item: QListWidgetItem) -> None:
        """Handle pattern selection."""
        # Don't insert if it's the placeholder
        if item.flags() == Qt.ItemFlag.NoItemFlags:
            return
        pattern_path = os.path.join(self.patterns_folder, item.text() + ".zxg")
        self.parent_panel.insert_pattern_from_sidebar(pattern_path)

    def contextMenuEvent(self, event: QContextMenuEvent) -> None:
        """Show context menu on right-click."""
        item = self.itemAt(event.pos())
        if item is None or item.flags() == Qt.ItemFlag.NoItemFlags:
            return
        menu = QMenu(self)

        insert_action = QAction("Insert", self)
        insert_action.triggered.connect(lambda: self._pattern_selected(item))
        menu.addAction(insert_action)
        menu.addSeparator()
        edit_action = QAction("Edit", self)
        edit_action.triggered.connect(lambda: self._edit_pattern(item))
        menu.addAction(edit_action)
        rename_action = QAction("Rename", self)
        rename_action.triggered.connect(lambda: self._rename_pattern(item))
        menu.addAction(rename_action)
        menu.addSeparator()
        delete_action = QAction("Delete", self)
        delete_action.triggered.connect(lambda: self._delete_pattern(item))
        menu.addAction(delete_action)
        menu.exec(event.globalPos())

    def _edit_pattern(self, item: QListWidgetItem) -> None:
        """Open the pattern file for editing."""
        pattern_path = os.path.join(self.patterns_folder, item.text() + ".zxg")
        main_window = self.parent_panel.window()
        if hasattr(main_window, 'open_file_from_path'):
            main_window.open_file_from_path(pattern_path)

    def _rename_pattern(self, item: QListWidgetItem) -> None:
        """Rename the pattern file."""
        old_name = item.text()
        new_name, ok = QInputDialog.getText(self, "Rename Pattern", "Enter new name:", text=old_name)
        if not ok or not new_name or new_name == old_name:
            return
        old_path = os.path.join(self.patterns_folder, old_name + ".zxg")
        new_path = os.path.join(self.patterns_folder, new_name + ".zxg")
        if os.path.exists(new_path):
            QMessageBox.warning(self, "Rename Failed", f"A pattern named '{new_name}' already exists.")
            return
        try:
            os.rename(old_path, new_path)
            self.refresh_patterns()
        except Exception as e:
            QMessageBox.warning(self, "Rename Failed", f"Could not rename pattern: {str(e)}")

    def _delete_pattern(self, item: QListWidgetItem) -> None:
        """Delete the pattern file after confirmation."""
        pattern_name = item.text()
        pattern_path = os.path.join(self.patterns_folder, pattern_name + ".zxg")
        reply = QMessageBox.question(
            self,
            "Delete Pattern",
            f"Are you sure you want to delete the pattern '{pattern_name}'?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No
        )
        if reply == QMessageBox.StandardButton.Yes:
            try:
                os.remove(pattern_path)
                self.refresh_patterns()
            except Exception as e:
                QMessageBox.warning(self, "Delete Failed", f"Could not delete pattern: {str(e)}")


def toolbar_select_node_edge(parent: EditorBasePanel) -> Iterator[ToolbarSection]:
    icon_size = QSize(32, 32)
    select = QToolButton(parent)  # Selected by default
    vertex = QToolButton(parent)
    edge = QToolButton(parent)
    select.setCheckable(True)
    vertex.setCheckable(True)
    edge.setCheckable(True)
    select.setChecked(True)
    select.setToolTip("Select (s)")
    vertex.setToolTip("Add Vertex (v)")
    edge.setToolTip("Add Edge (e)")
    select.setIcon(QIcon(get_data("icons/tikzit-tool-select.svg")))
    vertex.setIcon(QIcon(get_data("icons/tikzit-tool-node.svg")))
    edge.setIcon(QIcon(get_data("icons/tikzit-tool-edge.svg")))
    select.setShortcut("s")
    vertex.setShortcut("v")
    edge.setShortcut("e")
    select.setIconSize(icon_size)
    vertex.setIconSize(icon_size)
    edge.setIconSize(icon_size)
    select.clicked.connect(lambda: parent._tool_clicked(ToolType.SELECT))
    vertex.clicked.connect(lambda: parent._tool_clicked(ToolType.VERTEX))
    edge.clicked.connect(lambda: parent._tool_clicked(ToolType.EDGE))
    yield ToolbarSection(select, vertex, edge, exclusive=True)

    snap = QToolButton(parent)
    snap.setCheckable(True)
    snap.setChecked(True)
    snap.setIcon(QIcon(get_data("icons/vertex-snap-to-edge.svg")))
    snap.setToolTip("Snap vertices to the edge beneath them when adding vertices or edges (f)")
    snap.setShortcut("f")
    snap.clicked.connect(lambda: parent._snap_vertex_edge_clicked())
    yield ToolbarSection(snap)


def create_titled_widget(
    title: str,
    buttons: Optional[list[tuple[str, Callable[[], None], str]]] = None
) -> tuple[QWidget, QVBoxLayout]:
    """Create a container widget with a title label and multiple buttons on the right.

    Args:
        title: The title text
        buttons: List of (button_text, callback, tooltip) tuples
    """
    if buttons is None:
        buttons = []
    container = QWidget()
    layout = QVBoxLayout(container)
    layout.setContentsMargins(0, 0, 0, 0)
    layout.setSpacing(2)

    title_layout = QHBoxLayout()
    title_layout.setContentsMargins(0, 0, 0, 0)

    title_label = QLabel(title)
    title_label.setStyleSheet("font-weight: bold; padding: 4px;")
    title_layout.addWidget(title_label)

    # Add stretch to push buttons to the right
    title_layout.addStretch()
    for button_text, callback, tooltip in buttons:
        button = QPushButton(button_text)
        button.setFixedSize(24, 24)
        button.setStyleSheet("font-size: 16px; padding: 0px;")
        button.clicked.connect(callback)
        button.setToolTip(tooltip)
        title_layout.addWidget(button)

    layout.addLayout(title_layout)
    return container, layout


def create_list_widget(parent: EditorBasePanel,
                       data: dict[VertexType, DrawPanelNodeType] | dict[EdgeType, DrawPanelNodeType],
                       onclick: Callable[[VertexType], None] | Callable[[EdgeType], None],
                       ondoubleclick: Callable[[VertexType], None] | Callable[[EdgeType], None]) -> QListWidget:
    list_widget = QListWidget(parent)
    list_widget.setResizeMode(QListView.ResizeMode.Adjust)
    list_widget.setViewMode(QListView.ViewMode.IconMode)
    list_widget.setMovement(QListView.Movement.Static)
    list_widget.setUniformItemSizes(True)
    list_widget.setWordWrap(True)
    list_widget.setIconSize(QSize(24, 24))
    populate_list_widget(list_widget, data, onclick, ondoubleclick)
    list_widget.setCurrentItem(list_widget.item(0))
    return list_widget


def populate_list_widget(list_widget: QListWidget,
                         data: dict[VertexType, DrawPanelNodeType] | dict[EdgeType, DrawPanelNodeType],
                         onclick: Callable[[VertexType], None] | Callable[[EdgeType], None],
                         ondoubleclick: Callable[[VertexType], None] | Callable[[EdgeType], None]) -> None:
    row = list_widget.currentRow()
    list_widget.clear()
    for typ, value in data.items():
        icon = create_icon(*value["icon"])
        item = QListWidgetItem(icon, value["text"])
        item.setData(Qt.ItemDataRole.UserRole, typ)
        list_widget.addItem(item)
    list_widget.itemClicked.connect(lambda x: onclick(x.data(Qt.ItemDataRole.UserRole)))
    list_widget.itemDoubleClicked.connect(lambda x: ondoubleclick(x.data(Qt.ItemDataRole.UserRole)))
    list_widget.setCurrentRow(row)


def create_icon(shape: ShapeType, color: QColor) -> QIcon:
    icon = QIcon()
    pixmap = QPixmap(64, 64)
    pixmap.fill(Qt.GlobalColor.transparent)
    painter = QPainter(pixmap)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    painter.setPen(QPen(QColor(BLACK), 6))
    painter.setBrush(color)
    if shape == ShapeType.CIRCLE:
        painter.drawEllipse(4, 4, 56, 56)
    elif shape == ShapeType.SQUARE:
        painter.drawRect(4, 4, 56, 56)
    elif shape == ShapeType.TRIANGLE:
        painter.drawPolygon([QPoint(32, 10), QPoint(2, 60), QPoint(62, 60)])
    elif shape == ShapeType.LINE:
        painter.drawLine(0, 32, 64, 32)
    elif shape == ShapeType.DASHED_LINE:
        painter.setPen(QPen(QColor(color), 6, Qt.PenStyle.DashLine))
        painter.drawLine(0, 32, 64, 32)
    painter.end()
    icon.addPixmap(pixmap)
    return icon


def string_to_complex(string: str) -> complex:
    return complex(string) if string else complex(0)
