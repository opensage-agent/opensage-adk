"""Pytest configuration for unit tests."""

import logging
import os
import sys

import pytest

logger = logging.getLogger(__name__)


@pytest.fixture(scope="session", autouse=True)
def configure_logging():
    """Configure logging for tests."""
    # Disable LiteLLM verbose output
    os.environ["LITELLM_LOG"] = "ERROR"
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    logging.getLogger("litellm").setLevel(logging.ERROR)

    # Configure root logger for tests
    logging.basicConfig(
        level=logging.WARNING,
        format="%(asctime)s | %(levelname)-8s | %(name)s:%(funcName)s:%(lineno)d - %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
        stream=sys.stderr,
    )

    yield
