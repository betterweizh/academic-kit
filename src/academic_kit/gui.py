"""Launcher window (PyQt6): one button per installed tool.

The buttons come from :func:`academic_kit.tools.discover_tools`, so a
newly installed tool appears without changing this module. Pressing a
button starts the tool in its own process; the launcher stays open and
can start several tools, or the same tool more than once.

The buttons are square tiles of one fixed size, side by side in a row
that stays in the centre of the window; resizing the window moves the
row but never resizes the tiles. Each tile has room for a 64 x 64 icon
above the tool name.

The status text sits below the buttons in an area of fixed height, tall
enough for its longest message, so the buttons never move when the text
changes.

Settings > Logs opens a dialog that sets the folder and file name of
each program's log: the launcher's own and each tool's.
"""

import logging
import os
import platform
import subprocess
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import TracebackType

from PyQt6.QtCore import QEvent, QRect, QSize, Qt, pyqtSignal
from PyQt6.QtGui import QEnterEvent
from PyQt6.QtWidgets import (
    QApplication,
    QDialog,
    QDialogButtonBox,
    QFileDialog,
    QGridLayout,
    QHBoxLayout,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMessageBox,
    QPushButton,
    QToolButton,
    QVBoxLayout,
    QWidget,
)

from academic_kit import __version__
from academic_kit.logs import (
    LAUNCHER_NAME,
    LogTarget,
    check_file_name,
    check_targets,
    default_target,
    load_log_settings,
    log_uncaught,
    save_log_settings,
    setup_logging,
)
from academic_kit.tools import Tool, discover_tools, launch_tool

_LOGGER = logging.getLogger(__name__)

# Size the window opens at, unless its contents need more.
_START_WIDTH = 560
_START_HEIGHT = 380
_WINDOW_MARGIN = 16
# Side of each square tool button: a 64 x 64 icon, the tool name below
# it, and a margin all round.
_TILE_SIZE = 128
_ICON_SIZE = 64
_TILE_GAP = 16
_STATUS_GAP = 12
# Narrowest width the status text wraps at.
_STATUS_WIDTH = 260
# Tall enough for any wrapped text the label may measure.
_MEASURE_HEIGHT = 10_000
_IDLE_STATUS = 'Choose a tool to open.'
_NO_TOOLS_STATUS = (
    'No tools are installed. Run "uv sync" in the academic-kit folder.'
)
# Width of the folder fields in the log settings dialog, in characters.
_FOLDER_FIELD_CHARS = 70


class LogSettingsDialog(QDialog):
    """Dialog that sets the folder and file name of each program's log.

    Parameters
    ----------
    targets : mapping of str to LogTarget
        Current log location of each program, in display order.
    parent : QWidget or None, default None
        Window the dialog belongs to.

    Attributes
    ----------
    chosen : dict of str to LogTarget
        The checked locations, set when the user presses OK.
    """

    def __init__(
        self,
        targets: Mapping[str, LogTarget],
        parent: QWidget | None = None,
    ) -> None:
        super().__init__(parent)
        self.setWindowTitle('Log settings')
        self.chosen: dict[str, LogTarget] = dict(targets)
        self._fields: dict[str, tuple[QLineEdit, QLineEdit]] = {}

        layout = QVBoxLayout(self)
        intro = QLabel(
            'Each program writes its own log file. Tools opened from now on '
            'use these settings; tools already open keep theirs.'
        )
        intro.setWordWrap(True)
        layout.addWidget(intro)

        grid = QGridLayout()
        headings = (
            'Program',
            'Log folder',
            '',
            'Log file name',
        )
        for column, heading in enumerate(headings):
            grid.addWidget(QLabel(f'<b>{heading}</b>'), 0, column)
        for row, (program, target) in enumerate(targets.items(), start=1):
            self._add_row(grid, row, program, target)
        grid.setColumnStretch(1, 1)
        layout.addLayout(grid)

        buttons = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        defaults_button = buttons.addButton(
            'Use defaults',
            QDialogButtonBox.ButtonRole.ResetRole,
        )
        if defaults_button is not None:
            defaults_button.clicked.connect(self.use_defaults)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

    def _add_row(
        self,
        grid: QGridLayout,
        row: int,
        program: str,
        target: LogTarget,
    ) -> None:
        """Add the folder and file name fields of one program."""
        folder_field = QLineEdit(str(target.folder))
        char_width = (
            folder_field
            .fontMetrics()
            .averageCharWidth()
        )
        folder_field.setMinimumWidth(char_width * _FOLDER_FIELD_CHARS)
        name_field = QLineEdit(target.file_name)
        browse_button = QPushButton('Browse…')
        browse_button.clicked.connect(lambda _checked: self._browse(folder_field))
        grid.addWidget(QLabel(program), row, 0)
        grid.addWidget(folder_field, row, 1)
        grid.addWidget(browse_button, row, 2)
        grid.addWidget(name_field, row, 3)
        self._fields[program] = (folder_field, name_field)

    def field_texts(self, program: str) -> tuple[str, str]:
        """Return the folder and file name typed for one program."""
        folder_field, name_field = self._fields[program]
        return folder_field.text(), name_field.text()

    def set_field_texts(self, program: str, folder: str, file_name: str) -> None:
        """Fill in the folder and file name of one program."""
        folder_field, name_field = self._fields[program]
        folder_field.setText(folder)
        name_field.setText(file_name)

    def use_defaults(self) -> None:
        """Fill every row with its default folder and file name."""
        for program in self._fields:
            target = default_target(program)
            self.set_field_texts(program, str(target.folder), target.file_name)

    def read_targets(self) -> dict[str, LogTarget]:
        """Check the typed settings and return them.

        Returns
        -------
        dict of str to LogTarget
            One location per program; ``.log`` is added to names with
            no suffix.

        Raises
        ------
        ValueError
            If a folder or file name is missing or invalid, or two
            programs would write to the same file.
        """
        targets = {}
        for program in self._fields:
            folder_text, name_text = self.field_texts(program)
            folder = folder_text.strip()
            if not folder:
                raise ValueError(f'{program}: a log folder is needed.')
            try:
                file_name = check_file_name(name_text)
            except ValueError as err:
                raise ValueError(f'{program}: {err}') from err
            targets[program] = LogTarget(folder=Path(folder), file_name=file_name)
        check_targets(targets)
        return targets

    def accept(self) -> None:
        """Keep the settings if valid, otherwise say what is wrong."""
        try:
            self.chosen = self.read_targets()
        except ValueError as err:
            QMessageBox.warning(self, 'Log settings', str(err))
            return
        super().accept()

    def _browse(self, field: QLineEdit) -> None:
        """Choose a folder for one row."""
        chosen = QFileDialog.getExistingDirectory(
            self,
            'Folder for the log file',
            field.text(),
        )
        if chosen:
            field.setText(str(Path(chosen)))


