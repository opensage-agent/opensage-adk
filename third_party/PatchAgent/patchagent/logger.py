import logging

from colorama import Fore, Style, init

from patchagent.utils import debug_mode

init(autoreset=True)
LEVEL_COLORS = {
    logging.DEBUG: Fore.GREEN,
    logging.INFO: Fore.BLUE,
    logging.WARNING: Fore.YELLOW,
    logging.ERROR: Fore.RED,
    logging.CRITICAL: Fore.MAGENTA,
}


class ColoredFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        level_color = LEVEL_COLORS.get(record.levelno, Style.RESET_ALL)
        message = super().format(record)
        return f"{level_color}{message}{Style.RESET_ALL}"


def setup_logger(level: int = logging.DEBUG if debug_mode() else logging.INFO) -> logging.Logger:
    logger = logging.getLogger(__name__)
    logger.setLevel(level)

    handler = logging.StreamHandler()
    handler.setFormatter(ColoredFormatter("[%(asctime)s] %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    logger.addHandler(handler)

    return logger


logger = setup_logger()
