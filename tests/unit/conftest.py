"""Pytest configuration for unit tests."""

import logging
import os
import sys

import pytest
from loguru import logger


@pytest.fixture(scope="session", autouse=True)
def configure_logging():
    """Configure logging for tests to avoid loguru closed file errors."""
    # Disable LiteLLM verbose output
    os.environ["LITELLM_LOG"] = "ERROR"
    logging.getLogger("LiteLLM").setLevel(logging.ERROR)
    logging.getLogger("litellm").setLevel(logging.ERROR)

    # Remove all loguru handlers to avoid "I/O operation on closed file" errors
    logger.remove()

    # Add a simple handler that won't cause issues when closed
    logger.add(
        sys.stderr,
        format="<green>{time:YYYY-MM-DD HH:mm:ss}</green> | <level>{level: <8}</level> | <cyan>{name}</cyan>:<cyan>{function}</cyan>:<cyan>{line}</cyan> - <level>{message}</level>",
        level="WARNING",  # Only show warnings and errors in tests
        enqueue=True,  # Make it thread-safe
    )

    yield

    # Clean up loguru handlers at the end
    logger.remove()
