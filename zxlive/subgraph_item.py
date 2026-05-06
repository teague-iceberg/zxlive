"""Visual rendering of subgraph bounding boxes in the graph scene."""

from __future__ import annotations

from typing import Optional

from PySide6.QtCore import Qt, QRectF, QMarginsF
from PySide6.QtGui import QColor, QPainter, QPen, QBrush, QFont
from PySide6.QtWidgets import QGraphicsItem, QGraphicsRectItem, QStyleOptionGraphicsItem, QWidget

from pyzx.circuitlike import Subgraph, CliffordUnitary, PauliBox

from .common import SCALE

PADDING = 0.3 * SCALE  # padding around vertices
BOX_Z = -2  # behind edges and vertices


def _sg_color(sg: Subgraph) -> tuple[QColor, QColor, str]:
    """Return (fill, border, label) for a subgraph based on its type."""
    if isinstance(sg, CliffordUnitary):
        return (QColor(70, 130, 230, 40),
                QColor(70, 130, 230, 160),
                "Clifford")
    if isinstance(sg, PauliBox):
        label = f"Pauli: {sg.pauli_string}" if sg.pauli_string else "Pauli"
        return (QColor(230, 130, 70, 40),
                QColor(230, 130, 70, 160),
                label)
    return (QColor(150, 150, 150, 40),
            QColor(150, 150, 150, 160),
            "Subgraph")


class SubgraphBoxItem(QGraphicsRectItem):
    """Draws a colored bounding box around a subgraph's vertices."""

    def __init__(self, subgraph: Subgraph, vertex_positions: dict) -> None:
        super().__init__()
        self.subgraph = subgraph
        self.setZValue(BOX_Z)
        self.setFlag(QGraphicsItem.GraphicsItemFlag.ItemIsSelectable, True)
        self._update_rect(vertex_positions)

    def _update_rect(self, vertex_positions: dict) -> None:
        """Recompute the bounding rectangle from vertex positions."""
        positions = [vertex_positions[v] for v in self.subgraph.vertices
                     if v in vertex_positions]
        if not positions:
            self.setRect(QRectF())
            return

        min_x = min(p.x() for p in positions)
        max_x = max(p.x() for p in positions)
        min_y = min(p.y() for p in positions)
        max_y = max(p.y() for p in positions)

        rect = QRectF(min_x, min_y, max_x - min_x, max_y - min_y)
        rect = rect.marginsAdded(QMarginsF(PADDING, PADDING, PADDING, PADDING))
        self.setRect(rect)

    def refresh(self, vertex_positions: dict) -> None:
        """Update the box when vertices move."""
        self._update_rect(vertex_positions)
        self.update()

    def paint(self, painter: QPainter, option: QStyleOptionGraphicsItem,
              widget: Optional[QWidget] = None) -> None:
        fill, border, label = _sg_color(self.subgraph)

        if self.isSelected():
            border = QColor(border.red(), border.green(), border.blue(), 255)
            fill = QColor(fill.red(), fill.green(), fill.blue(), 80)

        pen = QPen(border)
        pen.setWidthF(3.0 if self.isSelected() else 2.0)
        pen.setStyle(Qt.PenStyle.SolidLine if self.isSelected() else Qt.PenStyle.DashLine)
        painter.setPen(pen)
        painter.setBrush(QBrush(fill))
        painter.drawRoundedRect(self.rect(), 8.0, 8.0)

        font = QFont()
        font.setPointSize(10)
        font.setBold(True)
        painter.setFont(font)
        painter.setPen(QPen(border))
        painter.drawText(
            self.rect().adjusted(4, 2, 0, 0),
            label,
        )
