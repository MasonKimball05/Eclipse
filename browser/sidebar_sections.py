# The sidebar's lists: Pinned and Today (TabSection), the Favorites grid, where a dragged tab will land, and
# the fade at the bottom of the list. (sidebar.py re-exports all of it, for whatever imports it from there.)

from PyQt6 import sip
from PyQt6.QtCore import QEvent, QPoint, QRect, QRectF, QSize, QTimer, Qt
from PyQt6.QtGui import QColor, QImage, QLinearGradient, QPainter, QPen
from PyQt6.QtWidgets import QFrame, QGraphicsOpacityEffect, QGridLayout, QLabel, QVBoxLayout, QWidget

from .models import FAVORITES, Folder, walk_folders
from .sidebar_common import FAVORITE_COLUMNS, INDENT, TAB_MIME, _with_alpha, compact
from .sidebar_rows import FavoriteTile, FolderRow, TabRow, dragged_tabs
from .theme import noise_pixmap


class DropZone(QWidget):
    """Base for areas that accept dragged tabs and folders.

    Subclasses implement drop_target(pos) -> (folder or None, index, indicator rect, into_folder).
    """

    def __init__(self, browser, kind, parent=None):
        super().__init__(parent)
        self.browser = browser
        self.kind = kind
        self.setAcceptDrops(True)
        self.indicator = QFrame(self)
        self.indicator.setObjectName("DropLine")
        self.indicator.hide()
        self.box = QFrame(self)  # outlines a folder when dropping into it
        self.box.setObjectName("DropBox")
        self.box.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.box.hide()

    def drop_target(self, pos):
        raise NotImplementedError

    def _dragged(self, event):
        node = self.browser.store.find_node(bytes(event.mimeData().data(TAB_MIME)).decode())
        if node is None or (isinstance(node, Folder) and self.kind == FAVORITES):
            return None, None
        target = self.drop_target(event.position().toPoint())
        folder = target[0]
        if isinstance(node, Folder) and folder is not None and (folder is node or folder in walk_folders(node.children)):
            return None, None
        store = self.browser.store
        # Not into a Live Folder (its feed fills it), nor one of its sub-folders out of it: the store would
        # refuse, and the tab would fold back with nothing said
        if folder is not None and (folder.live or store.live_folder_of(folder)):
            return None, None
        if isinstance(node, Folder) and store.live_folder_of(node):
            return None, None
        return node, target

    def _hide_indicators(self):
        self.indicator.hide()
        self.box.hide()

    def dragEnterEvent(self, event):
        if event.mimeData().hasFormat(TAB_MIME):
            event.acceptProposedAction()

    def dragMoveEvent(self, event):
        node, target = self._dragged(event)
        self._hide_indicators()
        if node is None:
            event.ignore()
            return
        _, _, rect, into_folder = target
        marker = self.box if into_folder else self.indicator
        marker.setGeometry(rect)
        marker.show()
        marker.raise_()
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hide_indicators()

    def dropEvent(self, event):
        self._hide_indicators()
        node, target = self._dragged(event)
        if node is None:
            return
        event.acceptProposedAction()
        folder, index, _, _ = target
        together = dragged_tabs(self.browser, event)
        # Defer so the drag's source widget isn't rebuilt while still inside drag.exec()
        if together:
            QTimer.singleShot(0, lambda: self.browser.move_tabs(together, self.kind, index, folder))
        else:
            QTimer.singleShot(0, lambda: self.browser.move_tab(node, self.kind, index, folder))


def _runs(indices) -> int:
    """How many separate runs of neighbouring positions: [3, 4, 5, 9] is two."""
    indices = sorted(indices)
    return sum(1 for n, i in enumerate(indices) if n == 0 or i != indices[n - 1] + 1)


