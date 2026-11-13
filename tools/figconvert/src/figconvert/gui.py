"""Tkinter window for converting figures to raster images.

Layout: two equal-width list panels (figures waiting to convert on the
left, finished output files on the right) and a sidebar of options and
actions, with the progress bar and status text at the sidebar's bottom.
Each figure moves from the left panel to the right one as soon as it is
converted; figures that fail stay on the left, shown in red.

Conversions run on a background thread so the window stays responsive;
results come back through a queue polled with ``after``, because Tk
widgets must only be touched from the main thread.
"""

import os
import queue
import subprocess
import sys
import threading
import tkinter as tk
from dataclasses import dataclass
from pathlib import Path
from tkinter import colorchooser, filedialog, messagebox, ttk

from PIL import ImageColor

from figconvert import __version__
from figconvert.core import (
    CONVERTED_PREFIX,
    RASTER_SUFFIXES,
    VECTOR_SUFFIXES,
    ConversionError,
    ConvertOptions,
    OutputFile,
    OutputFormat,
    clean_stem,
    convert_file,
    find_figures,
    is_supported,
    is_vector,
)

_WINDOW_SIZE = '1200x700'
_POLL_INTERVAL_MS = 100
_DPI_PRESETS = (
    '72',
    '96',
    '150',
    '300',
    '600',
    '1200',
)
_MAX_DPI = 2400
_MAX_QUALITY = 100
# Output formats that take the quality setting.
_QUALITY_FORMATS = (
    OutputFormat.JPEG,
    OutputFormat.WEBP,
)
_DEFAULT_BACKGROUND = '#ffffff'
# Default save folder, inside the first listed figure's folder.
_DEFAULT_OUT_SUBFOLDER = '_converted_figures'
_MAX_FAILURES_SHOWN = 15
_MAX_SHARED_NAMES_SHOWN = 5

# PROCESS_SYSTEM_DPI_AWARE, for Windows' SetProcessDpiAwareness.
_SYSTEM_DPI_AWARE = 1

# Labels shown in more than one place.
_CONVERT_LABEL = 'Convert  (Ctrl+Enter)'
_RENAME_LABEL = 'Rename output name…'
_REMOVE_LABEL = 'Remove from list'

# Treeview tag and colour of figures whose conversion failed.
_FAILED_TAG = 'failed'
_FAILED_COLOUR = '#c62828'
# Treeview region of a data cell, as reported by identify_region.
_CELL_REGION = 'cell'
# Treeview id of the editable "Output name" column (second column).
_OUTPUT_COLUMN_ID = '#2'
# Return value of a Tk event handler that stops further handling.
_BREAK = 'break'

_PENDING_COLUMNS = (
    'name',
    'output',
    'kind',
    'folder',
)
_PENDING_HEADINGS = (
    'File',
    'Output name ✎',
    'Type',
    'Folder',
)
_DONE_COLUMNS = (
    'name',
    'size',
    'source',
)
_DONE_HEADINGS = (
    'Output file',
    'Pixels',
    'From',
)
# Columns that share out the spare width when the window grows.
_STRETCH_COLUMNS = (
    'name',
    'output',
    'folder',
    'source',
)
# Grid columns of the two list panels, which share one width.
_PANEL_GRID_COLUMNS = (
    0,
    1,
)

# Screen resolution, in DPI, that the scaled sizes below are given for.
_REFERENCE_DPI = 96
# These sizes are for a 96-DPI (100 %) screen and are scaled to the real
# screen, so Windows display scaling does not squeeze the layout.
_SIDEBAR_TEXT_WIDTH = 230
_MIN_PANEL_WIDTH = 240
_MIN_COLUMN_WIDTH = 50
_MIN_HEIGHT_MARGIN = 30
_COLUMN_WIDTHS = {
    'name': 150,
    'output': 150,
    'kind': 60,
    'folder': 160,
    'size': 90,
    'source': 140,
}

# Padding in screen pixels, not scaled. Pairs are (before, after):
# (top, bottom) for pady and (left, right) for padx.
_WINDOW_PADDING = 8
_PANEL_GAP = 8
_GAP_BELOW = (
    0,
    8,
)
_SMALL_GAP_BEFORE = (
    4,
    0,
)
_MEDIUM_GAP_BEFORE = (
    6,
    0,
)
_LARGE_GAP_BEFORE = (
    8,
    0,
)
_SIDEBAR_PADX = (
    0,
    _WINDOW_PADDING,
)
_PENDING_PANEL_PADX = (
    0,
    _PANEL_GAP // 2,
)
_DONE_PANEL_PADX = (
    _PANEL_GAP // 2,
    0,
)


def _patterns(suffixes: frozenset[str]) -> str:
    """Return a space-separated glob list for a file dialog filter."""
    return ' '.join(
        f'*{suffix}' for suffix in sorted(suffixes)
    )


