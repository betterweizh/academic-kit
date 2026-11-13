"""Tests for tool discovery and launching."""

import sys
import tkinter as tk
from importlib import metadata

import pytest

from academic_kit import gui, tools
from academic_kit.tools import ENTRY_POINT_GROUP, Tool, discover_tools, launch_tool


def _entry_point(name, value):
    """Build a launcher entry point that belongs to no package."""
    return metadata.EntryPoint(name=name, value=value, group=ENTRY_POINT_GROUP)


def _fake_entry_points(entry_points):
    """Return a stand-in for ``importlib.metadata.entry_points``."""

    # A closure, so each test can choose which entry points exist.
    def fake(group):
        assert group == ENTRY_POINT_GROUP
        return entry_points

    return fake


def test_discover_tools_sorted_by_name(monkeypatch):
    """List tools alphabetically, ignoring case."""
    fake = _fake_entry_points(
        [
            _entry_point('zeta', 'zeta_tool'),
            _entry_point('Alpha', 'alpha_tool'),
        ]
    )
    monkeypatch.setattr(tools.metadata, 'entry_points', fake)

    found = discover_tools()

    assert found == [
        Tool(name='Alpha', module='alpha_tool', description=''),
        Tool(name='zeta', module='zeta_tool', description=''),
    ]


def test_discover_tools_attribute_raises(monkeypatch):
    """Reject an entry point that names an attribute, not a module."""
    fake = _fake_entry_points([_entry_point('bad', 'bad_tool.gui:main')])
    monkeypatch.setattr(tools.metadata, 'entry_points', fake)

    with pytest.raises(ValueError, match='must name a module'):
        discover_tools()


def test_discover_tools_finds_workspace_tools():
    """Find the tools installed from tools/ with their descriptions."""
    found = {tool.name: tool for tool in discover_tools()}

    expected_names = {
        'doi2ref',
        'figconvert',
    }
    assert expected_names <= set(found)
    assert found['doi2ref'].module == 'doi2ref'
    assert found['figconvert'].description


def test_launch_tool_runs_module_with_same_python(monkeypatch):
    """Run the tool as ``python -m <module>`` with this Python."""
    calls = []
    monkeypatch.setattr(tools.subprocess, 'Popen', calls.append)

    launch_tool(Tool(name='demo', module='demo_tool', description=''))

    expected_command = [
        sys.executable,
        '-m',
        'demo_tool',
    ]
    assert calls == [expected_command]


def test_launcher_app_one_button_per_tool(monkeypatch):
    """Build one button per tool and record started processes."""
    started = []
    monkeypatch.setattr(gui, 'launch_tool', started.append)
    demo_tools = [
        Tool(name='one', module='one_tool', description='First tool.'),
        Tool(name='two', module='two_tool', description='Second tool.'),
    ]
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available for Tk')
    try:
        app = gui.LauncherApp(root, demo_tools)
        frame = root.winfo_children()[0]
        labels = [
            child.cget('text') for child in frame.winfo_children()
            if child.winfo_class() == 'TButton'
        ]
        app.open_tool(demo_tools[1])
        status = app.status_var.get()
    finally:
        root.destroy()

    expected_labels = [
        'one',
        'two',
    ]
    assert labels == expected_labels
    assert started == [demo_tools[1]]
    assert status == 'Opened two.'


def _window_size(root):
    """Return the window's current width and height in pixels."""
    root.update()
    return root.winfo_width(), root.winfo_height()


def test_launcher_app_size_fixed_and_resizable():
    """Keep the window size when the status text changes."""
    long_description = ' '.join(['A very long tool description.'] * 12)
    demo_tools = [
        Tool(name='short', module='short_tool', description='Short.'),
        Tool(name='long', module='long_tool', description=long_description),
    ]
    try:
        root = tk.Tk()
    except tk.TclError:
        pytest.skip('no display available for Tk')
    try:
        app = gui.LauncherApp(root, demo_tools)
        start_size = _window_size(root)
        min_size = root.minsize()
        app.status_var.set(long_description)
        long_text_size = _window_size(root)
        app.status_var.set('x')
        short_text_size = _window_size(root)
        resizable = root.resizable()
        status_fits = app.status_label.winfo_reqheight() <= (
            app.status_label.winfo_height()
        )
    finally:
        root.destroy()

    assert long_text_size == start_size
    assert short_text_size == start_size
    assert min_size == start_size
    assert all(resizable)
    assert status_fits