class RowReveal(QWidget):
    """A row growing into the list (a new tab, a folder opening) or shrinking out of it (closed,
    archived, picked up to drag): the row keeps its full size inside, clipped, while this box's height
    changes, so the rows below glide instead of jumping. Fades as it goes; a row leaving also drifts
    down a little, the way Arc's Clear sweeps tabs away. `keep`: stay folded up when done (a row
    being dragged), ready to unfold again if it isn't dropped anywhere.

    Several rows that come or go together (a folder's tabs, as it opens or closes) move as one block, in one
    box: one height changing a frame, not one per row (which made a folder of tabs judder open)."""

    MS = 190

    def __init__(self, section, row, growing, delay=0, keep=False):
        super().__init__(section)
        self.rows = list(row) if isinstance(row, (list, tuple)) else [row]
        self.row = self.rows[0]
        self.section, self.keep = section, keep
        # Rows of a block sit as far apart as the list puts them (its spacing between them too), so when they go
        # back into it at the end nothing moves: without it they were a few pixels short, and jumped into place
        self.gap = section.layout_.spacing() if len(self.rows) > 1 else 0
        self.full = sum(r.height() or 34 for r in self.rows) + self.gap * (len(self.rows) - 1)
        self.shown = 0.0 if growing else 1.0
        self.growing = growing
        y = 0
        for each in self.rows:
            each.setParent(self)
            each.move(0, y)
            each.show()
            y += (each.height() or 34) + self.gap
        self.fade = QGraphicsOpacityEffect(self)
        self.setGraphicsEffect(self.fade)
        self._size(self.shown)
        target = 1.0 if growing else 0.0
        # The clock starts once this pass is over (the list laid out, its first frame drawn): started at once, the
        # time that takes was already "spent", and the first frame drawn was half-way, a leap
        QTimer.singleShot(delay, lambda: not sip.isdeleted(self) and self._go(target))

    def _go(self, target):
        from . import motion
        self.growing = target > self.shown
        ms = max(1, round(self.MS * abs(target - self.shown)))
        motion.animate(self, self.shown, target, self._set, ms, self._done)

    def _size(self, shown):
        """The room this box takes in the list, `shown` of the way open: its height and the list's gap after it,
        together. A box with no height still has that gap, so it's out of the list (hidden) for the last
        point instead: closing, the rows below used to jump up those 2 points as the box came out, after
        everything else had come to rest."""
        spacing = self.section.layout_.spacing()
        room = (self.full + spacing) * shown  # (what it should take, gap and all)
        height = room - spacing
        if room < spacing / 2:  # (nearer nothing than a gap: nothing, never more than a point out)
            self.setFixedHeight(0)
            self.hide()
        else:
            self.setFixedHeight(max(0, round(height)))
            if self.isHidden():
                self.show()
        self.fade.setOpacity(shown)

    def _set(self, shown):
        if any(sip.isdeleted(r) for r in self.rows):
            return
        self.shown = shown
        self._size(shown)
        # (one row leaving drifts down a little as it goes, like Arc's Clear; a folder's rows, going together, don't:
        # moving down while the rows below came up looked like a wobble)
        y, drift = 0, 0 if self.growing or len(self.rows) > 1 else round(6 * (1 - shown))
        for each in self.rows:
            each.move(0, y + drift)
            y += (each.height() or 34) + self.gap

    def unfold(self):
        """A dragged row that wasn't dropped anywhere: back where it was."""
        self.keep = False
        self._go(1.0)

    def resizeEvent(self, event):
        for each in self.rows:
            if not sip.isdeleted(each):
                each.resize(self.width(), each.height() or 34)

    def _done(self):
        if self.keep and self.shown == 0:
            return  # (folded up out of the way until the drag ends)
        self.section._reveal_done(self)

    def settle(self):
        """Jump to the end now (the list is being rebuilt again)."""
        animation = getattr(self, "_pb_animation", None)
        if animation is not None:
            animation.stop()
        self.keep = False
        self.section._reveal_done(self)


class DropGap(QWidget):
    """Room opening up where a dragged tab will land, the rows either side parting for it: an empty,
    faintly drawn slot, indented to the folder depth it'll land at."""

    MS = 150

    def __init__(self, section, indent):
        super().__init__(section)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.section = section
        self.indent = indent
        self.setFixedHeight(0)

    def open(self):
        self._go(1.0)

    def close(self, done=None):
        self._go(0.0, done)

    def _go(self, target, done=None):
        from . import motion
        start = self.height() / 34
        motion.animate(self, start, target, self._set, max(1, round(self.MS * abs(target - start))), done)

    def _set(self, value):
        self.setFixedHeight(round(34 * value))
        self.update()

    def paintEvent(self, event):
        if self.height() < 4:
            return
        p = QPainter(self)
        p.setRenderHint(QPainter.RenderHint.Antialiasing)
        text = self.section.browser.theme.text
        slot = QRectF(self.indent + 0.5, 1.5, self.width() - self.indent - 7, self.height() - 3.5)
        p.setOpacity(self.height() / 34)
        p.setPen(QPen(_with_alpha(text, 0.22), 1.2, Qt.PenStyle.DashLine))
        p.setBrush(_with_alpha(text, 0.06))
        p.drawRoundedRect(slot, 9, 9)
        p.end()


