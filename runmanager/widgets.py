#####################################################################
#                                                                   #
# /widgets.py                                                       #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Widgets for the Runmanager GUI
"""

import unicodedata

from qtutils.qt import QtCore, QtGui, QtWidgets
from qtutils.qt.QtCore import Qt

from labscript_utils.qtwidgets.leftclick import LeftClickMixin, MovableRowsMixin
from labscript_utils.qtwidgets.themed_window import best_contrast


def mix_colors(color0, color1, fraction):
    """The colour that is the given fraction of the way from color0 to color1 in RGB"""
    return QtGui.QColor.fromRgbF(
        *((1 - fraction) * a + fraction * b for a, b in zip(color0.getRgbF(), color1.getRgbF()))
    )


# Colour that is mixed into the button colour for the engage button, and its fraction:
ENGAGE_RED = '#dc3232'
ENGAGE_RED_FRACTION = 0.6
# Opacity of the highlight colour over selected cells:
SELECTION_FRACTION = 0.25


def engage_button_style_sheet(palette):
    """Style sheet for the engage button: the button colour of the palette mixed with
    ENGAGE_RED, lighter on hover and darker when pressed. The palette must not be the
    button's own, which has the background colour of the style sheet as its button
    colour."""
    background = mix_colors(
        palette.color(QtGui.QPalette.ColorRole.Button),
        QtGui.QColor(ENGAGE_RED),
        ENGAGE_RED_FRACTION,
    )
    return f"""
