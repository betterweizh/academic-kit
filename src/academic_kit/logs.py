"""Write the launcher's log and keep each program's log settings.

The launcher's log records each start, the tools it finds, each tool it
starts (with its process id) and every error with its traceback. It
also catches errors nobody handled, which a windowed program would
otherwise lose.

Every program has its own log file: a folder and a file name, set in
the launcher's Settings > Logs dialog and remembered in a small
settings file. By default each one is ``<program>.log`` in the folder
the launcher was started from. The launcher passes each tool the full
path of its file in the ``ACADEMIC_KIT_LOG_FILE`` environment variable.

When a file reaches 1 MB it is renamed with ``.1`` added (older ones
``.2``, ``.3``) and a new one is started, so it never grows without
bound.
"""

import json
import logging
import os
import sys
import threading
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from logging.handlers import RotatingFileHandler
from pathlib import Path
from types import TracebackType

__all__ = [
    'LAUNCHER_NAME',
    'LOG_SUFFIX',
    'LogTarget',
    'check_file_name',
    'check_targets',
    'default_target',
    'load_log_settings',
    'log_uncaught',
    'save_log_settings',
    'settings_file',
    'setup_logging',
]

# The launcher's own name in the log settings.
LAUNCHER_NAME = 'academic-kit'
LOG_SUFFIX = '.log'
_SETTINGS_NAME = 'settings.json'
_LOGS_KEY = 'logs'
_FOLDER_KEY = 'folder'
_FILE_KEY = 'file'
# Characters Windows does not allow in file names, and path separators.
_FORBIDDEN_NAME_CHARS = frozenset('<>:"/\\|?*')
_FIRST_PRINTABLE_CODE = 32
# Names Windows keeps for devices, with any suffix: "NUL.log" is NUL.
_DEVICE_NAMES = frozenset(
    {
        'CON',
        'PRN',
        'AUX',
        'NUL',
        *(f'COM{number}' for number in range(1, 10)),
        *(f'LPT{number}' for number in range(1, 10)),
    }
)
_MAX_LOG_BYTES = 1_000_000
_BACKUP_COUNT = 3
_FORMAT = '%(asctime)s %(levelname)-7s [%(threadName)s] %(name)s: %(message)s'
# The launcher's own messages, down to step-by-step details.
_PACKAGE_LOGGER = 'academic_kit'
_LOGGER = logging.getLogger(__name__)
# The file handler this module added, so a new file can replace it.
_state: dict[str, RotatingFileHandler] = {}


@dataclass(frozen=True, slots=True, kw_only=True)
class LogTarget:
    """Where one program writes its log.

    Attributes
    ----------
    folder : Path
        Folder of the log file.
    file_name : str
        Name of the log file, such as ``figconvert.log``.
    """

    folder: Path
    file_name: str

    @property
    def path(self) -> Path:
        """Full path of the log file."""
        return self.folder / self.file_name


def default_target(program: str) -> LogTarget:
    """Return a program's log location when none was chosen.

    Parameters
    ----------
    program : str
        Program name, such as ``academic-kit`` or ``figconvert``.

    Returns
    -------
    LogTarget
        ``<program>.log`` in the folder the launcher was started from.
    """
    return LogTarget(folder=Path.cwd(), file_name=program + LOG_SUFFIX)


def settings_file() -> Path:
    """Return the file that remembers the launcher's settings.

    Returns
    -------
    Path
        ``%APPDATA%/academic-kit/settings.json`` on Windows, otherwise
        ``$XDG_CONFIG_HOME/academic-kit/settings.json`` (default
        ``~/.config``).
    """
    home = Path.home()
    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or str(home / 'AppData' / 'Roaming')
    else:
        base = os.environ.get('XDG_CONFIG_HOME') or str(home / '.config')
    return Path(base) / LAUNCHER_NAME / _SETTINGS_NAME


def load_log_settings(programs: Iterable[str]) -> dict[str, LogTarget]:
    """Return each program's log location, as chosen or by default.

    Parameters
    ----------
    programs : iterable of str
        Program names to look up.

    Returns
    -------
    dict of str to LogTarget
        One entry per program. A program with no valid saved entry, or
        an unreadable settings file, gets ``default_target``.
    """
    settings = _read_settings()
    saved = settings.get(_LOGS_KEY)
    saved_logs = saved if isinstance(saved, dict) else {}
    targets = {}
    for program in programs:
        targets[program] = _target_from(saved_logs.get(program)) or default_target(
            program
        )
    return targets


