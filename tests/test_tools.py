"""Tests for tool discovery and launching."""

import sys
from importlib import metadata
from types import SimpleNamespace

import pytest
from PyQt6.QtCore import QPoint, Qt
from PyQt6.QtWidgets import QApplication

from academic_kit import gui, tools
from academic_kit.logs import LogTarget
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


def test_launch_tool_passes_log_file(monkeypatch, tmp_path):
    """Run ``python -m <module>`` with this Python and its log file."""
    calls = []

    def fake_popen(command, env):
        calls.append((command, env))

    monkeypatch.setattr(tools.subprocess, 'Popen', fake_popen)
    log_file = tmp_path / 'demo.log'

    launch_tool(Tool(name='demo', module='demo_tool', description=''), log_file)

    expected_command = [
        sys.executable,
        '-m',
        'demo_tool',
    ]
    command, env = calls[0]
    assert command == expected_command
    assert env[tools.LOG_FILE_ENV] == str(log_file)


_DEMO_TOOLS = [
    Tool(name='one', module='one_tool', description='First tool.'),
    Tool(name='two', module='two_tool', description='Second tool.'),
]


def _launcher(qtbot, demo_tools=None, log_targets=None):
    """Show a launcher window for the demo tools."""
    window = gui.LauncherApp(
        _DEMO_TOOLS if demo_tools is None else demo_tools,
        log_targets,
    )
    qtbot.addWidget(window)
    window.show()
    qtbot.waitExposed(window)
    return window


def _tiles(window):
    """Return the window's tool buttons, not the menu bar's own."""
    return window.findChildren(gui._ToolTile)


def _button_boxes(window):
    """Return each tool button's position and size in the window."""
    QApplication.processEvents()
    return [
        (
            button.mapTo(window, QPoint(0, 0)).x(),
            button.mapTo(window, QPoint(0, 0)).y(),
            button.width(),
            button.height(),
        )
        for button in _tiles(window)
    ]


def test_launcher_opens_tool_with_its_log_file(qtbot, monkeypatch, tmp_path):
    """Build one button per tool and start a tool with its own log."""
    started = []

    def fake_launch(tool, log_file):
        started.append((tool, log_file))
        return SimpleNamespace(pid=1234, poll=lambda: None)

    monkeypatch.setattr(gui, 'launch_tool', fake_launch)
    targets = {
        'two': LogTarget(folder=tmp_path, file_name='second.log'),
    }
    window = _launcher(qtbot, log_targets=targets)

    labels = [
        button.text() for button in _tiles(window)
    ]
    second = _tiles(window)[1]
    qtbot.mouseClick(second, Qt.MouseButton.LeftButton)

    assert labels == [
        'one',
        'two',
    ]
    assert started == [(_DEMO_TOOLS[1], tmp_path / 'second.log')]
    assert window.status_label.text() == 'Opened two.'


def test_launcher_buttons_square_centred_fixed(qtbot):
    """Keep square buttons of one size, in a centred row."""
    demo_tools = [
        Tool(name='a', module='a_tool', description=''),
        Tool(name='a much longer tool name', module='b_tool', description=''),
    ]
    window = _launcher(qtbot, demo_tools)
    start_boxes = _button_boxes(window)
    window.resize(900, 600)
    qtbot.waitUntil(lambda: window.width() == 900)
    large_boxes = _button_boxes(window)

    sizes = {
        (width, height) for _x, _y, width, height in start_boxes + large_boxes
    }
    (width, height), = sizes
    assert width == height
    assert width >= 64
    rows = {
        y for _x, y, _width, _height in large_boxes
    }
    assert len(rows) == 1
    left_margin = large_boxes[0][0]
    right_margin = window.width() - (large_boxes[-1][0] + width)
    assert abs(left_margin - right_margin) <= 1


def test_launcher_buttons_stay_when_status_changes(qtbot):
    """Keep the window size and button positions for any status text."""
    long_description = ' '.join(['A very long tool description.'] * 12)
    demo_tools = [
        Tool(name='short', module='short_tool', description='Short.'),
        Tool(name='long', module='long_tool', description=long_description),
    ]
    window = _launcher(qtbot, demo_tools)
    start_size = window.size()
    start_boxes = _button_boxes(window)

    window.status_label.setText(long_description)
    long_boxes = _button_boxes(window)
    long_fits = window.status_label.heightForWidth(window.status_label.width()) <= (
        window.status_label.height()
    )
    window.status_label.setText('x')
    short_boxes = _button_boxes(window)

    assert window.size() == start_size
    assert start_size.width() >= 560
    assert start_size.height() >= 380
    assert long_boxes == start_boxes
    assert short_boxes == start_boxes
    assert long_fits


def test_launcher_hover_shows_description(qtbot):
    """Show a tool's description while the pointer is on its button."""
    window = _launcher(qtbot)
    button = _tiles(window)[0]

    button.hovered.emit(True)
    inside = window.status_label.text()
    button.hovered.emit(False)
    outside = window.status_label.text()

    assert inside == 'First tool.'
    assert outside == 'Choose a tool to open.'


def test_second_copy_of_a_tool_gets_its_own_log(qtbot, monkeypatch, tmp_path):
    """Give a second running copy name-2.log, and reuse a freed name."""
    running = {}
    launched = []

    def fake_launch(tool, log_file):
        launched.append(log_file.name)
        process = SimpleNamespace(pid=len(launched), poll=lambda: running[log_file])
        running[log_file] = None
        return process

    monkeypatch.setattr(gui, 'launch_tool', fake_launch)
    targets = {
        'one': LogTarget(folder=tmp_path, file_name='one.log'),
    }
    window = _launcher(qtbot, log_targets=targets)

    window.open_tool(_DEMO_TOOLS[0])
    window.open_tool(_DEMO_TOOLS[0])
    running[tmp_path / 'one.log'] = 0
    window.open_tool(_DEMO_TOOLS[0])

    assert launched == [
        'one.log',
        'one-2.log',
        'one.log',
    ]


def test_error_dialog_names_current_log(qtbot, monkeypatch, tmp_path):
    """Name the log file in use when the error happens."""
    shown = []
    monkeypatch.setattr(
        gui.QMessageBox,
        'critical',
        lambda _parent, _title, text: shown.append(text),
    )
    monkeypatch.setattr(gui, 'log_uncaught', lambda *_args: None)
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)
    current = [tmp_path / 'first.log']
    gui._install_error_dialog(lambda: current[0])

    current[0] = tmp_path / 'moved.log'
    sys.excepthook(ValueError, ValueError('boom'), None)

    assert str(tmp_path / 'moved.log') in shown[0]
