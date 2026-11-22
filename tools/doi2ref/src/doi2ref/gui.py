"""Window (PyQt6) for turning DOIs into formatted references.

Layout: two equal-width text panels (DOIs, references) and a sidebar of
options and actions, with the progress bar and status text at the
sidebar's bottom. The sidebar has a fixed width, so the panels never
move when the status text changes. Convert appends each new reference
to the right panel as it arrives. Lookups run on a background thread so
the window stays responsive; results come back through a queue that a
timer polls, because widgets must only be touched from the main thread.
"""

import logging
import platform
import queue
import sys
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from types import TracebackType

from PyQt6.QtCore import Qt, QTimer, pyqtSignal
from PyQt6.QtGui import QFontDatabase, QKeyEvent, QKeySequence, QShortcut
from PyQt6.QtWidgets import (
    QApplication,
    QButtonGroup,
    QCheckBox,
    QComboBox,
    QDoubleSpinBox,
    QFormLayout,
    QGroupBox,
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QRadioButton,
    QSpinBox,
    QVBoxLayout,
    QWidget,
)

from doi2ref import __version__
from doi2ref.core import (
    CITATION_STYLES,
    DEFAULT_DELAY_SECONDS,
    DEFAULT_RETRIES,
    LOCALES,
    FetchResult,
    OutputFormat,
    RequestOptions,
    Source,
    append_reference,
    extract_dois,
    fetch_references,
)
from doi2ref.logs import log_uncaught, setup_logging

_LOGGER = logging.getLogger(__name__)

_POLL_INTERVAL_MS = 100
_START_WIDTH = 1200
_START_HEIGHT = 680
_SAMPLE_DOIS = '10.1038/nature14539\nhttps://doi.org/10.1126/science.aaa8415\n'
_MAX_DELAY_SECONDS = 60.0
_DELAY_STEP_SECONDS = 0.5
_MAX_RETRIES = 10
# Width of the sidebar, and smallest width of each text panel.
_SIDEBAR_WIDTH = 250
_MIN_PANEL_WIDTH = 220
_CONVERT_LABEL = 'Convert  (Ctrl+Enter)'
_STOP_LABEL = 'Stop  (Ctrl+Enter)'
_ENTER_KEYS = frozenset(
    {
        Qt.Key.Key_Return,
        Qt.Key.Key_Enter,
    }
)
# Failures listed in the warning dialog before "... and N more".
_MAX_FAILURES_SHOWN = 15


@dataclass(frozen=True, slots=True, kw_only=True)
class _ResultMessage:
    """Message from the worker thread: one DOI has finished."""

    result: FetchResult
    done: int
    total: int


@dataclass(frozen=True, slots=True, kw_only=True)
class _FinishedMessage:
    """Message from the worker thread: the batch has ended.

    Attributes
    ----------
    stopped : bool
        True if not every DOI was looked up.
    error : str or None, default None
        The unexpected error that ended the batch, or None if it ended
        normally or was stopped by the user.
    """

    stopped: bool
    error: str | None = None


@dataclass(frozen=True, slots=True, kw_only=True)
class _Batch:
    """Settings captured when Convert is pressed.

    Kept separately from the sidebar so changing an option mid-batch
    cannot change how the remaining results are shown.
    """

    options: RequestOptions
    numbered: bool


