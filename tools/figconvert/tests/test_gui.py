"""Tests for the figconvert window layout."""

import tkinter as tk

import pytest

from figconvert.gui import FigConvertApp

_STATUS_TEXTS = [
    'x',
    'Cleared.',
    '3 / 40: a_long_figure_name.svg failed — Cannot open: some long error',
]


def _sidebar_width(root, app, text):
    """Show a status text and return the sidebar's width in pixels."""
    app.status_var.set(text)
    root.update()
    sidebar = app.progress.master.master
    return sidebar.winfo_width()


def test_sidebar_width_ignores_status_text():
    """Keep the sidebar width, whatever the status text says."""
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available for Tk')
    try:
        app = FigConvertApp(root)
        widths = {
            _sidebar_width(root, app, text) for text in _STATUS_TEXTS
        }
    finally:
        root.destroy()

    assert len(widths) == 1
