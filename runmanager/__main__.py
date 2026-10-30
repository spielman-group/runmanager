#####################################################################
#                                                                   #
# __main__.py                                                       #
#                                                                   #
# Copyright 2013, Monash University                                 #
#                                                                   #
# This file is part of the program runmanager, in the labscript     #
# suite (see http://labscriptsuite.org), and is licensed under the  #
# Simplified BSD License. See the license.txt file in the root of   #
# the project for the full license.                                 #
#                                                                   #
#####################################################################
"""Runmanager GUI and supporting code
"""

import collections
import queue
import html
import logging
import os
import random
import sys
import labscript_utils.excepthook
labscript_utils.excepthook.install()

APPLICATION_NAME = 'runmanager'
RUNMANAGER_DIR = os.path.dirname(__file__)

# Associate app windows with OS menu shortcuts:
import desktop_app
desktop_app.set_process_appid(APPLICATION_NAME)

# Splash screen
from labscript_utils.splash import Splash
splash = Splash(
    os.path.join(RUNMANAGER_DIR, 'runmanager.svg'), application_name=APPLICATION_NAME
)
splash.show()

splash.update_text('importing standard library modules')
import time
import threading
import tempfile
import importlib.metadata
import importlib.resources

splash.update_text('importing matplotlib')
# Globals are evaluated in a thread with pylab imported, and importing pylab
# makes Qt calls, which a non-main thread can't make, so matplotlib's GUI
# integration is disabled:
import matplotlib
matplotlib.use('Agg')

from qtutils.qt import QtCore, QtGui, QtWidgets, QT_ENV

splash.update_text('importing labscript suite modules')
from labscript_utils.ls_zprocess import ProcessTree, ZMQServer
from labscript_utils.labconfig import (
    LabConfig,
    load_appconfig,
)
from labscript_utils.qtwidgets.application import (
    LabscriptGuiApplication,
    exec_qapplication,
    monospace_font,
    setup_application,
)
from labscript_utils.file_utils import next_available_indexed_filepath
from labscript_utils.lookup_format import unescape_braces
from labscript_utils.setup_logging import setup_logging
import labscript_utils.shared_drive as shared_drive
from labscript_utils.qtwidgets.link_indicator import LinkIndicator
from labscript_utils.qtwidgets.themed_window import FirstPaintWindow, ThemedWindow
from labscript_utils.text_editor import open_in_editor
from runmanager.widgets import (
    AlternatingColorModel,
    CenteredCheckDelegate,
    DummyRowModel,
    FingerTabWidget,
    GlobalsTreeView,
    ItemDelegate,
    ModelItem,
    TabToolButton,
    TreeView,
    engage_button_style_sheet,
    expression_cell_colors,
    hidden_unicode_issues,
    scroll_view_to_row_if_current,
    tooltip_with_hidden_unicode_warning,
)
from blacs.client import BlacsClient
from runviewer.client import RunviewerClient
from zprocess import port_in_use, raise_exception_in_thread, start_daemon
import runmanager
from runmanager.client import (
    BLACS_COMPLETED,
    DEFAULT_PORT,
    PROVIDER_NONE,
    PROVIDER_PAUSED,
    PROVIDER_PENDING,
    PROVIDER_SHOT,
    SHOT_OUTCOME_STATUSES,
    SUBMISSION_MODE_LAST_SEQUENCE,
    SUBMISSION_MODE_LAST_SEQUENCE_CLEAR_QUEUE,
    SUBMISSION_MODE_NEW_SEQUENCE,
    SUBMISSION_MODE_NEW_SEQUENCE_CLEAR_QUEUE,
    SequenceRefused,
)
from runmanager.analysis_submission import (
    AnalysisSubmission,
    set_icon_label_pixmap,
)
from runmanager.blacs_status import blacs_state
from runmanager.queueing import (
    COMPILE_MODE_EAGER,
    COMPILE_MODE_LAZY,
    EMPTY_QUEUE_DEFAULT_LABSCRIPT,
    EMPTY_QUEUE_NOTHING,
    QueueController,
    QueueManager,
    RunmanagerQueueWidget,
    shot_receipt,
)

from qtutils import (
    inmain,
    inmain_decorator,
    UiLoader,
    inthread,
    DisconnectContextManager,
    qtlock,
)
from labscript_utils.qtwidgets.outputbox import OutputBox
import labscript_utils.qtwidgets.file_dialogs as file_dialogs
from qtutils.icons import Icon


runmanager_dir = RUNMANAGER_DIR

# Bound here as well as under __main__, so that importing this module gives its
# methods a logger. setup_logging() returns this same object, with handlers.
logger = logging.getLogger(APPLICATION_NAME)

# The running window, which the tabs and RunmanagerServer's handlers reach through
# this name. Bound here so that code importing this module can set its own;
# running runmanager replaces it with the application it builds.
app = None

process_tree = ProcessTree.instance()

# Set a meaningful name for the zlock client's id:
process_tree.zlock_client.set_process_name(APPLICATION_NAME)

SubmissionMode = collections.namedtuple(
    'SubmissionMode', ['joins_sequence', 'clears_queue']
)

# See get_submission_anchor for which shot a joining batch is numbered after.
SUBMISSION_MODES = {
    SUBMISSION_MODE_NEW_SEQUENCE: SubmissionMode(
        joins_sequence=False, clears_queue=False
    ),
    SUBMISSION_MODE_LAST_SEQUENCE: SubmissionMode(
        joins_sequence=True, clears_queue=False
    ),
    SUBMISSION_MODE_NEW_SEQUENCE_CLEAR_QUEUE: SubmissionMode(
        joins_sequence=False, clears_queue=True
    ),
    SUBMISSION_MODE_LAST_SEQUENCE_CLEAR_QUEUE: SubmissionMode(
        joins_sequence=True, clears_queue=True
    ),
}




class ExpressionDelegate(ItemDelegate):
    def text_offset(self, option):
        return QtGui.QFontMetrics(option.font).horizontalAdvance('default') + 14

    def paint(self, painter, option, index):
        label = index.data(GroupTab.GLOBALS_ROLE_EXPRESSION)
        if label is None:
            return super().paint(painter, option, index)
        offset = self.text_offset(option)
        background_option = QtWidgets.QStyleOptionViewItem(option)
        background_option.text = ''
        background_option.icon = QtGui.QIcon()
        expression_option = QtWidgets.QStyleOptionViewItem(option)
        expression_option.rect = option.rect.adjusted(offset, 0, 0, 0)
        painter.save()
        painter.setClipRect(option.rect)
        option.widget.style().drawControl(
            QtWidgets.QStyle.ControlElement.CE_ItemViewItem,
            background_option,
            painter,
            option.widget,
        )
        QtWidgets.QStyledItemDelegate.paint(self, painter, expression_option, index)
        color_role = (
            QtGui.QPalette.ColorRole.HighlightedText
            if option.state & QtWidgets.QStyle.StateFlag.State_Selected
            else QtGui.QPalette.ColorRole.Text
        )
        label_palette = QtGui.QPalette(option.palette)
        label_color = QtGui.QColor(label_palette.color(color_role))
        label_color.setAlpha(180)
        label_palette.setColor(color_role, label_color)
        option.widget.style().drawItemText(
            painter,
            option.rect.adjusted(4, 0, -option.rect.width() + offset, 0),
            QtCore.Qt.AlignmentFlag.AlignLeft | QtCore.Qt.AlignmentFlag.AlignVCenter,
            label_palette,
            bool(option.state & QtWidgets.QStyle.StateFlag.State_Enabled),
            label,
            color_role,
        )
        painter.setPen(self._pen)
        painter.drawLine(option.rect.topLeft(), option.rect.bottomLeft())
        painter.restore()

    def sizeHint(self, option, index):
        size = super().sizeHint(option, index)
        return QtCore.QSize(size.width() + self.text_offset(option), size.height())

    def updateEditorGeometry(self, editor, option, index):
        expression_option = QtWidgets.QStyleOptionViewItem(option)
        expression_option.rect = option.rect.adjusted(self.text_offset(option), 0, 0, 0)
        super().updateEditorGeometry(editor, expression_option, index)

    def setEditorData(self, editor, index):
        if not editor.document().isModified():
            super().setEditorData(editor, index)
            editor.document().setModified(False)


class UnitsDelegate(ItemDelegate):
    """Show a Boolean global's units as Bool; the cell's text stays its stored units."""

    def initStyleOption(self, option, index):
        super().initStyleOption(option, index)
        if index.data(GroupTab.GLOBALS_ROLE_IS_BOOL):
            option.text = 'Bool'


