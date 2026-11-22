"""Tests for figconvert's log file."""

import logging
import sys
import threading

from figconvert.logs import (
    LOG_FILE_ENV,
    LOG_FILE_NAME,
    default_log_file,
    setup_logging,
)


def _exit_thread():
    """End a thread the normal way, with SystemExit."""
    raise SystemExit


def _fail_in_thread():
    """Raise an error nobody catches, as a crashing worker would."""
    raise ZeroDivisionError('worker crashed')


def test_setup_logging_writes_details_and_crashes(tmp_path, monkeypatch):
    """Keep figconvert's details and thread crashes, not chatter."""
    root = logging.getLogger()
    saved_handlers = list(root.handlers)
    saved_level = root.level
    # setattr with the current value, so monkeypatch restores it.
    monkeypatch.setattr(sys, 'excepthook', sys.excepthook)
    monkeypatch.setattr(threading, 'excepthook', threading.excepthook)
    try:
        log_file = setup_logging(tmp_path / 'logs' / 'run.log')
        logging.getLogger('figconvert.core').debug('step detail')
        logging.getLogger('PIL.Image').debug('library chatter')
        for target, name in (
            (_exit_thread, 'quitter'),
            (_fail_in_thread, 'worker'),
        ):
            thread = threading.Thread(target=target, name=name)
            thread.start()
            thread.join()
    finally:
        for handler in list(root.handlers):
            if handler not in saved_handlers:
                root.removeHandler(handler)
                handler.close()
        root.setLevel(saved_level)
        logging.getLogger('figconvert').setLevel(logging.NOTSET)
        logging.captureWarnings(False)

    assert log_file == tmp_path / 'logs' / 'run.log'
    text = log_file.read_text(encoding='utf-8')
    assert 'step detail' in text
    assert 'library chatter' not in text
    assert 'Unhandled error in thread worker' in text
    assert 'quitter' not in text
    assert 'ZeroDivisionError: worker crashed' in text


def test_setup_logging_without_folder_returns_none(tmp_path):
    """Run without a log when the folder cannot be created."""
    blocker = tmp_path / 'file'
    blocker.write_text('x', encoding='utf-8')

    assert setup_logging(blocker / 'logs' / LOG_FILE_NAME) is None


def test_default_log_file_follows_launcher(monkeypatch, tmp_path):
    """Use the file the launcher passed in its variable."""
    chosen = tmp_path / 'chosen' / 'figures.txt'
    monkeypatch.setenv(LOG_FILE_ENV, str(chosen))

    assert default_log_file() == chosen


def test_default_log_file_is_in_start_folder(monkeypatch, tmp_path):
    """Started on its own, log to the folder the program started in."""
    monkeypatch.delenv(LOG_FILE_ENV, raising=False)
    monkeypatch.chdir(tmp_path)

    assert default_log_file() == tmp_path / LOG_FILE_NAME