QToolButton {{
    background-color: {background.name()};
    border: 1px solid {background.darker(130).name()};
    border-radius: 3px;
    padding: 4px;
}}
QToolButton:hover {{
    background-color: {background.lighter(115).name()};
}}
QToolButton:pressed {{
    background-color: {background.darker(115).name()};
}}
"""


def composite_colors(r0, g0, b0, a0, r1, g1, b1, a1):
    """composite a second colour over a first with given alpha values and return the
    result"""
    a0 /= 255
    a1 /= 255
    a = a0 + a1 - a0 * a1
    r = (a1 * r1 + (1 - a1) * a0 * r0) / a
    g = (a1 * g1 + (1 - a1) * a0 * g0) / a
    b = (a1 * b1 + (1 - a1) * a0 * b0) / a
    return [int(round(x)) for x in (r, g, b, 255 * a)]


def expression_cell_colors():
    palette = QtWidgets.QApplication.instance().palette()
    text_color = palette.color(QtGui.QPalette.Text)
    active_candidates = [QtGui.QColor('#D7ECFF'), QtGui.QColor('#234A75')]
    error_candidates = [QtGui.QColor('#F79494'), QtGui.QColor('#8A2E2E')]
    bool_on_candidates = [QtGui.QColor('#63F731'), QtGui.QColor('#29A300')]
    bool_off_candidates = [QtGui.QColor('#608060'), QtGui.QColor('#003900')]
    active = best_contrast(text_color, active_candidates)
    error = best_contrast(text_color, error_candidates)
    bool_on = best_contrast(text_color, bool_on_candidates)
    bool_off = best_contrast(text_color, bool_off_candidates)
    return error.name(), active.name(), bool_on.name(), bool_off.name()


def hidden_unicode_issues(text):
    issues = []
    for index, char in enumerate(text, 1):
        category = unicodedata.category(char)
        if char in '\t\n\r':
            continue
        if not char.isprintable() or category in {'Cf', 'Cc', 'Cs', 'Co'}:
            issues.append(
                'col %d: U+%04X %s'
                % (index, ord(char), unicodedata.name(char, 'UNKNOWN'))
            )
    return issues


def tooltip_with_hidden_unicode_warning(base_tooltip, text):
    issues = hidden_unicode_issues(text)
    if not issues:
        return base_tooltip
    warning = 'Contains hidden Unicode character(s): ' + '; '.join(issues)
    if base_tooltip:
        return base_tooltip + '\n' + warning
    return warning


def scroll_view_to_row_if_current(view, item):
    """Checks to see if the item is in the row of the current item. If it is, scrolls
    the treeview/tableview vertically to ensure that row is visible. This is done by
    recording the horizontal scroll position, then using view.scrollTo(), and then
    restoring the horizontal position"""
    horizontal_scrollbar = view.horizontalScrollBar()
    existing_horizontal_position = horizontal_scrollbar.value()
    index = item.index()
    current_index = view.currentIndex()
    if index.row() == current_index.row() and index.parent() == current_index.parent():
        view.scrollTo(index)
        horizontal_scrollbar.setValue(existing_horizontal_position)


_RIGHT_SIDE = QtWidgets.QTabBar.ButtonPosition.RightSide


class FingerTabBarWidget(QtWidgets.QTabBar):

    """A TabBar with the tabs on the left and the text horizontal. Credit to
    @LegoStormtroopr, https://gist.github.com/LegoStormtroopr/5075267.
    FingerTabWidget installs one as its tab bar."""

    def __init__(self, parent=None, minwidth=180, minheight=30, **kwargs):
        QtWidgets.QTabBar.__init__(self, parent, **kwargs)
        self.minwidth = minwidth
        self.minheight = minheight
        self._movable = None
        self.tab_movable = {}
        self.paint_clip = None

    def setMovable(self, movable, index=None):
        """Set tabs movable on an individual basis, or set for all tabs if no
        index specified"""
        if index is None:
            self._movable = movable
            self.tab_movable = {}
            QtWidgets.QTabBar.setMovable(self, movable)
        else:
            self.tab_movable[int(index)] = bool(movable)

    def isMovable(self, index=None):
        if index is None:
            if self._movable is None:
                self._movable = QtWidgets.QTabBar.isMovable(self)
            return self._movable
        return self.tab_movable.get(index, self._movable)

    def setTabIconOnRight(self, index, on_right):
        """Draw the tab's icon at the right of the tab, to the left of its button,
        instead of to the left of its text"""
        self.setTabData(index, bool(on_right))
        self.update()

    def tabIconOnRight(self, index):
        return bool(self.tabData(index))

    def indexAtPos(self, point):
        for index in range(self.count()):
            if self.tabRect(index).contains(point):
                return index

    def mousePressEvent(self, event):
        index = self.indexAtPos(event.position().toPoint())
        if not self.tab_movable.get(index, self.isMovable()):
            # Disable dragging until they release the mouse:
            QtWidgets.QTabBar.setMovable(self, False)
        return QtWidgets.QTabBar.mousePressEvent(self, event)

    def mouseReleaseEvent(self, event):
        if self.isMovable():
            # Restore this in case it was temporarily disabled by mousePressEvent
            QtWidgets.QTabBar.setMovable(self, True)
        return QtWidgets.QTabBar.mouseReleaseEvent(self, event)

    def tabLayoutChange(self):
        total_height = 0
        for index in range(self.count()):
            tabRect = self.tabRect(index)
            total_height += tabRect.height()
        if total_height > self.parent().height():
            # Don't paint over the top of the scroll buttons:
            scroll_buttons_area_height = 2*self.style().pixelMetric(
                QtWidgets.QStyle.PixelMetric.PM_TabBarScrollButtonWidth)
            self.paint_clip = (
                self.width(), self.parent().height() - scroll_buttons_area_height
            )
        else:
            self.paint_clip = None

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        if self.paint_clip is not None:
            painter.setClipRect(0, 0, *self.paint_clip)

        option = QtWidgets.QStyleOptionTab()
        for index in range(self.count()):
            tabRect = self.tabRect(index)
            self.initStyleOption(option, index)
            painter.drawControl(
                QtWidgets.QStyle.ControlElement.CE_TabBarTabShape, option
            )
            text_left = tabRect.left() + 10
            text_right = tabRect.right() - 10
            button = self.tabButton(index, _RIGHT_SIDE)
            if button is not None:
                text_right = button.x() - 5
            if not self.tabIcon(index).isNull():
                icon_rect = QtCore.QRect(QtCore.QPoint(0, 0), self.iconSize())
                icon_rect.moveCenter(tabRect.center())
                if self.tabIconOnRight(index):
                    icon_rect.moveRight(text_right)
                    text_right = icon_rect.left() - 5
                else:
                    icon_rect.moveLeft(text_left)
                    text_left = icon_rect.right() + 6
                self.tabIcon(index).paint(painter, icon_rect)
            textRect = QtCore.QRect(tabRect)
            textRect.setLeft(text_left)
            textRect.setRight(text_right)
            text = painter.fontMetrics().elidedText(
                self.tabText(index), Qt.TextElideMode.ElideRight, textRect.width()
            )
            painter.drawText(textRect, Qt.AlignmentFlag.AlignVCenter, text)
        if self.paint_clip is not None:
            x_clip, y_clip = self.paint_clip
            painter.setClipping(False)
            palette = self.palette()
            mid_color = palette.color(QtGui.QPalette.ColorRole.Mid)
            painter.setPen(mid_color)
            painter.drawLine(0, y_clip, x_clip, y_clip)
        painter.end()

    def tabSizeHint(self, index):
        fontmetrics = QtGui.QFontMetrics(self.font())
        text_width = fontmetrics.horizontalAdvance(self.tabText(index))
        text_height = fontmetrics.height()
        height = text_height + 15
        height = max(self.minheight, height)
        width = text_width + 15
        if not self.tabIcon(index).isNull():
            width += self.iconSize().width() + 5

        button = self.tabButton(index, _RIGHT_SIDE)
        if button is not None:
            height = max(height, button.height() + 7)
            # Same amount of space around the button horizontally as it has vertically:
            width += button.width() + height - button.height()
        width = max(self.minwidth, width)
        return QtCore.QSize(width, height)

    def setTabButton(self, index, geometry, button):
        if button is not None and not isinstance(button, TabToolButton):
            raise TypeError(
                "Not a TabToolButton, won't paint correctly. Use a TabToolButton"
            )
        result = QtWidgets.QTabBar.setTabButton(self, index, geometry, button)
        if button is not None:
            button.move(*button.get_correct_position())
        return result


class TabToolButton(QtWidgets.QToolButton):
    def __init__(self, *args, **kwargs):
        QtWidgets.QToolButton.__init__(self, *args, **kwargs)
        self.setFocusPolicy(Qt.FocusPolicy.NoFocus)

    def paintEvent(self, event):
        painter = QtWidgets.QStylePainter(self)
        paint_clip = self.parent().paint_clip
        if paint_clip is not None:
            point = QtCore.QPoint(*paint_clip)
            global_point = self.parent().mapToGlobal(point)
            local_point = self.mapFromGlobal(global_point)
            painter.setClipRect(0, 0, local_point.x(), local_point.y())
        option = QtWidgets.QStyleOptionToolButton()
        self.initStyleOption(option)
        painter.drawComplexControl(
            QtWidgets.QStyle.ComplexControl.CC_ToolButton, option
        )

    def get_correct_position(self):
        parent = self.parent()
        for index in range(parent.count()):
            if parent.tabButton(index, _RIGHT_SIDE) is self:
                break
        else:
            raise LookupError('Tab not found')
        tabRect = parent.tabRect(index)
        tab_x, tab_y = tabRect.x(), tabRect.y()
        tab_width, tab_height = tabRect.width(), tabRect.height()
        size = self.sizeHint()
        width = size.width()
        height = size.height()
        padding = int((tab_height - height) / 2)
        correct_x = tab_x + tab_width - width - padding
        correct_y = tab_y + padding
        return correct_x, correct_y

    def moveEvent(self, event):
        try:
            correct_x, correct_y = self.get_correct_position()
        except LookupError:
            return # Things aren't initialised yet
        if self.x() != correct_x or self.y() != correct_y:
            # Move back! I shall not be moved!
            self.move(correct_x, correct_y)
        return QtWidgets.QToolButton.moveEvent(self, event)


# Shamelessly stolen from this thread:
#   http://www.riverbankcomputing.com/pipermail/pyqt/2005-December/011724.html
class FingerTabWidget(QtWidgets.QTabWidget):
    """A QTabWidget equivalent which uses our FingerTabBarWidget.

    `addTab` takes `closable=True` for a close button on the tab, which emits
    `tabCloseRequested`.
    """

    def __init__(self, parent, *args):
        QtWidgets.QTabWidget.__init__(self, parent, *args)
        self.setTabBar(FingerTabBarWidget(self))

    def addTab(self, *args, **kwargs):
        closeable = kwargs.pop('closable', False)
        icon_on_right = kwargs.pop('icon_on_right', False)
        index = QtWidgets.QTabWidget.addTab(self, *args, **kwargs)
        self.setTabClosable(index, closeable)
        self.tabBar().setTabIconOnRight(index, icon_on_right)
        return index

    def setTabClosable(self, index, closable):
        right_button = self.tabBar().tabButton(index, _RIGHT_SIDE)
        if closable:
            if not right_button:
                # Make one:
                close_button = TabToolButton(self.parent())
                close_button.setIcon(QtGui.QIcon(':/qtutils/fugue/cross'))
                self.tabBar().setTabButton(
                    index, _RIGHT_SIDE, close_button
                )
                close_button.clicked.connect(
                    lambda: self._on_close_button_clicked(close_button)
                )
        else:
            if right_button:
                # Get rid of it:
                self.tabBar().setTabButton(index, _RIGHT_SIDE, None)

    def _on_close_button_clicked(self, button):
        for index in range(self.tabBar().count()):
            if self.tabBar().tabButton(index, _RIGHT_SIDE) is button:
                self.tabCloseRequested.emit(index)
                break


def set_selection_palette(widget):
    """Give the widget the highlight colour of the application palette, partly
    transparent, and the window text colour as the highlighted text colour"""
    source = QtWidgets.QApplication.palette(widget)
    highlight = source.color(QtGui.QPalette.Highlight)
    highlight.setAlphaF(SELECTION_FRACTION)
    text = source.color(QtGui.QPalette.WindowText)
    palette = widget.palette()
    if palette.color(QtGui.QPalette.Highlight) == highlight and palette.color(
        QtGui.QPalette.HighlightedText
    ) == text:
        return
    for group in [QtGui.QPalette.Active, QtGui.QPalette.Inactive]:
        palette.setColor(group, QtGui.QPalette.Highlight, highlight)
        palette.setColor(group, QtGui.QPalette.HighlightedText, text)
    widget.setPalette(palette)


class ItemView(LeftClickMixin):
    """Mixin for QTableView and QTreeView that adds modified tab and arrow key
    behaviour and custom selection highlighting to LeftClickMixin."""

    def __init__(self, *args):
        super().__init__(*args)
        self.setAutoScroll(False)
        set_selection_palette(self)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.PaletteChange:
            set_selection_palette(self)
        super().changeEvent(event)

    def keyPressEvent(self, event):
        index = self.currentIndex()
        if (
            event.key() in [QtCore.Qt.Key_Space, QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]
            and index.isValid()
        ):
            if self.model().itemFromIndex(index).isEditable():
                # Space/enter edits editable items:
                self.edit(index)
            else:
                # Space/enter on non-editable items simulates a left click:
                self.leftClicked.emit(index)
        return super().keyPressEvent(event)

    def moveCursor(self, cursor_action, keyboard_modifiers):
        current_index = self.currentIndex()
        current_row, current_column = current_index.row(), current_index.column()
        if cursor_action == QtWidgets.QAbstractItemView.MoveUp:
            return current_index.sibling(current_row - 1, current_column)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveDown:
            return current_index.sibling(current_row + 1, current_column)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveLeft:
            return current_index.sibling(current_row, current_column - 1)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveRight:
            return current_index.sibling(current_row, current_column + 1)
        elif cursor_action == QtWidgets.QAbstractItemView.MovePrevious:
            return current_index.sibling(current_row, current_column - 1)
        elif cursor_action == QtWidgets.QAbstractItemView.MoveNext:
            return current_index.sibling(current_row, current_column + 1)
        else:
            return super().moveCursor(cursor_action, keyboard_modifiers)


class TreeView(ItemView, MovableRowsMixin, QtWidgets.QTreeView):
    """Treeview version of our customised ItemView"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set columns to their minimum size, disabling resizing. Caller may still
        # configure a specific section to stretch:
        self.header().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.setItemDelegate(ItemDelegate(self))


