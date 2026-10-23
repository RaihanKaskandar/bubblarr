# -*- coding: utf-8 -*-

"""
Setting up, using and altering the logger
"""

import logging
import logging.config
from collections import deque
from datetime import datetime
from io import StringIO
from logging.handlers import RotatingFileHandler
from os.path import exists, isdir, isfile, join
from re import compile as re_compile
from threading import Lock
from time import time
from typing import Any, Deque, Dict, List, Union

from backend.base.definitions import Constants


class UpToInfoFilter(logging.Filter):
    def filter(self, record: logging.LogRecord) -> bool:
        return record.levelno <= logging.INFO


class ErrorColorFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> Any:
        result = super().format(record)
        return f"\033[1;31:40m{result}\033[0m"


class MPRotatingFileHandler(RotatingFileHandler):
    def __init__(self,
        filename,
        mode="a",
        maxBytes=0,
        backupCount=0,
        encoding=None,
        delay=False,
        do_rollover=True
    ) -> None:
        self.do_rollover = do_rollover
        return super().__init__(
            filename, mode, maxBytes, backupCount, encoding, delay
        )

    def shouldRollover(self, record: logging.LogRecord) -> int:
        if not self.do_rollover:
            return 0
        return super().shouldRollover(record)


class MemoryLogHandler(logging.Handler):
    """Keeps the most recent log entries in memory, so that they can be shown
    in the web-UI (and read through the API) without parsing the log file.
    Every entry gets an ID that increases, so clients can ask for the entries
    after the last one they've seen.
    """

    MAX_ENTRIES = 5000

    # Format of the log file ("detailed" formatter)
    FILE_LINE = re_compile(
        r'^(\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}[+-]\d{4}) \| [^|]* \| '
        r'([^|]*) \| ([^|]*) \| ([A-Z]+) \| (.*)$'
    )

    def __init__(self) -> None:
        super().__init__(level=logging.DEBUG)
        self.entries: Deque[Dict[str, Any]] = deque(maxlen=self.MAX_ENTRIES)
        self.last_id = 0
        self.entries_lock = Lock()
        # Changes on every start, so clients know the IDs started over
        self.instance = str(round(time() * 1000))
        return

    def _add(
        self,
        timestamp: str,
        level: str,
        thread: str,
        source: str,
        message: str
    ) -> None:
        with self.entries_lock:
            self.last_id += 1
            self.entries.append({
                'id': self.last_id,
                'time': timestamp,
                'level': level,
                'thread': thread,
                'source': source,
                'message': message
            })
        return

    def emit(self, record: logging.LogRecord) -> None:
        try:
            message = record.getMessage()
            if record.exc_info:
                if not record.exc_text:
                    record.exc_text = logging.Formatter().formatException(
                        record.exc_info
                    )
                message += '\n' + record.exc_text

            self._add(
                datetime.fromtimestamp(record.created).astimezone()
                    .isoformat(timespec='seconds'),
                record.levelname,
                record.threadName or '',
                f'{record.filename}L{record.lineno}',
                message
            )
        except Exception:
            self.handleError(record)
        return

    def load_file(self, filepath: str) -> None:
        """Fill the entries with the end of an existing log file, so that the
        logs from before a restart are also available.

        Args:
            filepath (str): The log file in the "detailed" format.
        """
        if not exists(filepath):
            return

        try:
            with open(filepath, 'r', errors='replace') as f:
                lines = f.readlines()
        except OSError:
            return

        parsed: List[List[str]] = []
        for line in lines:
            line = line.rstrip('\n')
            match = self.FILE_LINE.match(line)
            if match:
                parsed.append(list(match.groups()))
            elif parsed:
                # Continuation of a multi-line message, like a traceback
                parsed[-1][4] += '\n' + line

        for timestamp, thread, source, level, message in (
            parsed[-self.MAX_ENTRIES:]
        ):
            try:
                timestamp = datetime.strptime(
                    timestamp, '%Y-%m-%dT%H:%M:%S%z'
                ).isoformat(timespec='seconds')
            except ValueError:
                pass
            self._add(
                timestamp, level.strip(), thread.strip(),
                source.strip(), message
            )
        return

    def get_entries(
        self,
        after: int = 0,
        level: int = logging.DEBUG,
        search: str = '',
        limit: int = 500
    ) -> List[Dict[str, Any]]:
        """Get log entries, oldest first.

        Args:
            after (int, optional): Only entries with an ID higher than this.
                Defaults to 0.
            level (int, optional): Only entries with at least this level.
                Defaults to logging.DEBUG.
            search (str, optional): Only entries that contain this text
                (case insensitive).
                Defaults to ''.
            limit (int, optional): The maximum amount of entries, the newest
                ones are returned.
                Defaults to 500.

        Returns:
            List[Dict[str, Any]]: The entries.
        """
        search = search.lower()
        with self.entries_lock:
            entries = list(self.entries)

        result = []
        for entry in reversed(entries):
            if entry['id'] <= after:
                break
            if logging._nameToLevel.get(entry['level'], 0) < level:
                continue
            if search and not (
                search in entry['message'].lower()
                or search in entry['source'].lower()
            ):
                continue
            result.append(entry)
            if len(result) >= limit:
                break

        result.reverse()
        return result


MEMORY_LOG_HANDLER = MemoryLogHandler()