class GroupTab:
    GLOBALS_COL_SCAN_ENABLED = 0
    GLOBALS_COL_JIT_ENABLED = 1
    GLOBALS_COL_NAME = 2
    GLOBALS_COL_VALUE = 3
    GLOBALS_COL_UNITS = 4
    GLOBALS_COL_EXPANSION = 5

    GLOBALS_ROLE_IS_DUMMY_ROW = QtCore.Qt.ItemDataRole.UserRole + 1
    GLOBALS_ROLE_SORT_DATA = QtCore.Qt.ItemDataRole.UserRole + 2
    GLOBALS_ROLE_PREVIOUS_TEXT = QtCore.Qt.ItemDataRole.UserRole + 3
    GLOBALS_ROLE_PREVIOUS_CHECKSTATE = QtCore.Qt.ItemDataRole.UserRole + 4
    GLOBALS_ROLE_IS_BOOL = QtCore.Qt.ItemDataRole.UserRole + 5
    GLOBALS_ROLE_EXPRESSION = QtCore.Qt.ItemDataRole.UserRole + 6

    GLOBALS_DUMMY_ROW_TEXT = '<Click to add global>'

    # The file field each column edits; the Value(s) column edits the
    # expression its item holds:
    GLOBALS_COLUMN_FIELDS = {
        GLOBALS_COL_SCAN_ENABLED: 'scan_enabled',
        GLOBALS_COL_JIT_ENABLED: 'jit_enabled',
        GLOBALS_COL_UNITS: 'units',
        GLOBALS_COL_EXPANSION: 'expansion',
    }
    GLOBALS_CHECKBOX_FIELDS = ('scan_enabled', 'jit_enabled')

    def __init__(self, tabWidget, globals_file, group_name):
        self.tabWidget = tabWidget

        loader = UiLoader()
        loader.registerCustomWidget(GlobalsTreeView)
        self.ui = loader.load(os.path.join(runmanager_dir, 'group.ui'))
        self.ui.gridLayout.setColumnStretch(1, 1)
        self.tabWidget.addTab(self.ui, group_name, closable=True)

        self.set_colors()
        # The globals of this group that are scanned over, for the tab's tooltip:
        self.scanned_descriptions = []
        self.set_file_and_group_name(globals_file, group_name)

        self.globals_model = AlternatingColorModel(
            view=self.ui.treeView_globals, dummy_row_role=self.GLOBALS_ROLE_IS_DUMMY_ROW
        )
        self.globals_model.setHorizontalHeaderLabels(
            ['Scan?', 'JIT?', 'Name', 'Value(s)', 'Units', 'Expansion']
        )
        self.globals_model.setSortRole(self.GLOBALS_ROLE_SORT_DATA)
        self.ui.treeView_globals.setModel(self.globals_model)
        self.ui.treeView_globals.setTreePosition(self.GLOBALS_COL_VALUE)
        self.ui.treeView_globals.setRootIsDecorated(True)
        self.ui.treeView_globals.setItemsExpandable(True)
        self.ui.treeView_globals.setSelectionBehavior(QtWidgets.QTreeView.SelectionBehavior.SelectRows)
        self.ui.treeView_globals.setSelectionMode(QtWidgets.QTreeView.SelectionMode.ExtendedSelection)
        self.ui.treeView_globals.setSortingEnabled(True)
        self.ui.treeView_globals.setEditTriggers(
            QtWidgets.QTreeView.EditTrigger.AnyKeyPressed | QtWidgets.QTreeView.EditTrigger.EditKeyPressed
        )
        self.ui.treeView_globals.header().setStretchLastSection(False)
        self.ui.treeView_globals.header().setSectionResizeMode(
            self.GLOBALS_COL_VALUE, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        self.value_delegate = ExpressionDelegate(self.ui.treeView_globals)
        self.ui.treeView_globals.setItemDelegateForColumn(
            self.GLOBALS_COL_VALUE, self.value_delegate
        )
        self.units_delegate = UnitsDelegate(self.ui.treeView_globals)
        self.ui.treeView_globals.setItemDelegateForColumn(
            self.GLOBALS_COL_UNITS, self.units_delegate
        )
        self.check_delegate = CenteredCheckDelegate(self.ui.treeView_globals)
        for column in (self.GLOBALS_COL_SCAN_ENABLED, self.GLOBALS_COL_JIT_ENABLED):
            self.ui.treeView_globals.setItemDelegateForColumn(column, self.check_delegate)
        self.ui.treeView_globals.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)
        self.action_globals_delete_selected = QtWidgets.QAction(
            Icon(':qtutils/fugue/minus'), 'Delete selected global(s)', self.ui
        )
        self.action_globals_set_selected_true = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box'), 'Set selected Booleans True', self.ui
        )
        self.action_globals_set_selected_false = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box-uncheck'),
            'Set selected Booleans False',
            self.ui,
        )
        self.connect_signals()

        self.populate_model()
        logger.info('Initial population of %s', self.group_name)
        for col in range(self.globals_model.columnCount()):
            if col != self.GLOBALS_COL_VALUE:
                self.ui.treeView_globals.resizeColumnToContents(col)
        if self.ui.treeView_globals.columnWidth(self.GLOBALS_COL_NAME) < 200:
            self.ui.treeView_globals.setColumnWidth(self.GLOBALS_COL_NAME, 200)
        if self.ui.treeView_globals.columnWidth(self.GLOBALS_COL_VALUE) < 220:
            self.ui.treeView_globals.setColumnWidth(self.GLOBALS_COL_VALUE, 220)
        if self.ui.treeView_globals.columnWidth(self.GLOBALS_COL_UNITS) < 100:
            self.ui.treeView_globals.setColumnWidth(self.GLOBALS_COL_UNITS, 100)
        if self.ui.treeView_globals.columnWidth(self.GLOBALS_COL_EXPANSION) < 100:
            self.ui.treeView_globals.setColumnWidth(self.GLOBALS_COL_EXPANSION, 100)

        self.tab_contains_errors = False

    def connect_signals(self):
        self.ui.treeView_globals.leftClicked.connect(self.on_treeView_globals_leftClicked)
        self.ui.treeView_globals.customContextMenuRequested.connect(
            self.on_treeView_globals_context_menu_requested
        )
        self.action_globals_set_selected_true.triggered.connect(
            lambda: self.on_globals_set_selected_bools_triggered('True')
        )
        self.action_globals_set_selected_false.triggered.connect(
            lambda: self.on_globals_set_selected_bools_triggered('False')
        )
        self.action_globals_delete_selected.triggered.connect(
            self.on_globals_delete_selected_triggered
        )
        self.globals_model.itemChanged.connect(self.on_globals_model_item_changed)
        self.globals_model_item_changed_disconnected = DisconnectContextManager(
            self.globals_model.itemChanged, self.on_globals_model_item_changed
        )

    def set_file_and_group_name(self, globals_file, group_name):
        self.globals_file = globals_file
        self.group_name = group_name
        self.ui.label_globals_file.setText(globals_file)
        self.ui.label_group_name.setText(group_name)
        index = self.tabWidget.indexOf(self.ui)
        self.tabWidget.setTabText(index, group_name)
        self.update_tab_tooltip()

    def update_tab_tooltip(self):
        tooltip = f'{self.group_name}\n({self.globals_file})'
        if self.scanned_descriptions:
            tooltip += '\n\nScanned over:\n' + '\n'.join(self.scanned_descriptions)
        index = self.tabWidget.indexOf(self.ui)
        if self.tabWidget.tabToolTip(index) != tooltip:
            self.tabWidget.setTabToolTip(index, tooltip)

    def set_tab_icon(self, icon_string):
        index = self.tabWidget.indexOf(self.ui)
        icon = Icon(icon_string) if icon_string is not None else QtGui.QIcon()
        tab_bar = self.tabWidget.tabBar()
        if tab_bar.tabStatusIcon(index).cacheKey() != icon.cacheKey():
            logger.debug('setting tab icon')
            tab_bar.setTabStatusIcon(index, icon)

    def populate_model(self):
        dummy_scan_enabled_item = ModelItem()
        dummy_scan_enabled_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_scan_enabled_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        dummy_scan_enabled_item.setToolTip('Click to add global')

        dummy_name_item = ModelItem(self.GLOBALS_DUMMY_ROW_TEXT)
        dummy_name_item.setFont(monospace_font())
        dummy_name_item.setToolTip('Click to add global')
        dummy_name_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_name_item.setData(self.GLOBALS_DUMMY_ROW_TEXT, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        dummy_name_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsEditable)

        dummy_jit_enabled_item = ModelItem()
        dummy_jit_enabled_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_jit_enabled_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        dummy_jit_enabled_item.setToolTip('Click to add global')

        dummy_default_item = ModelItem()
        dummy_default_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_default_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        dummy_default_item.setToolTip('Click to add global')

        dummy_units_item = ModelItem()
        dummy_units_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_units_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        dummy_units_item.setToolTip('Click to add global')

        dummy_expansion_item = ModelItem()
        dummy_expansion_item.setData(True, self.GLOBALS_ROLE_IS_DUMMY_ROW)
        dummy_expansion_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        dummy_expansion_item.setToolTip('Click to add global')

        self.globals_model.appendRow(
            [
                dummy_scan_enabled_item,
                dummy_jit_enabled_item,
                dummy_name_item,
                dummy_default_item,
                dummy_units_item,
                dummy_expansion_item,
            ]
        )
        self.add_global_rows(
            runmanager.globals_file.get_globals_details(
                {self.group_name: self.globals_file}
            )[self.group_name]
        )
        self.ui.treeView_globals.sortByColumn(self.GLOBALS_COL_NAME, QtCore.Qt.SortOrder.AscendingOrder)

    def add_global_rows(self, globals_details):
        """Show globals the file has, ``{name: record}``, above the dummy row."""
        for name, details in globals_details.items():
            row = self.make_global_row(
                name,
                default=details['default'],
                units=details['units'],
                scan_enabled=details['scan_enabled'],
                jit_enabled=details['jit_enabled'],
                scan=details['scan'],
                expansion=details['expansion'],
            )
            self.globals_model.insertRow(self.globals_model.rowCount() - 1, row)
            self.update_scan_controls(name)
            self._update_boolean_state(name)
            self.update_expansion_metadata(row[self.GLOBALS_COL_EXPANSION])

    def make_global_row(
        self,
        name,
        default='',
        units='',
        scan_enabled=False,
        jit_enabled=False,
        scan='',
        expansion='',
    ):
        logger.debug('%s:%s - make global row: %s ', self.globals_file, self.group_name, name)

        name_item = ModelItem(name)
        name_item.setData(name, self.GLOBALS_ROLE_SORT_DATA)
        name_item.setData(name, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        name_item.setToolTip(name)
        name_item.setFont(monospace_font())

        default_item = ModelItem(default)
        default_item.setData(default, self.GLOBALS_ROLE_SORT_DATA)
        default_item.setData(str(default), self.GLOBALS_ROLE_PREVIOUS_TEXT)
        default_item.setData('default', self.GLOBALS_ROLE_EXPRESSION)
        default_item.setFont(monospace_font())
        self.update_expression_item_metadata(default_item, 'Evaluating...')

        scan_enabled_item = ModelItem()
        scan_enabled_item.setCheckable(True)
        scan_enabled_item.setCheckState(
            QtCore.Qt.CheckState.Checked if scan_enabled else QtCore.Qt.CheckState.Unchecked
        )
        scan_enabled_item.setData(scan_enabled, self.GLOBALS_ROLE_PREVIOUS_CHECKSTATE)
        scan_enabled_item.setData(scan_enabled, self.GLOBALS_ROLE_SORT_DATA)
        scan_enabled_item.setEditable(False)
        scan_enabled_item.setToolTip('Use the Scan expression when generating sequences.')

        jit_enabled_item = ModelItem()
        jit_enabled_item.setCheckable(True)
        jit_enabled_item.setCheckState(
            QtCore.Qt.CheckState.Checked if jit_enabled else QtCore.Qt.CheckState.Unchecked
        )
        jit_enabled_item.setData(jit_enabled, self.GLOBALS_ROLE_PREVIOUS_CHECKSTATE)
        jit_enabled_item.setData(jit_enabled, self.GLOBALS_ROLE_SORT_DATA)
        jit_enabled_item.setEditable(False)
        jit_enabled_item.setToolTip('Re-evaluate this global at compile time.')

        scan_item = ModelItem(scan)
        scan_item.setData(scan, self.GLOBALS_ROLE_SORT_DATA)
        scan_item.setData(scan, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        scan_item.setData('scan', self.GLOBALS_ROLE_EXPRESSION)
        scan_item.setFont(monospace_font())
        self.update_expression_item_metadata(scan_item, 'Used when Scan? is checked.')

        units_item = ModelItem(units)
        units_item.setData(units, self.GLOBALS_ROLE_SORT_DATA)
        units_item.setData(units, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        units_item.setData(False, self.GLOBALS_ROLE_IS_BOOL)
        units_item.setToolTip('')

        expansion_item = ModelItem(expansion)
        expansion_item.setData(expansion, self.GLOBALS_ROLE_SORT_DATA)
        expansion_item.setData(expansion, self.GLOBALS_ROLE_PREVIOUS_TEXT)
        expansion_item.setToolTip('')

        active_item, inactive_item = (
            (scan_item, default_item) if scan_enabled else (default_item, scan_item)
        )
        child_row = [QtGui.QStandardItem() for _ in range(self.globals_model.columnCount())]
        for column, child_item in enumerate(child_row):
            if column == self.GLOBALS_COL_SCAN_ENABLED:
                child_item.setFlags(QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsSelectable)
            elif column != self.GLOBALS_COL_VALUE:
                child_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)
        child_row[self.GLOBALS_COL_VALUE] = inactive_item
        scan_enabled_item.appendRow(child_row)

        return [
            scan_enabled_item,
            jit_enabled_item,
            name_item,
            active_item,
            units_item,
            expansion_item,
        ]

    def on_treeView_globals_leftClicked(self, index):
        if qapplication.keyboardModifiers() != QtCore.Qt.KeyboardModifier.NoModifier:
            return
        item = self.globals_model.itemFromIndex(index)
        name_item = self.get_global_name_item(index)
        name_index = name_item.index()
        global_name = name_item.text()
        if item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
            self.ui.treeView_globals.setCurrentIndex(name_index)
            self.ui.treeView_globals.edit(name_index)
        elif item.column() == self.GLOBALS_COL_UNITS and item.data(self.GLOBALS_ROLE_IS_BOOL):
            active_item = self.get_active_value_item(global_name)
            active_item.setText('False' if item.checkState() == QtCore.Qt.CheckState.Checked else 'True')
        elif index.parent().isValid() and item.column() != self.GLOBALS_COL_VALUE:
            return
        elif item.column() not in (self.GLOBALS_COL_SCAN_ENABLED, self.GLOBALS_COL_JIT_ENABLED):
            if (
                self.ui.treeView_globals.currentIndex() != index
                or self.ui.treeView_globals.state() != QtWidgets.QTreeView.State.EditingState
            ):
                self.edit_cell(index)

    def edit_cell(self, index):
        self.ui.treeView_globals.setCurrentIndex(index)
        self.ui.treeView_globals.edit(index)

    def on_globals_model_item_changed(self, item):
        if item.column() == self.GLOBALS_COL_NAME:
            self.on_globals_model_name_changed(item)
            return
        if item.column() == self.GLOBALS_COL_VALUE:
            field = item.data(self.GLOBALS_ROLE_EXPRESSION)
        else:
            field = self.GLOBALS_COLUMN_FIELDS.get(item.column())
        if field is None:
            return
        if field in self.GLOBALS_CHECKBOX_FIELDS:
            new_value = item.checkState() == QtCore.Qt.CheckState.Checked
            previous_value = item.data(self.GLOBALS_ROLE_PREVIOUS_CHECKSTATE)
        else:
            new_value = item.text()
            previous_value = item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT)
        if new_value != previous_value:
            global_name = self.get_global_name_item(item.index()).text()
            self.change_global(global_name, field, previous_value, new_value)

    def on_globals_model_name_changed(self, item):
        item_text = item.text()
        if item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
            if item_text != self.GLOBALS_DUMMY_ROW_TEXT:
                self.new_global(item_text)
        else:
            new_global_name = item_text
            previous_global_name = item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT)
            if new_global_name != previous_global_name:
                self.rename_global(previous_global_name, new_global_name)

    def update_expansion_metadata(self, item):
        """Show in an Expansion cell's icon and tooltip how its global is expanded."""
        expansion = item.text()
        global_name = self.get_global_name_item(item.index()).text()
        with self.globals_model_item_changed_disconnected:
            scan_enabled_item = self.get_global_item_by_name(
                global_name, self.GLOBALS_COL_SCAN_ENABLED
            )
            scan_enabled = scan_enabled_item.checkState() == QtCore.Qt.CheckState.Checked
            if not scan_enabled:
                item.setData('', self.GLOBALS_ROLE_SORT_DATA)
                item.setToolTip('Enable Scan? to set an expansion mode.')
                item.setData(None, QtCore.Qt.ItemDataRole.DecorationRole)
            elif expansion == 'outer':
                item.setIcon(Icon(':qtutils/custom/outer'))
                item.setToolTip(
                    'This global will be interpreted as a list of values, and will be '
                    'outer producted with other lists to form a larger parameter space.'
                )
            elif expansion:
                item.setIcon(Icon(':qtutils/custom/zip'))
                item.setToolTip(
                    'This global will be interpreted as a list of values, and will be '
                    'iterated over in lock-step with other globals in the '
                    f"'{expansion}' zip group."
                )
            else:
                item.setData(None, QtCore.Qt.ItemDataRole.DecorationRole)
                item.setToolTip(
                    'This global will be interpreted as a single value and passed to '
                    'compilation as-is.'
                )

    def on_treeView_globals_context_menu_requested(self, point):
        menu = QtWidgets.QMenu(self.ui)
        menu.addAction(self.action_globals_set_selected_true)
        menu.addAction(self.action_globals_set_selected_false)
        menu.addAction(self.action_globals_delete_selected)
        menu.exec(QtGui.QCursor.pos())

    def selected_global_names(self):
        names = []
        for index in self.ui.treeView_globals.selectionModel().selectedRows():
            name_item = self.get_global_name_item(index)
            if (
                not name_item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW)
                and name_item.text() not in names
            ):
                names.append(name_item.text())
        return names

    def on_globals_delete_selected_triggered(self):
        names = self.selected_global_names()
        confirm_multiple = len(names) > 1
        if confirm_multiple and not app.question_dialog(
                f"Delete {len(names)} globals?"):
            return
        for name in names:
            self.delete_global(name, confirm=not confirm_multiple)

    def on_globals_set_selected_bools_triggered(self, state):
        for name in self.selected_global_names():
            self.get_active_value_item(name).setText(state)

    def close(self):
        self.tabWidget.removeTab(self.tabWidget.indexOf(self.ui))

    def get_global_name_item(self, index):
        """The Name item of the global whose row, or folded row, holds ``index``."""
        parent_index = index.parent() if index.parent().isValid() else index
        return self.globals_model.itemFromIndex(
            parent_index.siblingAtColumn(self.GLOBALS_COL_NAME)
        )

    def get_global_field_item(self, global_name, field):
        """The cell that shows one field of a global's file."""
        if field in ('default', 'scan'):
            return self.get_global_expression_item(global_name, field)
        columns = {name: column for column, name in self.GLOBALS_COLUMN_FIELDS.items()}
        return self.get_global_item_by_name(global_name, columns[field])

    def get_global_item_by_name(self, global_name, column, previous_name=None):
        possible_name_items = self.globals_model.findItems(global_name, column=self.GLOBALS_COL_NAME)
        if previous_name is not None:
            possible_name_items = [
                item
                for item in possible_name_items
                if item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT) == previous_name
            ]
        elif global_name != self.GLOBALS_DUMMY_ROW_TEXT:
            possible_name_items = [
                item
                for item in possible_name_items
                if not item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW)
            ]
        if len(possible_name_items) > 1:
            raise LookupError('Multiple items found')
        if not possible_name_items:
            raise LookupError('No item found')
        name_item = possible_name_items[0]
        return self.globals_model.itemFromIndex(
            name_item.index().sibling(name_item.index().row(), column)
        )

    def get_global_expression_item(self, global_name, expression, previous_name=None):
        name_item = self.get_global_item_by_name(
            global_name, self.GLOBALS_COL_NAME, previous_name=previous_name
        )
        parent_item = self.globals_model.item(name_item.row(), self.GLOBALS_COL_VALUE)
        if parent_item.data(self.GLOBALS_ROLE_EXPRESSION) == expression:
            return parent_item
        return self.globals_model.item(name_item.row(), self.GLOBALS_COL_SCAN_ENABLED).child(
            0, self.GLOBALS_COL_VALUE
        )

    def ensure_editable_globals_file(self):
        self.globals_file = app.ensure_editable_globals_file(self.globals_file, parent=self.ui)
        return self.globals_file

    def get_active_value_item(self, global_name):
        return self.get_global_item_by_name(global_name, self.GLOBALS_COL_VALUE)

    def _update_boolean_state(self, global_name):
        active_item = self.get_active_value_item(global_name)
        units_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_UNITS)
        value = active_item.text()
        if value == 'True':
            units_item.setData(True, self.GLOBALS_ROLE_IS_BOOL)
            units_item.setData('!1', self.GLOBALS_ROLE_SORT_DATA)
            units_item.setEditable(False)
            units_item.setCheckState(QtCore.Qt.CheckState.Checked)
            units_item.setBackground(QtGui.QBrush(QtGui.QColor(self.color_bool_on)))
        elif value == 'False':
            units_item.setData(True, self.GLOBALS_ROLE_IS_BOOL)
            units_item.setData('!0', self.GLOBALS_ROLE_SORT_DATA)
            units_item.setEditable(False)
            units_item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            units_item.setBackground(QtGui.QBrush(QtGui.QColor(self.color_bool_off)))
        else:
            units_item.setData(False, self.GLOBALS_ROLE_IS_BOOL)
            units_item.setData(units_item.text(), self.GLOBALS_ROLE_SORT_DATA)
            units_item.setEditable(True)
            units_item.setData(None, QtCore.Qt.ItemDataRole.CheckStateRole)
            units_item.setData(None, QtCore.Qt.ItemDataRole.BackgroundRole)

    def set_colors(self):
        colors = expression_cell_colors()
        (
            self.color_error,
            self.color_active,
            self.color_bool_on,
            self.color_bool_off,
            self.color_deprecated,
        ) = colors

    def redraw_colors(self):
        self.set_colors()
        for row in range(self.globals_model.rowCount()):
            name_item = self.globals_model.item(row, self.GLOBALS_COL_NAME)
            if not name_item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
                self._update_boolean_state(name_item.text())

    def update_scan_controls(self, global_name):
        scan_enabled_item = self.get_global_item_by_name(
            global_name, self.GLOBALS_COL_SCAN_ENABLED
        )
        jit_enabled_item = self.get_global_item_by_name(
            global_name, self.GLOBALS_COL_JIT_ENABLED
        )
        default_item = self.get_global_expression_item(global_name, 'default')
        scan_item = self.get_global_expression_item(global_name, 'scan')
        expansion_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_EXPANSION)
        scan_enabled = scan_enabled_item.checkState() == QtCore.Qt.CheckState.Checked
        jit_enabled = jit_enabled_item.checkState() == QtCore.Qt.CheckState.Checked
        active_expression = 'scan' if scan_enabled else 'default'
        if self.get_active_value_item(global_name).data(self.GLOBALS_ROLE_EXPRESSION) != active_expression:
            self.swap_value_items(global_name)
        default_tooltip = 'Used for standard shots and when Scan? is unchecked.'
        scan_tooltip = (
            'Expression used when Scan? is checked.'
            if scan_enabled
            else 'Editable, but used only when Scan? is checked.'
        )
        with self.globals_model_item_changed_disconnected:
            scan_enabled_item.setData(scan_enabled, self.GLOBALS_ROLE_PREVIOUS_CHECKSTATE)
            scan_enabled_item.setData(scan_enabled, self.GLOBALS_ROLE_SORT_DATA)
            jit_enabled_item.setData(jit_enabled, self.GLOBALS_ROLE_PREVIOUS_CHECKSTATE)
            jit_enabled_item.setData(jit_enabled, self.GLOBALS_ROLE_SORT_DATA)
            if scan_enabled:
                jit_enabled_item.setToolTip('Disable Scan? to enable JIT?.')
            else:
                jit_enabled_item.setToolTip('Re-evaluate this global at compile time.')
            scan_item.setEditable(True)
            if scan_enabled:
                expansion_item.setEditable(True)
                expansion_item.setToolTip(expansion_item.toolTip() or 'Evaluating...')
                expansion_item.setData(expansion_item.text(), self.GLOBALS_ROLE_SORT_DATA)
            else:
                expansion_item.setEditable(False)
                expansion_item.setText('')
                expansion_item.setData('', self.GLOBALS_ROLE_PREVIOUS_TEXT)
                expansion_item.setData('', self.GLOBALS_ROLE_SORT_DATA)
                expansion_item.setData(None, QtCore.Qt.ItemDataRole.DecorationRole)
                expansion_item.setToolTip('Enable Scan? to set an expansion mode.')
            self.update_expression_item_metadata(default_item, default_tooltip)
            self.update_expression_item_metadata(scan_item, scan_tooltip)
        active_item = scan_item if scan_enabled else default_item
        active_tooltip = scan_tooltip if scan_enabled else default_tooltip
        if not active_item.text().strip():
            self.update_expression_backgrounds(global_name, error=True)
            self.update_expression_item_metadata(
                active_item,
                active_tooltip + ' Expression is empty.',
                Icon(':qtutils/fugue/exclamation'),
            )
        else:
            self.update_expression_backgrounds(global_name)
        self.update_value_sort_data(global_name)

    def swap_value_items(self, global_name):
        view = self.ui.treeView_globals
        parent = self.get_global_item_by_name(global_name, self.GLOBALS_COL_SCAN_ENABLED)
        row = parent.row()
        current_item = self.globals_model.itemFromIndex(view.currentIndex())
        active = self.globals_model.item(row, self.GLOBALS_COL_VALUE)
        inactive = parent.child(0, self.GLOBALS_COL_VALUE)

        editor = view.indexWidget(view.currentIndex())
        if editor is not None and current_item in (active, inactive):
            self.value_delegate.commitData.emit(editor)
            self.value_delegate.closeEditor.emit(editor)
            parent = self.get_global_item_by_name(
                global_name, self.GLOBALS_COL_SCAN_ENABLED
            )
            row = parent.row()
        active = self.globals_model.takeItem(row, self.GLOBALS_COL_VALUE)
        inactive = parent.takeChild(0, self.GLOBALS_COL_VALUE)

        self.globals_model.setItem(row, self.GLOBALS_COL_VALUE, inactive)
        parent.setChild(0, self.GLOBALS_COL_VALUE, active)

        if current_item in (active, inactive) and not view.currentIndex().isValid():
            view.setCurrentIndex(current_item.index())
        if hidden_unicode_issues(active.text()):
            view.expand(parent.index())

    def update_value_sort_data(self, global_name):
        active_item = self.get_active_value_item(global_name)
        with self.globals_model_item_changed_disconnected:
            active_item.setData(active_item.text(), self.GLOBALS_ROLE_SORT_DATA)

    def update_expression_backgrounds(self, global_name, error=False, deprecated=False):
        default_item = self.get_global_expression_item(global_name, 'default')
        scan_item = self.get_global_expression_item(global_name, 'scan')
        active_item = self.get_active_value_item(global_name)
        inactive_item = default_item if active_item is scan_item else scan_item
        with self.globals_model_item_changed_disconnected:
            inactive_item.setData(None, QtCore.Qt.ItemDataRole.BackgroundRole)
            if error:
                color = self.color_error
            elif deprecated:
                color = self.color_deprecated
            else:
                color = self.color_active
            active_item.setBackground(QtGui.QBrush(QtGui.QColor(color)))

    def update_expression_item_metadata(self, item, base_tooltip, icon=None):
        tooltip = tooltip_with_hidden_unicode_warning(base_tooltip, item.text())
        if item.toolTip() != tooltip:
            item.setToolTip(tooltip)
        if icon is not None:
            item.setIcon(icon)
        elif hidden_unicode_issues(item.text()):
            item.setIcon(Icon(':qtutils/fugue/exclamation-red'))
        elif not item.icon().isNull():
            item.setData(None, QtCore.Qt.ItemDataRole.DecorationRole)
        if hidden_unicode_issues(item.text()) and item.index().parent().isValid():
            self.ui.treeView_globals.expand(item.index().parent())

    def do_model_sort(self):
        header = self.ui.treeView_globals.header()
        self.ui.treeView_globals.sortByColumn(
            header.sortIndicatorSection(), header.sortIndicatorOrder()
        )

    def new_global(self, global_name, interactive=True):
        logger.info('%s:%s - new global: %s', self.globals_file, self.group_name, global_name)
        # A GUI edit has typed the name into the dummy row, which is reset
        # below. A remote one has not, so it finds the dummy row as it stands.
        typed_name = global_name if interactive else self.GLOBALS_DUMMY_ROW_TEXT
        item = self.get_global_item_by_name(
            typed_name, self.GLOBALS_COL_NAME, previous_name=self.GLOBALS_DUMMY_ROW_TEXT
        )
        try:
            self.ensure_editable_globals_file()
            runmanager.new_global(self.globals_file, self.group_name, global_name)
        except Exception as e:
            if not interactive:
                raise
            app.error_dialog(str(e))
        else:
            global_row = self.make_global_row(global_name)
            self.globals_model.insertRow(self.globals_model.rowCount() - 1, global_row)
            self.update_scan_controls(global_name)
            self._update_boolean_state(global_name)
            self.do_model_sort()
            if interactive:
                default_item = self.get_global_expression_item(
                    global_name, 'default', previous_name=global_name
                )
                self.ui.treeView_globals.edit_after_commit(default_item.index())
            app.globals_changed()
        finally:
            item.setText(self.GLOBALS_DUMMY_ROW_TEXT)

    def rename_global(self, previous_global_name, new_global_name):
        logger.info(
            '%s:%s - rename global: %s -> %s',
            self.globals_file,
            self.group_name,
            previous_global_name,
            new_global_name,
        )
        item = self.get_global_item_by_name(
            new_global_name, self.GLOBALS_COL_NAME, previous_name=previous_global_name
        )
        try:
            self.ensure_editable_globals_file()
            runmanager.rename_global(
                self.globals_file, self.group_name, previous_global_name, new_global_name
            )
        except Exception as e:
            app.error_dialog(str(e))
            item.setText(previous_global_name)
        else:
            item.setData(new_global_name, self.GLOBALS_ROLE_PREVIOUS_TEXT)
            item.setData(new_global_name, self.GLOBALS_ROLE_SORT_DATA)
            self.do_model_sort()
            item.setToolTip(new_global_name)
            app.globals_changed()
            default_item = self.get_global_expression_item(new_global_name, 'default')
            if not default_item.text():
                if default_item.index().parent().isValid():
                    self.ui.treeView_globals.expand(default_item.index().parent())
                self.ui.treeView_globals.edit_after_commit(default_item.index())
            else:
                # If this changed the sort order, ensure the item is still visible:
                scroll_view_to_row_if_current(self.ui.treeView_globals, item)

    def show_global_field(self, item, field, value):
        """Set the cell of one field of a global to ``value``, as its file holds it."""
        with self.globals_model_item_changed_disconnected:
            if field in self.GLOBALS_CHECKBOX_FIELDS:
                item.setCheckState(QtCore.Qt.CheckState.Checked if value else QtCore.Qt.CheckState.Unchecked)
                item.setData(value, self.GLOBALS_ROLE_PREVIOUS_CHECKSTATE)
            else:
                item.setText(value)
                item.setData(value, self.GLOBALS_ROLE_PREVIOUS_TEXT)
            item.setData(value, self.GLOBALS_ROLE_SORT_DATA)

    def change_global(self, global_name, field, previous_value, new_value, interactive=True):
        """Write one field of a global to its file, and show it in the table.

        With ``interactive=False``, as a remote setter passes it, the write is made
        before returning, a failure is raised rather than shown in a dialog, and no
        cell is opened for editing."""
        logger.info(
            '%s:%s - change %s of %s: %s -> %s',
            self.globals_file,
            self.group_name,
            field,
            global_name,
            previous_value,
            new_value,
        )
        item = self.get_global_field_item(global_name, field)
        previous_background = item.background()
        previous_icon = item.icon()
        self.show_global_field(item, field, new_value)
        if field == 'default':
            item.setIcon(Icon(':qtutils/fugue/hourglass'))

        def write():
            try:
                self.ensure_editable_globals_file()
                runmanager.globals_file.set_field(
                    self.globals_file, self.group_name, global_name, field, new_value
                )
            except Exception as e:
                if interactive:
                    app.error_dialog(str(e))
                self.show_global_field(item, field, previous_value)
                with self.globals_model_item_changed_disconnected:
                    item.setData(previous_background, QtCore.Qt.ItemDataRole.BackgroundRole)
                    item.setIcon(previous_icon)
                if field in self.GLOBALS_CHECKBOX_FIELDS:
                    self.update_scan_controls(global_name)
                if not interactive:
                    raise
                return
            # The file holds Scan? and JIT? exclusive, and an expansion for a scan:
            if field == 'scan_enabled' and new_value:
                jit_item = self.get_global_field_item(global_name, 'jit_enabled')
                self.show_global_field(jit_item, 'jit_enabled', False)
                expansion_item = self.get_global_field_item(global_name, 'expansion')
                if not expansion_item.text():
                    expansion = runmanager.globals_file.get_field(
                        self.globals_file, self.group_name, global_name, 'expansion'
                    )
                    self.show_global_field(expansion_item, 'expansion', expansion)
            elif field == 'jit_enabled' and new_value:
                scan_enabled_item = self.get_global_field_item(global_name, 'scan_enabled')
                self.show_global_field(scan_enabled_item, 'scan_enabled', False)
            if field in self.GLOBALS_CHECKBOX_FIELDS:
                self.update_scan_controls(global_name)
            if field in ('scan_enabled', 'expansion'):
                self.update_expansion_metadata(
                    self.get_global_field_item(global_name, 'expansion')
                )
            if field in ('default', 'scan'):
                self.update_expression_item_metadata(item, 'Evaluating...')
            self.update_value_sort_data(global_name)
            self._update_boolean_state(global_name)
            self.do_model_sort()
            app.globals_changed()
            if not interactive:
                return
            # Go on to the cell a new global or a new scan needs filled in:
            if field == 'default':
                next_item = self.get_global_item_by_name(global_name, self.GLOBALS_COL_UNITS)
            elif field == 'scan_enabled' and new_value:
                next_item = self.get_active_value_item(global_name)
            else:
                next_item = None
            if next_item is None or not next_item.isEditable() or next_item.text():
                # If this changed the sort order, ensure the item is still visible:
                scroll_view_to_row_if_current(self.ui.treeView_globals, item)
            elif field == 'default':
                self.ui.treeView_globals.edit_after_commit(next_item.index())
            elif (
                self.ui.treeView_globals.state()
                != QtWidgets.QAbstractItemView.State.EditingState
            ):
                self.edit_cell(next_item.index())

        # The hourglass is drawn before a write the operator waits on:
        if field == 'default' and interactive:
            QtCore.QTimer.singleShot(1, write)
        else:
            write()

    def delete_global(self, global_name, confirm=True):
        logger.info('%s:%s - delete global: %s', self.globals_file, self.group_name, global_name)
        if confirm and not app.question_dialog(f"Delete the global '{global_name}'?"):
            return
        self.ensure_editable_globals_file()
        runmanager.globals_file.delete_global(self.globals_file, self.group_name, global_name)
        self.globals_model.removeRow(
            self.get_global_item_by_name(global_name, self.GLOBALS_COL_NAME).row()
        )
        app.globals_changed()

    def update_parse_indication(
        self, active_groups, sequence_globals, evaled_globals, deprecations, scanned_globals,
        dimensions
    ):
        if self.group_name in active_groups and active_groups[self.group_name] == self.globals_file:
            self.tab_contains_errors = False
            tab_contains_deprecations = False
            for i in range(self.globals_model.rowCount()):
                name_item = self.globals_model.item(i, self.GLOBALS_COL_NAME)
                if name_item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
                    continue
                scan_enabled_item = self.globals_model.item(i, self.GLOBALS_COL_SCAN_ENABLED)
                expansion_item = self.globals_model.item(i, self.GLOBALS_COL_EXPANSION)
                global_name = name_item.text()
                if global_name not in evaled_globals[self.group_name]:
                    # Created or renamed after this parse started; the parse that
                    # follows that change updates it.
                    continue
                default_item = self.get_global_expression_item(global_name, 'default')
                scan_item = self.get_global_expression_item(global_name, 'scan')
                value = evaled_globals[self.group_name][global_name]
                active_item = (
                    scan_item
                    if scan_enabled_item.checkState() == QtCore.Qt.CheckState.Checked
                    else default_item
                )
                inactive_item = default_item if active_item is scan_item else scan_item

                _, _, expansion = sequence_globals[self.group_name][global_name]
                with self.globals_model_item_changed_disconnected:
                    if expansion_item.data(self.GLOBALS_ROLE_PREVIOUS_TEXT) != expansion:
                        expansion_item.setData(expansion, self.GLOBALS_ROLE_PREVIOUS_TEXT)
                    if expansion_item.data(self.GLOBALS_ROLE_SORT_DATA) != expansion:
                        expansion_item.setData(expansion, self.GLOBALS_ROLE_SORT_DATA)
                expansion_item.setText(expansion)
                inactive_base_tooltip = (
                    'Used for standard shots and when Scan? is unchecked.'
                    if active_item is scan_item
                    else 'Used only when Scan? is checked.'
                )
                self.update_expression_item_metadata(inactive_item, inactive_base_tooltip)
                if isinstance(value, Exception):
                    self.update_expression_backgrounds(global_name, error=True)
                    self.update_expression_item_metadata(
                        active_item,
                        f'{value.__class__.__name__}: {value}',
                        Icon(':qtutils/fugue/exclamation'),
                    )
                    self.tab_contains_errors = True
                elif global_name in deprecations:
                    self.update_expression_backgrounds(global_name, deprecated=True)
                    # Rich text, so that the tooltip wraps:
                    self.update_expression_item_metadata(
                        active_item,
                        f'<p style="white-space:pre">{html.escape(repr(value))}</p>'
                        + f'<p>Warning: {html.escape(deprecations[global_name])}</p>'
                        + f'<p>{html.escape(runmanager.evaluator.PYLAB_NAMESPACE_NOTE)}</p>',
                        Icon(':qtutils/fugue/exclamation-diamond'),
                    )
                    tab_contains_deprecations = True
                else:
                    self.update_expression_backgrounds(global_name)
                    self.update_expression_item_metadata(active_item, repr(value))
                self._update_boolean_state(global_name)
            axes = {
                name: scanned_globals[name]
                for name in sequence_globals[self.group_name]
                if name in scanned_globals
            }
            self.scanned_descriptions = []
            for global_name, axis_name in sorted(axes.items()):
                expansion_type, zip_key = axis_name.split(' ', 1)
                n_values = dimensions[axis_name]
                values = '1 value' if n_values == 1 else f'{n_values} values'
                if expansion_type == 'outer':
                    self.scanned_descriptions.append(f'{global_name} (outer, {values})')
                else:
                    self.scanned_descriptions.append(
                        f"{global_name} (zip group '{zip_key}', {values})"
                    )
            self.update_tab_tooltip()
            if self.tab_contains_errors:
                self.set_tab_icon(':qtutils/fugue/exclamation')
            elif tab_contains_deprecations:
                self.set_tab_icon(':qtutils/fugue/exclamation-diamond')
            elif any(axis_name.startswith('outer ') for axis_name in axes.values()):
                self.set_tab_icon(':qtutils/custom/outer')
            elif axes:
                self.set_tab_icon(':qtutils/custom/zip')
            else:
                self.set_tab_icon(None)
        else:
            self.set_tab_icon(None)
            self.scanned_descriptions = []
            self.update_tab_tooltip()
            for row in range(self.globals_model.rowCount()):
                name_item = self.globals_model.item(row, self.GLOBALS_COL_NAME)
                if name_item.data(self.GLOBALS_ROLE_IS_DUMMY_ROW):
                    continue
                default_item = self.get_global_expression_item(name_item.text(), 'default')
                scan_item = self.get_global_expression_item(name_item.text(), 'scan')
                for item in (default_item, scan_item):
                    self.update_expression_item_metadata(item, 'Group inactive')
                self.update_expression_backgrounds(
                    self.globals_model.item(row, self.GLOBALS_COL_NAME).text()
                )