class GlobalsTreeView(TreeView):
    def moveCursor(self, cursor_action, keyboard_modifiers):
        if (
            cursor_action in (
                QtWidgets.QAbstractItemView.CursorAction.MoveNext,
                QtWidgets.QAbstractItemView.CursorAction.MovePrevious,
            )
            and not self.currentIndex().parent().isValid()
        ):
            return ItemView.moveCursor(self, cursor_action, keyboard_modifiers)
        return QtWidgets.QTreeView.moveCursor(self, cursor_action, keyboard_modifiers)

    def mouseReleaseEvent(self, event):
        index = self.indexAt(event.position().toPoint())
        if (
            index.isValid()
            and index.column() == self.treePosition()
            and event.position().x() < self.visualRect(index).left()
        ):
            self._pressed_index = None
        return super().mouseReleaseEvent(event)


class TableView(ItemView, QtWidgets.QTableView):
    """TableView version of our customised ItemView"""
    def __init__(self, parent=None):
        super().__init__(parent)
        # Set rows and columns to the minimum size, disabling interactive resizing.
        # Caller may still configure a specific column to stretch:
        self.verticalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.horizontalHeader().setSectionResizeMode(
            QtWidgets.QHeaderView.ResizeToContents
        )
        self.horizontalHeader().sectionResized.connect(self.on_column_resized)
        self.setItemDelegate(ItemDelegate(self))
        self.verticalHeader().hide()
        self.setShowGrid(False)
        self.horizontalHeader().setHighlightSections(False)

    def on_column_resized(self, col):
        for row in range(self.model().rowCount()):
            self.resizeRowToContents(row)