LOGGER = logging.getLogger(Constants.LOGGER_NAME)
LOGGING_CONFIG = {
    "version": 1,
    "disable_existing_loggers": False,
    "formatters": {
        "simple": {
            "format": "[%(asctime)s][%(levelname)s] %(message)s",
            "datefmt": "%H:%M:%S"
        },
        "simple_red": {
            "()": ErrorColorFormatter,
            "format": "[%(asctime)s][%(levelname)s] %(message)s",
            "datefmt": "%H:%M:%S"
        },
        "detailed": {
            "format": "%(asctime)s | %(processName)s | %(threadName)s | %(filename)sL%(lineno)s | %(levelname)s | %(message)s",
            "datefmt": "%Y-%m-%dT%H:%M:%S%z",
        }
    },
    "filters": {
        "up_to_info": {
            "()": UpToInfoFilter
        }
    },
    "handlers": {
        "console_error": {
            "class": "logging.StreamHandler",
            "level": "WARNING",
            "formatter": "simple_red",
            "stream": "ext://sys.stderr"
        },
        "console": {
            "class": "logging.StreamHandler",
            "level": "DEBUG",
            "formatter": "simple",
            "filters": ["up_to_info"],
            "stream": "ext://sys.stdout"
        },
        "file": {
            "()": MPRotatingFileHandler,
            "level": "DEBUG",
            "formatter": "detailed",
            "filename": "",
            "maxBytes": 1_000_000,
            "backupCount": 1,
            "do_rollover": True
        }
    },
    "loggers": {
        Constants.LOGGER_NAME: {}
    },
    "root": {
        "level": "INFO",
        "handlers": [
            "console",
            "console_error",
            "file"
        ]
    }
}


def setup_logging(
    log_folder: Union[str, None],
    log_file: Union[str, None],
    log_level: Union[int, None] = None,
    do_rollover: bool = True
) -> None:
    """Setup the basic config of the logging module.

    Args:
        log_folder (Union[str, None]): The folder to put the log file in.
            If `None`, the log file will be in the same folder as the
            application folder. It will be created if it doesn't exist yet.

        log_file (Union[str, None]): The filename of the log file.
            If `None`, the default filename will be used.  It will be created if
            it doesn't exist yet.

        log_level (Union[int, None], optional): The log level to set the logger
            to. If `None`, the default level will be used.
            Defaults to None.

        do_rollover (bool, optional): Whether to allow the log file to rollover
            when it reaches the maximum size.
            Defaults to True.

    Raises:
        ValueError: The given log folder is not a folder, or the given log file
            is not a file.
    """
    from backend.base.files import create_folder, folder_path

    if log_folder:
        if exists(log_folder) and not isdir(log_folder):
            raise ValueError("Logging folder is not a folder")

        create_folder(log_folder)

    if log_file:
        if exists(log_file) and not isfile(log_file):
            raise ValueError("Logging file is not a file")
    else:
        log_file = Constants.LOGGER_FILENAME

    if log_folder is None:
        LOGGING_CONFIG["handlers"]["file"]["filename"] = folder_path(log_file)
    else:
        LOGGING_CONFIG["handlers"]["file"]["filename"] = join(
            log_folder,
            log_file
        )

    LOGGING_CONFIG["handlers"]["file"]["do_rollover"] = do_rollover

    logging.config.dictConfig(LOGGING_CONFIG)

    # Keep recent entries in memory for the logs page, starting with what's
    # already in the log file
    if not MEMORY_LOG_HANDLER.entries:
        MEMORY_LOG_HANDLER.load_file(
            LOGGING_CONFIG["handlers"]["file"]["filename"]
        )
    logging.getLogger().addHandler(MEMORY_LOG_HANDLER)

    # Log uncaught exceptions using the logger instead of printing to stderr.
    # Logger goes to stderr anyway, so still visible in console but also logs
    # to file, so that downloaded log file also contains any exceptions.
    import sys
    import threading
    from traceback import format_exception

    def log_uncaught_exceptions(e_type, value, tb):
        LOGGER.error(
            "UNCAUGHT EXCEPTION:\n" +
            ''.join(format_exception(e_type, value, tb))
        )
        return

    def log_uncaught_threading_exceptions(args):
        LOGGER.exception(
            f"UNCAUGHT EXCEPTION IN THREAD: {args.exc_value}"
        )
        return

    sys.excepthook = log_uncaught_exceptions
    threading.excepthook = log_uncaught_threading_exceptions

    if log_level is not None:
        set_log_level(log_level)

    return


def get_log_filepath() -> str:
    """Get the filepath to the log file.

    Returns:
        str: The filepath.
    """
    return LOGGING_CONFIG["handlers"]["file"]["filename"]


def get_log_file_contents() -> StringIO:
    """Get all the logs from the log file(s).

    Raises:
        FileNotFound: The log file does not exist.

    Returns:
        StringIO: The contents of the log file(s).
    """
    from backend.base.custom_exceptions import FileNotFound

    file = get_log_filepath()
    if not exists(file):
        raise FileNotFound(file)

    sio = StringIO()
    for ext in ('.1', ''):
        lf = file + ext
        if not exists(lf):
            continue
        with open(lf, 'r') as f:
            sio.writelines(f)

    return sio


def set_log_level(
    level: Union[int, str]
) -> None:
    """Change the logging level.

    Args:
        level (Union[int, str]): The level to set the logging to.
            Should be a logging level, like `logging.INFO` or `"DEBUG"`.
    """
    if isinstance(level, str):
        level = logging._nameToLevel[level.upper()]

    root_logger = logging.getLogger()
    if root_logger.level == level:
        return

    LOGGER.debug(f'Setting logging level: {level}')
    root_logger.setLevel(level)

    return
