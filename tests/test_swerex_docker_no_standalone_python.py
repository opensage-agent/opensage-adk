#!/usr/bin/env python3
"""Test SWE-ReX Docker deployment without python_standalone_dir and run arvo to test AddressSanitizer.

This test will:
1. Create a DockerDeploymentConfig using the arvo image.
2. Start the deployment – this triggers the fallback behaviour:
   a. Try to call ``swerex-remote`` directly inside the container;
   b. If missing, install *pipx* and run ``pipx run swe-rex``.
3. Wait until the runtime reports ``is_alive``.
4. Run arvo command inside the container via SWE-ReX runtime.
5. Assert that AddressSanitizer error appears in stderr.

Run with::

    pytest tests/test_swerex_docker_no_standalone_python.py -v

Docker must be running and the image ``n132/arvo:67862-vul`` accessible locally
(or on your registry)."""
from __future__ import annotations

import asyncio
import logging
from contextlib import suppress

import pytest
from swerex.deployment.config import DockerDeploymentConfig
from swerex.deployment.docker import DockerDeployment
from swerex.runtime.abstract import BashAction, CreateBashSessionRequest

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s │ %(levelname)-8s │ %(name)s │ %(message)s",
    datefmt="%H:%M:%S",
)


@pytest.fixture
async def docker_deployment():
    """Create and start a Docker deployment for testing."""
    config = DockerDeploymentConfig(
        image="n132/arvo:59618-vul",
        # Do NOT build standalone python
        python_standalone_dir=None,
        # Dynamically pick a free host port
        port=None,
        pull="missing",
        # Keep container after stop so you can inspect logs: docker ps -a
        remove_container=False,
        startup_timeout=300.0,
    )

    deployment = DockerDeployment.from_config(config)

    try:
        await deployment.start()
        alive = await deployment.is_alive()
        assert alive, f"Runtime is not alive: {alive.message}"
        yield deployment
    finally:
        with suppress(Exception):
            await deployment.stop()


@pytest.mark.asyncio
async def test_arvo_sanitizer_error(docker_deployment):
    """Test that running arvo triggers AddressSanitizer error."""
    deployment = docker_deployment

    # Get the runtime from deployment
    runtime = deployment._runtime
    assert runtime is not None, "Runtime is not available"

    # Create a bash session (uses default session name)
    session_req = CreateBashSessionRequest()
    await runtime.create_session(session_req)
    print("Created bash session")

    # Run arvo command using default session
    print(f"\n>>> Executing: arvo")
    action = BashAction(command="arvo", timeout=30.0, check="silent")
    result = await runtime.run_in_session(action)
    # Assert that MemorySanitizer error appears in output
    assert (
        "MemorySanitizer: use-of-uninitialized-value" in result.output
    ), f"Expected MemorySanitizer error not found in output. Output: {result.output}"

    # Print the output for debugging
    if result.output:
        print(f"Output:\n{result.output}")
    if result.exit_code is not None:
        print(f"Exit code: {result.exit_code}")

    print("MemorySanitizer error detected as expected!")


if __name__ == "__main__":
    # Allow running the test directly for debugging
    pytest.main([__file__, "-v"])