class AlternatingColorModel(QtGui.QStandardItemModel):

    def __init__(self, view):
        QtGui.QStandardItemModel.__init__(self)
        self.view = view

        # A cache, store brushes so we don't have to recalculate them. Is faster.
        self.bg_brushes = {}

    def get_bgbrush(self, normal_brush, alternate, selected):
        """Get cell colour as a function of its ordinary colour, whether it is on an odd
        row, and whether it is selected."""
        normal_rgb = normal_brush.color().getRgb() if normal_brush is not None else None
        # The palette is read on each call and is part of the cache key, so that
        # brushes follow a change of theme. Clearing the cache on the window's
        # PaletteChange would refill it before this view has the new palette.
        palette = self.view.palette()
        normal_color = palette.color(QtGui.QPalette.Base)
        alternate_color = palette.color(QtGui.QPalette.AlternateBase)
        highlight_color = palette.color(QtGui.QPalette.Highlight)
        key = (
            normal_rgb, alternate, selected,
            normal_color.rgba(), alternate_color.rgba(), highlight_color.rgba(),
        )
        try:
            return self.bg_brushes[key]
        except KeyError:
            pass
        # Get the colour of the cell with alternate row shading:
        if normal_rgb is None:
            # No colour has been set. Use palette colours:
            if alternate:
                bg_color = alternate_color
            else:
                bg_color = normal_color
        else:
            bg_color = normal_brush.color()
            if alternate:
                # Modify alternate rows:
                r, g, b, a = normal_rgb
                nr, ng, nb, na = normal_color.getRgb()
                ar, ag, ab, aa = alternate_color.getRgb()
                alt_r = min(max(r + ar - nr, 0), 255)
                alt_g = min(max(g + ag - ng, 0), 255)
                alt_b = min(max(b + ab - nb, 0), 255)
                alt_a = min(max(a + aa - na, 0), 255)
                bg_color = QtGui.QColor(alt_r, alt_g, alt_b, alt_a)

        # If parent is a TableView, we handle selection highlighting as part of the
        # background colours:
        if selected and isinstance(self.view, QtWidgets.QTableView):
            # Overlay highlight colour:
            r_s, g_s, b_s, a_s = highlight_color.getRgb()
            r_0, g_0, b_0, a_0 = bg_color.getRgb()
            rgb = composite_colors(r_0, g_0, b_0, a_0, r_s, g_s, b_s, a_s)
            bg_color = QtGui.QColor(*rgb)

        brush = QtGui.QBrush(bg_color)
        self.bg_brushes[key] = brush
        return brush

    def data(self, index, role):
        """When background color data is being requested, returns modified colours for
        every second row, according to the palette of the view. This has the effect of
        making the alternate colours visible even when custom colors have been set - the
        same shading will be applied to the custom colours. Only really looks sensible
        when the normal and alternate colors are similar. Also applies selection
        highlight colour of the view's palette, similarly with alternate-row
        shading, for the case of a QTableView."""
        if role == QtCore.Qt.BackgroundRole:
            normal_brush = QtGui.QStandardItemModel.data(self, index, QtCore.Qt.BackgroundRole)
            selected = self.view.selectionModel().isSelected(index)
            alternate = index.row() % 2
            return self.get_bgbrush(normal_brush, alternate, selected)
        return QtGui.QStandardItemModel.data(self, index, role)


