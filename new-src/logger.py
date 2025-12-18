from loguru import logger
from rich.logging import RichHandler
import logging
import sys


def setup_logger():
    # Rich handler for readable console logs
    logging.basicConfig(
        level=logging.INFO,
        format="%(message)s",
        datefmt="[%X]",
        handlers=[RichHandler(rich_tracebacks=True)]
    )

    # Silence noisy httpx logs
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)

    # Loguru to stdout
    logger.remove()
    logger.add(sys.stdout, level="INFO", backtrace=False, diagnose=False)
    return logger
