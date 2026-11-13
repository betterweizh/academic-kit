"""Tkinter window for turning DOIs into formatted references.

Layout: two equal-width text panels (DOIs, references) and a sidebar of
options and actions, with the progress bar and status text at the
sidebar's bottom. The window cannot shrink below the size that shows
every control. Convert appends each new reference to the right panel as
it arrives. Lookups run on a background thread so the window stays
responsive; results come back through a queue polled with ``after``,
because Tk widgets must only be touched from the main thread.
"""

import queue
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from tkinter import font as tkfont
from tkinter import messagebox, ttk
from tkinter.scrolledtext import ScrolledText

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

_POLL_INTERVAL_MS = 100
_WINDOWS_TEXT_FONT = (
    'Consolas',
    10,
)
_OTHER_TEXT_FONT = (
    'Courier',
    11,
)
_TEXT_FONT = (
    _WINDOWS_TEXT_FONT
    if sys.platform == 'win32'
    else _OTHER_TEXT_FONT
)
_SAMPLE_DOIS = '10.1038/nature14539\nhttps://doi.org/10.1126/science.aaa8415\n'
_MAX_DELAY_SECONDS = 60.0
_MAX_RETRIES = 10
# Tk reports this many pixels per inch on a 100 % (unscaled) screen.
_BASE_DPI = 96
# These two sizes are for a 96-DPI (100 %) screen and are scaled to the
# real screen, so Windows display scaling does not squeeze the layout.
_SIDEBAR_TEXT_WIDTH = 230
_MIN_PANEL_WIDTH = 220
# Paddings in screen pixels, used as they are without scaling.
_WINDOW_PADDING = 8
_PANEL_GAP = 8
_BOX_PADDING = 6
_SIDEBAR_PADX = (
    0,
    _WINDOW_PADDING,
)
_LEFT_PANEL_PADX = (
    0,
    _PANEL_GAP // 2,
)
_RIGHT_PANEL_PADX = (
    _PANEL_GAP // 2,
    0,
)
_GAP_BELOW_BOX = (
    0,
    8,
)
_GAP_ABOVE_ROW = (
    6,
    0,
)
_GAP_ABOVE_STATUS = (
    4,
    0,
)
_TEXT_PANEL_COLUMNS = (
    0,
    1,
)
# A small requested size in characters and lines lets the window, not
# the text widget's default 80x24 characters, decide the panel size.
_TEXT_PANEL_REQUESTED_WIDTH = 10
_TEXT_PANEL_REQUESTED_HEIGHT = 5
# Status messages wrap; leave room for this many lines beyond the first.
_EXTRA_STATUS_LINES = 2
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
    """Message from the worker thread: the batch has ended."""

    stopped: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class _Batch:
    """Settings captured when Convert is pressed.

    Kept separately from the sidebar so changing an option mid-batch
    cannot change how the remaining results are shown.
    """

    options: RequestOptions
    numbered: bool


@dataclass(frozen=True, slots=True, kw_only=True)
class _TextPanel:
    """A labelled frame and the text widget it holds."""

    frame: ttk.LabelFrame
    text: ScrolledText