class Editor(QtWidgets.QTextEdit):
    """Popup editor with word wrapping and automatic resizing."""
    def __init__(self, parent):
        QtWidgets.QTextEdit.__init__(self, parent)
        self.setWordWrapMode(QtGui.QTextOption.WordWrap)
        self.setAcceptRichText(False)
        self.setVerticalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.setHorizontalScrollBarPolicy(QtCore.Qt.ScrollBarAlwaysOff)
        self.textChanged.connect(self.update_size)
        self.initial_height = None
        set_selection_palette(self)

    def changeEvent(self, event):
        if event.type() == QtCore.QEvent.PaletteChange:
            set_selection_palette(self)
        super().changeEvent(event)

    def update_size(self):
        if self.initial_height is not None:
            # Temporarily shrink back to the initial height, just so that the document
            # size below returns the preferred size rather than the current size.
            # QTextDocument doesn't have a sizeHint of minimumSizeHint method, so this
            # is the best we can do to get its minimum size.
            self.setFixedHeight(self.initial_height)
        preferred_height = self.document().size().toSize().height()
        # Do not shrink smaller than the initial height:
        if self.initial_height is not None and preferred_height >= self.initial_height:
            self.setFixedHeight(preferred_height)

    def resizeEvent(self, event):
        result = QtWidgets.QTextEdit.resizeEvent(self, event)
        # Record the initial height after it is first set:
        if self.initial_height is None:
            self.initial_height = self.height()
        return result
        