class _TextPanel(QPlainTextEdit):
    """Text area that turns Ctrl+Enter into a request to convert.

    The text area would otherwise take the key press itself, before the
    window's Ctrl+Enter shortcut could see it.

    Signals
    -------
    convert_requested
        Ctrl+Enter was pressed in the text area.
    """

    convert_requested = pyqtSignal(arguments=[])

    # Qt's method name, overridden.
    def keyPressEvent(  # noqa: N802  # pylint: disable=invalid-name
        self,
        e: QKeyEvent | None,
    ) -> None:
        """Report Ctrl+Enter; handle every other key as usual."""
        if (
            e is not None
            and e.key() in _ENTER_KEYS
            and e.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            self.convert_requested.emit()
            return
        super().keyPressEvent(e)


class Doi2RefApp(QMainWindow):
    """Main application window.

    Parameters
    ----------
    log_file : Path or None, default None
        Log file the program writes to, named in error messages; None
        if there is none.
    """

    def __init__(self, log_file: Path | None = None) -> None:
        super().__init__()
        self.setWindowTitle(f'doi2ref {__version__} — DOI to reference')
        self._log_file = log_file
        self._messages: queue.Queue[_ResultMessage | _FinishedMessage] = queue.Queue()
        self._stop_event = threading.Event()
        self._batch: _Batch | None = None
        self._batch_failures: list[FetchResult] = []
        self._batch_done = 0
        self._batch_started = 0.0
        # Case-folded DOIs converted successfully, for the "Remove
        # finished" button.
        self._finished_dois: set[str] = set()
        self._timer = QTimer(self)
        self._timer.setInterval(_POLL_INTERVAL_MS)
        self._timer.timeout.connect(self._poll_messages)

        self.doi_text = self._text_panel()
        self.doi_text.setPlainText(_SAMPLE_DOIS)
        self.output_text = self._text_panel()
        self.format_buttons: dict[OutputFormat, QRadioButton] = {}
        self.style_combo = QComboBox()
        self.locale_combo = QComboBox()
        self.numbered_check = QCheckBox('Number [1], [2] …')
        self.source_checks: dict[Source, QCheckBox] = {}
        self.delay_spin = QDoubleSpinBox()
        self.retries_spin = QSpinBox()
        self.convert_button = QPushButton(_CONVERT_LABEL)
        self.progress = QProgressBar()
        self.status_label = QLabel('Paste DOIs on the left, then Convert.')

        self.sidebar = self._build_sidebar()
        self._build_layout()
        self._bind_shortcuts()
        self._update_style_state()
        self.resize(_START_WIDTH, _START_HEIGHT)

    def _text_panel(self) -> _TextPanel:
        """Create a scrollable text area in a fixed-width font."""
        text = _TextPanel()
        text.setFont(QFontDatabase.systemFont(QFontDatabase.SystemFont.FixedFont))
        text.setMinimumWidth(_MIN_PANEL_WIDTH)
        text.convert_requested.connect(self.convert_or_stop)
        return text

    def _build_layout(self) -> None:
        """Place the two equal-width text panels and the sidebar."""
        body = QWidget()
        layout = QHBoxLayout(body)
        for title, text in (
            ('DOIs', self.doi_text),
            ('References', self.output_text),
        ):
            box = QGroupBox(title)
            box_layout = QVBoxLayout(box)
            box_layout.addWidget(text)
            # Equal stretch gives both panels the same width.
            layout.addWidget(box, stretch=1)
        layout.addWidget(self.sidebar)
        self.setCentralWidget(body)

    def _build_sidebar(self) -> QWidget:
        """Create the options and actions sidebar, of fixed width."""
        sidebar = QWidget()
        layout = QVBoxLayout(sidebar)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self._build_format_box())
        layout.addWidget(self._build_style_box())
        layout.addWidget(self._build_source_box())
        layout.addWidget(self._build_request_box())
        layout.addWidget(self._build_action_box())
        layout.addStretch(1)
        # The progress bar and status text sit at the bottom, level with
        # the lower edge of the text panels.
        layout.addWidget(self.progress)
        self.status_label.setWordWrap(True)
        layout.addWidget(self.status_label)
        # A fixed width stops the sidebar following the length of each
        # status message, which would shift the panels beside it.
        sidebar.setFixedWidth(_SIDEBAR_WIDTH)
        return sidebar

    def _build_format_box(self) -> QGroupBox:
        """Create the reference-type radio buttons."""
        box = QGroupBox('Reference type')
        layout = QVBoxLayout(box)
        group = QButtonGroup(box)
        for output_format in OutputFormat:
            button = QRadioButton(output_format.value)
            button.toggled.connect(self._update_style_state)
            group.addButton(button)
            layout.addWidget(button)
            self.format_buttons[output_format] = button
        self.format_buttons[OutputFormat.BIBTEX].setChecked(True)
        return box

    def _build_style_box(self) -> QGroupBox:
        """Create the citation style, language and numbering fields."""
        box = QGroupBox('Citation style')
        layout = QVBoxLayout(box)
        self.style_combo.addItems(list(CITATION_STYLES))
        layout.addWidget(self.style_combo)
        layout.addWidget(QLabel('Language'))
        self.locale_combo.addItems(list(LOCALES))
        layout.addWidget(self.locale_combo)
        layout.addWidget(self.numbered_check)
        return box

    def _build_source_box(self) -> QGroupBox:
        """Create the source check boxes, all ticked."""
        box = QGroupBox('Source')
        layout = QVBoxLayout(box)
        for source in Source:
            check = QCheckBox(source.value)
            check.setChecked(True)
            layout.addWidget(check)
            self.source_checks[source] = check
        return box

    def _build_request_box(self) -> QGroupBox:
        """Create the waiting-time and retry controls."""
        box = QGroupBox('Requests')
        layout = QFormLayout(box)
        self.delay_spin.setRange(0, _MAX_DELAY_SECONDS)
        self.delay_spin.setSingleStep(_DELAY_STEP_SECONDS)
        self.delay_spin.setDecimals(1)
        self.delay_spin.setValue(DEFAULT_DELAY_SECONDS)
        self.retries_spin.setRange(0, _MAX_RETRIES)
        self.retries_spin.setValue(DEFAULT_RETRIES)
        layout.addRow('Wait (s)', self.delay_spin)
        layout.addRow('Retries', self.retries_spin)
        return box

    def _build_action_box(self) -> QGroupBox:
        """Create the action buttons."""
        box = QGroupBox('Actions')
        layout = QVBoxLayout(box)
        self.convert_button.clicked.connect(self.convert_or_stop)
        layout.addWidget(self.convert_button)
        buttons = (
            ('Copy references', self.copy_output),
            ('Remove finished', self.remove_finished),
            ('Clear', self.clear),
        )
        for label, action in buttons:
            button = QPushButton(label)
            button.clicked.connect(action)
            layout.addWidget(button)
        return box

    def _bind_shortcuts(self) -> None:
        """Bind Ctrl+Enter (on either Enter key) to Convert or Stop.

        The text panels handle the keys themselves (see _TextPanel);
        this covers the rest of the window.
        """
        for keys in ('Ctrl+Return', 'Ctrl+Enter'):
            shortcut = QShortcut(QKeySequence(keys), self)
            shortcut.activated.connect(self.convert_or_stop)

    def _selected_format(self) -> OutputFormat:
        """Return the output format chosen in the sidebar."""
        for output_format, button in self.format_buttons.items():
            if button.isChecked():
                return output_format
        return OutputFormat.BIBTEX

    def _update_style_state(self) -> None:
        """Enable style options only for formatted citations."""
        if len(self.format_buttons) < len(OutputFormat):
            # Still building the radio buttons.
            return
        is_citation = self._selected_format() is OutputFormat.CITATION
        for widget in (
            self.style_combo,
            self.locale_combo,
            self.numbered_check,
        ):
            widget.setEnabled(is_citation)

    def _request_options(self) -> RequestOptions:
        """Build request options from the sidebar controls.

        Returns
        -------
        RequestOptions
            Options for the next batch.

        Raises
        ------
        ValueError
            If no source is ticked.
        """
        sources = tuple(
            source for source, check in self.source_checks.items()
            if check.isChecked()
        )
        if not sources:
            raise ValueError('Tick at least one source.')
        return RequestOptions(
            output_format=self._selected_format(),
            style=CITATION_STYLES[self.style_combo.currentText()],
            locale=LOCALES[self.locale_combo.currentText()],
            sources=sources,
            delay_seconds=self.delay_spin.value(),
            retries=self.retries_spin.value(),
        )

    def convert_or_stop(self) -> None:
        """Start a batch, or ask the running batch to stop."""
        if self._batch is not None:
            self._stop_event.set()
            _LOGGER.info('Stop requested')
            self.convert_button.setEnabled(False)
            self.status_label.setText('Stopping after the current request…')
            return
        self._start_batch()

    def _start_batch(self) -> None:
        """Look up every DOI in the left panel on a worker thread.

        Shows a dialog instead when the panel holds no DOI or a sidebar
        option is invalid.
        """
        dois = extract_dois(self.doi_text.toPlainText())
        if not dois:
            QMessageBox.information(self, 'doi2ref', 'No DOIs found in the left panel.')
            return
        try:
            options = self._request_options()
        except ValueError as err:
            QMessageBox.critical(self, 'doi2ref', str(err))
            return

        self._batch = _Batch(options=options, numbered=self.numbered_check.isChecked())
        self._batch_failures = []
        self._batch_done = 0
        self._stop_event.clear()
        self.convert_button.setText(_STOP_LABEL)
        self.progress.setRange(0, len(dois))
        self.progress.setValue(0)
        self.status_label.setText(f'Looking up {len(dois)} DOI(s)…')
        self._batch_started = time.perf_counter()
        _LOGGER.info('Batch started: %d DOI(s) with %s', len(dois), options)

        worker = threading.Thread(
            target=self._run_lookups,
            args=(
                dois,
                options,
            ),
            daemon=True,
        )
        worker.start()
        self._timer.start()

    def _run_lookups(self, dois: list[str], options: RequestOptions) -> None:
        """Fetch references on the worker thread and report back.

        Parameters
        ----------
        dois : list of str
            DOIs to look up.
        options : RequestOptions
            Request settings.
        """
        total = len(dois)
        processed = 0
        crash: str | None = None

        # A closure gives the callback access to ``total``, the queue
        # and the ``processed`` counter.
        def report(index: int, result: FetchResult) -> None:
            nonlocal processed
            processed = index + 1
            self._messages.put(
                _ResultMessage(
                    result=result,
                    done=index + 1,
                    total=total,
                ),
            )

        # Always send the finished message, even if something unexpected
        # goes wrong, so the window never stays stuck in "Stop" mode.
        try:
            fetch_references(
                dois,
                options,
                on_result=report,
                stop_event=self._stop_event,
            )
        # Logged with its traceback rather than lost with the thread;
        # the finally clause still frees the window.
        except Exception as err:  # pylint: disable=broad-exception-caught
            _LOGGER.exception('Lookups stopped by an unexpected error')
            crash = f'{type(err).__name__}: {err}'
        finally:
            self._messages.put(
                _FinishedMessage(stopped=processed < total, error=crash),
            )

    def _poll_messages(self) -> None:
        """Apply worker-thread messages to the widgets.

        Runs on the timer until the batch has finished.
        """
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                return
            if isinstance(message, _ResultMessage):
                self._show_result(message)
            else:
                self._timer.stop()
                self._finish_batch(message)
                return

    def _show_result(self, message: _ResultMessage) -> None:
        """Append one reference to the right panel and update progress.

        A failed lookup is recorded for the end-of-batch summary
        instead.

        Parameters
        ----------
        message : _ResultMessage
            The finished lookup and the batch progress.
        """
        assert self._batch is not None, 'results arrive only during a batch'
        result = message.result

        if result.reference is not None:
            folded_doi = (
                result
                .doi
                .casefold()
            )
            self._finished_dois.add(folded_doi)
            combined = append_reference(
                self.output_text.toPlainText(),
                result.reference,
                self._batch.options.output_format,
                numbered=self._batch.numbered,
            )
            self.output_text.setPlainText(combined + '\n')
            scroll_bar = self.output_text.verticalScrollBar()
            if scroll_bar is not None:
                scroll_bar.setValue(scroll_bar.maximum())
        else:
            self._batch_failures.append(result)

        self._batch_done = message.done
        self.progress.setValue(message.done)
        self.status_label.setText(
            f'{message.done} of {message.total} looked up, '
            f'{len(self._batch_failures)} failed.'
        )

    def _finish_batch(self, message: _FinishedMessage) -> None:
        """Reset the controls and report failures.

        Parameters
        ----------
        message : _FinishedMessage
            Whether the batch was stopped early.
        """
        self._batch = None
        self.convert_button.setText(_CONVERT_LABEL)
        self.convert_button.setEnabled(True)

        if message.error is not None:
            prefix = 'Stopped by an error'
        elif message.stopped:
            prefix = 'Stopped'
        else:
            prefix = 'Done'
        summary = (
            f'{prefix}: {self._batch_done} looked up, '
            f'{len(self._batch_failures)} failed.'
        )
        self.status_label.setText(summary)
        _LOGGER.info(
            'Batch finished in %.1f s: %s',
            time.perf_counter() - self._batch_started,
            summary,
        )
        if message.error is not None:
            QMessageBox.critical(
                self,
                'Lookups stopped by an error',
                'The batch ended early because of an unexpected error:\n\n'
                f'{message.error}{self._log_hint()}',
            )
        if self._batch_failures:
            QMessageBox.warning(
                self,
                'Some DOIs failed',
                self._failure_summary() + self._log_hint(),
            )

    def _failure_summary(self) -> str:
        """Describe the failed DOIs, short enough to fit on screen.

        Returns
        -------
        str
            A heading, one line per failure up to
            ``_MAX_FAILURES_SHOWN``, a count of the rest and a hint to
            retry.
        """
        shown = self._batch_failures[:_MAX_FAILURES_SHOWN]
        lines = [
            f'{result.doi} — {result.error}' for result in shown
        ]
        hidden_count = len(self._batch_failures) - len(shown)
        if hidden_count:
            lines.append(f'… and {hidden_count} more.')
        lines.append('\nThey stay in the DOI panel; press Convert to try again.')
        return 'These DOIs could not be converted:\n\n' + '\n'.join(lines)

    def _log_hint(self) -> str:
        """Return a sentence naming the log file, or '' if none."""
        if self._log_file is None:
            return ''
        return f'\n\nDetails are in the log file:\n{self._log_file}'

    def copy_output(self) -> None:
        """Copy the whole references panel to the clipboard."""
        text = (
            self.output_text
            .toPlainText()
            .strip()
        )
        if not text:
            self.status_label.setText('Nothing to copy.')
            return
        clipboard = QApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(text)
        self.status_label.setText('References copied to the clipboard.')

    def remove_finished(self) -> None:
        """Remove DOIs already converted from the left panel.

        The panel is rewritten as one bare DOI per line, so any other
        text in it, such as links or prose around the DOIs, is dropped.
        """
        if self._batch is not None:
            self.status_label.setText('Wait for the current batch to finish.')
            return

        dois = extract_dois(self.doi_text.toPlainText())
        remaining = [
            doi for doi in dois
            if doi.casefold() not in self._finished_dois
        ]
        removed_count = len(dois) - len(remaining)
        self.doi_text.setPlainText('\n'.join(remaining) + '\n' if remaining else '')
        self.status_label.setText(
            f'Removed {removed_count} finished DOI(s); {len(remaining)} left.'
        )

    def clear(self) -> None:
        """Empty both text panels and forget finished DOIs."""
        if self._batch is not None:
            self.status_label.setText('Wait for the current batch to finish.')
            return
        self.doi_text.clear()
        self.output_text.clear()
        self._finished_dois.clear()
        self.progress.setValue(0)
        self.status_label.setText('Cleared.')


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
    """Open the doi2ref window, logging the run to a file."""
    log_file = setup_logging()
    _LOGGER.info(
        'doi2ref %s started (Python %s, %s); log %s',
        __version__,
        platform.python_version(),
        platform.platform(),
        log_file,
    )
    app = QApplication(sys.argv)
    _install_error_dialog(log_file)
    window = Doi2RefApp(log_file)
    window.show()
    app.exec()
    _LOGGER.info('doi2ref closed')


if __name__ == '__main__':
    main()
