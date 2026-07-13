"""Region selection overlay — fullscreen drag-to-select via QRubberBand (Phase 4, #128)."""

from PySide6.QtCore import QPoint, QRect, Signal
from PySide6.QtGui import QColor, QPainter
from PySide6.QtWidgets import QApplication, QRubberBand, QWidget


class RegionOverlay(QWidget):
    """Frameless translucent fullscreen overlay for drag-to-select a rectangle.

    Emits region_selected(x1, y1, x2, y2) in GLOBAL PHYSICAL screen coords,
    or cancelled() on Esc.  Handles HiDPI scaling via devicePixelRatio().
    """

    region_selected = Signal(int, int, int, int)  # x1, y1, x2, y2 (physical)
    cancelled = Signal()

    def __init__(self, screen):
        from PySide6.QtCore import Qt  # noqa: PLC0415
        from PySide6.QtWidgets import QRubberBand  # noqa: PLC0415

        super().__init__(
            None,
            Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowStaysOnTopHint,
        )
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.setGeometry(screen.geometry())
        from PySide6.QtCore import Qt as Qt2  # noqa: PLC0415
        self.setCursor(Qt2.CursorShape.CrossCursor)
        self._screen = screen
        self._rubber = QRubberBand(QRubberBand.Shape.Rectangle, self)
        self._anchor: QPoint | None = None

    def paintEvent(self, _event) -> None:
        painter = QPainter(self)
        painter.fillRect(self.rect(), QColor(0, 0, 0, 90))

    def mousePressEvent(self, event) -> None:
        self._anchor = event.position().toPoint()
        self._rubber.setGeometry(QRect(self._anchor, self._anchor))
        self._rubber.show()

    def mouseMoveEvent(self, event) -> None:
        if self._anchor is not None:
            self._rubber.setGeometry(
                QRect(self._anchor, event.position().toPoint()).normalized()
            )

    def mouseReleaseEvent(self, event) -> None:
        if self._anchor is None:
            return
        rect = QRect(self._anchor, event.position().toPoint()).normalized()
        # Map to global logical coords then scale to physical pixels
        dpr = self._screen.devicePixelRatio()
        tl = self.mapToGlobal(rect.topLeft())
        br = self.mapToGlobal(rect.bottomRight())
        x1 = int(tl.x() * dpr)
        y1 = int(tl.y() * dpr)
        x2 = int(br.x() * dpr)
        y2 = int(br.y() * dpr)
        self.close()
        self.region_selected.emit(x1, y1, x2, y2)

    def keyPressEvent(self, event) -> None:
        from PySide6.QtCore import Qt  # noqa: PLC0415
        if event.key() == Qt.Key.Key_Escape:
            self.close()
            self.cancelled.emit()


def pick_screen_for_cursor() -> object:
    """Return the QScreen that currently contains the cursor."""
    from PySide6.QtGui import QCursor  # noqa: PLC0415
    pos = QCursor.pos()
    app = QApplication.instance()
    return app.screenAt(pos) or app.primaryScreen()
