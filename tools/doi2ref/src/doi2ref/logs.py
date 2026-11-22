"""Write doi2ref's log to a file, to follow runs and find errors.

The log records each start of the program, each batch with its
settings, each DOI request (source, attempt, retries) with its result,
and every error with its traceback. It also catches errors nobody
handled, which a windowed program would otherwise lose.

The academic-kit launcher's Settings > Logs dialog sets the folder and
name of each tool's log file, and passes the full path to the tools it
starts in the ``ACADEMIC_KIT_LOG_FILE`` environment variable. Started on
its own, doi2ref writes ``doi2ref.log`` in the folder it was
started from.

When the file reaches 1 MB it is renamed with ``.1`` added (older ones
``.2``, ``.3``) and a new one is started, so it never grows without
bound.
"""

import logging
import os
import sys
import threading
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

__all__ = [
    'LOG_FILE_ENV',
    'LOG_FILE_NAME',
    'default_log_file',
    'log_uncaught',
    'setup_logging',
]

LOG_FILE_NAME = 'doi2ref.log'
# Set by the academic-kit launcher to the log file chosen there.
LOG_FILE_ENV = 'ACADEMIC_KIT_LOG_FILE'
_MAX_LOG_BYTES = 1_000_000
_BACKUP_COUNT = 3
_FORMAT = '%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s'
# doi2ref's own messages, down to step-by-step details.
_PACKAGE_LOGGER = 'doi2ref'
_LOGGER = logging.getLogger(__name__)


def default_log_file() -> Path:
    """Return the file doi2ref writes its log to.

    Returns
    -------
    Path
        The file in ``ACADEMIC_KIT_LOG_FILE`` if the launcher set one,
        otherwise ``doi2ref.log`` in the folder the program was
        started from.
    """
    chosen = os.environ.get(LOG_FILE_ENV)
    return Path(chosen) if chosen else Path.cwd() / LOG_FILE_NAME


def setup_logging(log_file: Path | None = None) -> Path | None:
    """Send log messages to the log file and catch unhandled errors.

    doi2ref's own messages are kept from DEBUG up; other libraries'
    only from WARNING up, so their chatter does not fill the file.

    Parameters
    ----------
    log_file : Path or None, default None
        Log file to write; ``default_log_file()`` if None. Its folder
        is created if missing.

    Returns
    -------
    Path or None
        The log file, or None if it could not be opened. doi2ref
        then runs without a log rather than not at all.
    """
    log_file = default_log_file() if log_file is None else log_file
    try:
        log_file.parent.mkdir(parents=True, exist_ok=True)
        handler = RotatingFileHandler(
            log_file,
            maxBytes=_MAX_LOG_BYTES,
            backupCount=_BACKUP_COUNT,
            encoding='utf-8',
        )
    except OSError:
        return None
    handler.setFormatter(logging.Formatter(_FORMAT))
    root = logging.getLogger()
    root.addHandler(handler)
    root.setLevel(logging.WARNING)
    package_logger = logging.getLogger(_PACKAGE_LOGGER)
    package_logger.setLevel(logging.DEBUG)
    # Python warnings become log messages.
    logging.captureWarnings(True)
    sys.excepthook = log_uncaught
    threading.excepthook = _log_uncaught_in_thread
    return log_file


def log_uncaught(
    error_type: type[BaseException],
    error: BaseException,
    traceback: TracebackType | None,
) -> None:
    """Log an error that nothing caught, with its traceback.

    Parameters
    ----------
    error_type : type
        Class of the error.
    error : BaseException
        The error.
    traceback : types.TracebackType or None
        Where it was raised.
    """
    _LOGGER.critical(
        'Unhandled error',
        exc_info=(
            error_type,
            error,
            traceback,
        ),
    )


def _log_uncaught_in_thread(arguments: threading.ExceptHookArgs) -> None:
    """Log an error that ended a background thread.

    A thread ended with ``SystemExit`` is not an error and is skipped,
    as Python's own hook does.
    """
    if arguments.exc_value is None or arguments.exc_type is SystemExit:
        return
    thread_name = arguments.thread.name if arguments.thread else '?'
    _LOGGER.critical(
        'Unhandled error in thread %s',
        thread_name,
        exc_info=(
            arguments.exc_type,
            arguments.exc_value,
            arguments.exc_traceback,
        ),
    )