class _ToolTile(QToolButton):
    """Tool button that reports when the pointer enters or leaves it.

    Signals
    -------
    hovered : bool
        True when the pointer enters the button, False when it leaves.
    """

    hovered = pyqtSignal(bool, arguments=['inside'])

    # Qt's method names, overridden.
    def enterEvent(  # noqa: N802  # pylint: disable=invalid-name
        self,
        event: QEnterEvent | None,
    ) -> None:
        """Report that the pointer entered."""
        self.hovered.emit(True)
        super().enterEvent(event)

    def leaveEvent(  # noqa: N802  # pylint: disable=invalid-name
        self,
        a0: QEvent | None,
    ) -> None:
        """Report that the pointer left."""
        self.hovered.emit(False)
        super().leaveEvent(a0)


class LauncherApp(QMainWindow):
    """Launcher window with a button for each tool.

    Parameters
    ----------
    tools : list of Tool
        Tools to show, in button order.
    log_targets : mapping of str to LogTarget or None, default None
        Log location of the launcher (``academic-kit``) and of each
        tool; programs missing from it get ``default_target``.
    log_file : Path or None, default None
        Log file the launcher is writing to now, or None if none could
        be opened.

    Attributes
    ----------
    active_log_file : Path or None
        Log file the launcher is writing to now; named in error
        dialogs.
    """

    def __init__(
        self,
        tools: list[Tool],
        log_targets: Mapping[str, LogTarget] | None = None,
        log_file: Path | None = None,
    ) -> None:
        super().__init__()
        self.setWindowTitle(f'academic-kit {__version__}')
        self.tools = tools
        saved = {} if log_targets is None else dict(log_targets)
        self.log_targets = {
            program: saved.get(program) or default_target(program)
            for program in self._programs()
        }
        self.active_log_file = log_file
        # Started tools, with the log file each was given. Kept so their
        # process handles are not collected while they run (the
        # launcher never waits for them), and so a second copy of a tool
        # gets its own log file.
        self._processes: list[tuple[Path, subprocess.Popen[bytes]]] = []
        self._idle_status = _IDLE_STATUS if tools else _NO_TOOLS_STATUS

        self._build_menu()
        self.status_label = self._build_body()
        self.status_label.setText(self._idle_status)
        smallest = self.minimumSizeHint()
        self.resize(
            max(_START_WIDTH, smallest.width()),
            max(_START_HEIGHT, smallest.height()),
        )

    def _programs(self) -> list[str]:
        """Return the launcher's name, then each tool's."""
        return [LAUNCHER_NAME] + [
            tool.name for tool in self.tools
        ]

    def _build_menu(self) -> None:
        """Add the menu bar with the Settings menu."""
        menu_bar = self.menuBar()
        if menu_bar is None:
            return
        settings_menu = menu_bar.addMenu('&Settings')
        if settings_menu is None:
            return
        logs_action = settings_menu.addAction('&Logs…')
        if logs_action is not None:
            logs_action.triggered.connect(self.open_log_settings)
        folder_action = settings_menu.addAction('Open launcher log folder')
        if folder_action is not None:
            folder_action.triggered.connect(self.open_log_dir)

    def _build_body(self) -> QLabel:
        """Create the centred row of tool buttons and the status text.

        Returns
        -------
        QLabel
            The status label, of fixed height below the buttons.
        """
        body = QWidget()
        layout = QVBoxLayout(body)
        layout.setContentsMargins(
            _WINDOW_MARGIN,
            _WINDOW_MARGIN,
            _WINDOW_MARGIN,
            _WINDOW_MARGIN,
        )
        row = QHBoxLayout()
        row.setSpacing(_TILE_GAP)
        # Stretch on both sides keeps the fixed-size buttons centred.
        row.addStretch(1)
        for tool in self.tools:
            row.addWidget(self._tool_button(tool))
        row.addStretch(1)
        layout.addStretch(1)
        layout.addLayout(row)
        layout.addStretch(1)
        layout.addSpacing(_STATUS_GAP)

        gaps = max(0, len(self.tools) - 1)
        row_width = len(self.tools) * _TILE_SIZE + gaps * _TILE_GAP
        status_width = max(_STATUS_WIDTH, row_width)
        status_label = QLabel()
        status_label.setWordWrap(True)
        status_label.setAlignment(
            Qt.AlignmentFlag.AlignHCenter | Qt.AlignmentFlag.AlignTop
        )
        status_label.setStyleSheet('color: gray;')
        status_label.setMinimumWidth(status_width)
        # Tall enough for the longest text at the narrowest width, so
        # the space above, and the buttons in it, never changes.
        status_label.setFixedHeight(
            self._status_height(status_label, status_width),
        )
        layout.addWidget(status_label)
        self.setCentralWidget(body)
        return status_label

    def _status_texts(self) -> list[str]:
        """List every text the status label can show."""
        texts = [
            _IDLE_STATUS,
            _NO_TOOLS_STATUS,
        ]
        for tool in self.tools:
            texts.append(tool.description)
            texts.append(_opened_status(tool))
        return texts

    def _status_height(self, label: QLabel, width: int) -> int:
        """Return the height the tallest status text needs."""
        metrics = label.fontMetrics()
        flags = int(Qt.TextFlag.TextWordWrap.value) | int(
            Qt.AlignmentFlag.AlignHCenter.value
        )
        area = QRect(0, 0, width, _MEASURE_HEIGHT)
        return max(
            metrics.boundingRect(area, flags, text).height()
            for text in self._status_texts()
        )

    def _tool_button(self, tool: Tool) -> QToolButton:
        """Create the square button that opens one tool.

        Hovering over it shows the tool's description in the status
        line.
        """
        button = _ToolTile()
        button.setText(tool.name)
        button.setToolTip(tool.description)
        button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextUnderIcon)
        button.setIconSize(QSize(_ICON_SIZE, _ICON_SIZE))
        button.setFixedSize(_TILE_SIZE, _TILE_SIZE)
        button.clicked.connect(lambda _checked: self.open_tool(tool))
        button.hovered.connect(lambda inside: self._show_hover(tool, inside=inside))
        return button

    def _show_hover(self, tool: Tool, *, inside: bool) -> None:
        """Show a tool's description while the pointer is on it."""
        self.status_label.setText(tool.description if inside else self._idle_status)

    def open_tool(self, tool: Tool) -> None:
        """Start a tool in its own process, reporting failures.

        Parameters
        ----------
        tool : Tool
            Tool to start.
        """
        log_file = self._free_log_file(self.log_targets[tool.name].path)
        _LOGGER.info(
            'Starting %s (python -m %s), log %s',
            tool.name,
            tool.module,
            log_file,
        )
        try:
            process = launch_tool(tool, log_file)
        except OSError as err:
            _LOGGER.exception('Cannot start %s', tool.name)
            QMessageBox.critical(self, 'Cannot open tool', f'{tool.name}\n\n{err}')
            return
        _LOGGER.info('Started %s as process %s', tool.name, process.pid)
        self._processes.append((log_file, process))
        self.status_label.setText(_opened_status(tool))

    def _free_log_file(self, wanted: Path) -> Path:
        """Return a log file no running tool started here is using.

        Two processes rotating one log file would stop each other's
        logging, so a second copy of a tool gets ``name-2.log``, a third
        ``name-3.log``, and so on.

        Parameters
        ----------
        wanted : Path
            The tool's log file from the settings.

        Returns
        -------
        Path
            ``wanted`` if no running copy uses it, else the first free
            numbered name next to it.
        """
        busy = {
            log_file for log_file, process in self._processes
            if process.poll() is None
        }
        candidate = wanted
        number = 2
        while candidate in busy:
            candidate = wanted.with_name(f'{wanted.stem}-{number}{wanted.suffix}')
            number += 1
        return candidate

    def open_log_settings(self) -> None:
        """Show the log settings dialog and apply what is chosen."""
        dialog = LogSettingsDialog(self.log_targets, self)
        if dialog.exec() == QDialog.DialogCode.Accepted:
            self.apply_log_settings(dialog.chosen)

    def apply_log_settings(self, targets: Mapping[str, LogTarget]) -> bool:
        """Use new log locations from now on, and remember them.

        The launcher switches its own log at once; tools opened
        afterwards get their new file. Tools already open keep theirs.

        Parameters
        ----------
        targets : mapping of str to LogTarget
            New log location of each program.

        Returns
        -------
        bool
            False if the launcher's new log file cannot be opened; the
            old settings are then kept.
        """
        new_file = targets[LAUNCHER_NAME].path
        if new_file != self.active_log_file:
            opened = setup_logging(new_file)
            if opened is None:
                # Keep logging where it was rather than not at all.
                if self.active_log_file is not None:
                    setup_logging(self.active_log_file)
                QMessageBox.critical(
                    self,
                    'Cannot use this log file',
                    f'No log file can be written at:\n{new_file}',
                )
                return False
            self.active_log_file = opened
        _LOGGER.info(
            'Log settings changed: %s',
            {
                program: str(target.path) for program, target in targets.items()
            },
        )
        self.log_targets = dict(targets)
        try:
            save_log_settings(self.log_targets)
        except OSError:
            _LOGGER.exception('Cannot remember the log settings')
            QMessageBox.warning(
                self,
                'Log settings not remembered',
                'The new settings are used now, but could not be saved for '
                'the next start.',
            )
        return True

    def open_log_dir(self) -> None:
        """Open the launcher's log folder in the file explorer."""
        folder = self.log_targets[LAUNCHER_NAME].folder
        if not folder.is_dir():
            QMessageBox.information(
                self,
                'No log folder',
                f'The log folder does not exist yet:\n{folder}',
            )
            return
        _open_folder(folder)