def save_log_settings(targets: Mapping[str, LogTarget]) -> None:
    """Remember the programs' log locations for later runs.

    Other settings in the file are kept.

    Parameters
    ----------
    targets : mapping of str to LogTarget
        Log location of each program.

    Raises
    ------
    OSError
        If the settings file cannot be written.
    """
    settings = _read_settings()
    settings[_LOGS_KEY] = {
        program: {
            _FOLDER_KEY: str(target.folder),
            _FILE_KEY: target.file_name,
        }
        for program, target in targets.items()
    }
    path = settings_file()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(settings, indent=2), encoding='utf-8')


def check_file_name(text: str) -> str:
    """Check a typed log file name and add ``.log`` if it has no suffix.

    Parameters
    ----------
    text : str
        Name typed in the settings dialog.

    Returns
    -------
    str
        The name without leading spaces or trailing spaces and dots
        (which Windows drops), ending in a suffix.

    Raises
    ------
    ValueError
        If the name is empty, holds a path separator or a character
        Windows does not allow in file names, or is a Windows device
        name such as ``NUL`` or ``COM1``, whose output would be lost.
    """
    name = (
        text
        .strip()
        .rstrip('. ')
    )
    if not name:
        raise ValueError('A log file name is needed.')
    bad = sorted(
        {
            character for character in name
            if character in _FORBIDDEN_NAME_CHARS
            or ord(character) < _FIRST_PRINTABLE_CODE
        }
    )
    if bad:
        shown = ' '.join(repr(character) for character in bad)
        raise ValueError(f'"{name}" holds characters a file name cannot have: {shown}')
    base_name = (
        name
        .split('.')[0]
        .strip()
        .upper()
    )
    if base_name in _DEVICE_NAMES:
        raise ValueError(f'"{name}" is a name Windows keeps for a device.')
    if not Path(name).suffix:
        name += LOG_SUFFIX
    return name


def check_targets(targets: Mapping[str, LogTarget]) -> None:
    """Refuse two programs writing to the same log file.

    Two processes rotating one file would corrupt it.

    Parameters
    ----------
    targets : mapping of str to LogTarget
        Log location of each program.

    Raises
    ------
    ValueError
        If two programs have the same folder and file name.
    """
    owners: dict[str, str] = {}
    for program, target in targets.items():
        key = os.path.normcase(os.path.abspath(target.path))
        if key in owners:
            raise ValueError(
                f'{owners[key]} and {program} would both write to '
                f'{target.path}. Give each program its own file.'
            )
        owners[key] = program


def setup_logging(log_file: Path) -> Path | None:
    """Send log messages to a log file.

    Called again with another file, it closes the old one and continues
    in the new one. The launcher's own messages are kept from DEBUG up;
    other libraries' only from WARNING up. Unhandled errors, in any
    thread, are logged too, unless another hook already handles them.

    Parameters
    ----------
    log_file : Path
        Log file to write; its folder is created if missing.

    Returns
    -------
    Path or None
        The log file, or None if it could not be opened. The launcher
        then runs without a log rather than not at all.
    """
    root = logging.getLogger()
    old_handler = _state.pop('handler', None)
    if old_handler is not None:
        root.removeHandler(old_handler)
        old_handler.close()
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
    root.addHandler(handler)
    _state['handler'] = handler
    root.setLevel(logging.WARNING)
    package_logger = logging.getLogger(_PACKAGE_LOGGER)
    package_logger.setLevel(logging.DEBUG)
    logging.captureWarnings(True)
    # Only replace Python's own hooks: a hook the window set up (which
    # also shows the error) must survive a change of log file.
    if sys.excepthook is sys.__excepthook__:
        sys.excepthook = log_uncaught
    if threading.excepthook is threading.__excepthook__:
        threading.excepthook = _log_uncaught_in_thread
    return log_file


def _read_settings() -> dict[str, object]:
    """Return the settings file's contents, or {} if unreadable."""
    try:
        text = (
            settings_file()
            .read_text(encoding='utf-8')
        )
        settings = json.loads(text)
    except (OSError, ValueError):
        return {}
    return settings if isinstance(settings, dict) else {}


def _target_from(entry: object) -> LogTarget | None:
    """Turn one saved settings entry into a LogTarget, if valid."""
    if not isinstance(entry, dict):
        return None
    folder = entry.get(_FOLDER_KEY)
    file_name = entry.get(_FILE_KEY)
    if not (isinstance(folder, str) and folder and isinstance(file_name, str)):
        return None
    try:
        checked_name = check_file_name(file_name)
    except ValueError:
        return None
    return LogTarget(folder=Path(folder), file_name=checked_name)


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
