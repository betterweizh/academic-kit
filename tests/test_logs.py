"""Tests for the log files and the Settings > Logs dialog."""

import logging
import sys
import threading
from types import SimpleNamespace

import pytest

from academic_kit import gui, logs
from academic_kit.logs import LAUNCHER_NAME, LogTarget
from academic_kit.tools import Tool

_PROGRAMS = (
    LAUNCHER_NAME,
    'figconvert',
)


@pytest.fixture
def settings_path(tmp_path, monkeypatch):
    """Keep the launcher's settings file inside ``tmp_path``."""
    path = tmp_path / 'config' / 'settings.json'
    monkeypatch.setattr(logs, 'settings_file', lambda: path)
    return path


@pytest.fixture
def clean_logging(monkeypatch):
    """Undo the global logging changes setup_logging makes."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    # setattr with the current value, so monkeypatch restores it.
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)
    monkeypatch.setattr(threading, 'excepthook', threading.excepthook)
    yield
    for handler in list(root.handlers):
        if handler not in saved_handlers:
            root.removeHandler(handler)
            handler.close()
    logs._state.clear()
    root.setLevel(saved_level)
    logging.getLogger('academic_kit').setLevel(logging.NOTSET)
    logging.captureWarnings(False)


def test_settings_default_to_start_folder(settings_path, monkeypatch, tmp_path):
    """Without saved settings, log to <program>.log where it started."""
    monkeypatch.chdir(tmp_path)

    targets = logs.load_log_settings(_PROGRAMS)

    assert targets == {
        LAUNCHER_NAME: LogTarget(folder=tmp_path, file_name='academic-kit.log'),
        'figconvert': LogTarget(folder=tmp_path, file_name='figconvert.log'),
    }


def test_settings_are_remembered_per_program(settings_path, monkeypatch, tmp_path):
    """Remember each program's own folder and file name."""
    monkeypatch.chdir(tmp_path)
    chosen = {
        LAUNCHER_NAME: LogTarget(folder=tmp_path / 'a', file_name='kit.log'),
        'figconvert': LogTarget(folder=tmp_path / 'b', file_name='fig.txt'),
    }

    logs.save_log_settings(chosen)

    assert logs.load_log_settings(_PROGRAMS) == chosen


def test_broken_settings_fall_back(settings_path, monkeypatch, tmp_path):
    """Ignore an unreadable file or bad entries instead of failing."""
    monkeypatch.chdir(tmp_path)
    settings_path.parent.mkdir(parents=True)
    settings_path.write_text(
        '{"logs": {"figconvert": {"folder": "x", "file": "a/b"}}}',
        encoding='utf-8',
    )
    bad_entry = logs.load_log_settings(_PROGRAMS)['figconvert']
    settings_path.write_text('not json', encoding='utf-8')
    not_json = logs.load_log_settings(_PROGRAMS)['figconvert']

    assert bad_entry == logs.default_target('figconvert')
    assert not_json == logs.default_target('figconvert')


@pytest.mark.parametrize(
    ('text', 'expected'),
    [
        ('figconvert.log', 'figconvert.log'),
        (' my log ', 'my log.log'),
        ('trace.txt', 'trace.txt'),
        ('app.', 'app.log'),
        ('console.log', 'console.log'),
    ],
)
def test_check_file_name_accepts_and_adds_suffix(text, expected):
    """Keep good names and add .log to names without a suffix."""
    assert logs.check_file_name(text) == expected


@pytest.mark.parametrize(
    'text',
    [
        '',
        '  ',
        '..',
        'logs/run.log',
        'run?.log',
        'NUL',
        'con.log',
        'com1.txt',
    ],
)
def test_check_file_name_refuses_bad_names(text):
    """Refuse empty names, paths, bad characters and device names."""
    with pytest.raises(ValueError):
        logs.check_file_name(text)


