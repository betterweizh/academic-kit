"""Tkinter launcher window: one button per installed tool.

The buttons come from :func:`academic_kit.tools.discover_tools`, so a
newly installed tool appears without changing this module. Pressing a
button starts the tool in its own process; the launcher stays open and
can start several tools, or the same tool more than once.

The window opens at the smallest size that fits every status message,
and that size is also its minimum. Its size never follows the status
text, and the text re-wraps when the user resizes the window.
"""

import subprocess
import sys
import tkinter as tk
from tkinter import messagebox, ttk

from academic_kit import __version__
from academic_kit.tools import Tool, discover_tools, launch_tool

# Pixel sizes are for a 96-DPI (100 %) screen and are scaled to the real
# screen, so Windows display scaling does not squeeze the layout.
_BASE_DPI = 96
_WINDOW_PADDING = 16
_BUTTON_GAP = 6
_BUTTON_WIDTH_CHARS = 28
_BUTTON_INNER_PADDING = 6
_STATUS_WIDTH = 260
_IDLE_STATUS = 'Choose a tool to open.'
_NO_TOOLS_STATUS = (
    'No tools are installed. Run "uv sync" in the academic-kit folder.'
)


class LauncherApp:
    """Launcher window with a button for each tool.

    Parameters
    ----------
    root : tk.Tk
        Top-level window to build the interface in.
    tools : list of Tool
        Tools to show, in button order.
    """

    def __init__(self, root: tk.Tk, tools: list[Tool]) -> None:
        self.root = root
        self.root.title(f'academic-kit {__version__}')
        self._scale = max(1.0, self.root.winfo_fpixels('1i') / _BASE_DPI)
        # Started tools are kept so their process handles are not
        # collected while they run; the launcher never waits for them.
        self._processes: list[subprocess.Popen[bytes]] = []
        idle_status = _IDLE_STATUS if tools else _NO_TOOLS_STATUS
        self.status_var = tk.StringVar(value=idle_status)
        self.status_label = self._build_layout(tools)
        self._fix_window_size(self._status_texts(tools))
        # Bound only after the size is fixed, so measuring uses the
        # starting wrap width.
        self.status_label.bind('<Configure>', self._rewrap_status)

    def _px(self, pixels: int) -> int:
        """Scale a size given for a 96-DPI screen to this screen."""
        return round(pixels * self._scale)

    def _build_layout(self, tools: list[Tool]) -> ttk.Label:
        """Create one button per tool and the status text below them.

        Parameters
        ----------
        tools : list of Tool
            Tools to show, in button order.

        Returns
        -------
        tkinter.ttk.Label
            The status label. It takes the space left below the buttons,
            with its text in the top-left corner.
        """
        frame = ttk.Frame(self.root, padding=self._px(_WINDOW_PADDING))
        frame.pack(fill=tk.BOTH, expand=True)
        for tool in tools:
            self._add_tool_button(frame, tool)
        status_label = ttk.Label(
            frame,
            textvariable=self.status_var,
            wraplength=self._px(_STATUS_WIDTH),
            justify=tk.LEFT,
            anchor=tk.NW,
            foreground='gray40',
        )
        status_label.pack(
            fill=tk.BOTH,
            expand=True,
            pady=(
                self._px(_BUTTON_GAP),
                0,
            ),
        )
        return status_label

    def _status_texts(self, tools: list[Tool]) -> list[str]:
        """List every text the status label can show.

        Parameters
        ----------
        tools : list of Tool
            Tools shown in the window.

        Returns
        -------
        list of str
            The current text, the idle text, and each tool's
            description and "opened" message.
        """
        texts = [
            self.status_var.get(),
            _IDLE_STATUS,
        ]
        for tool in tools:
            texts.append(tool.description)
            texts.append(_opened_status(tool))
        return texts

    def _fix_window_size(self, texts: list[str]) -> None:
        """Open the window at the smallest size that fits every text.

        That size is also the window's minimum, so no status text is
        ever cut off, and the window no longer follows the size its
        contents ask for.

        Parameters
        ----------
        texts : list of str
            Every text the status label can show. The first one is shown
            again afterwards.
        """
        width = 0
        height = 0
        for text in texts:
            self.status_var.set(text)
            self.root.update_idletasks()
            width = max(width, self.root.winfo_reqwidth())
            height = max(height, self.root.winfo_reqheight())
        self.status_var.set(texts[0])
        self.root.minsize(width, height)
        self.root.geometry(f'{width}x{height}')

    def _rewrap_status(self, event: 'tk.Event[ttk.Label]') -> None:
        """Wrap the status text at the label's current width.

        Parameters
        ----------
        event : tkinter.Event
            Resize event of the status label.
        """
        if self.status_label.cget('wraplength') != event.width:
            self.status_label.configure(wraplength=event.width)

    def _add_tool_button(self, parent: ttk.Frame, tool: Tool) -> None:
        """Add the button that opens one tool.

        Hovering over the button shows the tool's description in the
        status line.

        Parameters
        ----------
        parent : ttk.Frame
            Frame the button is packed into.
        tool : Tool
            Tool the button opens.
        """
        button = ttk.Button(
            parent,
            text=tool.name,
            width=_BUTTON_WIDTH_CHARS,
            command=lambda: self.open_tool(tool),
        )
        button.pack(
            fill=tk.X,
            pady=(
                0,
                self._px(_BUTTON_GAP),
            ),
            ipady=self._px(_BUTTON_INNER_PADDING),
        )
        button.bind('<Enter>', lambda _event: self.status_var.set(tool.description))
        button.bind('<Leave>', lambda _event: self.status_var.set(_IDLE_STATUS))

    def open_tool(self, tool: Tool) -> None:
        """Start a tool in its own process, reporting failures.

        Parameters
        ----------
        tool : Tool
            Tool to start.
        """
        try:
            process = launch_tool(tool)
        except OSError as err:
            messagebox.showerror(
                'Cannot open tool',
                f'{tool.name}\n\n{err}',
                parent=self.root,
            )
            return
        self._processes.append(process)
        self.status_var.set(_opened_status(tool))


def _opened_status(tool: Tool) -> str:
    """Return the status text shown after a tool is opened."""
    return f'Opened {tool.name}.'


def _enable_high_dpi() -> None:
    """Ask Windows for sharp text on scaled displays."""
    if sys.platform != 'win32':
        return
    try:
        # Imported here because ctypes.windll exists only on Windows.
        import ctypes  # pylint: disable=import-outside-toplevel

        ctypes.windll.shcore.SetProcessDpiAwareness(1)
    except (AttributeError, OSError):
        # Older Windows versions lack this call; blurry text is fine.
        pass


def main() -> None:
    """Open the launcher window."""
    _enable_high_dpi()
    try:
        tools = discover_tools()
    except ValueError as err:
        root = tk.Tk()
        root.withdraw()
        messagebox.showerror('A tool is installed incorrectly', str(err))
        root.destroy()
        raise SystemExit(1) from err
    root = tk.Tk()
    LauncherApp(root, tools)
    root.mainloop()


if __name__ == '__main__':
    main()