class ItemDelegate(QtWidgets.QStyledItemDelegate):

    """An item delegate with a larger row height and column width, faint grey vertical
    lines between columns, and a custom editor for handling multi-line data"""
    MIN_ROW_HEIGHT = 22
    EXTRA_ROW_HEIGHT = 6
    EXTRA_COL_WIDTH = 20

    def __init__(self, *args, **kwargs):
        QtWidgets.QStyledItemDelegate.__init__(self, *args, **kwargs)
        self._pen = QtGui.QPen()
        self._pen.setWidth(1)
        self._pen.setColor(QtGui.QColor.fromRgb(128, 128, 128, 64))

    def sizeHint(self, *args):
        size = QtWidgets.QStyledItemDelegate.sizeHint(self, *args)
        if size.height() <= self.MIN_ROW_HEIGHT:
            height = self.MIN_ROW_HEIGHT
        else:
            # Esnure cells with multiple lines of text still have some padding:
            height = size.height() + self.EXTRA_ROW_HEIGHT
        return QtCore.QSize(size.width() + self.EXTRA_COL_WIDTH, height)

    def paint(self, painter, option, index):
        if isinstance(self.parent(), QtWidgets.QTableView):
            # Disable rendering of selection highlight for TableViews, they handle
            # it themselves with the background colour data:
            option.state &= ~(QtWidgets.QStyle.State_Selected)
        QtWidgets.QStyledItemDelegate.paint(self, painter, option, index)
        if index.column() > 0:
            painter.setPen(self._pen)
            painter.drawLine(option.rect.topLeft(), option.rect.bottomLeft())

    def eventFilter(self, obj, event):
        """Filter events before they get to the editor, so that editing is ended when
        the user presses tab, shift-tab or enter (which otherwise would not end editing
        in a QTextEdit)."""
        if event.type() == QtCore.QEvent.KeyPress:
            if event.key() in [QtCore.Qt.Key_Enter, QtCore.Qt.Key_Return]:
                # Allow shift-enter
                if not event.modifiers() & QtCore.Qt.ShiftModifier:
                    self.commitData.emit(obj)
                    self.closeEditor.emit(obj)
                    return True
            elif event.key() == QtCore.Qt.Key_Tab:
                self.commitData.emit(obj)
                self.closeEditor.emit(obj, QtWidgets.QStyledItemDelegate.EditNextItem)
                return True
            elif event.key() == QtCore.Qt.Key_Backtab:
                self.commitData.emit(obj)
                self.closeEditor.emit(obj, QtWidgets.QStyledItemDelegate.EditPreviousItem)
                return True
        return QtWidgets.QStyledItemDelegate.eventFilter(self, obj, event)

    def createEditor(self, parent, option, index):
        return Editor(parent)

    def setEditorData(self, editor, index):
        editor.setPlainText(index.data())
        font = index.data(QtCore.Qt.FontRole)
        default_font = QtWidgets.QApplication.instance().font(self.parent())
        if font is None:
            font = default_font
        font.setPointSize(default_font.pointSize())
        editor.setFont(font)
        font_height = QtGui.QFontMetrics(font).height()
        padding = (self.MIN_ROW_HEIGHT - font_height) / 2 - 1
        editor.document().setDocumentMargin(padding)
        editor.selectAll()
        
    def setModelData(self, editor, model, index):
        model.setData(index, editor.toPlainText())


