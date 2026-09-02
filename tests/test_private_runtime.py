import logging
import sys

from foundation.privacy import enable_private_runtime, private_logger


def test_private_runtime_disables_bytecode_cache_and_logging():
    enable_private_runtime()

    assert sys.dont_write_bytecode is True
    assert logging.root.manager.disable >= logging.CRITICAL


def test_private_logger_is_disabled_and_does_not_propagate():
    logger = private_logger("tests.private")

    assert logger.disabled is True
    assert logger.propagate is False
