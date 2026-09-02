"""Default privacy policy for normal PORTA use.

Normal use keeps work state in memory only. It must not create bytecode
caches, configure persistent logs, or depend on a writable project folder.
"""

from __future__ import annotations

import logging
import sys


def enable_private_runtime() -> None:
    """Disable runtime bytecode caches and all standard-library log output."""
    sys.dont_write_bytecode = True
    logging.disable(logging.CRITICAL)


def private_logger(name: str) -> logging.Logger:
    """Return a logger that is permanently silent in normal application use."""
    logger = logging.getLogger(name)
    logger.propagate = False
    logger.disabled = True
    return logger
