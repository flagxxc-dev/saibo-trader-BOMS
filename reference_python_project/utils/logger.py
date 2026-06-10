"""
Logging utilities.
"""

import logging
from logging.handlers import RotatingFileHandler
import sys

def setup_logging(log_file=None, log_level=logging.INFO):
    handlers = [logging.StreamHandler(sys.stdout)]
    if log_file:
        handlers.append(RotatingFileHandler(log_file, maxBytes=10*1024*1024, backupCount=5))

    if isinstance(log_level, str):
        log_level = log_level.upper()

    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        handlers=handlers
    )

def disable_console_logging():
    for handler in logging.root.handlers[:]:
        if isinstance(handler, logging.StreamHandler) and handler.stream in (sys.stdout, sys.stderr):
            logging.root.removeHandler(handler)

def get_logger(name):
    return logging.getLogger(name)

debug = logging.debug
info = logging.info
warning = logging.warning
warn = logging.warning
error = logging.error
critical = logging.critical

__all__ = [
    "get_logger",
    "setup_logging",
    "disable_console_logging",
    "print_banner",
    "debug",
    "info",
    "warning",
    "warn",
    "error",
    "critical",
]

BANNER = r"""
  ██████╗  ██████╗ ██╗  ██╗   ██╗███╗   ███╗ █████╗ ██████╗ ██╗  ██╗███████╗████████╗
  ██╔══██╗██╔═══██╗██║  ╚██╗ ██╔╝████╗ ████║██╔══██╗██╔══██╗██║ ██╔╝██╔════╝╚══██╔══╝
  ██████╔╝██║   ██║██║   ╚████╔╝ ██╔████╔██║███████║██████╔╝█████╔╝ █████╗     ██║
  ██╔═══╝ ██║   ██║██║    ╚██╔╝  ██║╚██╔╝██║██╔══██║██╔══██╗██╔═██╗ ██╔══╝    ██║
  ██║     ╚██████╔╝███████╗██║   ██║ ╚═╝ ██║██║  ██║██║  ██║██║  ██╗███████╗  ██║
  ╚═╝      ╚═════╝ ╚══════╝╚═╝   ╚═╝     ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚═╝  ╚═╝╚══════╝  ╚═╝

  by Genoshide  |  polymarket arbitrage script bot
"""

def print_banner() -> None:
    """Print the bot startup banner to stdout."""
    try:
        print(BANNER, flush=True)
    except UnicodeEncodeError:
        sys.stdout.buffer.write(BANNER.encode("utf-8", errors="replace") + b"\n")
        sys.stdout.flush()