_FILE_TYPES = [
    (
        'All figures',
        _patterns(VECTOR_SUFFIXES | RASTER_SUFFIXES),
    ),
    (
        'Vector (SVG, PDF, EPS, EMF …)',
        _patterns(VECTOR_SUFFIXES),
    ),
    (
        'Raster (PNG, JPEG, TIFF …)',
        _patterns(RASTER_SUFFIXES),
    ),
    (
        'All files',
        '*.*',
    ),
]


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


class FigConvertApp:
    """Main application window.

    Parameters
    ----------
    root : tkinter.Tk
        Top-level window to build the interface in.
    """

    def __init__(self, root: tk.Tk) -> None:
        self.root = root
        self.root.title(f'figconvert {__version__} — figure to raster image')
        self.root.geometry(_WINDOW_SIZE)
        self._scale = max(1.0, self.root.winfo_fpixels('1i') / _REFERENCE_DPI)

        self._messages: queue.Queue[_ResultMessage | _FinishedMessage] = queue.Queue()
        self._stop_event = threading.Event()
        self._running = False
        self._failures: list[tuple[Path, str]] = []
        self._batch_ok = 0
        self._last_dir = str(Path.home())

        self.format_var = tk.StringVar(value=OutputFormat.JPEG.value)
        self.quality_var = tk.StringVar(value='95')
        self.dpi_var = tk.StringVar(value='600')
        self.all_pages_var = tk.BooleanVar(value=False)
        self.transparent_var = tk.BooleanVar(value=True)
        self.background_var = tk.StringVar(value=_DEFAULT_BACKGROUND)
        self.recursive_var = tk.BooleanVar(value=False)
        self.out_dir_var = tk.StringVar(value='')
        self.overwrite_var = tk.BooleanVar(value=False)
        self.status_var = tk.StringVar(
            value='Add figures, choose a save folder, then Convert.',
        )

        self._build_layout()
        self._bind_shortcuts()
        self._update_option_state()

    def _px(self, pixels: int) -> int:
        """Scale a size given for a 96-DPI screen to this screen."""
        return round(pixels * self._scale)

    def _build_layout(self) -> None:
        """Create the sidebar and the two equal-width list panels."""
        # The sidebar is packed first so it keeps its width when the
        # window shrinks; the panels take the remaining space.
        sidebar = self._build_sidebar(self.root)
        sidebar.pack(side=tk.RIGHT, fill=tk.Y, padx=_SIDEBAR_PADX, pady=_WINDOW_PADDING)

        panels = ttk.Frame(self.root)
        panels.pack(
            side=tk.LEFT,
            fill=tk.BOTH,
            expand=True,
            padx=_WINDOW_PADDING,
            pady=_WINDOW_PADDING,
        )
        panels.columnconfigure(_PANEL_GRID_COLUMNS, weight=1, uniform='list_panels')
        panels.rowconfigure(0, weight=1)

        self.pending_frame, self.pending_tree = self._build_list_panel(
            panels,
            _PENDING_COLUMNS,
            _PENDING_HEADINGS,
        )
        self.pending_frame.grid(
            row=0,
            column=0,
            sticky=tk.NSEW,
            padx=_PENDING_PANEL_PADX,
        )
        self.pending_tree.tag_configure(_FAILED_TAG, foreground=_FAILED_COLOUR)
        self.pending_tree.bind('<<TreeviewSelect>>', self._on_pending_select)
        self.pending_tree.bind('<Delete>', lambda _event: self.remove_selected())
        self.pending_tree.bind('<Double-1>', self._on_pending_double_click)
        self.pending_tree.bind('<F2>', lambda _event: self._edit_output_name())
        self._name_editor: ttk.Entry | None = None
        self._name_editor_row = ''

        self.done_frame, self.done_tree = self._build_list_panel(
            panels,
            _DONE_COLUMNS,
            _DONE_HEADINGS,
        )
        self.done_frame.grid(row=0, column=1, sticky=tk.NSEW, padx=_DONE_PANEL_PADX)
        self.done_tree.bind('<Double-1>', self._open_finished)
        self._build_context_menus()
        self._update_panel_titles()

        self.root.update_idletasks()
        min_width = (
            sidebar.winfo_reqwidth()
            + 2 * self._px(_MIN_PANEL_WIDTH)
            + 4 * _PANEL_GAP
        )
        min_height = (
            sidebar.winfo_reqheight()
            + 2 * _WINDOW_PADDING
            + self._px(_MIN_HEIGHT_MARGIN)
        )
        self.root.minsize(min_width, min_height)

    def _build_context_menus(self) -> None:
        """Create the right-click menus of both panels."""
        self._menu_row = ''
        self.pending_menu = tk.Menu(self.root, tearoff=False)
        self.pending_menu.add_command(
            label='Open file',
            command=lambda: self._open_figure(Path(self._menu_row)),
        )
        self.pending_menu.add_command(
            label='Open file location',
            command=lambda: self._reveal_figure(Path(self._menu_row)),
        )
        self.pending_menu.add_separator()
        self.pending_menu.add_command(
            label=_RENAME_LABEL,
            command=lambda: self._edit_output_name(self._menu_row),
        )
        self.pending_menu.add_separator()
        self.pending_menu.add_command(label=_REMOVE_LABEL, command=self.remove_selected)

        self.done_menu = tk.Menu(self.root, tearoff=False)
        self.done_menu.add_command(
            label='Open file',
            command=lambda: self._open_figure(Path(self._menu_row)),
        )
        self.done_menu.add_command(
            label='Open file location',
            command=lambda: self._reveal_figure(Path(self._menu_row)),
        )

        right_click = '<Button-2>' if sys.platform == 'darwin' else '<Button-3>'
        self.pending_tree.bind(
            right_click,
            lambda event: self._show_menu(
                event,
                self.pending_tree,
                self.pending_menu,
            ),
        )
        self.done_tree.bind(
            right_click,
            lambda event: self._show_menu(
                event,
                self.done_tree,
                self.done_menu,
            ),
        )

    def _show_menu(
        self,
        event: tk.Event,
        tree: ttk.Treeview,
        menu: tk.Menu,
    ) -> str:
        """Focus the row under the pointer and pop up a menu for it.

        The row is added to the selection only if it is not already
        selected, so a multiple selection survives the right-click.

        Parameters
        ----------
        event : tkinter.Event
            Right-click on ``tree``.
        tree : tkinter.ttk.Treeview
            Panel list that was clicked.
        menu : tkinter.Menu
            Menu to pop up; the left panel's menu disables renaming and
            removing while a batch runs.

        Returns
        -------
        str
            ``'break'``, so Tk runs no further handlers for the click.
        """
        row = tree.identify_row(event.y)
        if not row:
            return _BREAK
        self._close_name_editor(save=True)
        if row not in tree.selection():
            tree.selection_set(row)
        tree.focus(row)
        self._menu_row = row
        if menu is self.pending_menu:
            editable = tk.DISABLED if self._running else tk.NORMAL
            menu.entryconfigure(_RENAME_LABEL, state=editable)
            menu.entryconfigure(_REMOVE_LABEL, state=editable)
        try:
            menu.tk_popup(event.x_root, event.y_root)
        finally:
            menu.grab_release()
        return _BREAK

    def _reveal_figure(self, path: Path) -> None:
        """Show a file selected in the system file explorer."""
        if not path.exists():
            self.status_var.set(f'{path.name} no longer exists.')
            return
        try:
            _reveal_path(path)
        except OSError as error:
            messagebox.showerror(
                'Cannot open folder',
                f'{path.parent}\n\n{error}',
                parent=self.root,
            )

    def _build_list_panel(
        self,
        parent: tk.Misc,
        columns: tuple[str, ...],
        headings: tuple[str, ...],
    ) -> tuple[ttk.LabelFrame, ttk.Treeview]:
        """Create a scrollable list with columns, not yet placed.

        Parameters
        ----------
        parent : tkinter.Misc
            Widget to create the list in.
        columns : tuple of str
            Column ids, each a key of ``_COLUMN_WIDTHS``.
        headings : tuple of str
            Column headings, one per column id.

        Returns
        -------
        frame : tkinter.ttk.LabelFrame
            Frame holding the list and its scroll bars, still without a
            title.
        tree : tkinter.ttk.Treeview
            The list itself.
        """
        frame = ttk.LabelFrame(parent, padding=4)
        frame.rowconfigure(0, weight=1)
        frame.columnconfigure(0, weight=1)
        tree = ttk.Treeview(
            frame,
            columns=columns,
            show='headings',
            selectmode='extended',
            height=5,
        )
        for column, heading in zip(columns, headings, strict=True):
            tree.heading(column, text=heading, anchor=tk.W)
            tree.column(
                column,
                width=self._px(_COLUMN_WIDTHS[column]),
                minwidth=self._px(_MIN_COLUMN_WIDTH),
                stretch=column in _STRETCH_COLUMNS,
                anchor=tk.W,
            )
        vertical_bar = ttk.Scrollbar(frame, orient=tk.VERTICAL, command=tree.yview)
        horizontal_bar = ttk.Scrollbar(frame, orient=tk.HORIZONTAL, command=tree.xview)
        tree.configure(
            yscrollcommand=vertical_bar.set,
            xscrollcommand=horizontal_bar.set,
        )
        tree.grid(row=0, column=0, sticky=tk.NSEW)
        vertical_bar.grid(row=0, column=1, sticky=tk.NS)
        horizontal_bar.grid(row=1, column=0, sticky=tk.EW)
        return frame, tree

    def _build_sidebar(self, parent: tk.Misc) -> ttk.Frame:
        """Create the options and actions sidebar, not yet packed."""
        sidebar = ttk.Frame(parent)
        self._build_input_box(sidebar)
        self._build_output_box(sidebar)
        self._build_resolution_box(sidebar)
        self._build_background_box(sidebar)
        self._build_action_box(sidebar)
        self._build_progress_box(sidebar)
        return sidebar

    def _build_input_box(self, sidebar: ttk.Frame) -> None:
        """Add the buttons that fill and empty the left panel."""
        box = ttk.LabelFrame(sidebar, text='Input figures', padding=6)
        box.pack(fill=tk.X, pady=_GAP_BELOW)
        self.input_buttons = [
            ttk.Button(
                box,
                text='Add files…',
                command=self.add_files,
            ),
            ttk.Button(
                box,
                text='Add folder…',
                command=self.add_folder,
            ),
            ttk.Button(
                box,
                text='Remove selected',
                command=self.remove_selected,
            ),
            ttk.Button(
                box,
                text='Clear list',
                command=self.clear_pending,
            ),
        ]
        for button in self.input_buttons:
            button.pack(fill=tk.X, pady=1)
        recursive_check = ttk.Checkbutton(
            box,
            text='Include sub-folders',
            variable=self.recursive_var,
        )
        recursive_check.pack(anchor=tk.W, pady=_SMALL_GAP_BEFORE)

    def _build_output_box(self, sidebar: ttk.Frame) -> None:
        """Add the output format, quality and save-folder controls."""
        box = ttk.LabelFrame(sidebar, text='Output', padding=6)
        box.pack(fill=tk.X, pady=_GAP_BELOW)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text='Format').grid(row=0, column=0, sticky=tk.W)
        format_combo = ttk.Combobox(
            box,
            textvariable=self.format_var,
            values=[
                output_format.value for output_format in OutputFormat
            ],
            state='readonly',
            width=8,
        )
        format_combo.grid(row=0, column=1, sticky=tk.E)
        format_combo.bind(
            '<<ComboboxSelected>>',
            lambda _event: self._update_option_state(),
        )
        quality_label = ttk.Label(box, text='Quality (JPEG/WEBP)')
        quality_label.grid(row=1, column=0, sticky=tk.W, pady=_MEDIUM_GAP_BEFORE)
        self.quality_spin = ttk.Spinbox(
            box,
            from_=1,
            to=_MAX_QUALITY,
            textvariable=self.quality_var,
            width=6,
        )
        self.quality_spin.grid(row=1, column=1, sticky=tk.E, pady=_MEDIUM_GAP_BEFORE)
        all_pages_check = ttk.Checkbutton(
            box,
            text='All pages of PDF/XPS',
            variable=self.all_pages_var,
        )
        all_pages_check.grid(
            row=2,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=_SMALL_GAP_BEFORE,
        )

        save_label = ttk.Label(box, text='Save to')
        save_label.grid(
            row=3,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=_LARGE_GAP_BEFORE,
        )
        folder_row = ttk.Frame(box)
        folder_row.grid(row=4, column=0, columnspan=2, sticky=tk.EW)
        folder_row.columnconfigure(0, weight=1)
        folder_entry = ttk.Entry(folder_row, textvariable=self.out_dir_var, width=18)
        folder_entry.grid(row=0, column=0, sticky=tk.EW)
        browse_button = ttk.Button(
            folder_row,
            text='Browse…',
            command=self.choose_out_dir,
        )
        browse_button.grid(row=0, column=1, padx=_SMALL_GAP_BEFORE)
        overwrite_check = ttk.Checkbutton(
            box,
            text='Overwrite existing files',
            variable=self.overwrite_var,
        )
        overwrite_check.grid(
            row=5,
            column=0,
            columnspan=2,
            sticky=tk.W,
            pady=_SMALL_GAP_BEFORE,
        )

    def _build_resolution_box(self, sidebar: ttk.Frame) -> None:
        """Add the DPI control."""
        box = ttk.LabelFrame(sidebar, text='Resolution', padding=6)
        box.pack(fill=tk.X, pady=_GAP_BELOW)
        box.columnconfigure(1, weight=1)
        ttk.Label(box, text='DPI').grid(row=0, column=0, sticky=tk.W)
        dpi_combo = ttk.Combobox(
            box,
            textvariable=self.dpi_var,
            values=_DPI_PRESETS,
            width=8,
        )
        dpi_combo.grid(row=0, column=1, sticky=tk.E)

    def _build_background_box(self, sidebar: ttk.Frame) -> None:
        """Add the transparency and background colour controls."""
        box = ttk.LabelFrame(sidebar, text='Background', padding=6)
        box.pack(fill=tk.X, pady=_GAP_BELOW)
        box.columnconfigure(1, weight=1)
        self.transparent_check = ttk.Checkbutton(
            box,
            text='Keep transparency',
            variable=self.transparent_var,
            command=self._update_option_state,
        )
        self.transparent_check.grid(row=0, column=0, columnspan=3, sticky=tk.W)
        colour_label = ttk.Label(box, text='Colour')
        colour_label.grid(row=1, column=0, sticky=tk.W, pady=_SMALL_GAP_BEFORE)
        self.swatch = tk.Label(box, width=3, relief=tk.SOLID, borderwidth=1)
        self.swatch.grid(row=1, column=1, sticky=tk.E, padx=4, pady=_SMALL_GAP_BEFORE)
        self.colour_button = ttk.Button(box, text='Pick…', command=self.pick_colour)
        self.colour_button.grid(row=1, column=2, sticky=tk.E, pady=_SMALL_GAP_BEFORE)
        self._refresh_swatch()

    def _build_action_box(self, sidebar: ttk.Frame) -> None:
        """Add Convert/Stop and the finished-list actions."""
        box = ttk.Frame(sidebar)
        box.pack(fill=tk.X, pady=_GAP_BELOW)
        self.convert_button = ttk.Button(
            box,
            text=_CONVERT_LABEL,
            command=self.convert_or_stop,
        )
        self.convert_button.pack(fill=tk.X, pady=2, ipady=3)
        open_folder_button = ttk.Button(
            box,
            text='Open output folder',
            command=self.open_out_dir,
        )
        open_folder_button.pack(fill=tk.X, pady=2)
        clear_button = ttk.Button(
            box,
            text='Clear finished',
            command=self.clear_finished,
        )
        clear_button.pack(fill=tk.X, pady=2)

    def _build_progress_box(self, sidebar: ttk.Frame) -> None:
        """Add the progress bar and status text below the options."""
        box = ttk.Frame(sidebar)
        box.pack(side=tk.BOTTOM, fill=tk.X)
        # The bar is as long as the status text's wrap width, so the
        # sidebar is always at least that wide. Without it, the sidebar
        # would follow the width of each status message and the list
        # panels beside it would shift.
        self.progress = ttk.Progressbar(
            box,
            mode='determinate',
            length=self._px(_SIDEBAR_TEXT_WIDTH),
        )
        self.progress.pack(fill=tk.X)
        status_label = ttk.Label(
            box,
            textvariable=self.status_var,
            wraplength=self._px(_SIDEBAR_TEXT_WIDTH),
            justify=tk.LEFT,
        )
        status_label.pack(anchor=tk.W, pady=_SMALL_GAP_BEFORE)

    def _bind_shortcuts(self) -> None:
        """Bind keyboard shortcuts."""
        self.root.bind('<Control-Return>', lambda _event: self.convert_or_stop())
        self.root.bind('<Control-o>', lambda _event: self.add_files())

    def _update_option_state(self) -> None:
        """Enable only the controls that apply to the chosen options."""
        output_format = OutputFormat(self.format_var.get())
        uses_quality = output_format in _QUALITY_FORMATS
        supports_alpha = output_format.supports_alpha
        uses_colour = not (supports_alpha and self.transparent_var.get())
        self.quality_spin.configure(state=tk.NORMAL if uses_quality else tk.DISABLED)
        self.transparent_check.configure(
            state=tk.NORMAL if supports_alpha else tk.DISABLED,
        )
        self.colour_button.configure(state=tk.NORMAL if uses_colour else tk.DISABLED)

    def _refresh_swatch(self) -> None:
        """Show the background colour, or white if it is invalid."""
        try:
            self.swatch.configure(background=self.background_var.get())
        except tk.TclError:
            self.swatch.configure(background=_DEFAULT_BACKGROUND)

    def pick_colour(self) -> None:
        """Ask for the background colour."""
        _rgb, colour = colorchooser.askcolor(
            color=self.background_var.get(),
            title='Background colour',
            parent=self.root,
        )
        if colour:
            self.background_var.set(colour)
            self._refresh_swatch()

    def _read_options(self) -> ConvertOptions:
        """Build conversion options from the sidebar controls.

        Returns
        -------
        ConvertOptions
            Settings for every figure in the batch.

        Raises
        ------
        ValueError
            If the DPI, quality or background colour is invalid.
        """
        try:
            dpi = float(self.dpi_var.get())
        except ValueError as error:
            raise ValueError('DPI must be a number.') from error
        if not 1 <= dpi <= _MAX_DPI:
            raise ValueError(f'DPI must be between 1 and {_MAX_DPI}.')

        try:
            quality = int(self.quality_var.get())
        except ValueError as error:
            raise ValueError('Quality must be a whole number.') from error
        if not 1 <= quality <= _MAX_QUALITY:
            raise ValueError(f'Quality must be between 1 and {_MAX_QUALITY}.')

        background = self.background_var.get()
        try:
            ImageColor.getrgb(background)
        except ValueError as error:
            raise ValueError(f'Unknown background colour: {background}') from error

        return ConvertOptions(
            output_format=OutputFormat(self.format_var.get()),
            dpi=dpi,
            transparent=self.transparent_var.get(),
            background=background,
            quality=quality,
            all_pages=self.all_pages_var.get(),
            overwrite=self.overwrite_var.get(),
        )

    def _update_panel_titles(self) -> None:
        """Show item counts in the panel titles."""
        waiting = len(self.pending_tree.get_children())
        finished = len(self.done_tree.get_children())
        self.pending_frame.configure(text=f'To convert ({waiting})')
        self.done_frame.configure(text=f'Finished ({finished})')

    def add_files(self) -> None:
        """Choose figures in a file dialog and list them on the left."""
        if self._running:
            return
        names = filedialog.askopenfilenames(
            parent=self.root,
            title='Select figures',
            initialdir=self._last_dir,
            filetypes=_FILE_TYPES,
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
        folder = filedialog.askdirectory(
            parent=self.root,
            title='Select a folder of figures',
            initialdir=self._last_dir,
        )
        if not folder:
            return
        self._last_dir = folder
        found = find_figures(Path(folder), recursive=self.recursive_var.get())
        if not found:
            messagebox.showinfo(
                'No figures',
                'No supported figures in that folder.',
                parent=self.root,
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
        for given_path in paths:
            path = given_path.resolve()
            key = str(path)
            if not is_supported(path) or self.pending_tree.exists(key):
                skipped += 1
                continue
            self.pending_tree.insert(
                '',
                tk.END,
                iid=key,
                values=(
                    path.name,
                    path.stem,
                    'Vector' if is_vector(path) else 'Raster',
                    path.parent,
                ),
            )
            added += 1
        if not self.out_dir_var.get() and added:
            first = Path(self.pending_tree.get_children()[0])
            self.out_dir_var.set(str(first.parent / _DEFAULT_OUT_SUBFOLDER))
        self._update_panel_titles()
        message = f'Added {added} figure(s).'
        if skipped:
            message += f' Skipped {skipped} (already listed or unsupported).'
        self.status_var.set(message)

    def remove_selected(self) -> None:
        """Remove the selected figures from the left panel."""
        if self._running:
            return
        selected = self.pending_tree.selection()
        if selected:
            self.pending_tree.delete(*selected)
            self._update_panel_titles()

    def clear_pending(self) -> None:
        """Empty the left panel."""
        if self._running:
            return
        self.pending_tree.delete(*self.pending_tree.get_children())
        self._update_panel_titles()

    def _on_pending_select(self, _event: object) -> None:
        """Show the selected figure's full path, or why it failed."""
        selected = self.pending_tree.selection()
        if len(selected) != 1 or self._running:
            return
        row = selected[0]
        error = next(
            (
                reason for path, reason in self._failures
                if str(path) == row
            ),
            None,
        )
        name = Path(row).name
        text = row if error is None else f'{name} failed: {error}'
        self.status_var.set(text)

    def _on_pending_double_click(self, event: tk.Event) -> str | None:
        """Edit the output name, or open the original figure.

        Double-clicking the Output name cell edits it; double-clicking
        any other cell opens the figure in the default program.

        Parameters
        ----------
        event : tkinter.Event
            Double-click on the left panel.

        Returns
        -------
        str or None
            ``'break'`` to stop Tk's default handling when a row's cell
            was hit, otherwise None.
        """
        if self.pending_tree.identify_region(event.x, event.y) != _CELL_REGION:
            return None
        row = self.pending_tree.identify_row(event.y)
        if not row:
            return None
        if self.pending_tree.identify_column(event.x) == _OUTPUT_COLUMN_ID:
            self._edit_output_name(row)
        else:
            self._open_figure(Path(row))
        return _BREAK

    def _open_figure(self, path: Path) -> None:
        """Open a file with the default program, reporting failures."""
        if not path.exists():
            self.status_var.set(f'{path.name} no longer exists.')
            return
        try:
            _open_path(path)
        except OSError as error:
            messagebox.showerror(
                'Cannot open',
                f'{path.name}\n\n{error}',
                parent=self.root,
            )

    def _edit_output_name(self, row: str | None = None) -> None:
        """Show a text box over a row's output-name cell.

        Does nothing while a batch runs or when the row is not visible.

        Parameters
        ----------
        row : str or None, default None
            Row to edit; the focused row if None or empty.
        """
        if self._running:
            return
        row = row or self.pending_tree.focus()
        if not row:
            return
        self._close_name_editor(save=True)
        self.pending_tree.see(row)
        self.pending_tree.update_idletasks()
        bbox = self.pending_tree.bbox(row, 'output')
        if not bbox:
            return
        left, top, width, height = bbox
        editor = ttk.Entry(self.pending_tree)
        editor.insert(0, self.pending_tree.set(row, 'output'))
        editor.select_range(0, tk.END)
        editor.place(x=left, y=top, width=width, height=height)
        editor.focus_set()
        editor.bind('<Return>', lambda _event: self._close_name_editor(save=True))
        editor.bind('<KP_Enter>', lambda _event: self._close_name_editor(save=True))
        editor.bind('<Escape>', lambda _event: self._close_name_editor(save=False))
        editor.bind('<FocusOut>', lambda _event: self._close_name_editor(save=True))
        self._name_editor = editor
        self._name_editor_row = row
        self.status_var.set('Type the output name (no extension); Enter to keep.')

    def _close_name_editor(self, *, save: bool) -> None:
        """Remove the name text box, keeping the typed name if asked.

        Parameters
        ----------
        save : bool
            Whether to keep the typed name, cleaned with ``clean_stem``.
            A name that cleans to nothing leaves the old one in place.
        """
        editor = self._name_editor
        if editor is None:
            return
        self._name_editor = None
        row = self._name_editor_row
        if save and self.pending_tree.exists(row):
            name = clean_stem(editor.get())
            if name:
                self.pending_tree.set(row, 'output', name)
                self.status_var.set(f'Output name set to “{name}”.')
            else:
                self.status_var.set('Name not changed: it was empty or invalid.')
        editor.destroy()
        self.pending_tree.focus_set()

    def choose_out_dir(self) -> None:
        """Choose the save folder in a folder dialog."""
        current = self.out_dir_var.get()
        initial = current if current and Path(current).exists() else self._last_dir
        folder = filedialog.askdirectory(
            parent=self.root,
            title='Select the save folder',
            initialdir=initial,
        )
        if folder:
            self.out_dir_var.set(str(Path(folder)))

    def open_out_dir(self) -> None:
        """Open the save folder in the system file explorer."""
        folder = Path(self.out_dir_var.get()) if self.out_dir_var.get() else None
        if folder is None or not folder.is_dir():
            messagebox.showinfo(
                'No folder',
                'The save folder does not exist yet.',
                parent=self.root,
            )
            return
        _open_path(folder)

    def _open_finished(self, _event: object) -> None:
        """Open the focused finished image with the default program."""
        selected = self.done_tree.focus()
        if selected:
            self._open_figure(Path(selected))

    def clear_finished(self) -> None:
        """Empty the right panel (the files stay on disk)."""
        self.done_tree.delete(*self.done_tree.get_children())
        self._update_panel_titles()

    def convert_or_stop(self) -> None:
        """Start a batch, or ask the running batch to stop."""
        if self._running:
            self._stop_event.set()
            self.convert_button.configure(state=tk.DISABLED)
            self.status_var.set('Stopping after the current figure…')
            return
        self._start_batch()

    def _out_dir_text(self) -> str:
        """Return the typed save folder without surrounding spaces."""
        return (
            self.out_dir_var
            .get()
            .strip()
        )

    def _start_batch(self) -> None:
        """Validate the settings and start the worker thread."""
        self._close_name_editor(save=True)
        rows = self.pending_tree.get_children()
        source_paths = [
            Path(iid) for iid in rows
        ]
        out_stems = [
            self.pending_tree.set(iid, 'output') for iid in rows
        ]
        sources = list(zip(source_paths, out_stems, strict=True))
        if not sources:
            messagebox.showinfo(
                'Nothing to convert',
                'Add figures first.',
                parent=self.root,
            )
            return
        if not self._out_dir_text():
            self.choose_out_dir()
            if not self._out_dir_text():
                return
        try:
            options = self._read_options()
        except ValueError as error:
            messagebox.showerror('Invalid setting', str(error), parent=self.root)
            return

        out_dir = Path(self._out_dir_text())
        if not self._confirm_shared_folder(out_dir, source_paths):
            return
        self._failures = []
        self._batch_ok = 0
        for iid in self.pending_tree.get_children():
            self.pending_tree.item(iid, tags=())
        self._stop_event.clear()
        self._running = True
        self._set_inputs_enabled(enabled=False)
        self.convert_button.configure(text='Stop')
        self.progress.configure(maximum=len(sources), value=0)
        self.status_var.set(f'Converting 0 / {len(sources)}…')

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
        self.root.after(_POLL_INTERVAL_MS, self._poll_messages)

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
        if self.overwrite_var.get():
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
        return messagebox.askokcancel(
            'Output folder holds input figures',
            'The save folder is the same folder as these input figures:\n'
            f'{names}\n\n{risk}\n\nContinue?',
            icon=messagebox.WARNING,
            default=messagebox.CANCEL,
            parent=self.root,
        )

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
                self._messages.put(_FinishedMessage(stopped=True))
                return
            outputs: list[OutputFile] = []
            error: str | None = None
            try:
                outputs = convert_file(source, out_dir, options, out_stem, reserved)
            except ConversionError as conversion_error:
                error = str(conversion_error)
            # An unexpected error is reported for its figure like any
            # other failure, so the rest of the batch still converts.
            except Exception as unexpected_error:  # pylint: disable=broad-exception-caught
                error = f'{type(unexpected_error).__name__}: {unexpected_error}'
            result = _ResultMessage(
                source=source,
                outputs=outputs,
                error=error,
                done=done,
                total=total,
            )
            self._messages.put(result)
        self._messages.put(_FinishedMessage(stopped=False))

    def _poll_messages(self) -> None:
        """Apply worker results; runs on the main thread."""
        try:
            while True:
                message = self._messages.get_nowait()
                if isinstance(message, _FinishedMessage):
                    self._finish_batch(stopped=message.stopped)
                    return
                self._apply_result(message)
        except queue.Empty:
            pass
        self.root.after(_POLL_INTERVAL_MS, self._poll_messages)

    def _apply_result(self, message: _ResultMessage) -> None:
        """Move a figure to the right panel, or mark it failed."""
        key = str(message.source)
        self.progress.configure(value=message.done)
        if message.error is not None:
            self._failures.append((message.source, message.error))
            if self.pending_tree.exists(key):
                self.pending_tree.item(key, tags=(_FAILED_TAG,))
            self.status_var.set(
                f'{message.done} / {message.total}: {message.source.name} failed — '
                f'{message.error}',
            )
        else:
            self._batch_ok += 1
            if self.pending_tree.exists(key):
                self.pending_tree.delete(key)
            for output in message.outputs:
                out_key = str(output.path)
                if self.done_tree.exists(out_key):
                    self.done_tree.delete(out_key)
                self.done_tree.insert(
                    '',
                    tk.END,
                    iid=out_key,
                    values=(
                        output.path.name,
                        f'{output.width} × {output.height}',
                        message.source.name,
                    ),
                )
                self.done_tree.see(out_key)
            self.status_var.set(
                f'Converted {message.done} / {message.total}: {message.source.name}',
            )
        self._update_panel_titles()

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
        self.convert_button.configure(text=_CONVERT_LABEL, state=tk.NORMAL)
        summary = f'Done: {self._batch_ok} converted, {len(self._failures)} failed.'
        if stopped:
            summary = 'Stopped. ' + summary
        self.status_var.set(summary)
        if not self._failures:
            return
        shown_failures = self._failures[:_MAX_FAILURES_SHOWN]
        lines = [
            f'• {path.name}: {reason}' for path, reason in shown_failures
        ]
        extra = len(self._failures) - _MAX_FAILURES_SHOWN
        if extra > 0:
            lines.append(f'… and {extra} more')
        messagebox.showwarning(
            'Some figures failed',
            'These stay in the left panel (in red):\n\n' + '\n'.join(lines),
            parent=self.root,
        )

    def _set_inputs_enabled(self, *, enabled: bool) -> None:
        """Enable or disable the buttons that change the left panel."""
        state = tk.NORMAL if enabled else tk.DISABLED
        for button in self.input_buttons:
            button.configure(state=state)


def _open_path(path: Path) -> None:
    """Open a file or folder with the system's default program."""
    if sys.platform == 'win32':
        # The path is a local file or folder the user chose.
        os.startfile(path)  # noqa: S606
    elif sys.platform == 'darwin':
        subprocess.Popen(
            [
                'open',
                str(path),
            ]
        )
    else:
        subprocess.Popen(
            [
                'xdg-open',
                str(path),
            ]
        )


def _reveal_path(path: Path) -> None:
    """Open the folder holding a file, selecting it where possible."""
    if sys.platform == 'win32':
        # Explorer needs the path quoted inside the /select argument.
        subprocess.Popen(f'explorer /select,"{path}"')
    elif sys.platform == 'darwin':
        subprocess.Popen(
            [
                'open',
                '-R',
                str(path),
            ]
        )
    else:
        subprocess.Popen(
            [
                'xdg-open',
                str(path.parent),
            ]
        )


def main() -> None:
    """Open the figconvert window."""
    if sys.platform == 'win32':
        # Sharp text on scaled Windows displays. ctypes is imported here
        # because only this Windows-only call needs it.
        try:
            import ctypes  # pylint: disable=import-outside-toplevel

            ctypes.windll.shcore.SetProcessDpiAwareness(_SYSTEM_DPI_AWARE)
        # Without the call (older Windows) or if it is refused, the
        # window still works, only with blurrier text.
        except (AttributeError, OSError):
            pass
    root = tk.Tk()
    FigConvertApp(root)
    root.mainloop()


if __name__ == '__main__':
    main()
