"""Tests for the doi2ref window layout."""

import tkinter as tk

import pytest

from doi2ref.gui import Doi2RefApp

_STATUS_TEXTS = [
    'x',
    'Cleared.',
    'Done: 3 of 5 looked up, 2 failed. Failed DOIs stay on the left.',
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
        app = Doi2RefApp(root)
        widths = {
            _sidebar_width(root, app, text) for text in _STATUS_TEXTS
        }
    finally:
        root.destroy()

    assert len(widths) == 1