class TabSection(DropZone):
    def __init__(self, browser, kind, empty_hint="", parent=None):
        super().__init__(browser, kind, parent)
        self.layout_ = QVBoxLayout(self)
        self.layout_.setContentsMargins(0, 0, 0, 0)
        self.layout_.setSpacing(2)
        # Rows packed at the top: while a folder closes, the list is for a moment taller than its rows (its own
        # height catches up a frame later), and spread over that room the rows bounced up and down
        self.layout_.setAlignment(Qt.AlignmentFlag.AlignTop)
        self.nodes: list = []
        self.entries: list[tuple] = []  # (row widget, node, parent folder, depth), in display order
        self.hint = QLabel(empty_hint)
        self.hint.setProperty("dim", True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setFixedHeight(30)
        self.layout_.addWidget(self.hint)
        self.animate_changes = False  # set by the sidebar: only for changes within one Space
        self._reveals = []
        self._gap = None  # where a dragged tab would land (opening), with any older ones closing
        self._gaps = []
        self.dragging = []  # the nodes being dragged out of this list, folded up while they are
        self.just_dropped = None  # the node dropped here, which takes its gap's place as the list rebuilds

    @property
    def rows(self) -> list[TabRow]:
        return [row for row, *_ in self.entries if isinstance(row, TabRow)]

    def set_tabs(self, nodes, refresh=True):
        """The list's rows for these tabs and folders. refresh=False: the rows still here are left as they are
        (opening a folder changes nothing about them)."""
        for reveal in list(self._reveals):
            reveal.settle()
        self._remove_gaps()  # (a dropped tab takes its gap's place in this same pass: nothing jumps)
        self.dragging = []
        dropped, self.just_dropped = self.just_dropped, None
        old = [e for e in self.entries if not sip.isdeleted(e[0]) and not getattr(e[0], "_pb_gone", False)]
        # Rows for tabs and folders that are still here are kept (refreshed, and put back in order)
        # rather than built again: cheaper, and nothing about them flickers
        reusable = {id(node): (row, depth) for row, node, _parent, depth in old}
        self.nodes = nodes
        self.entries = []
        self._add_rows(nodes, None, 0, reusable, refresh)
        reused = {id(row) for row, *_ in self.entries}
        listed = {id(row) for row, *_ in self.entries}
        for row, *_ in old:  # (rows going: out of the order first, so the ones staying keep their places)
            if id(row) not in listed and not sip.isdeleted(row):
                self.layout_.removeWidget(row)
        # Into order, moving only the rows not already where they belong (taking every row out and putting it
        # back was most of the time it took a folder to open)
        first = self.layout_.indexOf(self.hint) + 1
        for i, (row, *_) in enumerate(self.entries):
            if self.layout_.indexOf(row) != first + i:
                self.layout_.removeWidget(row)
                self.layout_.insertWidget(first + i, row)
            if row.isHidden() and self.isVisible():
                row.show()  # now, in the same pass the old rows went (a layout would only show it on the next)
        kept = {id(node) for _, node, *_ in self.entries}
        before = {id(node) for _, node, *_ in old}
        # (a tab just dropped here appears in its gap at once, rather than growing into it)
        added = [row for row, node, *_ in self.entries if id(node) not in before and node is not dropped]
        gone = [(i, row) for i, (row, node, *_) in enumerate(old) if id(node) not in kept]
        from . import motion
        # (a big change, many rows in many places, just shows; a folder of any size opening or closing is one
        # block, and glides)
        animate = (self.animate_changes and old and not motion.reduced() and self.isVisible()
                   and _runs([self.layout_.indexOf(r) for r in added]) <= 8 and len(added) <= 60
                   and (_runs([i for i, _ in gone]) <= 8 or not self.entries) and len(gone) <= 60)
        for i, (row, node, *_) in enumerate(old):
            if id(row) in reused or (animate and (i, row) in gone):
                continue  # still in the list, or shrinks away below
            # Out of sight and out of the layout now: deleteLater only happens on a later pass, and
            # until then the old rows would still show (and take up room) under the new ones
            row.hide()
            self.layout_.removeWidget(row)
            row.deleteLater()
        if animate:
            self._animate(old, kept, added, gone)
        self.hint.setVisible(not nodes and bool(self.hint.text()) and not (animate and gone))
        self.setMinimumHeight(0 if nodes or self.hint.text() else 12)
        self._lay_out_now()

    def _lay_out_now(self):
        """Laid out now, before anything's drawn, this list and then what holds it: otherwise, for a frame, a
        new box (a folder's rows on their way) sat at the list's top-left, and the list was sized from how it
        was a moment before (a closing folder's final height, its rows still all there), everything in it
        squeezed on top of each other."""
        self.layout_.invalidate()
        self.updateGeometry()
        holder = self.parentWidget()
        if holder is not None and holder.layout() is not None:
            holder.layout().invalidate()
            holder.layout().activate()
        self.layout_.activate()

    def _animate(self, old, kept, added, gone):
        # Rows next to each other in the list grow in together, as one block (a folder's tabs as it opens)
        blocks = []
        for row in added:
            index = self.layout_.indexOf(row)
            if blocks and blocks[-1][0] + len(blocks[-1][1]) == index:
                blocks[-1][1].append(row)
            else:
                blocks.append((index, [row]))
        for index, rows in reversed(blocks):  # (from the bottom up: the indices above stay true)
            for row in rows:
                self.layout_.removeWidget(row)
            reveal = RowReveal(self, rows, growing=True)
            self.layout_.insertWidget(index, reveal)
            self._reveals.append(reveal)
        # A row that's gone shrinks where it was: after the nearest row above it that's still here
        position = {id(node): row for row, node, *_ in self.entries}
        clearing = not self.entries  # everything going at once (Clear): a quick sweep, top to bottom
        inserted_after = {}
        # Rows that were next to each other and go together shrink away as one block (a folder closing); Clear's
        # sweep keeps them one at a time
        groups = []
        for i, row in gone:
            if not clearing and groups and groups[-1][0] + len(groups[-1][1]) == i:
                groups[-1][1].append(row)
            else:
                groups.append((i, [row]))
        for n, (i, rows) in enumerate(groups):
            anchor = next((position[id(old[k][1])] for k in range(i - 1, -1, -1) if id(old[k][1]) in kept), None)
            for row in rows:
                self.layout_.removeWidget(row)
            base = self.layout_.indexOf(anchor) + 1 if anchor is not None else 1  # (1: after the hint)
            offset = inserted_after.get(id(anchor), 0)
            inserted_after[id(anchor)] = offset + 1
            reveal = RowReveal(self, rows, growing=False, delay=n * 22 if clearing else 0)
            self.layout_.insertWidget(base + offset, reveal)
            self._reveals.append(reveal)

    def _reveal_done(self, reveal):
        if reveal not in self._reveals:
            return
        self._reveals.remove(reveal)
        index = self.layout_.indexOf(reveal)
        # The box out of the list first, then its rows in its place, and the list laid out now: the other way
        # round, for a frame both took up room and everything below dropped a block's height and came back
        reveal.hide()
        self.layout_.removeWidget(reveal)
        listed = {id(r) for r, *_ in self.entries}
        for row in reveal.rows:
            if sip.isdeleted(row):
                continue
            if reveal.growing and id(row) in listed:
                row.setParent(self)
                self.layout_.insertWidget(index, row)  # back into the list where it grew in
                index += 1
                row.show()
            else:
                row.hide()
                row._pb_gone = True  # (deleted on a later pass: set_tabs mustn't count it as still here)
                row.deleteLater()
        self.layout_.activate()
        reveal.setGraphicsEffect(None)
        reveal.deleteLater()
        if not self._reveals:
            self.hint.setVisible(not self.nodes and bool(self.hint.text()))

    def _add_rows(self, nodes, parent, depth, reusable=None, refresh=True):
        for node in nodes:
            kind = FolderRow if isinstance(node, Folder) else TabRow
            row, was_depth = (reusable or {}).get(id(node), (None, None))
            if row is not None and isinstance(row, kind) and was_depth == depth and row.parentWidget() is self:
                if refresh or isinstance(row, FolderRow):  # (a folder's own row shows it open or closed)
                    row.refresh()
            elif kind is FolderRow:
                row = FolderRow(self.browser, node, depth, self)
            else:
                row = TabRow(self.browser, node, self.kind, depth, self)
            self.entries.append((row, node, parent, depth))
            if isinstance(node, Folder) and node.expanded:
                self._add_rows(node.children, node, depth + 1, reusable, refresh)

    def _line(self, y, depth):
        x = 6 + depth * INDENT
        return QRect(x, max(y, 0), self.width() - x - 6, 2)

    # --- Dragging: the row lifts out, and the others part where it'll land -----------------------

    def lift(self, row):
        """A row picked up to drag: it folds out of the list (and unfolds if it's let go of nowhere)."""
        entry = next((e for e in self.entries if e[0] is row), None)
        if entry is None or sip.isdeleted(row) or isinstance(row.parentWidget(), RowReveal):
            return
        from . import motion
        if motion.reduced():
            return
        self.dragging = [*self.dragging, entry[1]]
        index = self.layout_.indexOf(row)
        self.layout_.removeWidget(row)
        reveal = RowReveal(self, row, growing=False, keep=True)
        self.layout_.insertWidget(index, reveal)
        self._reveals.append(reveal)

    def drag_ended(self, row):
        """The drag is over. If nothing rebuilt this list (it wasn't moved), the row comes back."""
        self.dragging = []
        if not sip.isdeleted(row) and isinstance(row.parentWidget(), RowReveal) and row.parentWidget().keep:
            row.parentWidget().unfold()
        self._close_gaps()

    def _holder(self, row):
        parent = row.parentWidget()
        return parent if isinstance(parent, RowReveal) else row

    def _rect(self, row):
        """Where a row is in this list, even while it's in a block growing in or shrinking away."""
        holder = self._holder(row)
        if holder is row:
            return row.geometry()
        return QRect(holder.geometry().topLeft() + row.pos(), QSize(holder.width(), row.height()))

    def _places(self):
        """[(row, node, parent, depth, rect)] where each row would be with no gap open (so the target
        doesn't jump about as the gap moves the rows), leaving out the one being dragged."""
        gaps = [g for g in self._gaps if not sip.isdeleted(g)]
        out = []
        for row, node, parent, depth in self.entries:
            if any(node is n for n in self.dragging) or sip.isdeleted(row):
                continue
            rect = self._rect(row)
            above = sum(g.height() + self.layout_.spacing() * (g.height() > 0) for g in gaps
                        if g.geometry().top() < rect.top())
            out.append((row, node, parent, depth, rect.translated(0, -above)))
        return out

    def drop_target(self, pos):
        places = self._places()
        for row, node, parent, depth, g in places:
            if pos.y() > g.bottom():
                continue
            siblings = parent.children if parent else self.nodes
            i = next((k for k, n in enumerate(siblings) if n is node), None)
            if i is None:
                continue  # (gone from the store since the rows were laid out: a Live Folder refreshed mid-drag)
            if isinstance(node, Folder):
                # The middle of a folder row means "put it inside"
                third = g.height() / 3
                if g.top() + third <= pos.y() <= g.bottom() - third:
                    return node, len(node.children), self._holder(row).geometry().adjusted(1, 0, -1, 0), True
            if pos.y() < g.center().y():
                return parent, i, self._line(g.top() - 2, depth), False
            if isinstance(node, Folder) and node.expanded:
                return node, 0, self._line(g.bottom() + 1, depth + 1), False
            return parent, i + 1, self._line(g.bottom() + 1, depth), False
        if places:
            return None, len(self.nodes), self._line(places[-1][4].bottom() + 1, 0), False
        return None, 0, self._line(self.height() // 2, 0), False

    def dragMoveEvent(self, event):
        node, target = self._dragged(event)
        self._hide_indicators()
        if node is None:
            self._close_gaps()
            event.ignore()
            return
        _, _, rect, into_folder = target
        if into_folder:
            self._close_gaps()
            self.box.setGeometry(rect)
            self.box.show()
            self.box.raise_()
        else:
            # The gap goes before the first row that would sit below the line
            after = next((self._holder(row) for row, _n, _p, _d, g in self._places() if g.top() >= rect.top()), None)
            self._open_gap(after, rect.left())
        event.acceptProposedAction()

    def dragLeaveEvent(self, event):
        self._hide_indicators()
        self._close_gaps()

    def dropEvent(self, event):
        node, _target = self._dragged(event)
        self.just_dropped = node
        super().dropEvent(event)
        # The move rebuilds this list a moment later, and the tab takes the gap's place then; a drop
        # that changes nothing (it was already there) still closes it
        QTimer.singleShot(200, self._close_gaps)

    def _open_gap(self, before, indent):
        gap = self._gap
        if gap is not None and not sip.isdeleted(gap) and gap.anchor is before and gap.indent == indent:
            return
        self._close_gaps()
        gap = DropGap(self, indent)
        gap.anchor = before
        index = self.layout_.indexOf(before) if before is not None else -1
        self.layout_.insertWidget(index if index >= 0 else self.layout_.count(), gap)
        gap.show()
        gap.open()
        self._gap = gap
        self._gaps.append(gap)

    def _close_gaps(self):
        self._gap = None
        for gap in list(self._gaps):
            if not sip.isdeleted(gap):
                gap.close(lambda g=gap: self._drop_gap(g))

    def _drop_gap(self, gap):
        if gap in self._gaps:
            self._gaps.remove(gap)
        if not sip.isdeleted(gap):
            gap.hide()
            self.layout_.removeWidget(gap)
            gap.deleteLater()

    def _remove_gaps(self):
        self._gap = None
        for gap in list(self._gaps):
            self._drop_gap(gap)


class FavoritesGrid(DropZone):
    def __init__(self, browser, parent=None):
        super().__init__(browser, FAVORITES, parent)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.tiles: list[FavoriteTile] = []
        self.hint = QLabel("Drag tabs here to favorite")
        self.hint.setProperty("dim", True)
        self.hint.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.hint.setFixedHeight(32)

    def set_tabs(self, tabs):
        # The same Favorites: kept, just refreshed, in the new Space's colors too (making them again is most of a
        # rebuild's time, and switching to a Space of another color made them all again)
        self._tabs = tabs
        same = (tuple(map(id, tabs)), self._columns_that_fit())
        if self.tiles and same == getattr(self, "_built", None) and all(not sip.isdeleted(t) for t in self.tiles):
            for tile in self.tiles:
                tile.refresh()
            return
        self._built = same
        for tile in self.tiles:
            tile.hide()  # (see TabSection.set_tabs: gone now, not on a later pass)
            self.grid.removeWidget(tile)
            tile.deleteLater()
        self.grid.removeWidget(self.hint)
        # A fresh grid each time: a QGridLayout never forgets a column it once had, so going from four
        # Favorites to three would leave an empty quarter of the row
        QWidget().setLayout(self.grid)  # (the old one goes with that throwaway widget)
        self.grid = QGridLayout(self)
        self.grid.setContentsMargins(0, 0, 0, 0)
        self.grid.setSpacing(6)
        self.tiles = [FavoriteTile(self.browser, t, self) for t in tabs]
        # (fewer than a row's worth share the row; a narrow sidebar has fewer columns, never tiles cut off)
        columns = max(1, min(len(self.tiles), self._columns_that_fit()))
        for i, tile in enumerate(self.tiles):
            self.grid.addWidget(tile, i // columns, i % columns)
            if self.isVisible():
                tile.show()  # (see TabSection.set_tabs)
        if self.tiles:
            self.hint.hide()
            for col in range(columns):  # equal widths, even when the last row isn't full
                self.grid.setColumnStretch(col, 1)
        else:
            self.grid.addWidget(self.hint, 0, 0)
            self.hint.show()

    def _columns_that_fit(self) -> int:
        """How many tiles fit side by side at this width (up to FAVORITE_COLUMNS)."""
        sidebar = self.browser.sidebar if hasattr(self.browser, "sidebar") else None
        if compact(self.browser):
            return 1  # (collapsed: one above another)
        if sidebar is None or sidebar.width() <= 0 or not self.tiles:
            return FAVORITE_COLUMNS
        margins = sidebar.layout().contentsMargins()
        room = sidebar.width() - margins.left() - margins.right()  # (not this grid's own: too many columns widen it)
        tile = max(t.minimumSizeHint().width() for t in self.tiles if not sip.isdeleted(t)) if any(
            not sip.isdeleted(t) for t in self.tiles) else 0
        if tile <= 0:
            return FAVORITE_COLUMNS
        return max(1, min(FAVORITE_COLUMNS, (room + 6) // (tile + 6)))

    def refit(self):
        """The sidebar changed width: as many columns as fit now."""
        tabs = getattr(self, "_tabs", None)
        built = getattr(self, "_built", None)
        if tabs is not None and built is not None and built[-1] != self._columns_that_fit():
            QTimer.singleShot(0, lambda: not sip.isdeleted(self) and self.set_tabs(self._tabs))

    def drop_target(self, pos):
        index = self._drop_index(pos)
        return None, index, self._indicator_rect(index), False

    def _drop_index(self, pos):
        for i, tile in enumerate(self.tiles):
            g = tile.geometry()
            same_row = g.top() <= pos.y() <= g.bottom() + self.grid.spacing()
            if same_row and pos.x() < g.center().x():
                return i
            if pos.y() < g.top():
                return i
        return len(self.tiles)

    def _indicator_rect(self, index):
        if not self.tiles:
            return QRect(6, self.height() // 2, self.width() - 12, 2)
        if index < len(self.tiles):
            g = self.tiles[index].geometry()
            return QRect(g.left() - 4, g.top() + 6, 2, g.height() - 12)
        g = self.tiles[-1].geometry()
        return QRect(g.right() + 2, g.top() + 6, 2, g.height() - 12)


class ListFade(QWidget):
    """The bottom of the tab list fades into the sidebar while there's more below, rather than ending on a
    row cut in half (by the Up Next card, say). Drawn with the sidebar's own background, fading in."""
    HEIGHT = 24

    def __init__(self, sidebar, scroll):
        super().__init__(scroll)
        self.sidebar, self.scroll = sidebar, scroll
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        bar = scroll.verticalScrollBar()
        bar.valueChanged.connect(self.place)
        bar.rangeChanged.connect(self.place)
        scroll.installEventFilter(self)

    def place(self, *_):
        bar = self.scroll.verticalScrollBar()
        self.setGeometry(0, self.scroll.height() - self.HEIGHT, self.scroll.width(), self.HEIGHT)
        self.setVisible(bar.value() < bar.maximum())
        self.raise_()
        overlay = getattr(self.scroll, "overlay_scrollbar", None)
        if overlay is not None:
            overlay.raise_()  # (the scrollbar stays on top of it)
        self.update()

    def eventFilter(self, obj, event):
        if event.type() in (QEvent.Type.Resize, QEvent.Type.Show):
            self.place()
        return False

    def paintEvent(self, event):
        theme = self.sidebar.browser.theme
        # Docked, the background is the window's; slid out over the page, it's the sidebar's own
        floating = self.sidebar.property("floating") is True
        surface = self.sidebar if floating else self.window()
        offset = self.mapTo(surface, QPoint(0, 0))
        ratio = self.devicePixelRatioF()
        image = QImage(max(1, round(self.width() * ratio)), max(1, round(self.height() * ratio)),
                       QImage.Format.Format_ARGB32_Premultiplied)
        image.setDevicePixelRatio(ratio)
        image.fill(Qt.GlobalColor.transparent)
        p = QPainter(image)
        p.fillRect(self.rect(), theme.gradient(surface.width(), surface.height(), offset.x(), offset.y()))
        if theme.grain and not floating:
            p.drawTiledPixmap(self.rect(), noise_pixmap(), QPoint(offset.x() % 128, offset.y() % 128))
        p.setCompositionMode(QPainter.CompositionMode.CompositionMode_DestinationIn)
        ramp = QLinearGradient(0, 0, 0, self.height())
        ramp.setColorAt(0, QColor(0, 0, 0, 0))
        ramp.setColorAt(1, QColor(0, 0, 0, 255))
        p.fillRect(self.rect(), ramp)
        p.end()
        painter = QPainter(self)
        painter.drawImage(0, 0, image)
        painter.end()
