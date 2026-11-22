"""Window (PyQt6) for converting figures to raster images.

Layout: two equal-width list panels (figures waiting to convert on the
left, finished output files on the right) and a sidebar of options and
actions, with the progress bar and status text at the sidebar's bottom.
Each figure moves from the left panel to the right one as soon as it is
converted; figures that fail stay on the left, shown in red.

Conversions run on a background thread so the window stays responsive;
results come back through a queue that a timer polls, because widgets
must only be touched from the main thread.
"""

import logging
import math
import os
import platform
import queue
import subprocess
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

import PIL
import pymupdf
from PIL import ImageColor
from PyQt6.QtCore import QPoint, Qt, QTimer
from PyQt6.QtGui import QBrush, QColor, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QAbstractItemDelegate,
    QAbstractItemView,
    QApplication,
    QCheckBox,
    QColorDialog,
    QComboBox,
    QFileDialog,
    QGridLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QSpinBox,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from figconvert import __version__
from figconvert.core import (
    CONVERTED_PREFIX,
    MAX_SIDE_PIXELS,
    MM_PER_INCH,
    RASTER_SUFFIXES,
    SVG_SUFFIX,
    VECTOR_SUFFIXES,
    ConversionError,
    ConvertOptions,
    OutputFile,
    OutputFormat,
    SizeUnit,
    cairosvg_problem,
    clean_stem,
    convert_file,
    find_figures,
    is_supported,
    is_vector,
)
from figconvert.logs import log_uncaught, setup_logging

_LOGGER = logging.getLogger(__name__)

_START_WIDTH = 1200
_START_HEIGHT = 700
_POLL_INTERVAL_MS = 100
_DPI_PRESETS = (
    '72',
    '96',
    '150',
    '300',
    '600',
    '1200',
)
_DEFAULT_DPI = '600'
_MAX_DPI = 2400
# Width or height field value that leaves the side automatic.
_AUTO_SIZE = 'auto'
_DEFAULT_QUALITY = 95
_MAX_QUALITY = 100
# Output formats that take the quality setting.
_QUALITY_FORMATS = (
    OutputFormat.JPEG,
    OutputFormat.WEBP,
)
_DEFAULT_BACKGROUND = '#ffffff'
# Default save folder, inside the first listed figure's folder.
_DEFAULT_OUT_SUBFOLDER = '_converted_figures'
_START_STATUS = 'Add figures, choose a save folder, then Convert.'
_CAIRO_MISSING_STATUS = 'Cairo not found: SVG files may render wrongly.'
# How to install the Cairo library, by sys.platform; Linux otherwise.
_CAIRO_INSTALL_HINTS = {
    'win32': (
        'Install the GTK3 runtime for Windows (it includes '
        'libcairo-2.dll), add its bin folder to PATH, then restart '
        'figconvert.'
    ),
    'darwin': 'Run "brew install cairo", then restart figconvert.',
}
_CAIRO_INSTALL_HINT_LINUX = (
    'Install the Cairo package (for example "sudo apt install '
    'libcairo2"), then restart figconvert.'
)
_MAX_FAILURES_SHOWN = 15
_MAX_SHARED_NAMES_SHOWN = 5

# Labels shown in more than one place.
_CONVERT_LABEL = 'Convert  (Ctrl+Enter)'
_STOP_LABEL = 'Stop  (Ctrl+Enter)'
_RENAME_LABEL = 'Rename output name…'
_REMOVE_LABEL = 'Remove from list'

# Colour of figures whose conversion failed.
_FAILED_COLOUR = '#c62828'
# Column of the editable output name in the left panel.
_OUTPUT_COLUMN = 1
_PENDING_HEADINGS = (
    'File',
    'Output name ✎',
    'Type',
    'Folder',
)
_DONE_HEADINGS = (
    'Output file',
    'Pixels',
    'From',
)
_PENDING_COLUMN_WIDTHS = (
    150,
    150,
    60,
    160,
)
_DONE_COLUMN_WIDTHS = (
    150,
    90,
    140,
)

# Widths in pixels; Qt scales them for high-DPI screens itself.
_SIDEBAR_WIDTH = 280
# Width of the fields on the right of the Output and Resolution boxes,
# so they all have one size and line up.
_FIELD_WIDTH = 90
_MIN_PANEL_WIDTH = 240
_SWATCH_SIZE = 22


def _patterns(suffixes: frozenset[str]) -> str:
    """Return a space-separated glob list for a file dialog filter."""
    return ' '.join(
        f'*{suffix}' for suffix in sorted(suffixes)
    )


_FILE_FILTER = ';;'.join(
    [
        f'All figures ({_patterns(VECTOR_SUFFIXES | RASTER_SUFFIXES)})',
        f'Vector (SVG, PDF, EPS, EMF …) ({_patterns(VECTOR_SUFFIXES)})',
        f'Raster (PNG, JPEG, TIFF …) ({_patterns(RASTER_SUFFIXES)})',
        'All files (*)',
    ]
)


@dataclass(frozen=True, slots=True, kw_only=True)
class _ResultMessage:
    """Message from the worker thread: one figure has finished.

    Parameters
    ----------
    source : Path
        Figure that was handled.
    outputs : list of OutputFile
        Images written for the figure; empty if it failed.
    error : str or None
        Why the conversion failed, or None if it succeeded.
    done : int
        Number of figures handled so far, this one included.
    total : int
        Number of figures in the batch.
    """

    source: Path
    outputs: list[OutputFile]
    error: str | None
    done: int
    total: int