class Doi2RefApp:
    """Main application window.

    Parameters
    ----------
    root : tkinter.Tk
        Top-level window to build the interface in.
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f'doi2ref {__version__} — DOI to reference')
        self.root.geometry('1200x680')
        self._scale = max(1.0, self.root.winfo_fpixels('1i') / _BASE_DPI)

        self._messages: queue.Queue[_ResultMessage | _FinishedMessage] = queue.Queue()
        self._stop_event = threading.Event()
        self._batch: _Batch | None = None
        self._batch_failures: list[FetchResult] = []
        self._batch_done = 0
        # Case-folded DOIs converted successfully, for the "Remove
        # finished" button.
        self._finished_dois: set[str] = set()

        self.format_var = tk.StringVar(value=OutputFormat.BIBTEX.value)
        self.style_var = tk.StringVar(value=next(iter(CITATION_STYLES)))
        self.locale_var = tk.StringVar(value=next(iter(LOCALES)))
        self.numbered_var = tk.BooleanVar(value=False)
        self.source_vars = {
            source: tk.BooleanVar(value=True) for source in Source
        }
        self.delay_var = tk.StringVar(value=str(DEFAULT_DELAY_SECONDS))
        self.retries_var = tk.StringVar(value=str(DEFAULT_RETRIES))
        self.status_var = tk.StringVar(value='Paste DOIs on the left, then Convert.')

        self._build_layout()
        self._bind_shortcuts()
        self._update_style_state()

    def _build_layout(self) -> None:
        """Create the sidebar and the two equal-width text panels."""
        # The sidebar is packed first so it keeps its width when the
        # window shrinks; the panels take the remaining space.
        sidebar = self._build_sidebar(self.root)
        sidebar.pack(
            side=tk.RIGHT,
            fill=tk.Y,
            padx=_SIDEBAR_PADX,
            pady=_WINDOW_PADDING,
        )

        panels = ttk.Frame(self.root)
        panels.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
            padx=_WINDOW_PADDING,
            pady=_WINDOW_PADDING,
        )
        # A shared "uniform" group makes grid give both columns exactly
        # the same width, however the window is resized.
        panels.columnconfigure(
            _TEXT_PANEL_COLUMNS,
            weight=1,
            uniform='text_panels',
        )
        panels.rowconfigure(0, weight=1)

        doi_panel = self._build_text_panel(panels, 'DOIs')
        doi_frame = doi_panel.frame
        doi_frame.grid(
            row=0,
            column=0,
            sticky=tk.NSEW,
            padx=_LEFT_PANEL_PADX,
        )
        self.doi_text = doi_panel.text
        self.doi_text.insert('1.0', _SAMPLE_DOIS)

        output_panel = self._build_text_panel(panels, 'References')
        output_frame = output_panel.frame
        output_frame.grid(
            row=0,
            column=1,
            sticky=tk.NSEW,
            padx=_RIGHT_PANEL_PADX,
        )
        self.output_text = output_panel.text

        self._set_minimum_size(sidebar)

    def _set_minimum_size(self, sidebar: ttk.Frame) -> None:
        """Stop the window shrinking below what shows every control.

        The height must fit the whole sidebar, plus room for the status
        text to wrap onto extra lines; the width must fit the sidebar
        and two usable text panels.

        Parameters
        ----------
        sidebar : tkinter.ttk.Frame
            The built sidebar, whose requested size sets the limits.
        """
        self.root.update_idletasks()
        line_height = tkfont.nametofont('TkDefaultFont').metrics('linespace')

        min_width = (
            sidebar.winfo_reqwidth()
            + 2 * self._scaled_pixels(_MIN_PANEL_WIDTH)
            + _PANEL_GAP
            + 3 * _WINDOW_PADDING
        )
        min_height = (
            sidebar.winfo_reqheight()
            + _EXTRA_STATUS_LINES * line_height
            + 2 * _WINDOW_PADDING
        )
        self.root.minsize(min_width, min_height)

    def _scaled_pixels(self, pixels: int) -> int:
        """Scale a size given for a 96-DPI screen to this screen.

        Parameters
        ----------
        pixels : int
            Size in pixels at 96 DPI.

        Returns
        -------
        int
            Size in pixels on the current screen, never smaller than
            ``pixels``.
        """
        return round(pixels * self._scale)

    def _build_text_panel(self, parent: tk.Misc, title: str) -> _TextPanel:
        """Create a labelled, scrollable text area, not yet placed.

        Parameters
        ----------
        parent : tkinter.Misc
            Widget to place the panel in.
        title : str
            Label shown above the text area.

        Returns
        -------
        _TextPanel
            The outer frame and the text widget inside it.
        """
        frame = ttk.LabelFrame(parent, text=title, padding=4)
        text = ScrolledText(
            frame,
            wrap=tk.WORD,
            font=_TEXT_FONT,
            undo=True,
            width=_TEXT_PANEL_REQUESTED_WIDTH,
            height=_TEXT_PANEL_REQUESTED_HEIGHT,
        )
        text.pack(fill=tk.BOTH, expand=True)
        # Handle Ctrl+Enter here and return "break", so the text
        # widget's own binding does not also insert a line break.
        text.bind('<Control-Return>', self._on_convert_shortcut)
        return _TextPanel(frame=frame, text=text)

    def _build_sidebar(self, parent: tk.Misc) -> ttk.Frame:
        """Create the options and actions sidebar, not yet packed.

        Parameters
        ----------
        parent : tkinter.Misc
            Widget to place the sidebar in.

        Returns
        -------
        tkinter.ttk.Frame
            The sidebar frame.
        """
        sidebar = ttk.Frame(parent)
        self._build_format_box(sidebar)
        self._build_style_box(sidebar)
        self._build_source_box(sidebar)
        self._build_request_box(sidebar)
        self._build_action_box(sidebar)
        self._build_progress_box(sidebar)
        return sidebar

    def _build_progress_box(self, sidebar: ttk.Frame) -> None:
        """Add the progress bar and status text at the sidebar's bottom.

        Packing at the bottom lines them up with the lower edge of the
        text panels, whatever the window height.

        Parameters
        ----------
        sidebar : tkinter.ttk.Frame
            Sidebar to add the widgets to.
        """
        box = ttk.Frame(sidebar)
        box.pack(side=tk.BOTTOM, fill=tk.X)
        # The bar is as long as the status text's wrap width, so the
        # sidebar is always at least that wide. Without it, the sidebar
        # would follow the width of each status message and the text
        # panels beside it would shift.
        self.progress = ttk.Progressbar(
            box,
            mode='determinate',
            length=self._scaled_pixels(_SIDEBAR_TEXT_WIDTH),
        )
        self.progress.pack(fill=tk.X)
        # Wrap long messages so they do not widen the sidebar.
        ttk.Label(
            box,
            textvariable=self.status_var,
            wraplength=self._scaled_pixels(_SIDEBAR_TEXT_WIDTH),
            justify=tk.LEFT,
        ).pack(anchor=tk.W, pady=_GAP_ABOVE_STATUS)

    def _build_format_box(self, sidebar: ttk.Frame) -> None:
        """Add the reference-type radio buttons.

        Parameters
        ----------
        sidebar : tkinter.ttk.Frame
            Sidebar to add the group to.
        """
        box = ttk.LabelFrame(sidebar, text='Reference type', padding=_BOX_PADDING)
        box.pack(fill=tk.X, pady=_GAP_BELOW_BOX)
        for output_format in OutputFormat:
            ttk.Radiobutton(
                box,
                text=output_format.value,
                value=output_format.value,
                variable=self.format_var,
                command=self._update_style_state,
            ).pack(anchor=tk.W)

    def _build_style_box(self, sidebar: ttk.Frame) -> None:
        """Add the citation style, language and numbering controls.

        Parameters
        ----------
        sidebar : tkinter.ttk.Frame
            Sidebar to add the group to.
        """
        box = ttk.LabelFrame(sidebar, text='Citation style', padding=_BOX_PADDING)
        box.pack(fill=tk.X, pady=_GAP_BELOW_BOX)
        self.style_combo = ttk.Combobox(
            box,
            textvariable=self.style_var,
            values=list(CITATION_STYLES),
            state='readonly',
        )
        self.style_combo.pack(fill=tk.X)
        ttk.Label(box, text='Language').pack(anchor=tk.W, pady=_GAP_ABOVE_ROW)
        self.locale_combo = ttk.Combobox(
            box,
            textvariable=self.locale_var,
            values=list(LOCALES),
            state='readonly',
        )
        self.locale_combo.pack(fill=tk.X)
        self.numbered_check = ttk.Checkbutton(
            box,
            text='Number [1], [2] …',
            variable=self.numbered_var,
        )
        self.numbered_check.pack(anchor=tk.W, pady=_GAP_ABOVE_ROW)

    def _build_source_box(self, sidebar: ttk.Frame) -> None:
        """Add the source check boxes.

        Parameters
        ----------
        sidebar : tkinter.ttk.Frame
            Sidebar to add the group to.
        """
        box = ttk.LabelFrame(sidebar, text='Source', padding=_BOX_PADDING)
        box.pack(fill=tk.X, pady=_GAP_BELOW_BOX)
        for source, variable in self.source_vars.items():
            ttk.Checkbutton(box, text=source.value, variable=variable).pack(anchor=tk.W)

    def _build_request_box(self, sidebar: ttk.Frame) -> None:
        """Add the waiting-time and retry controls.

        Parameters
        ----------
        sidebar : tkinter.ttk.Frame
            Sidebar to add the group to.
        """
        box = ttk.LabelFrame(sidebar, text='Requests', padding=_BOX_PADDING)
        box.pack(fill=tk.X, pady=_GAP_BELOW_BOX)
        box.columnconfigure(1, weight=1)

        ttk.Label(box, text='Wait (s)').grid(row=0, column=0, sticky=tk.W)
        ttk.Spinbox(
            box,
            textvariable=self.delay_var,
            from_=0,
            to=_MAX_DELAY_SECONDS,
            increment=0.5,
            width=6,
        ).grid(row=0, column=1, sticky=tk.E, pady=2)

        ttk.Label(box, text='Retries').grid(row=1, column=0, sticky=tk.W)
        ttk.Spinbox(
            box,
            textvariable=self.retries_var,
            from_=0,
            to=_MAX_RETRIES,
            increment=1,
            width=6,
        ).grid(row=1, column=1, sticky=tk.E, pady=2)

    def _build_action_box(self, sidebar: ttk.Frame) -> None:
        """Add the action buttons.

        Parameters
        ----------
        sidebar : tkinter.ttk.Frame
            Sidebar to add the group to.
        """
        box = ttk.LabelFrame(sidebar, text='Actions', padding=_BOX_PADDING)
        box.pack(fill=tk.X)
        self.convert_button = ttk.Button(
            box,
            text='Convert  (Ctrl+Enter)',
            command=self.convert_or_stop,
        )
        self.convert_button.pack(fill=tk.X, pady=2)
        buttons = [
            (
                'Copy references',
                self.copy_output,
            ),
            (
                'Remove finished',
                self.remove_finished,
            ),
            (
                'Clear',
                self.clear,
            ),
        ]
        for label, command in buttons:
            ttk.Button(box, text=label, command=command).pack(fill=tk.X, pady=2)

    def _bind_shortcuts(self) -> None:
        """Bind keyboard shortcuts to actions."""
        self.root.bind('<Control-Return>', self._on_convert_shortcut)

    def _on_convert_shortcut(self, _event: 'tk.Event[tk.Misc]') -> str:
        """Run Convert or Stop from the Ctrl+Enter shortcut.

        Parameters
        ----------
        _event : tkinter.Event
            The key event (unused).

        Returns
        -------
        str
            ``"break"``, which stops Tk from also handling the key press
            elsewhere, such as inserting a line break in a text panel.
        """
        self.convert_or_stop()
        return 'break'

    def _selected_format(self) -> OutputFormat:
        """Return the output format chosen in the sidebar."""
        return OutputFormat(self.format_var.get())

    def _update_style_state(self) -> None:
        """Enable style options only for formatted citations."""
        is_citation = self._selected_format() is OutputFormat.CITATION
        combo_state = 'readonly' if is_citation else 'disabled'
        self.style_combo.configure(state=combo_state)
        self.locale_combo.configure(state=combo_state)
        self.numbered_check.configure(state='normal' if is_citation else 'disabled')

    def _read_number(
        self,
        text: str,
        label: str,
        maximum: float,
    ) -> float:
        """Parse a number typed in a spin box.

        Parameters
        ----------
        text : str
            Text from the spin box.
        label : str
            Field name used in the error message.
        maximum : float
            Largest accepted value.

        Returns
        -------
        float
            The parsed value.

        Raises
        ------
        ValueError
            If the text is not a number between 0 and ``maximum``.
        """
        try:
            value = float(text)
        except ValueError as err:
            raise ValueError(f'{label} must be a number.') from err
        if not 0 <= value <= maximum:
            raise ValueError(f'{label} must be between 0 and {maximum:g}.')
        return value

    def _request_options(self) -> RequestOptions:
        """Build request options from the sidebar controls.

        Returns
        -------
        RequestOptions
            Options for the next batch.

        Raises
        ------
        ValueError
            If no source is ticked, a number field is invalid or the
            retry count is not a whole number.
        """
        sources = tuple(
            source for source, variable in self.source_vars.items()
            if variable.get()
        )
        if not sources:
            raise ValueError('Tick at least one source.')

        delay = self._read_number(self.delay_var.get(), 'Wait (s)', _MAX_DELAY_SECONDS)
        retries = self._read_number(self.retries_var.get(), 'Retries', _MAX_RETRIES)
        if not retries.is_integer():
            raise ValueError('Retries must be a whole number.')

        return RequestOptions(
            output_format=self._selected_format(),
            style=CITATION_STYLES[self.style_var.get()],
            locale=LOCALES[self.locale_var.get()],
            sources=sources,
            delay_seconds=delay,
            retries=int(retries),
        )

    def convert_or_stop(self) -> None:
        """Start a batch, or ask the running batch to stop."""
        if self._batch is not None:
            self._stop_event.set()
            self.convert_button.configure(state='disabled')
            self.status_var.set('Stopping after the current request…')
            return
        self._start_batch()

    def _start_batch(self) -> None:
        """Look up every DOI in the left panel on a worker thread.

        Shows a dialog instead when the panel holds no DOI or a sidebar
        option is invalid.
        """
        dois = extract_dois(self.doi_text.get('1.0', tk.END))
        if not dois:
            messagebox.showinfo('doi2ref', 'No DOIs found in the left panel.')
            return
        try:
            options = self._request_options()
        except ValueError as err:
            messagebox.showerror('doi2ref', str(err))
            return

        self._batch = _Batch(options=options, numbered=self.numbered_var.get())
        self._batch_failures = []
        self._batch_done = 0
        self._stop_event.clear()
        self.convert_button.configure(text='Stop  (Ctrl+Enter)')
        self.progress.configure(maximum=len(dois), value=0)
        self.status_var.set(f'Looking up {len(dois)} DOI(s)…')

        worker = threading.Thread(
            target=self._run_lookups,
            args=(
                dois,
                options,
            ),
            daemon=True,
        )
        worker.start()
        self.root.after(_POLL_INTERVAL_MS, self._poll_messages)

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
        finally:
            self._messages.put(_FinishedMessage(stopped=processed < total))

    def _poll_messages(self) -> None:
        """Apply worker-thread messages to the widgets.

        Polls again after ``_POLL_INTERVAL_MS`` until the batch has
        finished.
        """
        while True:
            try:
                message = self._messages.get_nowait()
            except queue.Empty:
                break

            if isinstance(message, _ResultMessage):
                self._show_result(message)
            else:
                self._finish_batch(message)
                return

        self.root.after(_POLL_INTERVAL_MS, self._poll_messages)

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
            existing = self.output_text.get('1.0', tk.END)
            combined = append_reference(
                existing,
                result.reference,
                self._batch.options.output_format,
                numbered=self._batch.numbered,
            )
            self.output_text.delete('1.0', tk.END)
            self.output_text.insert('1.0', combined + '\n')
            self.output_text.see(tk.END)
        else:
            self._batch_failures.append(result)

        self._batch_done = message.done
        self.progress.configure(value=message.done)
        self.status_var.set(
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
        self.convert_button.configure(text='Convert  (Ctrl+Enter)', state='normal')

        prefix = 'Stopped' if message.stopped else 'Done'
        self.status_var.set(
            f'{prefix}: {self._batch_done} looked up, '
            f'{len(self._batch_failures)} failed.'
        )
        if self._batch_failures:
            messagebox.showwarning('Some DOIs failed', self._failure_summary())

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

    def copy_output(self) -> None:
        """Copy the whole references panel to the clipboard."""
        text = (
            self.output_text
            .get('1.0', tk.END)
            .strip()
        )
        if not text:
            self.status_var.set('Nothing to copy.')
            return
        self.root.clipboard_clear()
        self.root.clipboard_append(text)
        self.status_var.set('References copied to the clipboard.')

    def remove_finished(self) -> None:
        """Remove DOIs already converted from the left panel.

        The panel is rewritten as one bare DOI per line, so any other
        text in it, such as links or prose around the DOIs, is dropped.
        """
        if self._batch is not None:
            self.status_var.set('Wait for the current batch to finish.')
            return

        dois = extract_dois(self.doi_text.get('1.0', tk.END))
        remaining = [
            doi for doi in dois
            if doi.casefold() not in self._finished_dois
        ]
        removed_count = len(dois) - len(remaining)

        self.doi_text.delete('1.0', tk.END)
        if remaining:
            self.doi_text.insert('1.0', '\n'.join(remaining) + '\n')
        self.status_var.set(
            f'Removed {removed_count} finished DOI(s); {len(remaining)} left.'
        )

    def clear(self) -> None:
        """Empty both text panels and forget finished DOIs."""
        if self._batch is not None:
            self.status_var.set('Wait for the current batch to finish.')
            return
        self.doi_text.delete('1.0', tk.END)
        self.output_text.delete('1.0', tk.END)
        self._finished_dois.clear()
        self.progress.configure(value=0)
        self.status_var.set('Cleared.')


def _enable_high_dpi() -> None:
    """Ask Windows for sharp text on high-DPI screens.

    Does nothing on other platforms.
    """
    if sys.platform != 'win32':
        return
    try:
        # ctypes.windll exists only on Windows, so the import waits
        # until the platform check has passed.
        import ctypes  # pylint: disable=import-outside-toplevel

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        # Older Windows versions lack shcore; the window is just
        # blurrier.
        pass


def main() -> None:
    """Open the doi2ref window."""
    _enable_high_dpi()
    root = tk.Tk()
    Doi2RefApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
