import logging
import sys
from pathlib import Path

LOG_FILE = Path(__file__).resolve().parent.parent.parent / "data" / "chat.log"

_logger: logging.Logger | None = None


def init() -> logging.Logger:
    global _logger
    LOG_FILE.parent.mkdir(parents=True, exist_ok=True)

    _logger = logging.getLogger("aichat")
    _logger.setLevel(logging.DEBUG)
    _logger.handlers.clear()

    fmt = logging.Formatter(
        "%(asctime)s  %(levelname)-5s  %(message)s",
        datefmt="%H:%M:%S",
    )

    fh = logging.FileHandler(str(LOG_FILE), mode="w", encoding="utf-8")
    fh.setLevel(logging.DEBUG)
    fh.setFormatter(fmt)
    _logger.addHandler(fh)

    sh = logging.StreamHandler(sys.stdout)
    sh.setLevel(logging.DEBUG)
    sh.setFormatter(fmt)
    _logger.addHandler(sh)

    _logger.info("=" * 50)
    _logger.info("AIChat 后端启动")
    return _logger


def get() -> logging.Logger:
    if _logger is None:
        return init()
    return _logger


def close():
    if _logger is not None:
        _logger.info("AIChat 后端关闭")
        for h in _logger.handlers:
            h.close()