@dataclass(frozen=True, slots=True, kw_only=True)
class _FinishedMessage:
    """Message from the worker thread: the batch has ended.

    Parameters
    ----------
    stopped : bool
        True if the batch was stopped before every figure was handled.
    """

    stopped: bool


class FigConvertApp(QMainWindow):
    """Main application window.

    Parameters
    ----------
    log_file : Path or None, default None
        Log file the program writes to, named in error messages; None
        if there is none.
    """

    def __init__(self, log_file: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f'figconvert {__version__} — figure to raster image')
        self._log_file = log_file
        self._messages: queue.Queue[_ResultMessage | _FinishedMessage] = queue.Queue()
        self._stop_event = threading.Event()
        self._running = False
        self._failures: list[tuple[Path, str]] = []
        self._batch_ok = 0
        self._batch_started = 0.0
        self._last_dir = str(Path.home())
        self._background = _DEFAULT_BACKGROUND
        # Set while the window itself changes an output name, so the
        # change is not taken for the user's edit.
        self._setting_name = False
        self._cairo_problem = cairosvg_problem()
        # The SVG fallback warning is shown once per session.
        self._cairo_warned = False
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll_messages)

        self.pending_tree = self._list_panel(_PENDING_HEADINGS, _PENDING_COLUMN_WIDTHS)
        self.done_tree = self._list_panel(_DONE_HEADINGS, _DONE_COLUMN_WIDTHS)
        self.pending_box = QGroupBox()
        self.done_box = QGroupBox()
        self.status_label = QLabel(
            _START_STATUS if self._cairo_problem is None else _CAIRO_MISSING_STATUS
        )
        self.progress = QProgressBar()
        self.convert_button = QPushButton(_CONVERT_LABEL)
        self.input_buttons: list[QPushButton] = []
        self.recursive_check = QCheckBox('Include sub-folders')
        self.format_combo = QComboBox()
        self.quality_spin = QSpinBox()
        self.all_pages_check = QCheckBox('All pages of PDF/XPS')
        self.out_dir_edit = QLineEdit()
        self.overwrite_check = QCheckBox('Overwrite existing files')
        self.dpi_combo = QComboBox()
        self.width_combo = QComboBox()
        self.height_combo = QComboBox()
        self.unit_combo = QComboBox()
        self.transparent_check = QCheckBox('Keep transparency')
        self.swatch = QLabel()
        self.colour_button = QPushButton('Pick…')

        self.sidebar = self._build_sidebar()
        self._build_layout()
        self._connect_panels()
        self._bind_shortcuts()
        self._update_option_state()
        self._update_panel_titles()
        self.resize(_START_WIDTH, _START_HEIGHT)

    # Layout

    def _list_panel(
        self,
        headings: tuple[str, ...],
        widths: tuple[int, ...],
    ) -> QTreeWidget:
        """Create a list with columns and several-row selection."""
        tree = QTreeWidget()
        tree.setHeaderLabels(list(headings))
        tree.setRootIsDecorated(False)
        tree.setUniformRowHeights(True)
        tree.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # Names are edited only through _edit_output_name.
        tree.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        tree.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        tree.setMinimumWidth(_MIN_PANEL_WIDTH)
        for column, width in enumerate(widths):
            tree.setColumnWidth(column, width)
        return tree

    def _build_layout(self) -> None:
        """Place the two equal-width list panels and the sidebar."""
        body = QWidget()
        layout = QHBoxLayout(body)
        for box, tree in (
            (self.pending_box, self.pending_tree),
            (self.done_box, self.done_tree),
        ):
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(tree)
            # Equal stretch gives both panels the same width.
            layout.addWidget(box, stretch=1)
        layout.addWidget(self.sidebar)
        self.setCentralWidget(body)

    def _build_sidebar(self) -> QWidget:
        """Create the options and actions sidebar, of fixed width."""
        sidebar = QWidget()
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_input_box())
        layout.addWidget(self._build_output_box())
        layout.addWidget(self._build_resolution_box())
        layout.addWidget(self._build_background_box())
        layout.addWidget(self._build_action_box())
        layout.addStretch(1)
        layout.addWidget(self.progress)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        # A fixed width stops the sidebar following the length of each
        # status message, which would shift the panels beside it.
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        return sidebar

    def _build_input_box(self) -> QGroupBox:
        """Create the buttons that fill and empty the left panel."""
        box = QGroupBox('Input figures')
        layout = QVBoxLayout(box)
        buttons = (
            ('Add files…', self.add_files),
            ('Add folder…', self.add_folder),
            ('Remove selected', self.remove_selected),
            ('Clear list', self.clear_pending),
        )
        for label, action in buttons:
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
            self.input_buttons.append(button)
        layout.addWidget(self.recursive_check)
        return box

    def _field_grid(self, box: QGroupBox) -> QGridLayout:
        """Return a grid with labels on the left, fields on the right.

        The label column takes the spare width, so the fields, all of
        width ``_FIELD_WIDTH``, line up at the right edge in every box.
        """
        grid = QGridLayout(box)
        grid.setColumnStretch(0, 1)
        return grid

    def _add_field(self, grid: QGridLayout, label: str, field: QWidget) -> None:
        """Add a labelled field of the shared width as a new row."""
        row = grid.rowCount()
        field.setFixedWidth(_FIELD_WIDTH)
        grid.addWidget(QLabel(label), row, 0)
        grid.addWidget(field, row, 1)

    def _build_output_box(self) -> QGroupBox:
        """Create the format, quality and save-folder fields."""
        box = QGroupBox('Output')
        grid = self._field_grid(box)
        self.format_combo.addItems(
            [
                output_format.value for output_format in OutputFormat
            ]
        )
        self.format_combo.setCurrentText(OutputFormat.JPEG.value)
        self.format_combo.currentTextChanged.connect(self._update_option_state)
        self._add_field(grid, 'Format', self.format_combo)
        self.quality_spin.setRange(1, _MAX_QUALITY)
        self.quality_spin.setValue(_DEFAULT_QUALITY)
        self._add_field(grid, 'Quality (JPEG/WEBP)', self.quality_spin)
        grid.addWidget(self.all_pages_check, grid.rowCount(), 0, 1, 2)
        grid.addWidget(QLabel('Save to'), grid.rowCount(), 0, 1, 2)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.out_dir_edit)
        browse_button = QPushButton('Browse…')
        browse_button.clicked.connect(self.choose_out_dir)
        folder_row.addWidget(browse_button)
        grid.addLayout(folder_row, grid.rowCount(), 0, 1, 2)
        grid.addWidget(self.overwrite_check, grid.rowCount(), 0, 1, 2)
        return box

    def _build_resolution_box(self) -> QGroupBox:
        """Create the DPI and output width, height and unit fields."""
        box = QGroupBox('Resolution')
        grid = self._field_grid(box)
        self.dpi_combo.setEditable(True)
        self.dpi_combo.addItems(list(_DPI_PRESETS))
        self.dpi_combo.setCurrentText(_DEFAULT_DPI)
        self._add_field(grid, 'DPI', self.dpi_combo)
        for label, combo in (
            ('Width', self.width_combo),
            ('Height', self.height_combo),
        ):
            combo.setEditable(True)
            combo.addItem(_AUTO_SIZE)
            self._add_field(grid, label, combo)
        self.unit_combo.addItems(
            [
                unit.value for unit in SizeUnit
            ]
        )
        self._add_field(grid, 'Unit', self.unit_combo)
        hint = QLabel('auto keeps the aspect ratio')
        hint.setStyleSheet('color: gray;')
        grid.addWidget(hint, grid.rowCount(), 0, 1, 2)
        return box

    def _build_background_box(self) -> QGroupBox:
        """Create the transparency and background colour fields."""
        box = QGroupBox('Background')
        grid = QGridLayout(box)
        grid.setColumnStretch(0, 1)
        self.transparent_check.setChecked(True)
        self.transparent_check.toggled.connect(self._update_option_state)
        grid.addWidget(self.transparent_check, 0, 0, 1, 3)
        grid.addWidget(QLabel('Colour'), 1, 0)
        self.swatch.setFixedSize(_SWATCH_SIZE, _SWATCH_SIZE)
        grid.addWidget(self.swatch, 1, 1)
        self.colour_button.clicked.connect(self.pick_colour)
        grid.addWidget(self.colour_button, 1, 2)
        self._refresh_swatch()
        return box

    def _build_action_box(self) -> QWidget:
        """Create Convert/Stop and the finished-list actions."""
        box = QWidget()
        layout = QVBoxLayout(box)
        layout.setContentsMargins(0, 0, 0, 0)
        self.convert_button.clicked.connect(self.convert_or_stop)
        layout.addWidget(self.convert_button)
        for label, action in (
            ('Open output folder', self.open_out_dir),
            ('Clear finished', self.clear_finished),
        ):
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        return box

    def _connect_panels(self) -> None:
        """Connect the list panels' clicks, edits and menus."""
        self.pending_tree.itemSelectionChanged.connect(self._on_pending_select)
        self.pending_tree.itemDoubleClicked.connect(self._on_pending_double_click)
        self.pending_tree.itemChanged.connect(self._on_name_changed)
        self.pending_tree.customContextMenuRequested.connect(self._show_pending_menu)
        self.done_tree.itemDoubleClicked.connect(
            lambda item, _column: self._open_figure(_item_path(item))
        )
        self.done_tree.customContextMenuRequested.connect(self._show_done_menu)

    def _bind_shortcuts(self) -> None:
        """Bind the keyboard shortcuts."""
        for keys in ('Ctrl+Return', 'Ctrl+Enter'):
            convert = QShortcut(QKeySequence(keys), self)
            convert.activated.connect(self.convert_or_stop)
        add_files = QShortcut(QKeySequence('Ctrl+O'), self)
        add_files.activated.connect(self.add_files)
        delete = QShortcut(QKeySequence.StandardKey.Delete, self.pending_tree)
        delete.setContext(Qt.ShortcutContext.WidgetShortcut)
        delete.activated.connect(self.remove_selected)
        rename = QShortcut(QKeySequence('F2'), self.pending_tree)
        rename.setContext(Qt.ShortcutContext.WidgetShortcut)
        rename.activated.connect(self._edit_output_name)

    # Options

    def _update_option_state(self) -> None:
        """Enable only the fields that apply to the chosen options."""
        output_format = OutputFormat(self.format_combo.currentText())
        supports_alpha = output_format.supports_alpha
        self.quality_spin.setEnabled(output_format in _QUALITY_FORMATS)
        self.transparent_check.setEnabled(supports_alpha)
        self.colour_button.setEnabled(
            not (supports_alpha and self.transparent_check.isChecked())
        )

    def _refresh_swatch(self) -> None:
        """Show the background colour in the swatch."""
        self.swatch.setStyleSheet(
            f'background-color: {self._background}; border: 1px solid gray;'
        )

    def pick_colour(self) -> None:
        """Ask for the background colour."""
        colour = QColorDialog.getColor(
            QColor(self._background),
            self,
            'Background colour',
        )
        if colour.isValid():
            self._background = colour.name()
            self._refresh_swatch()

    def _read_options(self) -> ConvertOptions:
        """Build conversion options from the sidebar fields.

        Returns
        -------
        ConvertOptions
            Settings for every figure in the batch.

        Raises
        ------
        ValueError
            If the DPI, width, height or background colour is invalid.
        """
        try:
            dpi = float(self.dpi_combo.currentText())
        except ValueError as error:
            raise ValueError('DPI must be a number.') from error
        if not 1 <= dpi <= _MAX_DPI:
            raise ValueError(f'DPI must be between 1 and {_MAX_DPI}.')
        unit = SizeUnit(self.unit_combo.currentText())
        width = _read_size(self.width_combo.currentText(), 'Width', unit, dpi)
        height = _read_size(self.height_combo.currentText(), 'Height', unit, dpi)
        try:
            ImageColor.getrgb(self._background)
        except ValueError as error:
            raise ValueError(
                f'Unknown background colour: {self._background}'
            ) from error
        return ConvertOptions(
            output_format=OutputFormat(self.format_combo.currentText()),
            dpi=dpi,
            width=width,
            height=height,
            size_unit=unit,
            transparent=self.transparent_check.isChecked(),
            background=self._background,
            quality=self.quality_spin.value(),
            all_pages=self.all_pages_check.isChecked(),
            overwrite=self.overwrite_check.isChecked(),
        )

    # Left panel

    def _update_panel_titles(self) -> None:
        """Show item counts in the panel titles."""
        waiting = self.pending_tree.topLevelItemCount()
        finished = self.done_tree.topLevelItemCount()
        self.pending_box.setTitle(f'To convert ({waiting})')
        self.done_box.setTitle(f'Finished ({finished})')

    def _pending_items(self) -> list[QTreeWidgetItem]:
        """Return the left panel's rows, top to bottom."""
        items = []
        for index in range(self.pending_tree.topLevelItemCount()):
            item = self.pending_tree.topLevelItem(index)
            if item is not None:
                items.append(item)
        return items

    def _find_pending(self, path: Path) -> QTreeWidgetItem | None:
        """Return the left panel's row for a figure, if listed."""
        return next(
            (
                item for item in self._pending_items()
                if _item_path(item) == path
            ),
            None,
        )

    def add_files(self) -> None:
        """Choose figures in a file dialog and list them on the left."""
        if self._running:
            return
        names, _filter = QFileDialog.getOpenFileNames(
            self,
            'Select figures',
            self._last_dir,
            _FILE_FILTER,
        )
        if names:
            self._last_dir = str(Path(names[0]).parent)
            paths = [
                Path(name) for name in names
            ]
            self._add_paths(paths)

    def add_folder(self) -> None:
        """Choose a folder and list every supported figure in it."""
        if self._running:
            return
        folder = QFileDialog.getExistingDirectory(
            self,
            'Select a folder of figures',
            self._last_dir,
        )
        if not folder:
            return
        self._last_dir = folder
        found = find_figures(Path(folder), recursive=self.recursive_check.isChecked())
        if not found:
            QMessageBox.information(
                self,
                'No figures',
                'No supported figures in that folder.',
            )
            return
        self._add_paths(found)

    def _add_paths(self, paths: list[Path]) -> None:
        """List figures in the left panel and report the count.

        Figures already listed and files of unsupported types are
        skipped. If no save folder is set yet, it defaults to
        ``_DEFAULT_OUT_SUBFOLDER`` inside the first listed figure's
        folder.

        Parameters
        ----------
        paths : list of Path
            Figures to add; each is resolved to an absolute path first.
        """
        added = skipped = 0
        listed = {
            _item_path(item) for item in self._pending_items()
        }
        self._setting_name = True
        try:
            for given_path in paths:
                path = given_path.resolve()
                if not is_supported(path) or path in listed:
                    skipped += 1
                    continue
                item = QTreeWidgetItem(
                    [
                        path.name,
                        path.stem,
                        'Vector' if is_vector(path) else 'Raster',
                        str(path.parent),
                    ]
                )
                item.setData(0, Qt.ItemDataRole.UserRole, str(path))
                item.setFlags(item.flags() | Qt.ItemFlag.ItemIsEditable)
                self.pending_tree.addTopLevelItem(item)
                listed.add(path)
                added += 1
        finally:
            self._setting_name = False
        if not self._out_dir_text() and added:
            first = _item_path(self._pending_items()[0])
            self.out_dir_edit.setText(str(first.parent / _DEFAULT_OUT_SUBFOLDER))
        self._update_panel_titles()
        message = f'Added {added} figure(s).'
        if skipped:
            message += f' Skipped {skipped} (already listed or unsupported).'
        self.status_label.setText(message)

    def remove_selected(self) -> None:
        """Remove the selected figures from the left panel."""
        if self._running:
            return
        for item in self.pending_tree.selectedItems():
            index = self.pending_tree.indexOfTopLevelItem(item)
            self.pending_tree.takeTopLevelItem(index)
        self._update_panel_titles()

    def clear_pending(self) -> None:
        """Empty the left panel."""
        if self._running:
            return
        self.pending_tree.clear()
        self._update_panel_titles()

    def _on_pending_select(self) -> None:
        """Show the selected figure's full path, or why it failed."""
        selected = self.pending_tree.selectedItems()
        if len(selected) != 1 or self._running:
            return
        path = _item_path(selected[0])
        error = next(
            (
                reason for failed, reason in self._failures
                if failed == path
            ),
            None,
        )
        self.status_label.setText(
            str(path) if error is None else f'{path.name} failed: {error}'
        )

    def _on_pending_double_click(self, item: QTreeWidgetItem, column: int) -> None:
        """Edit the output name, or open the original figure.

        Double-clicking the Output name cell edits it; double-clicking
        any other cell opens the figure in the default program.
        """
        if column == _OUTPUT_COLUMN:
            self._edit_output_name(item)
        else:
            self._open_figure(_item_path(item))

    def _edit_output_name(self, item: QTreeWidgetItem | None = None) -> None:
        """Start editing a row's output name in place.

        Does nothing while a batch runs.

        Parameters
        ----------
        item : QTreeWidgetItem or None, default None
            Row to edit; the current row if None.
        """
        if self._running:
            return
        item = item or self.pending_tree.currentItem()
        if item is None:
            return
        # The name before editing, to restore if the new one is empty.
        item.setData(
            _OUTPUT_COLUMN,
            Qt.ItemDataRole.UserRole,
            item.text(_OUTPUT_COLUMN),
        )
        self.pending_tree.editItem(item, _OUTPUT_COLUMN)
        self.status_label.setText('Type the output name (no extension); Enter to keep.')

    def _commit_name_edit(self) -> None:
        """Keep an output name still being typed, as Enter would.

        Ctrl+Enter starts a batch without closing the editor, so the
        typed name must be saved before the names are read.
        """
        viewport = self.pending_tree.viewport()
        if viewport is None:
            return
        for editor in viewport.findChildren(QLineEdit):
            self.pending_tree.commitData(editor)
            self.pending_tree.closeEditor(
                editor,
                QAbstractItemDelegate.EndEditHint.NoHint,
            )

    def _on_name_changed(self, item: QTreeWidgetItem, column: int) -> None:
        """Clean a typed output name, or put the old one back."""
        if self._setting_name or column != _OUTPUT_COLUMN:
            return
        name = clean_stem(item.text(_OUTPUT_COLUMN))
        previous = item.data(_OUTPUT_COLUMN, Qt.ItemDataRole.UserRole)
        self._setting_name = True
        try:
            if name:
                item.setText(_OUTPUT_COLUMN, name)
                self.status_label.setText(f'Output name set to “{name}”.')
            else:
                item.setText(_OUTPUT_COLUMN, previous or _item_path(item).stem)
                self.status_label.setText('Name not changed: it was empty or invalid.')
        finally:
            self._setting_name = False

    def _show_pending_menu(self, position: QPoint) -> None:
        """Show the left panel's right-click menu for a row."""
        item = self.pending_tree.itemAt(position)
        if item is None:
            return
        if not item.isSelected():
            self.pending_tree.setCurrentItem(item)
        path = _item_path(item)
        menu = QMenu(self)
        open_action = menu.addAction('Open file')
        location_action = menu.addAction('Open file location')
        menu.addSeparator()
        rename_action = menu.addAction(_RENAME_LABEL)
        menu.addSeparator()
        remove_action = menu.addAction(_REMOVE_LABEL)
        for action in (
            rename_action,
            remove_action,
        ):
            if action is not None:
                action.setEnabled(not self._running)
        viewport = self.pending_tree.viewport()
        if viewport is None:
            return
        chosen = menu.exec(viewport.mapToGlobal(position))
        if chosen is None:
            return
        if chosen is open_action:
            self._open_figure(path)
        elif chosen is location_action:
            self._reveal_figure(path)
        elif chosen is rename_action:
            self._edit_output_name(item)
        elif chosen is remove_action:
            self.remove_selected()

    def _show_done_menu(self, position: QPoint) -> None:
        """Show the right panel's right-click menu for a row."""
        item = self.done_tree.itemAt(position)
        viewport = self.done_tree.viewport()
        if item is None or viewport is None:
            return
        path = _item_path(item)
        menu = QMenu(self)
        open_action = menu.addAction('Open file')
        location_action = menu.addAction('Open file location')
        chosen = menu.exec(viewport.mapToGlobal(position))
        if chosen is not None and chosen is open_action:
            self._open_figure(path)
        elif chosen is not None and chosen is location_action:
            self._reveal_figure(path)

    def _open_figure(self, path: Path) -> None:
        """Open a file with the default program, reporting failures."""
        if not path.exists():
            self.status_label.setText(f'{path.name} no longer exists.')
            return
        try:
            _open_path(path)
        except OSError as error:
            QMessageBox.critical(self, 'Cannot open', f'{path.name}\n\n{error}')

    def _reveal_figure(self, path: Path) -> None:
        """Show a file selected in the system file explorer."""
        if not path.exists():
            self.status_label.setText(f'{path.name} no longer exists.')
            return
        try:
            _reveal_path(path)
        except OSError as error:
            QMessageBox.critical(
                self,
                'Cannot open folder',
                f'{path.parent}\n\n{error}',
            )

    # Save folder and right panel

    def _out_dir_text(self) -> str:
        """Return the typed save folder without surrounding spaces."""
        return (
            self.out_dir_edit
            .text()
            .strip()
        )

    def choose_out_dir(self) -> None:
        """Choose the save folder in a folder dialog."""
        current = self._out_dir_text()
        initial = current if current and Path(current).exists() else self._last_dir
        folder = QFileDialog.getExistingDirectory(
            self,
            'Select the save folder',
            initial,
        )
        if folder:
            self.out_dir_edit.setText(str(Path(folder)))

    def open_out_dir(self) -> None:
        """Open the save folder in the system file explorer."""
        folder = Path(self._out_dir_text()) if self._out_dir_text() else None
        if folder is None or not folder.is_dir():
            QMessageBox.information(
                self,
                'No folder',
                'The save folder does not exist yet.',
            )
            return
        _open_path(folder)

    def clear_finished(self) -> None:
        """Empty the right panel (the files stay on disk)."""
        self.done_tree.clear()
        self._update_panel_titles()

    def _log_hint(self) -> str:
        """Return a sentence naming the log file, or '' if none."""
        if self._log_file is None:
            return ''
        return f'\n\nDetails are in the log file:\n{self._log_file}'

    # Batch

    def convert_or_stop(self) -> None:
        """Start a batch, or ask the running batch to stop."""
        if self._running:
            self._stop_event.set()
            _LOGGER.info('Stop requested')
            self.convert_button.setEnabled(False)
            self.status_label.setText('Stopping after the current figure…')
            return
        self._start_batch()

    def _start_batch(self) -> None:
        """Check the settings and start the worker thread."""
        self._commit_name_edit()
        items = self._pending_items()
        sources = [
            (
                _item_path(item),
                item.text(_OUTPUT_COLUMN),
            )
            for item in items
        ]
        source_paths = [
            path for path, _stem in sources
        ]
        if not sources:
            QMessageBox.information(self, 'Nothing to convert', 'Add figures first.')
            return
        if not self._out_dir_text():
            self.choose_out_dir()
            if not self._out_dir_text():
                return
        try:
            options = self._read_options()
        except ValueError as error:
            QMessageBox.critical(self, 'Invalid setting', str(error))
            return
        if not self._confirm_svg_fallback(source_paths):
            return
        out_dir = Path(self._out_dir_text())
        if not self._confirm_shared_folder(out_dir, source_paths):
            return

        self._failures = []
        self._batch_ok = 0
        for item in items:
            _paint_item(item, failed=False)
        self._stop_event.clear()
        self._running = True
        self._set_inputs_enabled(enabled=False)
        self.convert_button.setText(_STOP_LABEL)
        self.progress.setRange(0, len(sources))
        self.progress.setValue(0)
        self.status_label.setText(f'Converting 0 / {len(sources)}…')
        self._batch_started = time.perf_counter()
        _LOGGER.info(
            'Batch started: %d figure(s) into %s with %s',
            len(sources),
            out_dir,
            options,
        )
        thread = threading.Thread(
            target=self._worker,
            args=(
                sources,
                out_dir,
                options,
            ),
            daemon=True,
        )
        thread.start()
        self._timer.start()

    def _confirm_svg_fallback(self, sources: list[Path]) -> bool:
        """Warn, once per session, that SVG files fall back to PyMuPDF.

        Parameters
        ----------
        sources : list of Path
            Figures in the batch.

        Returns
        -------
        bool
            True to go on converting: CairoSVG works, the batch has no
            SVG file, the warning was already accepted, or the user
            accepts it now. False if the user cancels.
        """
        has_svg = any(
            _lower_suffix(path) == SVG_SUFFIX for path in sources
        )
        if self._cairo_problem is None or self._cairo_warned or not has_svg:
            return True
        hint = _CAIRO_INSTALL_HINTS.get(sys.platform, _CAIRO_INSTALL_HINT_LINUX)
        # The loader lists every library name it tried; the first line
        # is enough here.
        details = self._cairo_problem.splitlines()[0]
        answer = QMessageBox.warning(
            self,
            'Cairo not found',
            'SVG files will be drawn with PyMuPDF instead of CairoSVG. '
            'PyMuPDF ignores clipping and replaces missing fonts with '
            'Times, so plots (Matplotlib ones especially) can come out '
            'wrong.\n\n'
            f'{hint}\n\n'
            f'Details: {details}\n\n'
            'Convert with PyMuPDF anyway?',
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Ok,
        )
        go_on = answer == QMessageBox.StandardButton.Ok
        _LOGGER.info('Cairo fallback warning shown; go on: %s', go_on)
        self._cairo_warned = go_on
        return go_on

    def _confirm_shared_folder(self, out_dir: Path, sources: list[Path]) -> bool:
        """Warn when the save folder also holds input figures.

        Parameters
        ----------
        out_dir : Path
            Chosen save folder.
        sources : list of Path
            Figures about to be converted, as absolute paths.

        Returns
        -------
        bool
            True to go ahead, False if the user cancelled. True without
            asking when no figure is in the save folder or the folder
            cannot be resolved.
        """
        try:
            target = out_dir.resolve()
        except OSError:
            return True
        shared = [
            path for path in sources
            if path.parent == target
        ]
        if not shared:
            return True
        names = ', '.join(
            path.name for path in shared[:_MAX_SHARED_NAMES_SHOWN]
        )
        if len(shared) > _MAX_SHARED_NAMES_SHOWN:
            names += f', … ({len(shared)} in total)'
        if self.overwrite_check.isChecked():
            risk = (
                '“Overwrite existing files” is ticked, so existing images in that '
                'folder with the same name and format will be replaced '
                '(the input figures themselves are never overwritten).'
            )
        else:
            risk = (
                'Outputs whose name is already used there (in any format) get '
                f'the prefix “{CONVERTED_PREFIX}1_”, “{CONVERTED_PREFIX}2_” …, '
                'so nothing will be overwritten — '
                'but the folder will mix originals and converted figures.'
            )
        answer = QMessageBox.warning(
            self,
            'Output folder holds input figures',
            'The save folder is the same folder as these input figures:\n'
            f'{names}\n\n{risk}\n\nContinue?',
            QMessageBox.StandardButton.Ok | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Ok

    def _worker(
        self,
        sources: list[tuple[Path, str]],
        out_dir: Path,
        options: ConvertOptions,
    ) -> None:
        """Convert each figure in turn; runs on the background thread.

        Puts a ``_ResultMessage`` on the queue for each figure and a
        ``_FinishedMessage`` at the end, stopping before the next
        figure once the stop event is set.

        Parameters
        ----------
        sources : list of (Path, str) tuples
            Figures to convert, each with its output name.
        out_dir : Path
            Folder to save the images in.
        options : ConvertOptions
            Settings for every figure.
        """
        total = len(sources)
        reserved: set[str] = set()
        for done, (source, out_stem) in enumerate(sources, start=1):
            if self._stop_event.is_set():
                _LOGGER.info('Batch stopped by the user before %s', source)
                self._messages.put(_FinishedMessage(stopped=True))
                return
            outputs: list[OutputFile] = []
            error: str | None = None
            _LOGGER.info('Figure %d / %d: %s', done, total, source)
            started = time.perf_counter()
            try:
                outputs = convert_file(source, out_dir, options, out_stem, reserved)
            except ConversionError as conversion_error:
                error = str(conversion_error)
                _LOGGER.warning(
                    'Figure %d / %d failed: %s: %s',
                    done,
                    total,
                    source,
                    error,
                    exc_info=True,
                )
            # An unexpected error is reported for its figure like any
            # other failure, so the rest of the batch still converts.
            except Exception as unexpected_error:  # pylint: disable=broad-exception-caught
                error = f'{type(unexpected_error).__name__}: {unexpected_error}'
                _LOGGER.exception(
                    'Figure %d / %d failed unexpectedly: %s',
                    done,
                    total,
                    source,
                )
            else:
                _LOGGER.info(
                    'Figure %d / %d done in %.2f s',
                    done,
                    total,
                    time.perf_counter() - started,
                )
            self._messages.put(
                _ResultMessage(
                    source=source,
                    outputs=outputs,
                    error=error,
                    done=done,
                    total=total,
                )
            )
        self._messages.put(_FinishedMessage(stopped=False))

    def _poll_messages(self) -> None:
        """Apply worker results; runs on the timer."""
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                return
            if isinstance(message, _FinishedMessage):
                self._timer.stop()
                self._finish_batch(stopped=message.stopped)
                return
            self._apply_result(message)

    def _apply_result(self, message: _ResultMessage) -> None:
        """Move a figure to the right panel, or mark it failed."""
        item = self._find_pending(message.source)
        self.progress.setValue(message.done)
        if message.error is not None:
            self._failures.append((message.source, message.error))
            if item is not None:
                _paint_item(item, failed=True)
            self.status_label.setText(
                f'{message.done} / {message.total}: {message.source.name} failed — '
                f'{message.error}',
            )
        else:
            self._batch_ok += 1
            if item is not None:
                self.pending_tree.takeTopLevelItem(
                    self.pending_tree.indexOfTopLevelItem(item)
                )
            for output in message.outputs:
                self._add_finished(output, message.source)
            self.status_label.setText(
                f'Converted {message.done} / {message.total}: {message.source.name}',
            )
        self._update_panel_titles()

    def _add_finished(self, output: OutputFile, source: Path) -> None:
        """List a written image on the right, replacing its old row."""
        for index in range(self.done_tree.topLevelItemCount()):
            existing = self.done_tree.topLevelItem(index)
            if existing is not None and _item_path(existing) == output.path:
                self.done_tree.takeTopLevelItem(index)
                break
        item = QTreeWidgetItem(
            [
                output.path.name,
                f'{output.width} × {output.height}',
                source.name,
            ]
        )
        item.setData(0, Qt.ItemDataRole.UserRole, str(output.path))
        self.done_tree.addTopLevelItem(item)
        self.done_tree.scrollToItem(item)

    def _finish_batch(self, *, stopped: bool) -> None:
        """Reset the controls and report the outcome.

        Parameters
        ----------
        stopped : bool
            Whether the batch was stopped before every figure was
            handled.
        """
        self._running = False
        self._set_inputs_enabled(enabled=True)
        self.convert_button.setText(_CONVERT_LABEL)
        self.convert_button.setEnabled(True)
        summary = f'Done: {self._batch_ok} converted, {len(self._failures)} failed.'
        if stopped:
            summary = 'Stopped. ' + summary
        self.status_label.setText(summary)
        _LOGGER.info(
            'Batch finished in %.1f s: %s',
            time.perf_counter() - self._batch_started,
            summary,
        )
        if not self._failures:
            return
        lines = [
            f'• {path.name}: {reason}'
            for path, reason in self._failures[:_MAX_FAILURES_SHOWN]
        ]
        extra = len(self._failures) - _MAX_FAILURES_SHOWN
        if extra > 0:
            lines.append(f'… and {extra} more')
        QMessageBox.warning(
            self,
            'Some figures failed',
            'These stay in the left panel (in red):\n\n'
            + '\n'.join(lines)
            + self._log_hint(),
        )

    def _set_inputs_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the buttons that change the left panel."""
        for button in self.input_buttons:
            button.setEnabled(enabled)


def _lower_suffix(path: Path) -> str:
    """Return the file extension in lower case, with the dot."""
    return (
        path.suffix
        .lower()
    )


def _item_path(item: QTreeWidgetItem) -> Path:
    """Return the file a list row stands for."""
    return Path(str(item.data(0, Qt.ItemDataRole.UserRole)))


def _paint_item(item: QTreeWidgetItem, *, failed: bool) -> None:
    """Show a row in red if its figure failed, normally otherwise."""
    brush = QBrush(QColor(_FAILED_COLOUR)) if failed else QBrush()
    for column in range(item.columnCount()):
        item.setForeground(column, brush)


def _read_size(text: str, name: str, unit: SizeUnit, dpi: float) -> float | None:
    """Read a width or height field.

    Parameters
    ----------
    text : str
        Field contents: a size in ``unit``, or ``auto`` (or nothing) to
        leave the side automatic.
    name : str
        Field name used in error messages, such as ``'Width'``.
    unit : SizeUnit
        Unit of the size: whole pixels, or millimetres (decimals
        allowed).
    dpi : float
        Resolution that turns millimetres into pixels.

    Returns
    -------
    float or None
        The size in ``unit``, or None for automatic.

    Raises
    ------
    ValueError
        If the text is neither ``auto`` nor a valid size, or the size
        is outside 1 to ``MAX_SIDE_PIXELS`` pixels.
    """
    value = text.strip()
    if not value or value.casefold() == _AUTO_SIZE:
        return None
    if unit is SizeUnit.PX:
        try:
            pixels = int(value)
        except ValueError as error:
            raise ValueError(
                f'{name} must be a whole number of pixels or "{_AUTO_SIZE}".'
            ) from error
        if not 1 <= pixels <= MAX_SIDE_PIXELS:
            raise ValueError(f'{name} must be between 1 and {MAX_SIDE_PIXELS} pixels.')
        return pixels
    try:
        millimetres = float(value)
    except ValueError:
        # Caught below with the same message as "inf" and "nan".
        millimetres = math.nan
    if not math.isfinite(millimetres):
        raise ValueError(
            f'{name} must be a number of millimetres or "{_AUTO_SIZE}".'
        )
    pixels = round(millimetres / MM_PER_INCH * dpi)
    if not 1 <= pixels <= MAX_SIDE_PIXELS:
        raise ValueError(
            f'{name} of {millimetres:g} mm is {pixels} px at {dpi:g} DPI; '
            f'it must be between 1 and {MAX_SIDE_PIXELS} pixels.'
        )
    return millimetres


def _open_path(path: Path) -> None:
    """Open a file or folder with the system's default program."""
    if sys.platform == 'win32':
        # The path is a local file or folder the user chose.
        os.startfile(path)  # noqa: S606
    elif sys.platform == 'darwin':
        # Not waited for: the program runs on its own.
        subprocess.Popen(  # pylint: disable=consider-using-with
            [
                'open',
                str(path),
            ]
        )
    else:
        subprocess.Popen(  # pylint: disable=consider-using-with
            [
                'xdg-open',
                str(path),
            ]
        )


def _reveal_path(path: Path) -> None:
    """Open the folder holding a file, selecting it where possible."""
    if sys.platform == 'win32':
        # Explorer needs the path quoted inside the /select argument.
        subprocess.Popen(  # pylint: disable=consider-using-with
            f'explorer /select,"{path}"'
        )
    elif sys.platform == 'darwin':
        subprocess.Popen(  # pylint: disable=consider-using-with
            [
                'open',
                '-R',
                str(path),
            ]
        )
    else:
        subprocess.Popen(  # pylint: disable=consider-using-with
            [
                'xdg-open',
                str(path.parent),
            ]
        )


def _install_error_dialog(log_file: Path | None) -> None:
    """Log errors nothing caught and show them in a message box.

    Without this, PyQt6 ends the program when a button's action raises.
    """

    def report(
        error_type: type[BaseException],
        error: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        log_uncaught(error_type, error, traceback)
        hint = ''
        if log_file is not None:
            hint = f'\n\nDetails are in the log file:\n{log_file}'
        QMessageBox.critical(
            None,
            'Unexpected error',
            f'{error_type.__name__}: {error}{hint}',
        )

    sys.excepthook = report


def main() -> None:
    """Open the figconvert window, logging the run to a file."""
    log_file = setup_logging()
    _LOGGER.info(
        'figconvert %s started (Python %s, %s; Pillow %s, PyMuPDF %s); log %s',
        __version__,
        platform.python_version(),
        platform.platform(),
        PIL.__version__,
        pymupdf.VersionBind,
        log_file,
    )
    app = QApplication(sys.argv)
    _install_error_dialog(log_file)
    window = FigConvertApp(log_file)
    window.show()
    app.exec()
    _LOGGER.info('figconvert closed')


if __name__ == '__main__':
    main()