class RunmanagerMainWindow(ThemedWindow, FirstPaintWindow, QtWidgets.QMainWindow):

    def closeEvent(self, event):
        if app.on_close_event():
            return QtWidgets.QMainWindow.closeEvent(self, event)
        else:
            event.ignore()


class PoppedOutOutputBoxWindow(QtWidgets.QDialog):

    def closeEvent(self, event):
        app.on_output_popout_button_clicked()


class RunManager(LabscriptGuiApplication):

    app_name = 'runmanager'
    default_config_filename = 'runmanager.toml'
    config_description = 'which groups are active/open and other GUI state'

    # How long Restart subprocess waits for the compiler to exit before killing it,
    # in seconds. The compiler exits within batch_compiler.ABORT_TIMEOUT +
    # batch_compiler.KILL_LOCK_TIMEOUT = 65 s unless it has stopped answering:
    QUIT_TIMEOUT = 70

    # Constants for the model in the axes tab:
    AXES_COL_NAME = 0
    AXES_COL_LENGTH = 1
    AXES_COL_SHUFFLE = 2
    AXES_ROLE_NAME = QtCore.Qt.ItemDataRole.UserRole + 1

    # Constants for the model in the groups tab:
    GROUPS_COL_NAME = 0
    GROUPS_COL_ACTIVE = 1
    GROUPS_COL_DELETE = 2
    GROUPS_COL_OPENCLOSE = 3
    GROUPS_ROLE_IS_DUMMY_ROW = QtCore.Qt.ItemDataRole.UserRole + 1
    GROUPS_ROLE_PREVIOUS_NAME = QtCore.Qt.ItemDataRole.UserRole + 2
    GROUPS_ROLE_SORT_DATA = QtCore.Qt.ItemDataRole.UserRole + 3
    GROUPS_ROLE_GROUP_IS_OPEN = QtCore.Qt.ItemDataRole.UserRole + 4
    GROUPS_DUMMY_ROW_TEXT = '<Click to add group>'

    def __init__(self):
        logger.info('Python version: %s', sys.version)
        logger.info('Platform: %s', sys.platform)
        logger.info('Qt environment: %s', QT_ENV)
        logger.info('PySide/PyQt version: %s', importlib.metadata.version(QT_ENV))
        logger.info('Qt version: %s', QtCore.qVersion())
        logger.info("qtutils version: %s", importlib.metadata.version('qtutils'))
        logger.info("runmanager version: %s", runmanager.__version__)

        splash.update_text('loading graphical interface')
        loader = UiLoader()
        loader.registerCustomWidget(FingerTabWidget)
        loader.registerCustomWidget(TreeView)
        self.ui = loader.load(
            os.path.join(runmanager_dir, 'main.ui'), RunmanagerMainWindow()
        )
        self.ui.label_non_default_folder.setIcon(Icon(':/qtutils/fugue/exclamation-white'))
        self.init_config_window_title()

        self.output_box = OutputBox(self.ui.verticalLayout_output_tab)

        output_tab_index = self.ui.tabWidget.indexOf(self.ui.tab_output)
        self.output_popout_button = TabToolButton(self.ui.tabWidget.parent())
        self.output_popout_button.setIcon(Icon(':/qtutils/fugue/arrow-out'))
        self.output_popout_button.setToolTip('Toggle whether the output box is in a separate window')
        self.ui.tabWidget.tabBar().setTabButton(
            output_tab_index,
            QtWidgets.QTabBar.ButtonPosition.RightSide,
            self.output_popout_button,
        )
        # The output tab's tooltip when it shows no error:
        self.output_tab_tooltip = self.ui.tabWidget.tabToolTip(output_tab_index)
        # Fix the first four tabs in place:
        for index in range(4):
            self.ui.tabWidget.tabBar().setMovable(False, index=index)
        # Whether or not the output box is currently popped out:
        self.output_box_is_popped_out = False
        # The window it will be moved to when popped out:
        self.output_box_window = PoppedOutOutputBoxWindow(
            self.ui, QtCore.Qt.WindowType.WindowSystemMenuHint
        )
        self.output_box_window_verticalLayout = QtWidgets.QVBoxLayout(self.output_box_window)
        self.output_box_window_verticalLayout.setContentsMargins(0, 0, 0, 0)
        self.output_box_window.setWindowTitle('runmanager output')
        self.output_box_window.resize(800, 1000)
        self.setup_config()
        self.setup_axes_tab()
        self.setup_groups_tab()
        # The compiler subprocess and its lock must be in place before the
        # queue manager starts its worker thread, as that thread compiles
        # shots via self.compile_run_file():
        self.compiler_lock = threading.Lock()
        # Set by Empty queue and Restart subprocess, whose interrupted compile is no
        # error to flag on the output tab:
        self.compile_interrupted = threading.Event()
        # For shot files that never run: test compiles, and batches compiled with
        # BLACS unticked. Removed when runmanager exits, since Windows never clears
        # %TEMP%; a file runviewer still holds open on Windows is left behind.
        self.scratch_dir = tempfile.TemporaryDirectory(
            prefix='runmanager_scratch_', ignore_cleanup_errors=True
        )
        # Each sequence made or added to here, by (sequence_id, sequence_index), as
        # two started in the same second share an id: its newest shot's path,
        # attributes, next run number and name format. In memory, lost on restart.
        self.sequences = {}
        # A default shot is produced off the request thread; these track the one
        # being made and the one waiting to be handed over:
        self._default_shot_lock = threading.Lock()
        self._default_shot_preparing = False
        self._default_shot_ready = None
        # How many times the globals have changed, and how many there had been
        # when the default shot now being made or waiting was started:
        self.globals_changes = 0
        self._default_shot_globals_changes = 0

        splash.update_text('starting compiler subprocess')
        self.to_child, self.from_child, self.child = runmanager.start_compiler(
            self.output_box.port
        )
        logger.info('compiler subprocess started')
        # Set while the subprocess can be sent work. Restarting it clears this
        # until the new one is up, so no compile is sent to the one quitting.
        self.child_ready = threading.Event()
        self.child_ready.set()

        self.queue_controller = QueueController()
        self.queue_manager = QueueManager(
            controller=self.queue_controller,
            prepare_run_file=self.prepare_queue_shot,
            compile_run_file=self.compile_run_file,
            send_to_runviewer=self.send_to_runviewer,
            output=self.output_box.output,
        )
        self.setup_queue_tab()
        self.analysis_submission = AnalysisSubmission(self.ui, self.queue_controller.record_lyse)
        # The three destination checkboxes wear their applications' logos, which
        # tell them apart faster than their names do.
        set_icon_label_pixmap(
            self.ui.checkBox_run_shots_icon, importlib.resources.files('blacs') / 'blacs.svg'
        )
        set_icon_label_pixmap(
            self.ui.checkBox_view_shots_icon,
            importlib.resources.files('runviewer') / 'runviewer.svg',
        )
        blacs = BlacsClient()
        self.blacs_link = LinkIndicator(
            'BLACS',
            blacs.host,
            blacs.port,
            command='get_status',
            on_answer=self.update_blacs_status,
        )
        self.ui.blacs_link_layout.addWidget(self.blacs_link)
        self.blacs_link.start()
        runviewer = RunviewerClient(host='localhost')
        self.runviewer_link = LinkIndicator('runviewer', runviewer.host, runviewer.port)
        self.ui.runviewer_link_layout.addWidget(self.runviewer_link)
        self.runviewer_link.start()
        self.connect_signals()
        logger.info('UI loaded')

        # The last location from which a labscript file was selected, defaults
        # to labscriptlib:
        self.last_opened_labscript_folder = self.exp_config.get('paths', 'labscriptlib')
        # The last location from which a globals file was selected, defaults
        # to experiment_shot_storage:
        self.last_opened_globals_folder = self.exp_config.get('paths', 'experiment_shot_storage')
        # The last file to which the user saved or loaded a configuration:
        self.last_save_config_file = None
        # The last manually selected shot output folder, defaults to
        # experiment_shot_storage:
        self.last_selected_shot_output_folder = self.exp_config.get('paths', 'experiment_shot_storage')
        self.experiment_shot_storage = self.exp_config.get('paths', 'experiment_shot_storage')
        # Store the currently open groups as {(globals_filename, group_name): GroupTab}
        self.currently_open_groups = {}

        # A thread that will evaluate globals when they change, allowing us to
        # show their values and any errors in the tabs they came from.
        self.preparse_globals_thread = threading.Thread(
            target=self.preparse_globals_loop, daemon=True
        )
        # Tells the preparser thread that globals have changed. A queue rather than
        # an Event, so that callers can Queue.join() to wait for parsing race-free.
        self.preparse_globals_required = queue.Queue()
        self.preparse_globals_thread.start()

        # A few attributes for self.guess_expansion_modes() to keep track of
        # its state, and thus detect changes:
        self.previous_evaled_globals = {}
        self.previous_global_hierarchy = {}
        self.previous_expansion_types = {}
        self.previous_expansions = {}

        # The prospective number of shots resulting from compilation
        self.n_shots = None

        # Is blank until a labscript file is selected:
        self.previous_default_output_folder = ''

        # Start a thread to monitor the time of day and create new shot output
        # folders for each day:
        inthread(self.rollover_shot_output_folder)
        self.non_default_folder = None

        # The data from the last time we saved the configuration, so we can
        # know if something's changed:
        self.last_save_data = None

        # autoload a config file, if labconfig is set to do so:
        active_config_file = self.get_default_config_file()
        try:
            autoload_config_file = self.exp_config.get('runmanager', 'autoload_config_file')
        except (LabConfig.NoOptionError, LabConfig.NoSectionError):
            self.output_box.output('Ready.\n\n')
        else:
            active_config_file = autoload_config_file
            self.ui.setEnabled(False)
            self.output_box.output(f'Loading default config file {autoload_config_file}...')

            def load_the_config_file():
                try:
                    self.load_configuration(autoload_config_file, at_startup=True)
                    self.output_box.output('done.\n')
                except Exception as e: # noqa: BLE001 send all errors to output box
                    self.output_box.output(f'\nCould not load config file: {e.__class__.__name__}: {e!s}\n\n', red=True)
                else:
                    self.output_box.output('Ready.\n\n')
                finally:
                    self.ui.setEnabled(True)
            # Defer this until 50ms after the window has shown,
            # so that the GUI pops up faster in the meantime
            self.ui.firstPaint.connect(lambda: QtCore.QTimer.singleShot(50, load_the_config_file))

        self.set_config_window_title(active_config_file)
        splash.update_text('done')
        self.ui.show()

    def setup_config(self):
        required_config_params = {"default": ["apparatus_name"],
                                  "ports": ['runviewer', 'lyse'],
                                  "paths": ["shared_drive",
                                            "experiment_shot_storage",
                                            "labscriptlib",
                                            ],
                                  }
        self.exp_config = LabConfig(required_params = required_config_params)
        logger.info('LabConfig loaded')

    def setup_axes_tab(self):
        self.axes_model = QtGui.QStandardItemModel()

        name_header_item = QtGui.QStandardItem('Name')
        name_header_item.setToolTip('The name of the global or zip group being iterated over')
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_NAME, name_header_item)

        length_header_item = QtGui.QStandardItem('Length')
        length_header_item.setToolTip('The number of elements in the axis of the parameter space')
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_LENGTH, length_header_item)

        shuffle_header_item = QtGui.QStandardItem('Shuffle')
        shuffle_header_item.setToolTip('Whether or not the order of the axis should be randomised')
        shuffle_header_item.setIcon(Icon(':qtutils/fugue/arrow-switch'))
        self.axes_model.setHorizontalHeaderItem(self.AXES_COL_SHUFFLE, shuffle_header_item)

        self.ui.treeView_axes.setModel(self.axes_model)
                                                          
        self.ui.treeView_axes.header().setStretchLastSection(False)
        self.ui.treeView_axes.header().setSectionResizeMode(
            self.AXES_COL_NAME, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
                                                          
    def setup_groups_tab(self):
        self.groups_model = DummyRowModel(dummy_row_role=self.GROUPS_ROLE_IS_DUMMY_ROW)
        self.groups_model.setHorizontalHeaderLabels(['File/group name', 'Active', 'Delete', 'Open/Close'])
        self.groups_model.setSortRole(self.GROUPS_ROLE_SORT_DATA)
        self.ui.treeView_groups.setModel(self.groups_model)
        self.ui.treeView_groups.setAnimated(True)  # Pretty
        self.ui.treeView_groups.setSelectionMode(QtWidgets.QTreeView.SelectionMode.ExtendedSelection)
        self.ui.treeView_groups.setSortingEnabled(True)
        self.ui.treeView_groups.set_unsortable_columns([self.GROUPS_COL_DELETE])
        self.ui.treeView_groups.sortByColumn(self.GROUPS_COL_NAME, QtCore.Qt.SortOrder.AscendingOrder)
        self.ui.treeView_groups.setColumnWidth(self.GROUPS_COL_NAME, 400)
        # Make it so the user can just start typing on an item to edit:
        self.ui.treeView_groups.setEditTriggers(QtWidgets.QTreeView.EditTrigger.AnyKeyPressed |
                                                QtWidgets.QTreeView.EditTrigger.EditKeyPressed |
                                                QtWidgets.QTreeView.EditTrigger.SelectedClicked)
        # Ensure the clickable region of the open/close button doesn't extend forever:
        self.ui.treeView_groups.header().setStretchLastSection(False)
        self.ui.treeView_groups.header().setSectionResizeMode(
            self.GROUPS_COL_NAME, QtWidgets.QHeaderView.ResizeMode.Stretch
        )
        for column in range(self.groups_model.columnCount()):
            if column != self.GROUPS_COL_NAME:
                self.ui.treeView_groups.resizeColumnToContents(column)

        self.ui.treeView_groups.setTextElideMode(QtCore.Qt.TextElideMode.ElideMiddle)
        self.ui.treeView_groups.setContextMenuPolicy(QtCore.Qt.ContextMenuPolicy.CustomContextMenu)

        self.action_groups_set_selection_active = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box'), 'Set selected group(s) active', self.ui)
        self.action_groups_set_selection_inactive = QtWidgets.QAction(
            Icon(':qtutils/fugue/ui-check-box-uncheck'), 'Set selected group(s) inactive', self.ui)
        self.action_groups_delete_selected = QtWidgets.QAction(
            Icon(':qtutils/fugue/minus'), 'Delete selected group(s)', self.ui)
        self.action_groups_open_selected = QtWidgets.QAction(
            Icon(':/qtutils/fugue/plus'), 'Open selected group(s)', self.ui)
        self.action_groups_close_selected_groups = QtWidgets.QAction(
            Icon(':/qtutils/fugue/cross'), 'Close selected group(s)', self.ui)
        self.action_groups_close_selected_files = QtWidgets.QAction(
            Icon(':/qtutils/fugue/cross'), 'Close selected file(s)', self.ui)

        # The recursion depth of on_groups_model_active_changed(), so that it acts
        # on a change made from outside and not on the changes it makes itself:
        self.on_groups_model_active_changed_recursion_depth = 0

    def setup_queue_tab(self):
        self.tab_queue = self.ui.tab_queue
        self.verticalLayout_queue_tab = self.ui.verticalLayout_queue_tab
        self.queue_compile_mode_combo = self.ui.queue_compile_mode_combo
        self.queue_compile_mode_combo.addItem('Eager compile', COMPILE_MODE_EAGER)
        self.queue_compile_mode_combo.addItem('Lazy compile', COMPILE_MODE_LAZY)
        self.queue_empty_policy_combo = self.ui.queue_empty_policy_combo
        self.queue_empty_policy_combo.addItem('Send nothing', EMPTY_QUEUE_NOTHING)
        self.queue_empty_policy_combo.addItem(
            'Send default shot', EMPTY_QUEUE_DEFAULT_LABSCRIPT
        )
        self.queue_pause_button = self.ui.queue_pause_button
        pause_icon = QtGui.QIcon()
        pause_icon.addPixmap(
            QtGui.QPixmap(':/qtutils/fugue/control.png'),
            QtGui.QIcon.Mode.Normal,
            QtGui.QIcon.State.Off,
        )
        pause_icon.addPixmap(
            QtGui.QPixmap(':/qtutils/fugue/control-pause.png'),
            QtGui.QIcon.Mode.Normal,
            QtGui.QIcon.State.On,
        )
        self.queue_pause_button.setIcon(pause_icon)
        self.queue_widget = RunmanagerQueueWidget(self.tab_queue)
        self.queue_widget.setSizePolicy(
            QtWidgets.QSizePolicy.Policy.Expanding, QtWidgets.QSizePolicy.Policy.Expanding
        )
        self.verticalLayout_queue_tab.addWidget(self.queue_widget)
        self.ui.tabWidget.setCurrentWidget(self.tab_queue)
        self.refresh_queue_tab()

    def connect_signals(self):
        self.output_popout_button.clicked.connect(self.on_output_popout_button_clicked)

        # The OS switching between light and dark:
        self.ui.themeChanged.connect(self.on_theme_changed)
        self.set_engage_button_style()

        # The menu items:
        self.ui.actionLoad_configuration.triggered.connect(self.on_load_configuration_triggered)
        self.ui.actionRevert_configuration.triggered.connect(self.on_revert_configuration_triggered)
        self.ui.actionSave_configuration.triggered.connect(self.on_save_configuration_triggered)
        self.ui.actionSave_configuration_as.triggered.connect(self.on_save_configuration_as_triggered)
        self.ui.actionQuit.triggered.connect(self.ui.close)

        # labscript file and folder selection stuff:
        self.ui.toolButton_select_labscript_file.clicked.connect(self.on_select_labscript_file_clicked)
        self.ui.toolButton_select_default_labscript_file.clicked.connect(
            self.on_select_default_labscript_file_clicked
        )
        self.ui.toolButton_select_shot_output_folder.clicked.connect(self.on_select_shot_output_folder_clicked)
        self.ui.toolButton_edit_labscript_file.clicked.connect(self.on_edit_labscript_file_clicked)
        self.ui.toolButton_edit_default_labscript_file.clicked.connect(
            self.on_edit_default_labscript_file_clicked
        )
        self.ui.toolButton_reset_shot_output_folder.clicked.connect(self.on_reset_shot_output_folder_clicked)
        self.ui.lineEdit_labscript_file.textChanged.connect(self.on_labscript_file_text_changed)
        self.ui.lineEdit_default_labscript_file.textChanged.connect(
            self.on_default_labscript_file_text_changed
        )
        self.ui.lineEdit_shot_output_folder.textChanged.connect(self.on_shot_output_folder_text_changed)

        # Control buttons; engage, empty queue, restart subprocess:
        self.setup_engage_submission_menu()
        self.ui.pushButton_engage.clicked.connect(self.on_engage_clicked)
        self.ui.pushButton_abort.clicked.connect(self.on_abort_clicked)
        self.ui.pushButton_restart_subprocess.clicked.connect(self.on_restart_subprocess_clicked)
        
        # shuffle master control
        self.ui.pushButton_shuffle.stateChanged.connect(self.on_master_shuffle_clicked)

        # Tab closebutton clicked:
        self.ui.tabWidget.tabCloseRequested.connect(self.on_tabCloseRequested)

        # Axes tab; reordering
        axes = self.ui.treeView_axes
        self.ui.toolButton_axis_to_top.clicked.connect(lambda: axes.move_selected_rows('top'))
        self.ui.toolButton_axis_up.clicked.connect(lambda: axes.move_selected_rows('up'))
        self.ui.toolButton_axis_down.clicked.connect(lambda: axes.move_selected_rows('down'))
        self.ui.toolButton_axis_to_bottom.clicked.connect(
            lambda: axes.move_selected_rows('bottom')
        )
        axes.set_selection_controls([
            self.ui.toolButton_axis_to_top,
            self.ui.toolButton_axis_up,
            self.ui.toolButton_axis_down,
            self.ui.toolButton_axis_to_bottom,
        ])
        axes.rowsReordered.connect(lambda order: self.update_axes_indentation())
        # axes tab item changed handler
        self.axes_model.itemChanged.connect(self.on_axes_item_changed)
        self.axes_model.rowsRemoved.connect(self.update_global_shuffle_state)
        self.axes_model.rowsInserted.connect(self.update_global_shuffle_state)

        # Groups tab; right click menu, menu actions, open globals file, new globals file, diff globals file,
        self.ui.treeView_groups.customContextMenuRequested.connect(self.on_treeView_groups_context_menu_requested)
        self.action_groups_set_selection_active.triggered.connect(
            lambda: self.on_groups_set_selection_active_triggered(QtCore.Qt.CheckState.Checked))
        self.action_groups_set_selection_inactive.triggered.connect(
            lambda: self.on_groups_set_selection_active_triggered(QtCore.Qt.CheckState.Unchecked))
        self.action_groups_delete_selected.triggered.connect(self.on_groups_delete_selected_triggered)
        self.action_groups_open_selected.triggered.connect(self.on_groups_open_selected_triggered)
        self.action_groups_close_selected_groups.triggered.connect(self.on_groups_close_selected_groups_triggered)
        self.action_groups_close_selected_files.triggered.connect(self.on_groups_close_selected_files_triggered)

        self.ui.pushButton_open_globals_file.clicked.connect(self.on_open_globals_file_clicked)
        self.ui.pushButton_new_globals_file.clicked.connect(self.on_new_globals_file_clicked)
        self.ui.pushButton_diff_globals_file.clicked.connect(self.on_diff_globals_file_clicked)
        self.ui.treeView_groups.leftClicked.connect(self.on_treeView_groups_leftClicked)
        self.ui.treeView_groups.doubleLeftClicked.connect(self.on_treeView_groups_doubleLeftClicked)
        self.groups_model.itemChanged.connect(self.on_groups_model_item_changed)
        # A context manager with which we can temporarily disconnect the above connection.
        self.groups_model_item_changed_disconnected = DisconnectContextManager(
            self.groups_model.itemChanged, self.on_groups_model_item_changed)

        self.queue_manager.queueChanged.connect(self.refresh_queue_tab)
        self.queue_compile_mode_combo.currentIndexChanged.connect(
            self.on_queue_compile_mode_changed
        )
        self.queue_empty_policy_combo.currentIndexChanged.connect(
            self.on_queue_empty_policy_changed
        )
        self.queue_pause_button.toggled.connect(self.queue_manager.set_paused)
        self.queue_widget.deleteRowsRequested.connect(self.queue_manager.delete_rows)
        
        # Keyboard shortcuts:
        engage_shortcut = QtGui.QShortcut('F5', self.ui,
            lambda: self.ui.pushButton_engage.clicked.emit(False))
        engage_shortcut.setAutoRepeat(False)
        QtGui.QShortcut('ctrl+W', self.ui, self.close_current_tab)
        QtGui.QShortcut('ctrl+Tab', self.ui, lambda: self.switch_tabs(+1))
        QtGui.QShortcut('ctrl+shift+Tab', self.ui, lambda: self.switch_tabs(-1))
        logger.info('Signals connected')

    def set_engage_button_style(self):
        self.ui.pushButton_engage.setStyleSheet(
            engage_button_style_sheet(QtWidgets.QApplication.palette(self.ui.pushButton_engage))
        )

    def on_theme_changed(self):
        self.set_engage_button_style()
        for tab in self.currently_open_groups.values():
            tab.redraw_colors()
        # Parsing recolours the expression cells. Not globals_changed(), which
        # would discard a prepared default shot:
        self.preparse_globals_required.put(None)

    def on_close_event(self):
        if not self.offer_to_save_changes('Quit runmanager', quitting=True):
            return False
        # Prepared for BLACS and never handed over, it would stay a shot no one ran:
        self.discard_default_shot()
        self.analysis_submission.shutdown()
        self.blacs_link.shutdown()
        self.runviewer_link.shutdown()
        self.queue_manager.shutdown()
        self.to_child.put(['quit', None])
        self.output_box.shutdown()
        return True

    def close_current_tab(self):
        current_tab_widget = self.ui.tabWidget.currentWidget()
        for (globals_file, group_name), tab in self.currently_open_groups.items():
            if tab.ui is current_tab_widget:
                self.close_group(globals_file, group_name)
                break

    def switch_tabs(self, change):
        current_index = self.ui.tabWidget.currentIndex()
        n_tabs = self.ui.tabWidget.count()
        new_index = (current_index + change) % n_tabs
        self.ui.tabWidget.setCurrentIndex(new_index)

    def on_queue_empty_policy_changed(self, index):
        empty_queue_policy = self.queue_empty_policy_combo.itemData(index)
        self.queue_manager.set_empty_queue_policy(empty_queue_policy)

    def on_queue_compile_mode_changed(self, index):
        compile_mode = self.queue_compile_mode_combo.itemData(index)
        self.queue_manager.set_compile_mode(compile_mode)

    def update_blacs_status(self, reachable, answer):
        if reachable:
            self.blacs_link.show_state(*blacs_state(answer))

    @inmain_decorator()
    def refresh_queue_tab(self):
        # The widget reserves its first row for the shot that went to BLACS and
        # lays the waiting work out beneath it, so there is nothing to divide
        # up here.
        self.queue_widget.set_display_items(
            self.queue_controller.get_queue_display_items()
        )

    @inmain_decorator()  # Can be called from a compiling thread
    def set_output_tab_error(self, message):
        """Show an error icon on the output tab, with the message as its tooltip. If
        message is None, remove the error icon and show the output tab's usual
        tooltip."""
        index = self.ui.tabWidget.indexOf(self.ui.tab_output)
        tab_bar = self.ui.tabWidget.tabBar()
        if message is None:
            tab_bar.setTabStatusIcon(index, None)
            self.ui.tabWidget.setTabToolTip(index, self.output_tab_tooltip)
        else:
            tab_bar.setTabStatusIcon(index, Icon(':qtutils/fugue/exclamation'))
            self.ui.tabWidget.setTabToolTip(
                index, f'<p style="white-space:pre-wrap">{html.escape(message)}</p>'
            )

    def on_output_popout_button_clicked(self):
        if self.output_box_is_popped_out:
            self.ui.verticalLayout_output_tab.addWidget(self.output_box.output_textedit)
            self.output_box_window.hide()
            self.output_popout_button.setIcon(Icon(':/qtutils/fugue/arrow-out'))
        else:
            self.output_box_window_verticalLayout.addWidget(self.output_box.output_textedit)
            self.output_popout_button.setIcon(Icon(':/qtutils/fugue/arrow-in'))
            self.output_box_window.show()
        self.output_box_is_popped_out = not self.output_box_is_popped_out

    def select_labscript_file(self, title):
        labscript_file = file_dialogs.open_file(
            self.ui, title, self.last_opened_labscript_folder, "Python files (*.py)"
        )
        if not labscript_file:
            return None
        if not os.path.isfile(labscript_file):
            self.error_dialog(f"No such file {labscript_file}.")
            return None
        self.last_opened_labscript_folder = os.path.dirname(labscript_file)
        return labscript_file

    def on_select_labscript_file_clicked(self, checked):
        labscript_file = self.select_labscript_file('Select labscript file')
        if not labscript_file:
            return
        self.ui.lineEdit_labscript_file.setText(labscript_file)

    def on_select_default_labscript_file_clicked(self, checked):
        labscript_file = self.select_labscript_file('Select default-shot labscript file')
        if not labscript_file:
            return
        self.ui.lineEdit_default_labscript_file.setText(labscript_file)

    def on_edit_labscript_file_clicked(self, checked):
        open_in_editor(self.ui.lineEdit_labscript_file.text(), parent=self.ui)

    def on_edit_default_labscript_file_clicked(self, checked):
        open_in_editor(self.ui.lineEdit_default_labscript_file.text(), parent=self.ui)

    def on_select_shot_output_folder_clicked(self, checked):
        shot_output_folder = file_dialogs.choose_folder(
            self.ui, 'Select shot output folder', self.last_selected_shot_output_folder
        )
        if not shot_output_folder:
            # User cancelled selection
            return
        # Save the containing folder for use next time we open the dialog box:
        self.last_selected_shot_output_folder = os.path.dirname(shot_output_folder)
        self.ui.lineEdit_shot_output_folder.setText(shot_output_folder)
        # Update our knowledge about whether this is the default output folder or not:
        self.check_output_folder_update()

    def on_reset_shot_output_folder_clicked(self, checked):
        current_default_output_folder = self.get_default_output_folder()
        if not current_default_output_folder:
            return
        self.ui.lineEdit_shot_output_folder.setText(current_default_output_folder)
        self.check_output_folder_update()

    def on_labscript_file_text_changed(self, text):
        enabled = bool(text)
        self.ui.toolButton_edit_labscript_file.setEnabled(enabled)
        self.ui.toolButton_select_shot_output_folder.setEnabled(enabled)
        self.ui.lineEdit_labscript_file.setToolTip(text)
        self.check_output_folder_update()

    def on_default_labscript_file_text_changed(self, text):
        self.ui.toolButton_edit_default_labscript_file.setEnabled(bool(text))
        self.ui.lineEdit_default_labscript_file.setToolTip(text)
        self.queue_manager.set_default_labscript_file(text)

    def on_shot_output_folder_text_changed(self, text):
        if text == self.get_default_output_folder():
            self.non_default_folder = False
        else:
            self.non_default_folder = True
        self.ui.toolButton_reset_shot_output_folder.setEnabled(self.non_default_folder)
        self.ui.label_non_default_folder.setVisible(self.non_default_folder)
        self.ui.lineEdit_shot_output_folder.setToolTip(text)

    def setup_engage_submission_menu(self):
        button = self.ui.pushButton_engage
        button.setPopupMode(QtWidgets.QToolButton.ToolButtonPopupMode.DelayedPopup)
        self.engage_submission_menu = QtWidgets.QMenu(button)
        self.engage_new_sequence_action = self.engage_submission_menu.addAction(
            'Add shots to new sequence'
        )
        self.engage_new_sequence_action.triggered.connect(
            lambda: self.on_engage_clicked(submission_mode=SUBMISSION_MODE_NEW_SEQUENCE)
        )
        self.engage_new_sequence_action.setToolTip(
            'Submit the new batch as a new shot sequence.'
        )
        self.engage_new_sequence_action.setStatusTip(
            'Submit the new batch as a new shot sequence.'
        )
        self.engage_last_sequence_action = self.engage_submission_menu.addAction(
            'Add shots to last sequence'
        )
        self.engage_last_sequence_action.triggered.connect(
            lambda: self.on_engage_clicked(submission_mode=SUBMISSION_MODE_LAST_SEQUENCE)
        )
        self.engage_last_sequence_action.setToolTip(
            'Submit the new batch onto the same shot sequence as the queued shots.'
        )
        self.engage_last_sequence_action.setStatusTip(
            'Submit the new batch onto the same shot sequence as the queued shots.'
        )
        self.engage_new_sequence_clear_queue_action = self.engage_submission_menu.addAction(
            'Empty queue, then add shots to new sequence'
        )
        self.engage_new_sequence_clear_queue_action.triggered.connect(
            lambda: self.on_engage_clicked(
                submission_mode=SUBMISSION_MODE_NEW_SEQUENCE_CLEAR_QUEUE
            )
        )
        self.engage_new_sequence_clear_queue_action.setToolTip(
            'Delete the queued shots BLACS is not running, then submit the replacement batch as a new shot sequence.'
        )
        self.engage_new_sequence_clear_queue_action.setStatusTip(
            'Delete the queued shots BLACS is not running, then submit the replacement batch as a new shot sequence.'
        )
        self.engage_last_sequence_clear_queue_action = self.engage_submission_menu.addAction(
            'Empty queue, then add shots to last sequence'
        )
        self.engage_last_sequence_clear_queue_action.triggered.connect(
            lambda: self.on_engage_clicked(
                submission_mode=SUBMISSION_MODE_LAST_SEQUENCE_CLEAR_QUEUE
            )
        )
        self.engage_last_sequence_clear_queue_action.setToolTip(
            'Delete the queued shots BLACS is not running, then submit the replacement batch onto the same shot sequence.'
        )
        self.engage_last_sequence_clear_queue_action.setStatusTip(
            'Delete the queued shots BLACS is not running, then submit the replacement batch onto the same shot sequence.'
        )
        self.engage_submission_menu.aboutToShow.connect(
            self.update_engage_submission_menu_actions
        )
        button.setMenu(self.engage_submission_menu)
        button.setToolTip(
            """<html><head/><body><p>Compile pending shots, put them in the queue for BLACS if the "BLACS" checkbox is checked, and send them to runviewer if "runviewer" is checked.</p><p>Press and hold to choose alternate queue submission modes.</p><p><span style="font-style:italic;">Empty queue, then add shots to new sequence</span> and <span style="font-style:italic;">Empty queue, then add shots to last sequence</span> delete the queued shots BLACS is not running before submitting the replacement batch. With lazy compile enabled, later compile failures are still possible when BLACS requests those shots.</p></body></html>"""
        )

    def get_queue_append_filepath(self):
        # A default shot belongs to the day's default sequence, which no batch
        # joins, so it is passed over here and offer_shot() does not record one
        # as the shot last sent.
        queue_paths = self.queue_controller.get_queue_paths()
        if not queue_paths:
            return None
        return os.path.abspath(queue_paths[-1])

    def get_submission_anchor(self, submission_mode):
        """The shot this mode numbers its batch after, or None for a new sequence.

        A mode that joins a sequence tries the last queued shot, then the shot last
        sent; one that empties the queue tries them the other way round, since the
        queued rows are about to be deleted. None means there is no sequence to
        join. The queue changes under the server thread, so this reads it once,
        where the answer is used."""
        mode = SUBMISSION_MODES[submission_mode]
        if not mode.joins_sequence:
            return None
        last_sent = self.queue_controller.get_queue_state()['last_sent_from_queue']
        if mode.clears_queue:
            return last_sent or self.get_queue_append_filepath()
        return self.get_queue_append_filepath() or last_sent

    def can_use_alternate_submission_mode(self):
        """Whether the alternate submission modes are on offer.

        They need BLACS ticked, and a last sequence for their wording to name."""
        return (
            self.ui.checkBox_run_shots.isChecked()
            and self.get_submission_anchor(SUBMISSION_MODE_LAST_SEQUENCE) is not None
        )

    def update_engage_submission_menu_actions(self):
        enabled = self.can_use_alternate_submission_mode()
        self.engage_last_sequence_action.setEnabled(enabled)
        self.engage_new_sequence_clear_queue_action.setEnabled(enabled)
        self.engage_last_sequence_clear_queue_action.setEnabled(enabled)

    def reindex_run_file_infos(
        self, run_file_infos, indexed_path_base, index_start=None, name_format=None
    ):
        """Name and number a batch after the shot ``indexed_path_base``, whose sequence it joins.

        With ``name_format``, that sequence's ``(folder, prefix)``, each shot is
        named from the prefix and its own globals, as make_run_files names them."""
        if not run_file_infos:
            return run_file_infos
        candidate_stem = os.path.splitext(os.path.basename(indexed_path_base))[0]
        _, _, index_str = candidate_stem.rpartition('_')
        width = len(index_str) if index_str.isdigit() else 1
        suffix_format = f'_{{index:0{width}d}}'
        # A shot still compiling writes its file after this batch is named, and
        # deletes it again if its row has gone, so its name is not given out.
        compiling = self.queue_controller.get_compiling_paths()
        next_index = index_start
        for run_file_info in run_file_infos:
            run_file_info['name_format'] = name_format
            if name_format is None:
                run_file, next_index = next_available_indexed_filepath(
                    indexed_path_base,
                    suffix_format,
                    start=next_index,
                )
                while os.path.abspath(run_file) in compiling:
                    run_file, next_index = next_available_indexed_filepath(
                        indexed_path_base,
                        suffix_format,
                        start=next_index + 1,
                    )
            else:
                folder, prefix = name_format
                run_file, next_index = runmanager.next_run_file(
                    folder,
                    prefix,
                    run_file_info['shot_globals'],
                    next_index,
                    width,
                    compiling,
                )
            run_file_info['path'] = run_file
            # A shot file is named after the run number written into it, so a batch
            # added to a sequence is numbered on from it, not restarted at run 0 in
            # files named for other numbers.
            run_file_info['run_no'] = next_index
            next_index += 1
        # How far the sequence reaches as of these shots (see make_single_run_file);
        # those written earlier keep the n_runs they were written with.
        runs_in_sequence = next_index
        for run_file_info in run_file_infos:
            run_file_info['n_runs'] = runs_in_sequence
        return run_file_infos

    def on_engage_clicked(self, checked=False, submission_mode=SUBMISSION_MODE_NEW_SEQUENCE):
        logger.info('Engage')
        self.set_output_tab_error(None)
        try:
            self.engage(submission_mode)
        except Exception as e:
            self.output_box.output(f'{e!s}\n\n', red=True)
            self.set_output_tab_error(str(e))
        logger.info('end engage')

    def engage(self, submission_mode=SUBMISSION_MODE_NEW_SEQUENCE):
        """Make the batch the Engage button makes, returning its queue records."""
        if submission_mode not in SUBMISSION_MODES:
            raise ValueError(f'Unknown submission mode {submission_mode!r}.')
        send_to_blacs = self.ui.checkBox_run_shots.isChecked()
        send_to_runviewer = self.ui.checkBox_view_shots.isChecked()
        mode = SUBMISSION_MODES[submission_mode]
        if (mode.joins_sequence or mode.clears_queue) and not send_to_blacs:
            # A mode that reads the queue for a sequence to join, or
            # empties it, is about the queue -- so with nothing going to
            # BLACS there is nothing for it to do.
            raise ValueError(
                "Alternate queue submission modes require 'BLACS' to be selected."
            )
        return self.compile_and_queue_shots(
            submission_mode,
            send_to_blacs,
            send_to_runviewer,
            self.expand_pending_shots(),
        )

    def expand_pending_shots(self):
        """The window's shot globals, each paired with its globals to freeze, in making order."""
        active_groups = self.get_active_groups(interactive=False)
        expansion_order = {}
        for i in range(self.axes_model.rowCount()):
            item = self.axes_model.item(i, self.AXES_COL_NAME)
            shuffle_item = self.axes_model.item(i, self.AXES_COL_SHUFFLE)
            name = item.data(self.AXES_ROLE_NAME)
            expansion_order[name] = {'order':i, 'shuffle':shuffle_item.checkState() == QtCore.Qt.CheckState.Checked}
        logger.info('Parsing globals...')
        try:
            pending, _, deprecations = runmanager.get_shots_with_frozen_globals(
                runmanager.globals_file.get_globals_details(active_groups),
                expansion_order,
            )
        except Exception as e:
            raise ValueError(f'The globals could not be evaluated: {e}') from e
        for global_name, message in deprecations.items():
            self.output_box.output(f'Warning: global {global_name}: {message}\n', red=True)
        if deprecations:
            self.output_box.output(
                f'{runmanager.evaluator.PYLAB_NAMESPACE_NOTE}\n', red=True
            )
        if not pending:
            raise ValueError('The globals expand into no shots.')
        if self.ui.pushButton_shuffle.checkState() == QtCore.Qt.CheckState.Checked:
            # Per-global shuffles leave the batch ordered by the outermost
            # global; only shuffling the whole batch takes that out.
            random.shuffle(pending)
        return pending

    def compile_and_queue_shots(
        self, submission_mode, send_to_blacs, send_to_runviewer, batch, sequence=None
    ):
        """Make the shots of one batch and put them in the queue.

        ``batch`` is one ``(shot globals, globals to freeze)`` pair per shot, in
        making order. The batch is made in one go, since a filename and a run
        number are claimed by the batch being made. ``sequence``, if given, is the
        ``(sequence_id, sequence_index)`` of a sequence made or added to here,
        which the batch joins in place of the one the mode would find; an index of
        None names the one sequence recorded with that id.

        Raises rather than writing to the output box, which Engage does itself.
        Returns the queue records, each carrying its row's identifier, in the
        order the shots were given."""
        mode = SUBMISSION_MODES[submission_mode]
        indexed_path_base = self.get_submission_anchor(submission_mode)
        labscript_file = self.ui.lineEdit_labscript_file.text()
        if not labscript_file:
            raise ValueError('No labscript file is selected.')
        output_folder = self.ui.lineEdit_shot_output_folder.text()
        if send_to_blacs and not output_folder:
            raise ValueError('No output folder is selected.')
        active_groups = self.get_active_groups(interactive=False)
        index_start = None
        sequence_attrs = None
        name_format = None
        if indexed_path_base is not None and (mode.clears_queue or sequence is None):
            # Read before the Clear, which takes the anchor's row and the
            # sequence recorded with it.
            sequence_attrs = self.queue_controller.get_queued_sequence_attrs(
                indexed_path_base
            )
            if sequence_attrs is None:
                raise SequenceRefused(
                    f'Cannot add shots to the sequence of {indexed_path_base}: '
                    'runmanager has no record of its sequence.'
                )
            if mode.clears_queue:
                # Numbering starts from 0 again, taking back the names the
                # shots being deleted gave up; the ones whose files are still
                # there are skipped over.
                index_start = 0
            else:
                key = (sequence_attrs['sequence_id'], sequence_attrs['sequence_index'])
                if key in self.sequences:
                    sequence = key
        if sequence is not None:
            sequence_id, sequence_index = sequence
            # An id alone names the one sequence recorded with it.
            keys = [
                key for key in list(self.sequences)
                if key[0] == sequence_id and sequence_index in (None, key[1])
            ]
            if len(keys) != 1:
                raise SequenceRefused(
                    f'Cannot add shots to sequence {sequence_id}: runmanager has no record of '
                    'it, or has two and was not told which'
                )
            indexed_path_base, sequence_attrs, index_start, name_format = (
                self.sequences[keys[0]]
            )
        # A sequence is one labscript file's shots, and is refused before a
        # replacement's Clear, so that a refusal leaves the queue as it was.
        if sequence_attrs is not None:
            joined = sequence_attrs['script_basename']
            if joined != os.path.splitext(os.path.basename(labscript_file))[0]:
                raise SequenceRefused(
                    f"Cannot add shots to sequence {sequence_attrs['sequence_id']}: "
                    f'it is a sequence of {joined}, not of {labscript_file}'
                )
        if mode.clears_queue:
            self.queue_manager.clear()
        logger.info('Making h5 files')
        if not send_to_blacs:
            # Shots that will never run are not a sequence: they claim no
            # sequence index, and leave nothing in the shot output folder.
            output_folder = tempfile.mkdtemp(dir=self.scratch_dir.name)
        run_files = self.name_run_files(
            labscript_file,
            output_folder,
            [shot_globals for shot_globals, _ in batch],
            indexed_path_base=indexed_path_base,
            index_start=index_start,
            sequence_attrs=sequence_attrs,
            name_format=name_format,
            increment_sequence_index=send_to_blacs,
        )
        compile_mode = self.queue_compile_mode_combo.currentData()
        queue_records = []
        for run_file_info, (_, frozen) in zip(run_files, batch):
            name_format = run_file_info['name_format']
            queue_records.append(
                {
                    'path': run_file_info['path'],
                    'labscript_file': labscript_file,
                    'active_groups': dict(active_groups),
                    'compile_mode': compile_mode,
                    'compiled': False,
                    'frozen_globals': frozen,
                    'sequence_attrs': run_file_info['sequence_attrs'],
                    'run_no': run_file_info['run_no'],
                    'n_runs': run_file_info['n_runs'],
                }
            )
        # For a later batch to join. Its next run number is kept here rather
        # than read from files, which shots still being compiled do not have,
        # and never goes down, though a replacement numbers from 0 again.
        if send_to_blacs:
            last = queue_records[-1]
            attrs = last['sequence_attrs']
            key = (attrs['sequence_id'], attrs['sequence_index'])
            next_run = max(last['run_no'] + 1, self.sequences.get(key, (None, None, 0))[2])
            self.sequences[key] = (last['path'], last['sequence_attrs'], next_run, name_format)
        return self.queue_manager.compile_shots(
            queue_records, send_to_blacs, send_to_runviewer
        )

    def on_abort_clicked(self):
        """Empty the queue as the replacement modes' Clear does, and fail the compile in progress.

        Waiting rows go; running, failed, rejected and cancelled rows are kept. A
        batch being compiled for runviewer alone stops there."""
        self.queue_manager.stop_compile_shots()
        self.queue_manager.clear()
        # During a restart there is no compile to interrupt, and the old
        # compiler may be gone:
        if self.child_ready.is_set():
            self.compile_interrupted.set()
            self.to_child.put(['abort', None])

    def on_restart_subprocess_clicked(self):
        self.ui.pushButton_restart_subprocess.setEnabled(False)
        # Cleared before the fake reply, which frees a compile waiting on the
        # old child to go on to the next shot. That shot must wait for the new
        # child rather than be sent to this one.
        self.child_ready.clear()
        self.compile_interrupted.set()
        self.to_child.put(['quit', None])
        self.from_child.put(
            ['done', False, 'The compiler subprocess was restarted during the compile.']
        )
        self.output_box.output('Asking subprocess to quit...')
        # It interrupts any compile, and exits once no HDF5 file is open for writing:
        timeout_time = time.time() + self.QUIT_TIMEOUT
        QtCore.QTimer.singleShot(50, lambda: self.check_child_exited(timeout_time))

    def check_child_exited(self, timeout_time):
        self.child.poll()
        if self.child.returncode is None and time.time() < timeout_time:
            QtCore.QTimer.singleShot(50, lambda: self.check_child_exited(timeout_time))
            return
        elif self.child.returncode is None:
            self.child.kill()
            self.output_box.output('Killed\n', red=True)
        else:
            self.output_box.output('done.\n')
        self.output_box.output('Spawning new compiler subprocess...')
        try:
            self.to_child, self.from_child, self.child = runmanager.start_compiler(
                self.output_box.port
            )
            self.child_ready.set()
            self.output_box.output('done.\n')
            self.output_box.output('Ready.\n\n')
        except Exception as e:
            logger.exception('Could not spawn the compiler subprocess')
            # Compiles stay held, rather than going to a child that has gone,
            # until a restart gets a new one up:
            self.output_box.output(
                f'failed: {e}\nCompiling waits until Restart subprocess succeeds.\n\n',
                red=True,
            )
        finally:
            self.ui.pushButton_restart_subprocess.setEnabled(True)

    def on_tabCloseRequested(self, index):
        tab_page = self.ui.tabWidget.widget(index)
        for (globals_file, group_name), group_tab in self.currently_open_groups.items():
            if group_tab.ui is tab_page:
                self.close_group(globals_file, group_name)
                break

    def on_axes_item_changed(self, item):
        if item.column() == self.AXES_COL_SHUFFLE:
            self.update_global_shuffle_state()
            
    def update_global_shuffle_state(self, *args, **kwargs):
        all_checked = True
        none_checked = True
        for i in range(self.axes_model.rowCount()):
            check_state = self.axes_model.item(i, self.AXES_COL_SHUFFLE).checkState() == QtCore.Qt.CheckState.Checked
            all_checked = all_checked and check_state
            none_checked = none_checked and not check_state
            
        if not all_checked and not none_checked:
            self.ui.pushButton_shuffle.setTristate(True)
            self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
        elif none_checked and not all_checked:
            self.ui.pushButton_shuffle.setTristate(False)
            self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.CheckState.Unchecked)
        elif all_checked and not none_checked:
            self.ui.pushButton_shuffle.setTristate(False)
            self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.CheckState.Checked)
        else:
            # No axes: a partially checked master box becomes checked, else it is left alone:
            if self.ui.pushButton_shuffle.checkState() == QtCore.Qt.CheckState.PartiallyChecked:
                self.ui.pushButton_shuffle.setTristate(False)
                self.ui.pushButton_shuffle.setCheckState(QtCore.Qt.CheckState.Checked)


    def on_master_shuffle_clicked(self, state):
        # stateChanged delivers an int, which never equals a Qt.CheckState member.
        state = QtCore.Qt.CheckState(state)
        if state in [QtCore.Qt.CheckState.Checked, QtCore.Qt.CheckState.Unchecked]:
            self.ui.pushButton_shuffle.setTristate(False)
            for i in range(self.axes_model.rowCount()):
                item = self.axes_model.item(i, self.AXES_COL_SHUFFLE)
                if item.checkState() != state:
                    self.axes_model.item(i, self.AXES_COL_SHUFFLE).setCheckState(state)

    def on_treeView_groups_context_menu_requested(self, point):
        menu = QtWidgets.QMenu(self.ui)
        menu.addAction(self.action_groups_set_selection_active)
        menu.addAction(self.action_groups_set_selection_inactive)
        menu.addAction(self.action_groups_delete_selected)
        menu.addAction(self.action_groups_open_selected)
        menu.addAction(self.action_groups_close_selected_groups)
        menu.addAction(self.action_groups_close_selected_files)
        copy_menu = QtWidgets.QMenu('Copy selected group(s) to...', menu)
        copy_menu.setIcon(Icon(':/qtutils/fugue/blue-document-copy'))
        menu.addMenu(copy_menu)
        move_menu = QtWidgets.QMenu('Move selected group(s) to...', menu)
        move_menu.setIcon(Icon(':/qtutils/fugue/blue-document--arrow'))
        menu.addMenu(move_menu)

        # Create a dict of all filepaths -> filenames
        filenames = {}
        for index in range(self.groups_model.rowCount()):
            filepath = self.groups_model.item(index, self.GROUPS_COL_NAME).text()
            filenames[filepath] = filepath.split(os.sep)[-1]

        # Add parent folders to duplicate file names until there are none:
        i = 2
        while True:
            all_filenames = list(filenames.values())
            duplicates = [
                filepath for filepath, filename in filenames.items()
                if all_filenames.count(filename) > 1
            ]
            if not duplicates:
                break
            for filepath in duplicates:
                filenames[filepath] = os.sep.join(filepath.split(os.sep)[-i:])
            i += 1

        for filepath, filename in filenames.items():
            copy_menu.addAction(filename, lambda filepath=filepath: self.on_groups_copy_selected_groups_triggered(filepath, False))
            move_menu.addAction(filename, lambda filepath=filepath: self.on_groups_copy_selected_groups_triggered(filepath, True))

        menu.exec(QtGui.QCursor.pos())

    def selected_group_items(self, column):
        """The selected groups' items in ``column``, leaving out the files' rows."""
        selected_items = (
            self.groups_model.itemFromIndex(index)
            for index in self.ui.treeView_groups.selectedIndexes()
        )
        return [
            item
            for item in selected_items
            if item.column() == column and item.parent() is not None
        ]

    def on_groups_copy_selected_groups_triggered(self, dest_globals_file=None, delete_source_group=False):
        for item in self.selected_group_items(self.GROUPS_COL_NAME):
            source_globals_file = item.parent().text()
            self.copy_group(source_globals_file, item.text(), dest_globals_file, delete_source_group)

    def on_groups_set_selection_active_triggered(self, checked_state):
        for item in self.selected_group_items(self.GROUPS_COL_ACTIVE):
            item.setCheckState(checked_state)

    def on_groups_delete_selected_triggered(self):
        name_items = self.selected_group_items(self.GROUPS_COL_NAME)
        # If multiple selected, show 'delete n groups?' message. Otherwise,
        # pass confirm=True to self.delete_group so it can show the regular
        # message.
        confirm_multiple = (len(name_items) > 1)
        if confirm_multiple:
            if not self.question_dialog(f"Delete {len(name_items)} groups?"):
                return
        for item in name_items:
            globals_file = item.parent().text()
            group_name = item.text()
            self.delete_group(globals_file, group_name, confirm=not confirm_multiple)

    def on_groups_open_selected_triggered(self):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        selected_items = [self.groups_model.itemFromIndex(index) for index in selected_indexes]
        name_items = self.selected_group_items(self.GROUPS_COL_NAME)

        # Include all groups of selected globals files:
        for item in selected_items:
            if item.parent() is None:
                children = [item.child(i) for i in range(item.rowCount())]
                # Exclude <add new group> item, which is not selectable
                name_items += [child for child in children if child.isSelectable() ]

        for item in name_items:
            globals_file = item.parent().text()
            group_name = item.text()
            if (globals_file, group_name) not in self.currently_open_groups:
                self.open_group(globals_file, group_name, trigger_preparse=False)
        if name_items:
            self.globals_changed()

    def on_groups_close_selected_groups_triggered(self):
        for item in self.selected_group_items(self.GROUPS_COL_NAME):
            globals_file = item.parent().text()
            group_name = item.text()
            if (globals_file, group_name) in self.currently_open_groups:
                self.close_group(globals_file, group_name)

    def on_groups_close_selected_files_triggered(self):
        selected_indexes = self.ui.treeView_groups.selectedIndexes()
        selected_items = (self.groups_model.itemFromIndex(index) for index in selected_indexes)
        name_items = [item for item in selected_items
                      if item.column() == self.GROUPS_COL_NAME
                      and item.parent() is None]
        child_openclose_items = [item.child(i, self.GROUPS_COL_OPENCLOSE)
                                 for item in name_items
                                 for i in range(item.rowCount())]
        child_is_open = [child_item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
                         for child_item in child_openclose_items]
        if any(child_is_open):
            if not self.question_dialog(
                    f'Close {len(name_items)} file(s)? This will close '
                    f'{child_is_open.count(True)} currently open group(s).'):
                return
        for item in name_items:
            globals_file = item.text()
            self.close_globals_file(globals_file, confirm=False)

    def on_open_globals_file_clicked(self):
        globals_file = file_dialogs.open_file(
            self.ui,
            'Select globals file',
            self.last_opened_globals_folder,
            "Globals files (*.toml *.h5 *.hdf5)",
        )
        if not globals_file:
            # User cancelled selection
            return
        if not os.path.isfile(globals_file):
            self.error_dialog(f"No such file {globals_file}.")
            return
        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_globals_folder = os.path.dirname(globals_file)
        self.open_globals_file(globals_file)

    def on_new_globals_file_clicked(self):
        # A save panel given no extension supplies one from the host's type database,
        # not necessarily the filter's, so it is offered 'untitled.toml': typing
        # replaces only the stem and '.toml' survives.
        suggested_file = os.path.join(self.last_opened_globals_folder, 'untitled.toml')
        globals_file = file_dialogs.save_file(
            self.ui, 'Create new globals file', suggested_file, "TOML files (*.toml)"
        )
        if not globals_file:
            # User cancelled
            return
        if not globals_file.lower().endswith('.toml'):
            globals_file += '.toml'
        # Save the containing folder for use next time we open the dialog box:
        self.last_opened_globals_folder = os.path.dirname(globals_file)
        runmanager.globals_file.new_globals_file(globals_file)
        self.open_globals_file(globals_file)

    def on_diff_globals_file_clicked(self):
        globals_file = file_dialogs.open_file(
            self.ui,
            'Select globals file to compare',
            self.last_opened_globals_folder,
            "Globals files (*.toml *.h5 *.hdf5)",
        )
        if not globals_file:
            # User cancelled
            return

        active_groups = self.get_active_groups()
        if active_groups is None:
            # Invalid group selection
            return

        other_groups = runmanager.get_all_groups(globals_file)

        self.ui.tabWidget.setCurrentWidget(self.ui.tab_output)
        self.output_box.output(f'Globals diff with:\n{globals_file}\n\n')

        globals_diff_table = runmanager.globals_diff_groups(active_groups, other_groups)
        self.output_box.output(globals_diff_table)
        self.output_box.output('Ready.\n\n')

    def on_treeView_groups_leftClicked(self, index):
        if qapplication.keyboardModifiers() != QtCore.Qt.KeyboardModifier.NoModifier:
            # Only handle mouseclicks with no keyboard modifiers.
            return
        item = self.groups_model.itemFromIndex(index)
        # The 'name' item in the same row:
        name_index = index.sibling(index.row(), self.GROUPS_COL_NAME)
        name_item = self.groups_model.itemFromIndex(name_index)
        # The parent item, None if there is no parent:
        parent_item = item.parent()
        if item.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
            # Edit the name item, to enter the new group's name:
            self.ui.treeView_groups.setCurrentIndex(name_index)
            self.ui.treeView_groups.edit(name_index)
        elif item.column() == self.GROUPS_COL_ACTIVE:
            # Toggled by hand: a checkable item changes the model before the click
            # is caught, so a sort by this column would run before the item was
            # selected, and the selection would not follow the item.
            state = item.checkState()
            if state in (QtCore.Qt.CheckState.Unchecked, QtCore.Qt.CheckState.PartiallyChecked):
                item.setCheckState(QtCore.Qt.CheckState.Checked)
            elif state == QtCore.Qt.CheckState.Checked:
                item.setCheckState(QtCore.Qt.CheckState.Unchecked)
            else:
                raise AssertionError('Invalid Check state')
            # If this changed the sort order, ensure the item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, item)
        elif parent_item is None:
            globals_file = name_item.text()
            if item.column() == self.GROUPS_COL_OPENCLOSE:
                self.close_globals_file(globals_file)
        else:
            globals_file = parent_item.text()
            group_name = name_item.text()
            if item.column() == self.GROUPS_COL_DELETE:
                self.delete_group(globals_file, group_name, confirm=True)
            elif item.column() == self.GROUPS_COL_OPENCLOSE:
                group_is_open = item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
                if group_is_open:
                    self.close_group(globals_file, group_name)
                else:
                    self.open_group(globals_file, group_name)

    def on_treeView_groups_doubleLeftClicked(self, index):
        item = self.groups_model.itemFromIndex(index)
        # The parent item, None if there is no parent:
        parent_item = item.parent()
        if item.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
            return
        elif parent_item and item.column() == self.GROUPS_COL_NAME:
            globals_file = parent_item.text()
            group_name = item.text()
            if (globals_file, group_name) not in self.currently_open_groups:
                self.open_group(globals_file, group_name)
            group_tab = self.currently_open_groups[globals_file, group_name]
            for i in range(self.ui.tabWidget.count()):
                if self.ui.tabWidget.widget(i) is group_tab.ui:
                    self.ui.tabWidget.setCurrentIndex(i)
                    break

    def on_groups_model_item_changed(self, item):
        """Respond to a change in the groups model.

        Creates or renames a group, and keeps the rest of the model consistent.
        Changes made here may trigger this again, and must die out. To stop one,
        use ``groups_model_item_changed_disconnected``, but sparingly: a required
        update can be forgotten."""
        if item.column() == self.GROUPS_COL_NAME:
            self.on_groups_model_name_changed(item)
        elif item.column() == self.GROUPS_COL_ACTIVE:
            self.on_groups_model_active_changed(item)
        elif item.column() == self.GROUPS_COL_OPENCLOSE:
            self.on_groups_model_openclose_changed(item)

    def on_groups_model_name_changed(self, item):
        """Handles group renaming and creation of new groups due to the user
        editing the <click to add group> item"""
        parent_item = item.parent()
        # File rows are supposed to be uneditable, but just to be sure we have
        # a group row:
        assert parent_item is not None
        if item.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
            item_text = item.text()
            if item_text != self.GROUPS_DUMMY_ROW_TEXT:
                globals_file = parent_item.text()
                group_name = item_text
                self.new_group(globals_file, group_name)
        else:
            new_group_name = item.text()
            previous_group_name = item.data(self.GROUPS_ROLE_PREVIOUS_NAME)
            # Ensure it truly is a name change, and not something else about
            # the item changing:
            if new_group_name != previous_group_name:
                globals_file = parent_item.text()
                self.rename_group(globals_file, previous_group_name, new_group_name)

    def on_groups_model_active_changed(self, item):
        """Set the item's sort data and, for a change from outside, update related rows.

        A file row's state is applied to its groups, and a group's change updates
        its file row. A change this method makes itself, which the recursion depth
        identifies, only sets sort data."""
        self.on_groups_model_active_changed_recursion_depth += 1
        try:
            check_state = item.checkState()
            # Ensure sort data matches active state. The number orders rows by
            # state, where the enum itself would order them by its name.
            item.setData(check_state.value, self.GROUPS_ROLE_SORT_DATA)
            if self.on_groups_model_active_changed_recursion_depth > 1:
                # Prevent all below code from running in response to data changes
                # initiated from within this method itself. The code above this
                # check still runs in response to all changes.
                return

            parent_item = item.parent()
            if parent_item is not None:
                # A group changed: its file row shows the state of its groups.
                children = [parent_item.child(i, self.GROUPS_COL_ACTIVE) for i in range(parent_item.rowCount())]
                child_states = [child.checkState() for child in children
                                if not child.data(self.GROUPS_ROLE_IS_DUMMY_ROW)]
                parent_active_index = parent_item.index().sibling(parent_item.index().row(), self.GROUPS_COL_ACTIVE)
                parent_active_item = self.groups_model.itemFromIndex(parent_active_index)
                if all(state == QtCore.Qt.CheckState.Checked for state in child_states):
                    parent_active_item.setCheckState(QtCore.Qt.CheckState.Checked)
                elif all(state == QtCore.Qt.CheckState.Unchecked for state in child_states):
                    parent_active_item.setCheckState(QtCore.Qt.CheckState.Unchecked)
                else:
                    parent_active_item.setCheckState(QtCore.Qt.CheckState.PartiallyChecked)
            else:
                # A file changed: apply its state to its groups.
                name_index = item.index().sibling(item.index().row(), self.GROUPS_COL_NAME)
                name_item = self.groups_model.itemFromIndex(name_index)
                checkstate = item.checkState()
                children = [name_item.child(i, self.GROUPS_COL_ACTIVE) for i in range(name_item.rowCount())]
                for child in children:
                    if not child.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
                        child.setCheckState(checkstate)
        finally:
            self.on_groups_model_active_changed_recursion_depth -= 1
            if self.on_groups_model_active_changed_recursion_depth == 0:
                self.do_model_sort()
                self.globals_changed()

    def on_groups_model_openclose_changed(self, item):
        """Sets item sort data and icon in response to the open/close state of a group
        changing."""
        parent_item = item.parent()
        # The open/close state of a globals group changed. It is definitely a
        # group, not a file, as the open/close state of a file shouldn't be
        # changing.
        assert parent_item is not None  # Just to be sure.
        group_is_open = item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
        item.setData(group_is_open, self.GROUPS_ROLE_SORT_DATA)
        # Changing the icon emits itemChanged even for the same icon, so the
        # signal is disconnected while the icons are set, to avoid infinite recursion.
        with self.groups_model_item_changed_disconnected:
            if group_is_open:
                item.setIcon(Icon(':qtutils/fugue/cross'))
                item.setToolTip('Close globals group.')
            else:
                item.setIcon(Icon(':qtutils/fugue/plus'))
                item.setToolTip('Load globals group into runmanager.')
            self.do_model_sort()
            # If this changed the sort order, ensure the item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, item)

    @inmain_decorator()
    def get_default_output_folder(self):
        """The default output folder now, for the selected labscript file, or '' if none.

        It is not created, and not checked for existence."""
        current_labscript_file = self.ui.lineEdit_labscript_file.text()
        if not current_labscript_file:
            return ''
        _, default_output_folder, _ = runmanager.new_sequence_details(
            current_labscript_file,
            config=self.exp_config,
            increment_sequence_index=False,
        )
        default_output_folder = os.path.normpath(default_output_folder)
        # This is still a template: unresolved globals[...] lookups are shown as
        # written, and literal braces are escaped for the per-shot pass. Undo
        # that escaping for display.
        return unescape_braces(default_output_folder)

    def rollover_shot_output_folder(self):
        """Runs in a thread: every 30 seconds, check whether the default output folder changed.

        It changes with the date, and when another runmanager increments the
        sequence index."""
        while True:
            time.sleep(30)
            try:
                self.check_output_folder_update()
            except Exception as e:
                # Don't stop the thread.
                logger.exception("error checking default output folder: %s", e)

    @inmain_decorator()
    def check_output_folder_update(self):
        """Check once whether the output folder needs updating.

        Runs in the main thread, so that no other Qt call interleaves."""
        current_default_output_folder = self.get_default_output_folder()
        if not current_default_output_folder:
            # No labscript file selected:
            return
        currently_selected_output_folder = self.ui.lineEdit_shot_output_folder.text()
        if current_default_output_folder != self.previous_default_output_folder:
            # A new day, or a new labscript file: if the default folder was selected,
            # select the new default:
            if currently_selected_output_folder == self.previous_default_output_folder:
                self.ui.lineEdit_shot_output_folder.setText(current_default_output_folder)
            self.previous_default_output_folder = current_default_output_folder

    @inmain_decorator()
    def globals_changed(self):
        """Note that globals have changed, so that they are parsed again.

        Called by the window, a GroupTab and the server."""
        self.globals_changes += 1
        self.ui.pushButton_engage.setEnabled(False)
        self.preparse_globals_required.put(None)

    def update_axes_indentation(self):
        for i in range(self.axes_model.rowCount()):
            item = self.axes_model.item(i, self.AXES_COL_NAME)
            text = item.text().lstrip()
            text = '    '*i + text
            item.setText(text)
            
    @inmain_decorator()  # Is called by preparser thread
    def update_axes_tab(self, expansions, dimensions):
        expansion_list = []
        for global_name, expansion in expansions.items():
            if expansion:
                if expansion == 'outer':
                    expansion_list.append('outer '+global_name)
                else:
                    expansion_list.append('zip '+expansion)
                
        expansion_list = set(expansion_list)
                
        for i in reversed(range(self.axes_model.rowCount())):
            item = self.axes_model.item(i, self.AXES_COL_NAME)
            name = item.data(self.AXES_ROLE_NAME)
            if name not in expansion_list:
                item = self.axes_model.takeRow(i)
                del item
            else:
                length_item = self.axes_model.item(i, self.AXES_COL_LENGTH)
                if name in dimensions:
                    length_item.setText(f"{dimensions[name]}")
                else:
                    length_item.setText('Unknown')
                
                # remove from expansions list so we don't add it again
                expansion_list.remove(name)
            
        for expansion_name in expansion_list:
            shuffle = self.ui.pushButton_shuffle.checkState() != QtCore.Qt.CheckState.Unchecked
            self.add_item_to_axes_model(expansion_name, shuffle, dimensions)
                
        self.update_axes_indentation() 

    def add_item_to_axes_model(self, expansion_name, shuffle, dimensions = None):
        if dimensions is None:
            dimensions = {}
        
        items = []
        
        expansion_type, name = expansion_name.split(None, 1)
        name_item = QtGui.QStandardItem(name)
        name_item.setData(expansion_name, self.AXES_ROLE_NAME)
        if expansion_type == 'outer':
            name_item.setIcon(Icon(':qtutils/custom/outer'))
        else:
            name_item.setIcon(Icon(':qtutils/custom/zip'))
        items.append(name_item)
        
        length = 'Unknown'
        if expansion_name in dimensions:
            length = f"{dimensions[expansion_name]}"
        length_item = QtGui.QStandardItem(length)
        items.append(length_item)
        
        shuffle_item = QtGui.QStandardItem()
        shuffle_item.setCheckable(True)
        shuffle_item.setCheckState(QtCore.Qt.CheckState.Checked if shuffle else QtCore.Qt.CheckState.Unchecked)
        
        items.append(shuffle_item)
        
        self.axes_model.appendRow(items)
    
    @inmain_decorator()  # Is called by preparser thread
    def update_tabs_parsing_indication(
        self, active_groups, sequence_globals, evaled_globals, deprecations, scanned_globals,
        dimensions, n_shots
    ):
        for group_tab in self.currently_open_groups.values():
            group_tab.update_parse_indication(
                active_groups, sequence_globals, evaled_globals, deprecations, scanned_globals,
                dimensions
            )
        self.ui.pushButton_engage.setEnabled(True)
        if n_shots == 1:
            n_shots_string = '(1 shot)'
        else:
            n_shots_string = f'({n_shots} shots)'
        self.ui.pushButton_engage.setText(f'Engage {n_shots_string}')

    def preparse_globals(self):
        active_groups = self.get_active_groups()
        if active_groups is None:
            # There was an error, get_active_groups has already shown
            # it to the user.
            return
        # Expansion mode is automatically updated when the global's
        # type changes. If this occurs, we will have to parse again to
        # include the change:
        while True:
            results = self.parse_globals(active_groups, raise_exceptions=False, expand_globals=False)
            sequence_globals, _, evaled_globals, global_hierarchy, expansions, deprecations = results
            expansions_changed = self.guess_expansion_modes(
                active_groups, evaled_globals, global_hierarchy, expansions)
            if not expansions_changed:
                # Count the shots without making them, which for a large scan
                # would take all the memory there is. Only once the guessing has
                # settled, so that the counted expansions are the stored ones:
                self.n_shots, dimensions = runmanager.count_shots(
                    sequence_globals, evaled_globals, return_dimensions=True
                )
                break
        scanned_globals = runmanager.scanned_globals(
            sequence_globals, evaled_globals, global_hierarchy
        )
        self.update_tabs_parsing_indication(
            active_groups, sequence_globals, evaled_globals, deprecations, scanned_globals,
            dimensions, self.n_shots,
        )
        self.update_axes_tab(expansions, dimensions)
        logger.info('Globals parsed')

    def preparse_globals_loop(self):
        """Runs in a thread: whenever globals have changed, evaluate them and show
        the results in their tabs."""
        while True:
            try:
                self.preparse_globals_required.get()
                n_requests = 1
                # Wait until the main thread is idle before clearing the queue of
                # requests, so that preparsing triggered several times within it is
                # answered once, after they are all done.
                with qtlock:
                    while True:
                        try:
                            self.preparse_globals_required.get(block=False)
                            n_requests += 1
                        except queue.Empty:
                            break
                logger.info('Pre-parsing globals with %d requests', n_requests)
                try:
                    self.preparse_globals()
                finally:
                    # Tell any callers calling preparse_globals_required.join() that we
                    # are done with their request, even if it failed, or they wait
                    # forever:
                    for _ in range(n_requests):
                        self.preparse_globals_required.task_done()
            except Exception: # noqa: BLE001
                # Raise the error, but keep going so we don't take down the
                # whole thread if there is a bug.
                exc_info = sys.exc_info()
                raise_exception_in_thread(exc_info)

    def wait_until_preparse_complete(self):
        """Block until the preparse loop has finished pending work"""
        self.preparse_globals_required.join()

    def get_group_item_by_name(self, globals_file, group_name, column, previous_name=None):
        """Returns an item from the row representing a globals group in the
        groups model. Which item is returned is set by the column argument."""
        parent_item = self.groups_model.findItems(globals_file, column=self.GROUPS_COL_NAME)[0]
        possible_name_items = self.groups_model.findItems(group_name, QtCore.Qt.MatchFlag.MatchRecursive,
                                                          column=self.GROUPS_COL_NAME)
        # Don't accidentally match on other groups or files with the same name
        # as this group:
        possible_name_items = [item for item in possible_name_items if item.parent() == parent_item]
        if previous_name is not None:
            # Also filter by previous name, to tell rows apart when a rename is in
            # progress and two rows may briefly hold the same name (the rename code
            # throws an error and reverts it).
            possible_name_items = [item for item in possible_name_items
                                   if item.data(self.GROUPS_ROLE_PREVIOUS_NAME) == previous_name]
        elif group_name != self.GROUPS_DUMMY_ROW_TEXT:
            # Don't return the dummy item unless they asked for it explicitly
            # - if a new group is being created, its name might be
            # simultaneously present in its own row and the dummy row too.
            possible_name_items = [item for item in possible_name_items
                                   if not item.data(self.GROUPS_ROLE_IS_DUMMY_ROW)]

        if len(possible_name_items) > 1:
            raise LookupError('Multiple items found')
        elif not possible_name_items:
            raise LookupError('No item found')
        name_item = possible_name_items[0]
        name_index = name_item.index()
        item_index = name_index.sibling(name_index.row(), column)
        item = self.groups_model.itemFromIndex(item_index)
        return item

    def do_model_sort(self):
        header = self.ui.treeView_groups.header()
        sort_column = header.sortIndicatorSection()
        sort_order = header.sortIndicatorOrder()
        self.ui.treeView_groups.sortByColumn(sort_column, sort_order)

    @inmain_decorator()  # Can be called from a non-main thread
    def get_active_groups(self, interactive=True):
        """The active groups, ``{group_name: globals_file}``.

        If two active groups share a name, shows an error dialog and returns None,
        or raises RuntimeError if ``interactive`` is False."""
        active_groups = {}
        for globals_file, groups in self.group_active_items().items():
            for group_name, active_item in groups.items():
                if active_item.checkState() == QtCore.Qt.CheckState.Checked:
                    if group_name in active_groups:
                        msg = (
                            f'There are two active groups named {group_name}. '
                            + 'Active groups must have unique names.'
                        )
                        if interactive:
                            self.error_dialog(msg)
                            return
                        raise RuntimeError(msg)
                    active_groups[group_name] = globals_file
        return active_groups

    def group_active_items(self):
        """``{globals_file: {group_name: Active item}}`` for every open file."""
        files = {}
        for i in range(self.groups_model.rowCount()):
            file_item = self.groups_model.item(i, self.GROUPS_COL_NAME)
            groups = files[file_item.text()] = {}
            for j in range(file_item.rowCount()):
                name_item = file_item.child(j, self.GROUPS_COL_NAME)
                if not name_item.data(self.GROUPS_ROLE_IS_DUMMY_ROW):
                    groups[name_item.text()] = file_item.child(j, self.GROUPS_COL_ACTIVE)
        return files

    def replace_globals_file_path(self, old_path, new_path):
        if old_path == new_path:
            return new_path
        matching_items = self.groups_model.findItems(old_path, column=self.GROUPS_COL_NAME)
        if not matching_items:
            return new_path
        if self.groups_model.findItems(new_path, column=self.GROUPS_COL_NAME):
            raise RuntimeError(f"A globals file named {new_path} is already open.")
        file_name_item = matching_items[0]
        with self.groups_model_item_changed_disconnected:
            file_name_item.setText(new_path)
            file_name_item.setToolTip(new_path)
            file_name_item.setData(new_path, self.GROUPS_ROLE_SORT_DATA)
        updated_groups = {}
        for (globals_file, group_name), group_tab in self.currently_open_groups.items():
            if globals_file == old_path:
                group_tab.set_file_and_group_name(new_path, group_name)
                updated_groups[new_path, group_name] = group_tab
            else:
                updated_groups[globals_file, group_name] = group_tab
        self.currently_open_groups = updated_groups
        self.do_model_sort()
        return new_path

    def ensure_editable_globals_file(self, globals_file, parent=None):
        if not runmanager.globals_file.is_legacy_hdf5(globals_file):
            return globals_file
        suggested_path = runmanager.globals_file.default_toml_path(globals_file)
        destination = file_dialogs.save_file(
            parent or self.ui,
            'Convert globals file to TOML for editing',
            suggested_path,
            "TOML files (*.toml)",
        )
        if not destination:
            raise RuntimeError('Conversion to TOML cancelled.')
        if not destination.lower().endswith('.toml'):
            destination += '.toml'
        if destination != globals_file and self.groups_model.findItems(destination, column=self.GROUPS_COL_NAME):
            raise RuntimeError(f"A globals file named {destination} is already open.")
        runmanager.globals_file.convert_to_toml(globals_file, destination)
        self.last_opened_globals_folder = os.path.dirname(destination)
        return self.replace_globals_file_path(globals_file, destination)

    def open_globals_file(self, globals_file):
        # Do nothing if this file is already open:
        if self.groups_model.findItems(globals_file, column=self.GROUPS_COL_NAME):
            return globals_file

        # Legacy HDF5 globals and shot files remain readable from the file
        # selector. Conversion to TOML is deferred until the user attempts an
        # edit operation.
        groups = runmanager.globals_file.get_group_names(globals_file)
        file_name_item = ModelItem(globals_file)
        file_name_item.setEditable(False)
        file_name_item.setToolTip(globals_file)
        file_name_item.setData(globals_file, self.GROUPS_ROLE_SORT_DATA)

        file_active_item = ModelItem()
        file_active_item.setCheckState(QtCore.Qt.CheckState.Unchecked)
        # Sort column by CheckState - must keep this updated when checkstate changes:
        file_active_item.setData(QtCore.Qt.CheckState.Unchecked.value, self.GROUPS_ROLE_SORT_DATA)
        file_active_item.setEditable(False)
        file_active_item.setToolTip('Check to set all the file\'s groups as active.')

        file_delete_item = ModelItem()  # Blank, only groups have a delete button
        file_delete_item.setEditable(False)
        file_delete_item.setSelectable(False)

        file_close_item = ModelItem()
        file_close_item.setIcon(Icon(':qtutils/fugue/cross'))
        file_close_item.setEditable(False)
        file_close_item.setToolTip('Close globals file.')

        self.groups_model.appendRow([file_name_item, file_active_item, file_delete_item, file_close_item])

        for group_name in groups:
            row = self.make_group_row(group_name)
            file_name_item.appendRow(row)

        dummy_name_item = ModelItem(self.GROUPS_DUMMY_ROW_TEXT)
        dummy_name_item.setToolTip('Click to add group')
        # This lets later code know that this row does
        # not correspond to an actual globals group:
        dummy_name_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_name_item.setData(self.GROUPS_DUMMY_ROW_TEXT, self.GROUPS_ROLE_PREVIOUS_NAME)
        # Clears the 'selectable' flag:
        dummy_name_item.setFlags(
            QtCore.Qt.ItemFlag.ItemIsEnabled | QtCore.Qt.ItemFlag.ItemIsEditable
        )

        dummy_active_item = ModelItem()
        dummy_active_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_active_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)

        dummy_delete_item = ModelItem('')
        dummy_delete_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_delete_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)

        dummy_open_close_item = ModelItem()
        dummy_open_close_item.setData(True, self.GROUPS_ROLE_IS_DUMMY_ROW)
        dummy_open_close_item.setFlags(QtCore.Qt.ItemFlag.NoItemFlags)

        file_name_item.appendRow([dummy_name_item, dummy_active_item, dummy_delete_item, dummy_open_close_item])
        self.ui.treeView_groups.setExpanded(file_name_item.index(), True)
        self.globals_changed()
        self.do_model_sort()
        # If this changed the sort order, ensure the file item is visible:
        scroll_view_to_row_if_current(self.ui.treeView_groups, file_name_item)
        return globals_file

    def make_group_row(self, group_name):
        """Returns a new row representing one group in the groups tab, ready to be
        inserted into the model."""
        group_name_item = ModelItem(group_name)
        # We keep the previous name around so that we can detect what changed:
        group_name_item.setData(group_name, self.GROUPS_ROLE_PREVIOUS_NAME)
        # Sort column by name:
        group_name_item.setData(group_name, self.GROUPS_ROLE_SORT_DATA)

        group_active_item = ModelItem()
        group_active_item.setCheckState(QtCore.Qt.CheckState.Unchecked)
        # Sort column by CheckState - must keep this updated whenever the
        # checkstate changes:
        group_active_item.setData(QtCore.Qt.CheckState.Unchecked.value, self.GROUPS_ROLE_SORT_DATA)
        group_active_item.setEditable(False)
        group_active_item.setToolTip(
            'Whether or not the globals within this group should be used by runmanager for compilation.')

        group_delete_item = ModelItem()
        group_delete_item.setIcon(Icon(':qtutils/fugue/minus'))
        group_delete_item.setEditable(False)
        group_delete_item.setSelectable(False)
        group_delete_item.setToolTip('Delete globals group from file.')

        group_open_close_item = ModelItem()
        group_open_close_item.setIcon(Icon(':qtutils/fugue/plus'))
        group_open_close_item.setData(False, self.GROUPS_ROLE_GROUP_IS_OPEN)
        # Sort column by whether group is open - must keep this manually
        # updated when the state changes:
        group_open_close_item.setData(False, self.GROUPS_ROLE_SORT_DATA)
        group_open_close_item.setEditable(False)
        group_open_close_item.setToolTip('Load globals group into runmanager.')

        row = [group_name_item, group_active_item, group_delete_item, group_open_close_item]
        return row

    def close_globals_file(self, globals_file, confirm=True):
        item = self.groups_model.findItems(globals_file, column=self.GROUPS_COL_NAME)[0]
        child_name_items = [item.child(i, self.GROUPS_COL_NAME) for i in range(item.rowCount())]
        child_openclose_items = [item.child(i, self.GROUPS_COL_OPENCLOSE) for i in range(item.rowCount())]
        child_is_open = [child_item.data(self.GROUPS_ROLE_GROUP_IS_OPEN)
                         for child_item in child_openclose_items]
        if confirm and any(child_is_open):
            if not self.question_dialog(
                    f'Close {globals_file}? This will close '
                    f'{child_is_open.count(True)} currently open group(s).'):
                return
        to_close = [name_item for name_item, is_open in zip(child_name_items, child_is_open) if is_open]
        for name_item in to_close:
            group_name = name_item.text()
            self.close_group(globals_file, group_name)

        self.groups_model.removeRow(item.row())
        self.globals_changed()

    def copy_group(self, source_globals_file, source_group_name, dest_globals_file=None, delete_source_group=False):
        """Copy a group to ``dest_globals_file``, or to its own file if None, deleting
        the source group afterwards if ``delete_source_group``."""
        if delete_source_group and source_globals_file == dest_globals_file:
            return
        try:
            if delete_source_group:
                source_globals_file = self.ensure_editable_globals_file(source_globals_file)
            if dest_globals_file is None:
                dest_globals_file = source_globals_file
            dest_globals_file = self.ensure_editable_globals_file(dest_globals_file)
            if source_globals_file == dest_globals_file and delete_source_group:
                source_globals_file = dest_globals_file
            dest_group_name = runmanager.globals_file.copy_group(
                source_globals_file, source_group_name, dest_globals_file, delete_source_group
            )
        except Exception as e:
            self.error_dialog(str(e))
        else:
            (file_item,) = self.groups_model.findItems(
                dest_globals_file, column=self.GROUPS_COL_NAME
            )
            group_row = self.insert_group_row(file_item, dest_group_name)

            self.open_group(dest_globals_file, dest_group_name)
            name_item = group_row[self.GROUPS_COL_NAME]
            self.globals_changed()
            self.ui.treeView_groups.setCurrentIndex(name_item.index())

            if delete_source_group:
                self.delete_group(source_globals_file, source_group_name, confirm=False)

            # If this changed the sort order, ensure the group item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, name_item)

    def insert_group_row(self, file_item, group_name):
        """Add a group's row under its file's, above the dummy row, and return it."""
        group_row = self.make_group_row(group_name)
        file_item.insertRow(file_item.rowCount() - 1, group_row)
        self.do_model_sort()
        return group_row

    def new_group(self, globals_file, group_name, interactive=True):
        # A GUI edit has typed the name into the dummy row, which is reset
        # below. A remote one has not, so it finds the dummy row as it stands.
        typed_name = group_name if interactive else self.GROUPS_DUMMY_ROW_TEXT
        item = self.get_group_item_by_name(globals_file, typed_name, self.GROUPS_COL_NAME,
                                           previous_name=self.GROUPS_DUMMY_ROW_TEXT)
        # Two active groups of one name make the preparse open a dialog. The
        # operator can fix that; a remote caller could not even dismiss it, so
        # its group is left inactive instead:
        active = interactive or group_name not in self.get_active_groups(interactive=False)
        try:
            globals_file = self.ensure_editable_globals_file(globals_file)
            runmanager.new_group(globals_file, group_name)
        except Exception as e:
            if not interactive:
                raise
            self.error_dialog(str(e))
        else:
            group_row = self.insert_group_row(item.parent(), group_name)
            # Open the group and mark it active:
            self.open_group(globals_file, group_name)
            active_item = group_row[self.GROUPS_COL_ACTIVE]
            name_item = group_row[self.GROUPS_COL_NAME]
            if active:
                active_item.setCheckState(QtCore.Qt.CheckState.Checked)
            self.globals_changed()
            self.ui.treeView_groups.setCurrentIndex(name_item.index())
            # If this changed the sort order, ensure the group item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, name_item)
        finally:
            # Set the dummy row's text back ready for another group to be created:
            item.setText(self.GROUPS_DUMMY_ROW_TEXT)

    def open_group(self, globals_file, group_name, trigger_preparse=True):
        assert (globals_file, group_name) not in self.currently_open_groups  # sanity check
        group_tab = GroupTab(self.ui.tabWidget, globals_file, group_name)
        self.currently_open_groups[globals_file, group_name] = group_tab

        # Set the open/close state in the groups_model. itemChanged will be
        # emitted and self.on_groups_model_item_changed will handle updating
        # the other data roles, icons etc:
        openclose_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_OPENCLOSE)
        openclose_item.setData(True, self.GROUPS_ROLE_GROUP_IS_OPEN)
        # Trigger a preparse in light of this. A caller opening several groups passes
        # trigger_preparse=False to avoid one for each, and calls
        # self.globals_changed() itself.
        if trigger_preparse:
            self.globals_changed()

    def rename_group(self, globals_file, previous_group_name, new_group_name):
        item = self.get_group_item_by_name(globals_file, new_group_name, self.GROUPS_COL_NAME,
                                           previous_name=previous_group_name)
        try:
            globals_file = self.ensure_editable_globals_file(globals_file)
            runmanager.rename_group(globals_file, previous_group_name, new_group_name)
        except Exception as e:
            self.error_dialog(str(e))
            # Set the item text back to the old name, since the rename failed:
            item.setText(previous_group_name)
        else:
            item.setData(new_group_name, self.GROUPS_ROLE_PREVIOUS_NAME)
            item.setData(new_group_name, self.GROUPS_ROLE_SORT_DATA)
            self.do_model_sort()
            # If this changed the sort order, ensure the group item is still visible:
            scroll_view_to_row_if_current(self.ui.treeView_groups, item)
            group_tab = self.currently_open_groups.pop((globals_file, previous_group_name), None)
            if group_tab is not None:
                # Change labels and tooltips appropriately if the group is open:
                group_tab.set_file_and_group_name(globals_file, new_group_name)
                # Re-add it to the dictionary under the new name:
                self.currently_open_groups[globals_file, new_group_name] = group_tab

    def close_group(self, globals_file, group_name):
        group_tab = self.currently_open_groups.pop((globals_file, group_name), None)
        assert group_tab is not None  # Just in case
        group_tab.close()
        openclose_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_OPENCLOSE)
        openclose_item.setData(False, self.GROUPS_ROLE_GROUP_IS_OPEN)

    def delete_group(self, globals_file, group_name, confirm=True):
        if confirm:
            if not self.question_dialog(f"Delete the group '{group_name}'?"):
                return
        # If the group is open, close it:
        group_tab = self.currently_open_groups.get((globals_file, group_name))
        if group_tab is not None:
            self.close_group(globals_file, group_name)
        globals_file = self.ensure_editable_globals_file(globals_file)
        runmanager.globals_file.delete_group(globals_file, group_name)
        # Find the entry for this group in self.groups_model and remove it:
        name_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_NAME)
        name_item.parent().removeRow(name_item.row())
        self.globals_changed()

    def get_save_data(self):
        # Get the currently open files and active groups:
        files = self.group_active_items()
        h5_files_open = list(files)
        active_groups = [
            (globals_file, group_name)
            for globals_file, groups in files.items()
            for group_name, active_item in groups.items()
            if active_item.checkState() == QtCore.Qt.CheckState.Checked
        ]
        # Get the currently open groups:
        groups_open = []
        for i in range(self.ui.tabWidget.count()):
            tab_page = self.ui.tabWidget.widget(i)
            for (globals_file_name, group_name), group_tab in self.currently_open_groups.items():
                if group_tab.ui is tab_page:
                    groups_open.append((globals_file_name, group_name))
                    break
        # Get the labscript file, output folder, and whether the output folder
        # is default:
        current_labscript_file = self.ui.lineEdit_labscript_file.text()
        shot_output_folder = self.ui.lineEdit_shot_output_folder.text()
        is_using_default_shot_output_folder = (shot_output_folder == self.get_default_output_folder())
        # Only save the shot output folder if not using the default, that way
        # the folder updating as the day rolls over will not be detected as a
        # change to the save data:
        if is_using_default_shot_output_folder:
            shot_output_folder = ''

        send_to_runviewer = self.ui.checkBox_view_shots.isChecked()
        send_to_blacs = self.ui.checkBox_run_shots.isChecked()
        shuffle = self.ui.pushButton_shuffle.isChecked()

        # axes tab information
        axes = []
        for i in range(self.axes_model.rowCount()):
            name_item = self.axes_model.item(i, self.AXES_COL_NAME)
            shuffle_item = self.axes_model.item(i, self.AXES_COL_SHUFFLE)
            shuffle_state = shuffle_item.checkState()
            
            axes.append(
                (
                    name_item.data(self.AXES_ROLE_NAME),
                    1 if shuffle_state == QtCore.Qt.CheckState.Checked else 0,
                )
            )
        
        save_data = {'h5_files_open': h5_files_open,
                     'active_groups': active_groups,
                     'groups_open': groups_open,
                     'current_labscript_file': current_labscript_file,
                     'shot_output_folder': shot_output_folder,
                     'is_using_default_shot_output_folder': is_using_default_shot_output_folder,
                     'send_to_runviewer': send_to_runviewer,
                     'send_to_blacs': send_to_blacs,
                     'shuffle': shuffle,
                     'axes': axes,
                     'queue_state': self.queue_controller.get_save_data(),
                     'analysis_submission': self.analysis_submission.get_save_data()}
        save_data.update(self.geometry_save_data())
        return save_data

    def load_configuration(self, filename, at_startup=False):
        # Close all files:
        save_data = self.get_save_data()
        for globals_file in save_data['h5_files_open']:
            self.close_globals_file(globals_file, confirm=False)
        appconfig, save_target = load_appconfig(filename, return_save_path=True)
        runmanager_config = appconfig.get('runmanager_state', {})

        has_been_a_warning = [False]
        def warning(message):
            if not has_been_a_warning[0]:
                has_been_a_warning[0] = True
                self.output_box.output('\n')
            self.output_box.output(f'Warning: {message}\n', red=True)

        opened_globals_files = {}
        for globals_file in runmanager_config.get('h5_files_open', []):
            if os.path.exists(globals_file):
                try:
                    opened_globals_file = self.open_globals_file(globals_file)
                    opened_globals_files[globals_file] = opened_globals_file
                    self.last_opened_globals_folder = os.path.dirname(opened_globals_file)
                except Exception: # noqa: BLE001 redirect all exceptions to appropriate thread
                    raise_exception_in_thread(sys.exc_info())
                    continue
            else:
                self.output_box.output(f'\nWarning: globals file {globals_file} no longer exists\n', red=True)

        for globals_file, group_name in runmanager_config.get('active_groups', []):
            globals_file = opened_globals_files.get(globals_file, globals_file)
            try:
                group_active_item = self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_ACTIVE)
                group_active_item.setCheckState(QtCore.Qt.CheckState.Checked)
            except LookupError:
                warning(f"previously active group '{group_name}' in {globals_file} no longer exists")

        for globals_file, group_name in runmanager_config.get('groups_open', []):
            globals_file = opened_globals_files.get(globals_file, globals_file)
            # First check if it exists:
            try:
                self.get_group_item_by_name(globals_file, group_name, self.GROUPS_COL_NAME)
            except LookupError:
                warning(f"previously open group '{group_name}' in {globals_file} no longer exists")
            else:
                self.open_group(globals_file, group_name)

        current_labscript_file = runmanager_config.get('current_labscript_file')
        if current_labscript_file is not None:
            if os.path.exists(current_labscript_file):
                self.ui.lineEdit_labscript_file.setText(current_labscript_file)
                self.last_opened_labscript_folder = os.path.dirname(current_labscript_file)
            elif current_labscript_file:
                warning(f'previously selected labscript file {current_labscript_file} no longer exists')

        shot_output_folder = runmanager_config.get('shot_output_folder')
        if shot_output_folder is not None:
            self.ui.lineEdit_shot_output_folder.setText(shot_output_folder)
            self.last_selected_shot_output_folder = os.path.dirname(shot_output_folder)

        if runmanager_config.get('is_using_default_shot_output_folder', False):
            default_output_folder = self.get_default_output_folder()
            self.ui.lineEdit_shot_output_folder.setText(default_output_folder)
            self.last_selected_shot_output_folder = os.path.dirname(default_output_folder)

        send_to_runviewer = runmanager_config.get('send_to_runviewer')
        if send_to_runviewer is not None:
            self.ui.checkBox_view_shots.setChecked(send_to_runviewer)

        send_to_blacs = runmanager_config.get('send_to_blacs')
        if send_to_blacs is not None:
            self.ui.checkBox_run_shots.setChecked(send_to_blacs)
        
        # clear the axes model first
        if self.axes_model.rowCount():
            self.axes_model.removeRows(0, self.axes_model.rowCount())

        # Set the master shuffle button first, so that if no axes load afterwards (the
        # saved globals may no longer be expansion globals) it still has an
        # approximate state to apply to whatever globals are expanded.
        if runmanager_config.get('shuffle', False):
            self.ui.pushButton_shuffle.setChecked(True)

        # Load the axes states (order and shuffle), which also brings the shuffle
        # button into line with them, since its tri-state is not saved:
        axes = runmanager_config.get('axes')
        if axes is not None and isinstance(axes, list):
            for name, shuffle in axes:
                self.add_item_to_axes_model(name, shuffle)
            self.update_axes_indentation() 

        queue_state = runmanager_config.get('queue_state')
        if isinstance(queue_state, dict):
            # The queue's shots come back only when runmanager starts. A load
            # during a session applies the queue's settings and leaves its shots:
            self.queue_manager.restore_save_data(queue_state, restore_rows=at_startup)
            restored_queue_state = self.queue_controller.get_queue_state()
            if at_startup and restored_queue_state['n_items']:
                # Restored rows that were not compiled yet are compiled ahead,
                # as they would have been after an Engage:
                self.queue_manager.compile_ahead()
            self.ui.lineEdit_default_labscript_file.setText(
                restored_queue_state['default_labscript_file']
            )
            self.queue_compile_mode_combo.setCurrentIndex(
                self.queue_compile_mode_combo.findData(restored_queue_state['compile_mode'])
            )
            self.queue_empty_policy_combo.setCurrentIndex(
                self.queue_empty_policy_combo.findData(restored_queue_state['empty_queue_policy'])
            )
            self.queue_pause_button.setChecked(restored_queue_state['paused'])

        self.analysis_submission.restore_save_data(
            runmanager_config.get('analysis_submission')
        )
        self.restore_geometry(runmanager_config)
        self.configuration_loaded(save_target)

    def compile_run_file(self, labscript_file, run_file):
        """Return ``(success, error)``: whether the file compiled and, if not, its traceback."""
        with self.compiler_lock:
            self.child_ready.wait()
            self.compile_interrupted.clear()
            success, error = runmanager.compile_with(
                self.to_child, self.from_child, labscript_file, run_file
            )
            interrupted = self.compile_interrupted.is_set()
        if not success and not interrupted:
            self.set_output_tab_error(
                f'Compilation of {os.path.basename(run_file)} failed. '
                'The output shows the error.'
            )
        return success, error

    def test_compile(self):
        """Compile the window's first shot into a scratch file, queueing nothing."""
        labscript_file = inmain(self.ui.lineEdit_labscript_file.text)
        if not labscript_file:
            raise ValueError('No labscript file is selected.')
        active_groups = inmain(self.get_active_groups, interactive=False)
        _, frozen = inmain(self.expand_pending_shots)[0]
        # Claims no sequence index, which the next real sequence keeps:
        sequence_attrs, _, _ = runmanager.new_sequence_details(
            labscript_file, config=self.exp_config, increment_sequence_index=False
        )
        record = {
            'path': os.path.join(self.scratch_dir.name, 'test_compile.h5'),
            'labscript_file': labscript_file,
            'active_groups': active_groups,
            'frozen_globals': frozen,
            'sequence_attrs': sequence_attrs,
            'run_no': 0,
            'n_runs': 1,
            'send_to_runviewer': False,
        }
        success, error = self.queue_manager.compile_shot(record)
        return {'success': success, 'error': error, 'path': record['path']}

    def parse_globals(self, active_groups, raise_exceptions=True, expand_globals=True, expansion_order = None):
        sequence_globals = runmanager.get_globals(active_groups)
        evaled_globals, global_hierarchy, expansions, deprecations = (
            runmanager.evaluator._evaluate_globals(sequence_globals, raise_exceptions)
        )
        if expand_globals:
            shots = runmanager.expand_globals(sequence_globals, evaled_globals, expansion_order)
        else:
            shots = []
        return sequence_globals, shots, evaled_globals, global_hierarchy, expansions, deprecations

    def guess_expansion_modes(self, active_groups, evaled_globals, global_hierarchy, expansions):
        """Guess expansion types for globals, and store the changed ones in the globals file.

        A global referencing an iterable global is zipped with it, not outer
        producted. Returns whether any stored type changed; the caller then parses
        again with the new types, until this returns False. The previous parse is
        kept in the ``previous_*`` attributes, to detect changes."""

        # An error is not a value to guess from, and can hide what a global
        # depends on, so an error never changes a stored expansion:
        for group_name in evaled_globals:
            for global_name in evaled_globals[group_name]:
                if isinstance(evaled_globals[group_name][global_name], Exception):
                    return False
        def store_guess(group_name, global_name, guess):
            # A legacy HDF5 globals file is read-only until the user converts it,
            # so no guess is stored in one:
            filename = active_groups[group_name]
            if runmanager.globals_file.is_legacy_hdf5(filename):
                return False
            try:
                runmanager.globals_file.set_field(
                    filename, group_name, global_name, 'expansion', guess
                )
            except runmanager.globals_file.GlobalsFileError:
                # Deleted or renamed after the globals were read; the parse that
                # follows that change updates it.
                return False
            return True

        # Did the guessed expansion type for any of the globals change?
        expansion_types_changed = False
        expansion_types = {}
        for group_name in evaled_globals:
            for global_name in evaled_globals[group_name]:
                if not expansions[global_name]:
                    # Not scanned: its stored expansion is kept for when it is.
                    continue
                new_value = evaled_globals[group_name][global_name]
                try:
                    previous_value = self.previous_evaled_globals[group_name][global_name]
                except KeyError:
                    if group_name in self.previous_evaled_globals:
                        # The global was created since the previous parse. A previous
                        # value of 0 gives a previous guess of '', so that the
                        # expansion type is guessed from the new value.
                        previous_value = 0
                    else:
                        # The group has just been loaded or activated, and the
                        # expansion types stored in the globals file are kept.
                        previous_value = new_value

                new_guess = runmanager.guess_expansion_type(new_value)
                previous_guess = runmanager.guess_expansion_type(previous_value)

                if new_guess == 'outer':
                    expansion_types[global_name] = {'previous_guess': previous_guess,
                                                    'new_guess': new_guess,
                                                    'group_name': group_name}
                elif new_guess != previous_guess:
                    if store_guess(group_name, global_name, new_guess):
                        expansions[global_name] = new_guess
                        expansion_types_changed = True

        # recursively find dependencies and add them to a zip group!
        def find_dependencies(global_name, global_hierarchy, expansion_types):
            results = set()
            for name, dependencies in global_hierarchy.items():
                if name in expansion_types and global_name in dependencies:
                    results.add(name)
                    results = results.union(find_dependencies(name, global_hierarchy, expansion_types))
            return results

        def global_depends_on_global_with_outer_product(global_name, global_hierarchy, expansions):
            if global_name not in global_hierarchy:
                return False
            else:
                for dependency in global_hierarchy[global_name]:
                    if expansions[dependency]:
                        return True

        def set_expansion_type_guess(expansion_types, expansions, global_name, expansion_to_set, new=True):
            if new:
                key = 'new_guess'
            else:
                key = 'previous_guess'

            # only do this if the expansion is *not* already set to a specific zip group
            if global_name in expansions and expansions[global_name] != '' and expansions[global_name] != 'outer':
                expansion_types[global_name][key] = expansions[global_name]
            else:
                expansion_types[global_name][key] = expansion_to_set
                expansions[global_name] = expansion_to_set
            
        
        for global_name in sorted(expansion_types):
            # we have a global that does not depend on anything that has an
            # expansion type of 'outer'
            if not global_depends_on_global_with_outer_product(global_name, global_hierarchy, expansions):
                current_dependencies = find_dependencies(global_name, global_hierarchy, expansion_types)

                # if this global has other globals that use it, then add them
                # all to a zip group with the name of this global
                if current_dependencies:
                    for dependency in current_dependencies:                        
                        set_expansion_type_guess(expansion_types, expansions, dependency,  str(global_name))
                            
                    set_expansion_type_guess(expansion_types, expansions, global_name,  str(global_name))

        for global_name in sorted(self.previous_expansion_types):
            if not global_depends_on_global_with_outer_product(
                global_name, self.previous_global_hierarchy, self.previous_expansions
            ):
                old_dependencies = find_dependencies(global_name, self.previous_global_hierarchy, self.previous_expansion_types)
                # if this global has other globals that use it, then add them
                # all to a zip group with the name of this global
                if old_dependencies:
                    for dependency in old_dependencies:
                        if dependency in expansion_types:
                            set_expansion_type_guess(expansion_types, self.previous_expansions, dependency, str(global_name), new=False)
                    if global_name in expansion_types:
                        set_expansion_type_guess(expansion_types, self.previous_expansions, global_name, str(global_name), new=False)

        for global_name, guesses in expansion_types.items():
            if guesses['new_guess'] != guesses['previous_guess']:
                if store_guess(guesses['group_name'], global_name, str(guesses['new_guess'])):
                    expansions[global_name] = guesses['new_guess']
                    expansion_types_changed = True

        self.previous_evaled_globals = evaled_globals
        self.previous_global_hierarchy = global_hierarchy
        self.previous_expansion_types = expansion_types
        self.previous_expansions = expansions

        return expansion_types_changed

    def name_run_files(
        self,
        labscript_file,
        output_folder,
        shots,
        indexed_path_base=None,
        index_start=None,
        sequence_attrs=None,
        name_format=None,
        increment_sequence_index=True,
    ):
        """Name and number the shot files of a batch, without writing them.

        Returns a record per entry of ``shots``. ``indexed_path_base`` is a shot
        whose sequence the batch joins, with ``sequence_attrs`` that sequence's;
        with none, the batch starts a sequence, which claims a sequence index if
        ``increment_sequence_index``."""
        if indexed_path_base is not None:
            # The shot being added to supplies the folder, filename stem and
            # sequence a new sequence would be given, so new_sequence_details
            # is not asked: it would claim a sequence index never used.
            run_files = self.reindex_run_file_infos(
                [
                    {
                        'shot_globals': shot_globals,
                        'sequence_attrs': sequence_attrs,
                    }
                    for shot_globals in shots
                ],
                indexed_path_base,
                index_start=index_start,
                name_format=name_format,
            )
        else:
            using_default = output_folder == self.previous_default_output_folder
            # A folder the user chose is used as it is. Only the default one has
            # a subdirectory that is a template, so that is the one it separates.
            sequence_attrs, default_output_dir, filename_prefix = (
                runmanager.new_sequence_details(
                    labscript_file,
                    config=self.exp_config,
                    increment_sequence_index=increment_sequence_index,
                    subdirectory_in_prefix=using_default,
                )
            )
            if using_default:
                # The UI's default folder is refreshed only once a second, so
                # take the race-free one from new_sequence_details in case the
                # date or sequence index has moved on.
                output_folder = default_output_dir
            self.check_output_folder_update()
            run_files = runmanager.make_run_files(
                output_folder,
                None,
                shots,
                sequence_attrs,
                filename_prefix,
                return_infos=True,
                create_files=False,
            )
            # How a later batch joining this sequence names its shots.
            name_format = (output_folder, filename_prefix)
            run_files = [dict(info, name_format=name_format) for info in run_files]
        logger.debug(run_files)
        return run_files

    def prepare_queue_shot(self, item, default_globals=False):
        active_groups = item.get('active_groups') or inmain(
            self.get_active_groups, interactive=False
        )
        sequence_globals, runglobals = runmanager.get_queue_compile_globals(
            active_groups, item.get('frozen_globals'), default_globals=default_globals
        )
        runmanager.make_single_run_file(
            item['path'],
            sequence_globals,
            runglobals,
            item['sequence_attrs'],
            item['run_no'],
            item['n_runs'],
            # A record with no id is written without the attribute, which tells a
            # shot nobody submitted from one that was. Every queue row has an id;
            # a record that never became one may not.
            shot_id=item.get('shot_id'),
        )

    def send_to_runviewer(self, run_file):
        # Called on whichever thread compiled the shot, so with a client of its own:
        runviewer = RunviewerClient(host='localhost', timeout=0.5)
        agnostic_path = shared_drive.path_to_agnostic(run_file)
        try:
            runviewer.say_hello(timeout=1)
        except Exception:
            logger.info('runviewer not running, attempting to start...')
            # Runviewer not running, start it:
            if os.name == 'nt':
                scripts_dir = desktop_app.environment.get_scripts_dir('runviewer')
                start_daemon([str(scripts_dir / 'runviewer-gui')])
            else:
                start_daemon([sys.executable, '-m', 'runviewer'])
            try:
                runviewer.say_hello(timeout=15)
            except Exception as e:
                self.output_box.output(f'Couldn\'t submit shot to runviewer: {e!s}\n\n', red=True)
                return

        try:
            runviewer.add_shot(agnostic_path)
            self.output_box.output(f'Shot {os.path.basename(run_file)} sent to runviewer.\n')
        except Exception as e:
            self.output_box.output(f'Couldn\'t submit shot to runviewer: {e!s}\n\n', red=True)

    def discard_default_shot(self):
        """Drop a prepared default shot that is not going to be handed over.

        That is when the queue has taken over, the empty-queue policy no longer
        calls for one, or the globals have changed: its globals were read when it
        was made, so BLACS must not get it as though they were current."""
        with self._default_shot_lock:
            row = self._default_shot_ready
            self._default_shot_ready = None
        if row is not None:
            self.queue_manager.delete_shot_files([row['path']])

    def take_default_shot(self, labscript_file):
        """A compiled default shot's queue row, or None while one is being produced.

        At most one is produced at a time; a later request collects it once ready."""
        # A default shot started before the latest change to the globals read
        # the old ones. Drop it now if it is ready; one still compiling is
        # dropped by the request that finds it ready:
        if self._default_shot_globals_changes != self.globals_changes:
            self.discard_default_shot()
        with self._default_shot_lock:
            row = self._default_shot_ready
            if row is not None:
                self._default_shot_ready = None
                return row
            if self._default_shot_preparing:
                return None
            self._default_shot_preparing = True
            self._default_shot_globals_changes = self.globals_changes
        send_to_runviewer = inmain(self.ui.checkBox_view_shots.isChecked)
        thread = threading.Thread(
            target=self.prepare_default_shot,
            args=(labscript_file, send_to_runviewer),
            daemon=True,
        )
        thread.start()
        return None

    def prepare_default_shot(self, labscript_file, send_to_runviewer):
        """Write and compile one default shot, and leave it ready to hand over."""
        row = None
        run_file = None
        try:
            active_groups = inmain(self.get_active_groups, interactive=False)
            _, runglobals = runmanager.get_queue_compile_globals(
                active_groups, default_globals=True
            )
            sequence_attrs, output_folder, filename_prefix = (
                runmanager.new_sequence_details(
                    labscript_file,
                    config=self.exp_config,
                    default=True,
                    subdirectory_in_prefix=True,
                )
            )
            key = (sequence_attrs['sequence_id'], sequence_attrs['sequence_index'])
            # After a restart, numbered on from the folder's files:
            start = self.sequences[key][2] if key in self.sequences else None
            run_file, default_index = runmanager.next_run_file(
                output_folder, filename_prefix, runglobals, start
            )
            self.sequences[key] = (run_file, sequence_attrs, default_index + 1, None)
            # No shot_id, deliberately: a file without one is visibly not a shot
            # anybody submitted, which is what a caller matching results to what
            # it asked for needs. Numbered within the day's default sequence.
            shot = {
                'path': run_file,
                'labscript_file': labscript_file,
                'active_groups': active_groups,
                'sequence_attrs': sequence_attrs,
                'run_no': default_index,
                'n_runs': default_index + 1,
                'send_to_runviewer': send_to_runviewer,
            }
            if not self.queue_manager.compile_shot(shot, default_globals=True)[0]:
                raise RuntimeError(
                    f'Compilation failed for {os.path.basename(run_file)}'
                )
            row = dict(shot, compiled=True, default_shot=True)
        except Exception as e:
            self.output_box.output(
                f'Could not produce a default shot: {e}\n', red=True
            )
            if run_file is not None:
                self.queue_manager.delete_shot_files([run_file])
        finally:
            with self._default_shot_lock:
                self._default_shot_ready = row
                self._default_shot_preparing = False

    def queue_exchange(self, outcome=None, request_shot=True):
        """Apply BLACS's outcome for the shot it was offered, then offer one.

        The outcome is applied first, so that one exchange can retire the row BLACS
        has just finished and offer the next. offer_shot() can then reclaim any row
        still marked running, which this BLACS is demonstrably not executing."""
        no_shot = {'state': PROVIDER_NONE, 'shot_id': None, 'path': None}
        try:
            if outcome is not None:
                self.apply_shot_outcome(outcome)
            if not request_shot:
                return no_shot
            return self.offer_shot()
        except Exception as exc:
            # Answered rather than raised: the server hands a raised exception
            # back to BLACS, which takes it for an unreachable runmanager and
            # would resend the outcome for ever over a fault on this side.
            self.output_box.output(
                f'Runmanager could not answer BLACS: {exc}\n', red=True
            )
            return no_shot

    def apply_shot_outcome(self, outcome):
        """Record how BLACS says the shot it was offered turned out.

        Only completed shots are submitted for analysis, with the file BLACS
        actually ran, which is not the queued one when it made a fresh copy to
        re-run a shot that already held data. An outcome runmanager cannot read
        -- not a mapping, naming no shot, or with an unknown status -- is refused
        and reported, leaving the queue as it was.

        The completion is reported before the row is retired: a failure between
        them leaves the row queued, for BLACS's next request to reclaim and run
        again, which costs a run of the apparatus but not its data."""
        fields = outcome if isinstance(outcome, dict) else {}
        shot_id = str(fields.get('shot_id') or '')
        status = str(fields.get('status', ''))
        if not shot_id or status not in SHOT_OUTCOME_STATUSES:
            # Refused rather than raised: a refusal returns normally, so the
            # exchange goes on to offer_shot. A raise would end it at
            # queue_exchange's guard, offering nothing until BLACS asks again.
            self.output_box.output(
                f'BLACS sent a shot outcome runmanager could not read: {outcome}\n',
                red=True,
            )
            return
        message = str(fields.get('message', ''))
        if status != BLACS_COMPLETED:
            self.queue_manager.shot_finished(shot_id, status, message)
            return
        agnostic_path = fields.get('path')
        agnostic_path = str(agnostic_path) if agnostic_path else ''
        if not agnostic_path:
            # BLACS named no file, so use the row's own path, if a row matches. Read
            # while the row is still in the queue, as the submission below is.
            queued_path = self.queue_controller.get_shot_path(shot_id)
            if queued_path:
                agnostic_path = shared_drive.path_to_agnostic(queued_path)
        if agnostic_path:
            self.queue_controller.expect_lyse(
                shot_id, shared_drive.path_to_local(agnostic_path)
            )
            self.analysis_submission.notify_shot_complete(agnostic_path)
        record = self.queue_manager.shot_finished(shot_id, status, message)
        if record is None:
            # The row can be gone because runmanager restarted while BLACS ran the
            # shot, or because a lost reply is being sent again for a row already
            # retired. The two cannot be told apart, and the queue needs nothing.
            self.output_box.output(
                f'BLACS reported shot {shot_id} as completed, but no queued shot has '
                'that id; the queue is unchanged.\n',
                red=True,
            )

    def offer_shot(self):
        """Offer BLACS a shot if there is one.

        Returns the exchange response, with the shot's id and agnostic path when
        one is offered."""
        no_shot = {'state': PROVIDER_NONE, 'shot_id': None, 'path': None}
        if self.queue_controller.get_queue_state()['paused']:
            # Pause governs only this runmanager's queue, so the answer is "nothing
            # for you", not "stop": a second runmanager sharing one BLACS must not
            # be able to stop it. Checked first, so nothing is chosen or marked running.
            return {'state': PROVIDER_PAUSED, 'shot_id': None, 'path': None}
        item = self.queue_manager.offer_next()
        if item is None:
            # A queued shot not yet compiled stays at the head and is compiled off
            # this thread, so that a slow compile does not hold up the server or
            # lose the shot when BLACS stops waiting. BLACS asks again shortly:
            if self.queue_manager.compile_next_in_background():
                # Pending rather than none, so that BLACS can wait for this shot
                # instead of running its local override in the gap:
                return dict(no_shot, state=PROVIDER_PENDING)
            queue_state = self.queue_controller.get_queue_state()
            labscript_file = queue_state['default_labscript_file']
            # No default shot when the policy does not ask for one, there is no
            # labscript file, or the queue is not empty after all: it can gain work
            # since the offer attempt above, which a default shot would queue behind.
            if (
                queue_state['empty_queue_policy'] != EMPTY_QUEUE_DEFAULT_LABSCRIPT
                or not labscript_file
                or queue_state['n_items']
            ):
                self.discard_default_shot()
                # The queue is empty between batches; the anchor stands.
                return no_shot
            if not os.path.isfile(labscript_file):
                raise RuntimeError(
                    f'Default-shot labscript file does not exist: {labscript_file}'
                )
            # Producing a default shot evaluates the globals, writes the shot file and
            # compiles it, so it happens off this thread, as a queued shot's compile
            # does, to not hold up the server or finish into a client that left.
            row = self.take_default_shot(labscript_file)
            if row is None:
                return no_shot
            # It joins the queue as an ordinary row and is offered like one, so the
            # queue's own rules show, retry, delete and analyse it, and a red one
            # at the head stops a second being made.
            self.queue_manager.enqueue([row])
            item = self.queue_manager.offer_next()
            if item is None:
                return no_shot
        else:
            # The queue has taken over, so any default shot prepared while it
            # was empty is now out of date:
            self.discard_default_shot()
        agnostic_path = shared_drive.path_to_agnostic(item['path'])
        if not item['default_shot']:
            self.queue_manager.set_last_sent_from_queue(
                agnostic_path, item['sequence_attrs']
            )
        return {
            'state': PROVIDER_SHOT,
            'shot_id': item['shot_id'],
            'path': agnostic_path,
        }