def _opened_status(tool: Tool) -> str:
    """Return the status text shown after a tool is opened."""
    return f'Opened {tool.name}.'


def _open_folder(folder: Path) -> None:
    """Open a folder in the system file explorer."""
    if sys.platform == 'win32':
        # The folder is the log folder the user chose.
        os.startfile(folder)  # noqa: S606
    else:
        opener = 'open' if sys.platform == 'darwin' else 'xdg-open'
        # Not waited for: the explorer runs on its own.
        subprocess.Popen(  # pylint: disable=consider-using-with
            [
                opener,
                str(folder),
            ]
        )


def _install_error_dialog(current_log_file: Callable[[], Path | None]) -> None:
    """Log errors nothing caught and show them in a message box.

    Without this, PyQt6 ends the program when a button's action raises.

    Parameters
    ----------
    current_log_file : callable
        Returns the log file in use when the error happens, or None; it
        changes when the log settings do.
    """

    def report(
        error_type: type[BaseException],
        error: BaseException,
        traceback: TracebackType | None,
    ) -> None:
        log_uncaught(error_type, error, traceback)
        log_file = current_log_file()
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
    """Open the launcher window, logging the run to a file."""
    log_file = setup_logging(load_log_settings([LAUNCHER_NAME])[LAUNCHER_NAME].path)
    _LOGGER.info(
        'academic-kit %s started (Python %s, %s); log %s',
        __version__,
        platform.python_version(),
        platform.platform(),
        log_file,
    )
    app = QApplication(sys.argv)
    window: LauncherApp | None = None

    def current_log_file() -> Path | None:
        return log_file if window is None else window.active_log_file

    _install_error_dialog(current_log_file)
    try:
        tools = discover_tools()
    except ValueError as err:
        _LOGGER.exception('A tool is installed incorrectly')
        QMessageBox.critical(None, 'A tool is installed incorrectly', str(err))
        raise SystemExit(1) from err
    _LOGGER.info(
        'Found %d tool(s): %s',
        len(tools),
        ', '.join(tool.name for tool in tools) or 'none',
    )
    targets = load_log_settings(
        [LAUNCHER_NAME] + [
            tool.name for tool in tools
        ]
    )
    window = LauncherApp(tools, targets, log_file)
    window.show()
    app.exec()
    _LOGGER.info('academic-kit closed')


if __name__ == '__main__':
    main()