class CenteredCheckDelegate(ItemDelegate):
    """Center a checkbox and make its entire cell clickable."""

    def paint(self, painter, option, index):
        if not index.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable:
            return super().paint(painter, option, index)
        style = option.widget.style()
        background_option = QtWidgets.QStyleOptionViewItem(option)
        self.initStyleOption(background_option, index)
        background_option.features &= ~(
            QtWidgets.QStyleOptionViewItem.ViewItemFeature.HasCheckIndicator
        )
        background_option.checkState = QtCore.Qt.CheckState.Unchecked
        width = style.pixelMetric(QtWidgets.QStyle.PixelMetric.PM_IndicatorWidth)
        height = style.pixelMetric(QtWidgets.QStyle.PixelMetric.PM_IndicatorHeight)
        check = QtWidgets.QStyleOptionButton()
        check.rect = QtCore.QRect(
            option.rect.center().x() - width // 2,
            option.rect.center().y() - height // 2,
            width,
            height,
        )
        check.state = QtWidgets.QStyle.StateFlag.State_Enabled
        checked = QtCore.Qt.CheckState(index.data(QtCore.Qt.ItemDataRole.CheckStateRole))
        check.state |= (
            QtWidgets.QStyle.StateFlag.State_On
            if checked == QtCore.Qt.CheckState.Checked
            else QtWidgets.QStyle.StateFlag.State_Off
        )
        painter.save()
        style.drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            background_option,
            painter,
            option.widget,
        )
        style.drawPrimitive(QtWidgets.QStyle.PrimitiveElement.PE_IndicatorCheckBox, check, painter)
        if index.column() > 0:
            painter.setPen(self._pen)
            painter.drawLine(option.rect.topLeft(), option.rect.bottomLeft())
        painter.restore()

    def editorEvent(self, event, model, option, index):
        if not index.flags() & QtCore.Qt.ItemFlag.ItemIsUserCheckable:
            return False
        mouse_events = (
            QtCore.QEvent.Type.MouseButtonPress,
            QtCore.QEvent.Type.MouseButtonRelease,
            QtCore.QEvent.Type.MouseButtonDblClick,
        )
        if event.type() in mouse_events and event.button() == QtCore.Qt.MouseButton.LeftButton:
            if event.modifiers() != QtCore.Qt.KeyboardModifier.NoModifier:
                return False
            if not option.rect.contains(event.position().toPoint()):
                return False
            if event.type() == QtCore.QEvent.Type.MouseButtonRelease:
                checked = QtCore.Qt.CheckState(
                    index.data(QtCore.Qt.ItemDataRole.CheckStateRole)
                )
                state = (
                    QtCore.Qt.CheckState.Unchecked
                    if checked == QtCore.Qt.CheckState.Checked
                    else QtCore.Qt.CheckState.Checked
                )
                model.setData(index, state, QtCore.Qt.ItemDataRole.CheckStateRole)
            return True
        return super().editorEvent(event, model, option, index)