class RunmanagerServer(ZMQServer):
    server = 'runmanager'
    default_port = DEFAULT_PORT

    def __init__(self, app, **kwargs):
        self.app = app
        super().__init__(**kwargs)

    # A global's fields as a remote caller names them, and as its file does.
    GLOBAL_FIELDS = {
        'value': 'default',
        'units': 'units',
        'scan': 'scan',
        'scan_enabled': 'scan_enabled',
        'jit_enabled': 'jit_enabled',
        'expansion': 'expansion',
    }

    def _get_active_global_locations(self, names=None):
        active_groups = inmain(self.app.get_active_groups, interactive=False)
        globals_details = runmanager.globals_file.get_globals_details(active_groups)
        locations = {}
        groups_of = {}
        for group_name, group_globals in globals_details.items():
            for global_name in group_globals:
                groups_of.setdefault(global_name, []).append(group_name)
                locations.setdefault(global_name, (active_groups[group_name], group_name))
        # Only the globals the caller reads or writes must be defined once. A
        # getter of all of them reads every one.
        for global_name in (locations if names is None else names):
            if len(groups_of.get(global_name, ())) > 1:
                raise RuntimeError(
                    f'Global {global_name} is defined in multiple active groups: '
                    f"{' and '.join(groups_of[global_name])}"
                )
        return active_groups, globals_details, locations

    def _get_global_field_values(self, field):
        _, globals_details, locations = self._get_active_global_locations()
        values = {}
        for global_name, (_, group_name) in locations.items():
            values[global_name] = globals_details[group_name][global_name][field]
        return values

    def _get_expression_field_values(self, field, raw=False):
        active_groups, globals_details, locations = self._get_active_global_locations()
        sequence_globals = {group_name: {} for group_name in active_groups}
        for global_name, (_, group_name) in locations.items():
            expression = globals_details[group_name][global_name][field]
            sequence_globals[group_name][global_name] = (expression, '', '')
        return self._merge_groups(sequence_globals, raw)

    @staticmethod
    def _merge_groups(sequence_globals, raw):
        if raw:
            return runmanager.flatten_globals(sequence_globals)
        evaled_globals, _, _, _ = runmanager.evaluator._evaluate_globals(
            sequence_globals, raise_exceptions=False
        )
        return runmanager.flatten_globals(evaled_globals, evaluated=True)

    @staticmethod
    def _coerce_remote_boolean(value, name):
        if isinstance(value, bool):
            return value
        if isinstance(value, int) and value in (0, 1):
            return bool(value)
        raise TypeError(f'{name} must be a bool, not {value.__class__.__name__}')

    @staticmethod
    def _coerce_remote_text(value, name):
        if isinstance(value, str):
            return value
        raise TypeError(f'{name} must be a str, not {value.__class__.__name__}')

    @staticmethod
    def _coerce_remote_expression(value, name):
        return runmanager.expander.value_expression(value)

    @staticmethod
    def _with_trailing_comment(expression, previous):
        """``expression``, with the comment ``previous`` ends with if it has none."""
        comments = runmanager.find_comments(previous)
        if comments and not runmanager.find_comments(expression):
            comment_start, comment_end = comments[-1]
            if comment_end == len(previous):
                expression += previous[comment_start:comment_end]
        return expression

    @staticmethod
    def _check_before_writing(locations, globals):
        # Refused before any write: a batch must not be half applied, and the
        # Convert dialog would stop the server answering.
        for global_name in globals:
            if global_name not in locations:
                raise ValueError(
                    f"Global {global_name} not found in any active group"
                )
        for path in {locations[global_name][0] for global_name in globals}:
            RunmanagerServer._refuse_legacy_hdf5(path)

    @staticmethod
    def _refuse_legacy_hdf5(path):
        if runmanager.globals_file.is_legacy_hdf5(path):
            raise ValueError(
                f'{path} is a legacy HDF5 globals file. Convert it to TOML in '
                'runmanager first.'
            )

    @inmain_decorator()
    def _set_field_values(self, field, globals, coerce, skip_missing=False, keep_comment=False):
        _, _, locations = self._get_active_global_locations(globals)
        skipped = []
        if skip_missing:
            skipped = [name for name in globals if name not in locations]
            globals = {
                name: value for name, value in globals.items() if name in locations
            }
        globals = {
            name: coerce(value, f'global {name}') for name, value in globals.items()
        }
        self._check_before_writing(locations, globals)
        try:
            for global_name, new_value in globals.items():
                globals_file, group_name = locations[global_name]
                previous_value = runmanager.globals_file.get_field(
                    globals_file, group_name, global_name, field
                )
                if keep_comment:
                    new_value = self._with_trailing_comment(new_value, previous_value)
                try:
                    group_tab = self.app.currently_open_groups[globals_file, group_name]
                except KeyError:
                    runmanager.globals_file.set_field(
                        globals_file, group_name, global_name, field, new_value
                    )
                else:
                    group_tab.change_global(
                        global_name, field, previous_value, new_value, interactive=False
                    )
        finally:
            self.app.globals_changed()
        return skipped

    def handle_get_values(self, raw=False):
        return self._get_expression_field_values('default', raw=raw)

    def handle_get_globals(self, raw=False):
        active_groups = inmain(self.app.get_active_groups, interactive=False)
        return self._merge_groups(runmanager.get_globals(active_groups), raw)

    def handle_get_scans(self, raw=False):
        return self._get_expression_field_values('scan', raw=raw)

    def handle_get_scan_enabled(self):
        return self._get_global_field_values('scan_enabled')

    def handle_get_jit_enabled(self):
        return self._get_global_field_values('jit_enabled')

    def handle_get_expansions(self):
        # The preparse guesses the expansion of a global whose type changed.
        self.app.wait_until_preparse_complete()
        return self._get_global_field_values('expansion')

    def handle_get_units(self):
        return self._get_global_field_values('units')

    @inmain_decorator()
    def handle_get_groups(self):
        return {
            globals_file: {
                group_name: item.checkState() == QtCore.Qt.CheckState.Checked
                for group_name, item in groups.items()
            }
            for globals_file, groups in self.app.group_active_items().items()
        }

    def handle_set_values(self, globals, raw=False, skip_missing=False, preserve_comments=True):
        return self._set_field_values(
            'default',
            globals,
            self._coerce_remote_text if raw else self._coerce_remote_expression,
            skip_missing,
            keep_comment=preserve_comments and not raw,
        )

    def handle_set_scans(self, globals, raw=False, skip_missing=False, preserve_comments=True):
        return self._set_field_values(
            'scan',
            globals,
            self._coerce_remote_text if raw else self._coerce_remote_expression,
            skip_missing,
            keep_comment=preserve_comments and not raw,
        )

    def handle_set_scan_enabled(self, globals):
        return self._set_field_values(
            'scan_enabled',
            globals,
            self._coerce_remote_boolean,
        )

    def handle_set_jit_enabled(self, globals):
        return self._set_field_values(
            'jit_enabled',
            globals,
            self._coerce_remote_boolean,
        )

    def handle_set_expansions(self, globals, skip_missing=False):
        return self._set_field_values(
            'expansion',
            globals,
            self._coerce_remote_text,
            skip_missing,
        )

    @inmain_decorator()
    def handle_set_groups_active(self, groups):
        items = self.app.group_active_items()
        active = {
            (globals_file, group_name): item.checkState() == QtCore.Qt.CheckState.Checked
            for globals_file, file_items in items.items()
            for group_name, item in file_items.items()
        }
        changes = []
        # Refused before any is changed, so a batch is not half applied.
        for globals_file, states in groups.items():
            for group_name, state in states.items():
                state = self._coerce_remote_boolean(state, f'group {group_name}')
                if group_name not in items.get(globals_file, {}):
                    raise ValueError(
                        f'Group {group_name} not found in open globals file {globals_file}'
                    )
                active[globals_file, group_name] = state
                changes.append((items[globals_file][group_name], state))
        # Two active groups of one name would make the preparse open a modal
        # dialog, which would hold this server until someone dismissed it.
        names = [group_name for (_, group_name), on in active.items() if on]
        repeated = sorted({name for name in names if names.count(name) > 1})
        if repeated:
            raise ValueError(
                'Cannot leave two active groups with the same name: '
                f"{', '.join(repeated)}."
            )
        for item, state in changes:
            item.setCheckState(QtCore.Qt.CheckState.Checked if state else QtCore.Qt.CheckState.Unchecked)

    @inmain_decorator()
    def handle_new_globals_file(self, path):
        path = os.path.abspath(self._coerce_remote_text(path, 'path'))
        if os.path.exists(path):
            raise FileExistsError(f'{path} already exists.')
        runmanager.globals_file.new_globals_file(path)
        return self.app.open_globals_file(path)

    @inmain_decorator()
    def handle_open_globals_file(self, path):
        path = os.path.abspath(self._coerce_remote_text(path, 'path'))
        if not os.path.isfile(path):
            raise FileNotFoundError(f'No such file {path}.')
        return self.app.open_globals_file(path)

    @inmain_decorator()
    def handle_close_globals_file(self, path):
        if path not in self.app.group_active_items():
            raise ValueError(f'{path} is not an open globals file.')
        self.app.close_globals_file(path, confirm=False)

    @inmain_decorator()
    def handle_new_group(self, globals_file, group_name):
        group_name = self._coerce_remote_text(group_name, 'group_name')
        if globals_file not in self.app.group_active_items():
            raise ValueError(f'{globals_file} is not an open globals file.')
        self._refuse_legacy_hdf5(globals_file)
        self.app.new_group(globals_file, group_name, interactive=False)
        item = self.app.group_active_items()[globals_file][group_name]
        return item.checkState() == QtCore.Qt.CheckState.Checked

    @inmain_decorator()
    def handle_new_globals(self, globals_file, group_name, globals):
        records = {}
        for global_name, fields in globals.items():
            record = records[global_name] = {}
            for field, value in fields.items():
                if field not in self.GLOBAL_FIELDS:
                    raise ValueError(f'Global {global_name} has no field {field}.')
                if field in ('scan_enabled', 'jit_enabled'):
                    coerce = self._coerce_remote_boolean
                else:
                    coerce = self._coerce_remote_text
                record[self.GLOBAL_FIELDS[field]] = coerce(
                    value, f'{field} of global {global_name}'
                )
            if record.get('scan_enabled') and record.get('jit_enabled'):
                raise ValueError(
                    f'Global {global_name} cannot have both Scan? and JIT? ticked.'
                )
        self._refuse_legacy_hdf5(globals_file)
        runmanager.new_globals(globals_file, group_name, records)
        stored = runmanager.globals_file.get_globals_details({group_name: globals_file})
        created = {name: stored[group_name][name] for name in records}
        group_tab = self.app.currently_open_groups.get((globals_file, group_name))
        if group_tab is not None:
            group_tab.add_global_rows(created)
            group_tab.do_model_sort()
        self.app.globals_changed()
        return {
            name: {field: record[key] for field, key in self.GLOBAL_FIELDS.items()}
            for name, record in created.items()
        }

    @inmain_decorator()
    def handle_delete_global(self, global_name):
        _, _, locations = self._get_active_global_locations([global_name])
        self._check_before_writing(locations, {global_name: ''})
        globals_file, group_name = locations[global_name]
        try:
            group_tab = self.app.currently_open_groups[globals_file, group_name]
        except KeyError:
            runmanager.globals_file.delete_global(globals_file, group_name, global_name)
            self.app.globals_changed()
        else:
            group_tab.delete_global(global_name, confirm=False)
        return global_name

    def handle_engage(self, submission_mode=SUBMISSION_MODE_NEW_SEQUENCE):
        self.app.wait_until_preparse_complete()
        return [shot_receipt(record) for record in inmain(self.app.engage, submission_mode)]

    @inmain_decorator()
    def handle_abort(self):
        self.app.on_abort_clicked()

    @inmain_decorator()
    def handle_get_run_shots(self):
        return self.app.ui.checkBox_run_shots.isChecked()

    @inmain_decorator()
    def handle_set_run_shots(self, value):
        self.app.ui.checkBox_run_shots.setChecked(
            self._coerce_remote_boolean(value, 'value')
        )

    @inmain_decorator()
    def handle_get_view_shots(self):
        return self.app.ui.checkBox_view_shots.isChecked()

    @inmain_decorator()
    def handle_set_view_shots(self, value):
        self.app.ui.checkBox_view_shots.setChecked(
            self._coerce_remote_boolean(value, 'value')
        )

    def handle_get_analyse_shots(self):
        return self.app.analysis_submission.send_to_server

    def handle_set_analyse_shots(self, value):
        self.app.analysis_submission.send_to_server = self._coerce_remote_boolean(value, 'value')

    @inmain_decorator()
    def handle_get_shuffle(self):
        return self.app.ui.pushButton_shuffle.isChecked()

    @inmain_decorator()
    def handle_set_shuffle(self, value):
        self.app.ui.pushButton_shuffle.setChecked(
            self._coerce_remote_boolean(value, 'value')
        )

    def handle_n_shots(self):
        # Wait until any current preparsing is done, to ensure this is not racy w.r.t
        # previous remote calls:
        self.app.wait_until_preparse_complete()
        return self.app.n_shots

    @inmain_decorator()
    def handle_get_labscript_file(self):
        labscript_file = self.app.ui.lineEdit_labscript_file.text()
        return os.path.abspath(labscript_file) if labscript_file else ''

    @inmain_decorator()
    def handle_set_labscript_file(self, value):
        labscript_file = os.path.abspath(value)
        self.app.ui.lineEdit_labscript_file.setText(labscript_file)

    @inmain_decorator()
    def handle_get_shot_output_folder(self):
        shot_output_folder = self.app.ui.lineEdit_shot_output_folder.text()
        return os.path.abspath(shot_output_folder) if shot_output_folder else ''

    @inmain_decorator()
    def handle_set_shot_output_folder(self, value):
        shot_output_folder = os.path.abspath(value)
        self.app.ui.lineEdit_shot_output_folder.setText(shot_output_folder)

    def handle_error_in_globals(self):
        try:
            # This will raise an exception if there are multiple active groups of the
            # same name:
            active_groups = inmain(self.app.get_active_groups, interactive=False)
            sequence_globals = runmanager.get_globals(active_groups)
            # This will raise an exception if any of the globals can't be evaluated:
            runmanager.evaluator._evaluate_globals(sequence_globals, raise_exceptions=True)
        except Exception:
            return True
        return False

    def handle_is_output_folder_default(self):
        return not self.app.non_default_folder

    @inmain_decorator()
    def handle_reset_shot_output_folder(self):
        self.app.on_reset_shot_output_folder_clicked(None)

    def handle_submit_shots(self, entries, sequence_id=None, sequence_index=None):
        """Submit one shot per entry, as ``RunmanagerClient.submit_shots`` describes.

        Every entry is evaluated before any shot is made, and the whole batch is
        then made in one go, since a filename and a run number are claimed by the
        batch being made. A refusal therefore queues nothing."""
        entries = [dict(entry) for entry in entries]
        if not entries:
            # A caller that produced no entries this round has asked for
            # nothing, rather than asked wrongly. Nothing is made, so nothing
            # claims a filename or a run number.
            return []
        names = set(entries[0])
        if any(set(entry) != names for entry in entries):
            raise ValueError('Cannot submit entries that name different globals')
        # The preparse writes each global's expansion type, so the globals are
        # read once it has finished, as an Engage reads them.
        self.app.wait_until_preparse_complete()
        active_groups = inmain(self.app.get_active_groups, interactive=False)
        globals_details = runmanager.globals_file.get_globals_details(active_groups)
        group_of = {
            name: group for group, records in globals_details.items() for name in records
        }
        missing = sorted(names - set(group_of))
        if missing:
            raise ValueError(f'Global {missing[0]} not found in any active group')
        # The shot would run the scan, or a value read at compile time, and not
        # the one filed and receipted for it:
        held = sorted(
            name
            for name in names
            if globals_details[group_of[name]][name]['scan_enabled']
            or globals_details[group_of[name]][name]['jit_enabled']
        )
        if held:
            raise ValueError(
                'Cannot submit a value for a global with its Scan? or JIT? box '
                f"ticked, since the shot would not run it: {', '.join(held)}."
            )
        send_to_runviewer = self.handle_get_view_shots()
        batch = []
        for entry in entries:
            # The globals as the window would hold them with this entry set.
            details = {group: dict(records) for group, records in globals_details.items()}
            for name, value in entry.items():
                group_records = details[group_of[name]]
                previous = group_records[name]['default']
                default = self._with_trailing_comment(
                    self._coerce_remote_expression(value, name), previous
                )
                group_records[name] = dict(group_records[name], default=default)
            pairs, expansions, _ = runmanager.get_shots_with_frozen_globals(details)
            if len(pairs) != 1:
                expanding = sorted(name for name in expansions if expansions[name])
                raise ValueError(
                    f'Cannot submit {entry!r} as one shot: the globals as they stand '
                    f"produce {len(pairs)}. Expanded by: {', '.join(expanding) or 'none'}."
                )
            batch.append(pairs[0])
        self.handle_set_values(entries[-1])
        # Joined by id, not by the queue's last shot, which is whoever
        # submitted last: an operator's Engage in between would otherwise take
        # the session's later shots into the operator's sequence.
        records = inmain(
            self.app.compile_and_queue_shots,
            SUBMISSION_MODE_NEW_SEQUENCE if sequence_id is None else SUBMISSION_MODE_LAST_SEQUENCE,
            True,
            send_to_runviewer,
            batch,
            sequence=None if sequence_id is None else (sequence_id, sequence_index),
        )
        return [shot_receipt(record) for record in records]

    def handle_shot_status(self, shot_ids):
        """The record of each of these shots, as ``shot_status`` returns them.

        The queue controller is safe to ask from any thread, so this is not a GUI
        read."""
        return self.app.queue_controller.get_shot_statuses(list(shot_ids))

    def handle_get_queue(self):
        """Read-only, and the queue controller is safe to ask from any thread."""
        return self.app.queue_controller.get_queue()

    def handle_test_compile(self):
        self.app.wait_until_preparse_complete()
        return self.app.test_compile()

    def handle_queue_exchange(self, outcome=None, request_shot=True):
        return self.app.queue_exchange(outcome, bool(request_shot))

    def handle_get_version(self):
        return runmanager.__version__


if __name__ == "__main__":
    logger = setup_logging(APPLICATION_NAME)
    labscript_utils.excepthook.set_logger(logger)
    logger.info('\n\n===============starting===============\n')
    port = LabConfig().getint('ports', 'runmanager', fallback=DEFAULT_PORT)
    if port_in_use(port):
        message = (
            f"runmanager cannot start because port {port}, which is the runmanager "
            "port in the labconfig, is in use. Another copy of runmanager is probably "
            "running."
        )
        logger.error(message)
        splash.hide()
        QtWidgets.QMessageBox.critical(None, 'runmanager', message)
        sys.exit(1)
    qapplication = splash.qapplication
    setup_application(qapplication)
    app = RunManager()
    splash.update_text('Starting remote server')
    runmanager_server = RunmanagerServer(app)
    splash.hide()

    exec_qapplication(qapplication)
    runmanager_server.shutdown()
