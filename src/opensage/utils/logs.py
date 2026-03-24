from __future__ import annotations

import logging
import os
import sys
import tempfile
import time

# Logging format with timestamp, level, location, and message
LOGGING_FORMAT = "%(asctime)s | %(levelname)-8s | %(name)s:%(lineno)d - %(message)s"
LOGGING_DATE_FORMAT = "%Y-%m-%d %H:%M:%S"


class ColoredFormatter(logging.Formatter):
    """Custom formatter with colored output for different log levels."""

    # ANSI color codes
    COLORS = {
        "DEBUG": "\033[36m",  # Cyan
        "INFO": "\033[32m",  # Green
        "WARNING": "\033[33m",  # Yellow
        "ERROR": "\033[31m",  # Red
        "CRITICAL": "\033[35m",  # Magenta
    }
    RESET = "\033[0m"

    def format(self, record):
        # Add color to levelname
        levelname = record.levelname
        if levelname in self.COLORS:
            record.levelname = f"{self.COLORS[levelname]}{levelname}{self.RESET}"

        # Format the message
        result = super().format(record)

        # Restore original levelname for other handlers
        record.levelname = levelname
        return result


def setup_opensage_logging(level=None, use_colors=None):
    """Setup logging configuration for OpenSage.

    This function can be called multiple times - it will reconfigure the logger
    each time. This allows users to override the default configuration.

    Args:
        level: Log level (int or string). If None, uses OPENSAGE_LOG_LEVEL env var
               (default: INFO)
        use_colors: Enable colored output. If None, auto-detects based on
                    NO_COLOR env var and stderr.isatty()
    Returns:
        The configured opensage logger

    Examples:
        # Use default configuration
        import opensage

        # Reconfigure after import
        import opensage
        opensage.setup_opensage_logging(level=logging.WARNING)

        # Change log level at runtime
        opensage.setup_opensage_logging(level=logging.DEBUG, use_colors=True)
    """
    opensage_logger = logging.getLogger("opensage")

    # Determine log level
    if level is None:
        log_level_name = os.getenv("OPENSAGE_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, log_level_name, logging.INFO)
    elif isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # Determine color usage
    if use_colors is None:
        use_colors = os.getenv("NO_COLOR") is None and sys.stderr.isatty()

    # Clear existing handlers to allow reconfiguration
    opensage_logger.handlers = []

    # Create and configure handler
    handler = logging.StreamHandler(sys.stderr)
    handler.setLevel(level)

    # Choose formatter based on color preference
    if use_colors:
        formatter = ColoredFormatter(
            fmt=LOGGING_FORMAT,
            datefmt=LOGGING_DATE_FORMAT,
        )
    else:
        formatter = logging.Formatter(
            fmt=LOGGING_FORMAT,
            datefmt=LOGGING_DATE_FORMAT,
        )

    handler.setFormatter(formatter)
    opensage_logger.addHandler(handler)
    opensage_logger.setLevel(level)

    return opensage_logger


def log_to_tmp_folder(
    level=None,
    use_colors=False,
    sub_folder: str = "opensage_logs",
    log_file_prefix: str = "opensage",
    log_file_timestamp: str | None = None,
) -> str:
    """Log to system temp folder instead of stderr.

    This is useful for long-running evaluations where you want to
    keep logs in a file for later inspection.

    Args:
        level: Log level (int or string). If None, uses OPENSAGE_LOG_LEVEL env var
        use_colors: Enable colored output in log file (default: False)
        sub_folder (str): Subdirectory name under system temp folder
        log_file_prefix (str): Prefix for log filename
        log_file_timestamp (str | None): Timestamp for log filename. If None, uses current time
    Returns:
        str: Full path to the log file

    Examples:
        import opensage
        log_path = opensage.log_to_tmp_folder()
        # Logs now go to /tmp/opensage_logs/opensage.20251017_103045.log
        print(f"Logs saved to: {log_path}")

        # Access latest log
        # tail -F /tmp/opensage_logs/opensage.latest.log
    """
    # Determine log level
    if level is None:
        log_level_name = os.getenv("OPENSAGE_LOG_LEVEL", "INFO").upper()
        level = getattr(logging, log_level_name, logging.INFO)
    elif isinstance(level, str):
        level = getattr(logging, level.upper(), logging.INFO)

    # Generate timestamp if not provided
    if log_file_timestamp is None:
        log_file_timestamp = time.strftime("%Y%m%d_%H%M%S")

    # Create log directory and file path
    log_dir = os.path.join(tempfile.gettempdir(), sub_folder)
    log_filename = f"{log_file_prefix}.{log_file_timestamp}.log"
    log_filepath = os.path.join(log_dir, log_filename)

    os.makedirs(log_dir, exist_ok=True)

    # Create file handler
    file_handler = logging.FileHandler(log_filepath, mode="w")
    file_handler.setLevel(level)

    formatter = logging.Formatter(
        fmt=LOGGING_FORMAT,
        datefmt=LOGGING_DATE_FORMAT,
    )

    file_handler.setFormatter(formatter)

    # Configure root logger
    root_logger = logging.getLogger()
    root_logger.setLevel(level)
    root_logger.addHandler(file_handler)

    print(f"Log file setup complete: {log_filepath}")

    # Create symlink to latest log (for easy access)
    latest_log_link = os.path.join(log_dir, f"{log_file_prefix}.latest.log")
    if os.path.islink(latest_log_link):
        os.unlink(latest_log_link)
    os.symlink(log_filepath, latest_log_link)

    print(f"To access latest log: tail -F {latest_log_link}")
    return log_filepath