def test_check_targets_refuses_shared_file(tmp_path):
    """Refuse two programs writing to the same file."""
    shared = {
        LAUNCHER_NAME: LogTarget(folder=tmp_path, file_name='all.log'),
        'figconvert': LogTarget(folder=tmp_path, file_name='ALL.log'),
    }
    if sys.platform != 'win32':
        shared['figconvert'] = LogTarget(folder=tmp_path, file_name='all.log')

    with pytest.raises(ValueError, match='both write'):
        logs.check_targets(shared)


def test_setup_logging_moves_to_new_file(clean_logging, tmp_path):
    """Close the old file and continue in the new one."""
    first = logs.setup_logging(tmp_path / 'first' / 'a.log')
    logging.getLogger('academic_kit').info('before the move')
    second = logs.setup_logging(tmp_path / 'second' / 'b.log')
    logging.getLogger('academic_kit').info('after the move')

    first_text = first.read_text(encoding='utf-8')
    assert 'before the move' in first_text
    assert 'after the move' not in first_text
    assert 'after the move' in second.read_text(encoding='utf-8')


def _dialog(qtbot, tmp_path):
    """Return a log settings dialog for the launcher and figconvert."""
    targets = {
        program: LogTarget(folder=tmp_path, file_name=program + '.log')
        for program in _PROGRAMS
    }
    dialog = gui.LogSettingsDialog(targets)
    qtbot.addWidget(dialog)
    return dialog


def test_dialog_reads_rows_and_adds_suffix(qtbot, tmp_path):
    """Read each row's folder and name, adding .log where missing."""
    dialog = _dialog(qtbot, tmp_path)
    dialog.set_field_texts('figconvert', str(tmp_path / 'fig'), 'figures')

    targets = dialog.read_targets()

    assert targets['figconvert'] == LogTarget(
        folder=tmp_path / 'fig',
        file_name='figures.log',
    )
    assert targets[LAUNCHER_NAME].path == tmp_path / 'academic-kit.log'


@pytest.mark.parametrize(
    ('folder', 'name', 'message'),
    [
        ('', 'x.log', 'folder is needed'),
        ('{tmp}', 'bad|name', 'cannot have'),
        ('{tmp}', 'academic-kit.log', 'both write'),
    ],
)
def test_dialog_refuses_invalid_rows(qtbot, tmp_path, folder, name, message):
    """Refuse a missing folder, a bad name or a shared file."""
    dialog = _dialog(qtbot, tmp_path)
    dialog.set_field_texts('figconvert', folder.format(tmp=tmp_path), name)

    with pytest.raises(ValueError, match=message):
        dialog.read_targets()


def test_dialog_use_defaults(qtbot, tmp_path, monkeypatch):
    """Fill every row with the start folder and <program>.log."""
    monkeypatch.chdir(tmp_path)
    dialog = _dialog(qtbot, tmp_path / 'elsewhere')

    dialog.use_defaults()

    assert dialog.field_texts('figconvert') == (str(tmp_path), 'figconvert.log')


def test_apply_log_settings_switches_and_remembers(
    qtbot,
    settings_path,
    clean_logging,
    monkeypatch,
    tmp_path,
):
    """Switch and remember the logs, and pass each tool its file."""
    launched = []

    def fake_launch(tool, log_file):
        launched.append(log_file)
        return SimpleNamespace(pid=1, poll=lambda: None)

    monkeypatch.setattr(gui, 'launch_tool', fake_launch)
    tool = Tool(name='figconvert', module='figconvert', description='')
    window = gui.LauncherApp([tool])
    qtbot.addWidget(window)
    chosen = {
        LAUNCHER_NAME: LogTarget(folder=tmp_path / 'kit', file_name='kit.log'),
        'figconvert': LogTarget(folder=tmp_path / 'fig', file_name='fig.log'),
    }

    applied = window.apply_log_settings(chosen)
    window.open_tool(tool)

    assert applied
    assert (tmp_path / 'kit' / 'kit.log').is_file()
    assert logs.load_log_settings(_PROGRAMS) == chosen
    assert launched == [tmp_path / 'fig' / 'fig.log']
